"""Typer + Rich command line interface for PySubZone.

Command shape follows PyAddic7's contract so other programs can consume
results: every command accepts ``--json`` and uses stable exit codes
(0 success, 1 nothing found, 2 error).

    subz search dexter --json
    subz versions dexter-resurrection-season-2 -l fa
    subz download dexter-resurrection-season-2/farsi_persian/3770171
    subz url https://subzone.ir/subtitles/the-odyssey-2026 -n 2
    subz filename Dexter.Resurrection.S02E01.1080p.AMZN.WEB-DL-NTb.mkv
    subz auto /media/shows --dry-run
    subz auto /media/shows --map-json results.json
    subz title dexter-resurrection-season-2
    subz languages
    subz config set defaults.min-score 60
    subz tui
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import typer
from rich import box
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from .client import DEFAULT_BASE_URL, DEFAULT_USER_AGENT, SubZoneClient
from .config import (
    CONFIG_SPEC,
    config_path,
    load_config,
    set_config_value,
    unset_config_value,
)
from .exceptions import NoSubtitleFoundError, SubZoneError, TitleNotFoundError
from .languages import DEFAULT_LANGUAGE, display_name, resolve
from .models import DownloadResult, TitleResult
from .parsing import parse_subtitles_url
from .scoring import rank_versions

app = typer.Typer(
    name="subz",
    help="Search and download subtitles from subzone.ir (Subf2m database).",
    add_completion=False,
    no_args_is_help=True,
    rich_markup_mode="rich",
)

EXIT_OK = 0
EXIT_NOT_FOUND = 1
EXIT_ERROR = 2

console = Console()
err_console = Console(stderr=True)

SECTION_STYLE = {
    "exact": "bold green",
    "tv_series": "bold cyan",
    "close": "bold yellow",
    "popular": "bold magenta",
}


class Settings:
    """Global options collected by the app callback."""

    def __init__(
        self,
        base_url: str,
        user_agent: str,
        timeout: float,
        retries: int,
        verbose: bool,
        config: Optional[Dict[str, Any]] = None,
        min_interval: float = 0.0,
        cache_ttl: float = 0.0,
    ) -> None:
        self.base_url = base_url
        self.user_agent = user_agent
        self.timeout = timeout
        self.retries = retries
        self.verbose = verbose
        self.config: Dict[str, Any] = config or {}
        self.min_interval = min_interval
        self.cache_ttl = cache_ttl


def _cfg(settings: Settings, key: str, fallback: Any) -> Any:
    """Config-file value for *key*, falling back when it is not set."""
    return settings.config.get(key, fallback)


def _client(settings: Settings) -> SubZoneClient:
    return SubZoneClient(
        base_url=settings.base_url,
        user_agent=settings.user_agent,
        timeout=settings.timeout,
        max_retries=settings.retries,
        verbose=settings.verbose,
        min_request_interval=settings.min_interval,
        cache_ttl=settings.cache_ttl,
    )


def _emit(data: Any, as_json: bool) -> None:
    if as_json:
        console.print_json(json.dumps(data, ensure_ascii=False, default=str))


def _fail(message: str, code: int) -> int:
    err_console.print(f"[red]{message}[/red]")
    return code


def _resolve_out(out: Optional[Path], source: Optional[Path], out_cwd: bool = False) -> Optional[Path]:
    """Resolve -o/--out for the auto/filename commands.

    A relative path is created inside *source* (the videos folder), so that
    ``-o subs`` collects subtitles in a ``subs`` folder next to the videos.
    Absolute paths are used unchanged, and ``out_cwd=True`` restores the old
    behavior of resolving against the current directory.  ``None`` means no
    output folder was requested (caller falls back to its own default).
    """
    if out is None:
        return None
    if out.is_absolute() or out_cwd or source is None:
        return out
    return (source / out).resolve()


def _is_slug_like(value: str) -> bool:
    return bool(value) and all(c.isalnum() or c in "-_" for c in value) and " " not in value


def _resolve_title_arg(client: SubZoneClient, title: str) -> TitleResult:
    """Accept a slug or a show/movie name; always return a TitleResult."""
    if title.startswith("/subtitles"):
        slug, _lang, _sub = parse_subtitles_url(title)
        return TitleResult(name=slug, slug=slug, url=f"/subtitles/{slug}")
    if _is_slug_like(title):
        try:
            page = client.title(title)
            return TitleResult(
                name=page.name, slug=page.slug, year=page.year, url=f"/subtitles/{page.slug}"
            )
        except (TitleNotFoundError, SubZoneError):
            pass  # fall through to search
    return client.search_exact(title)


def _print_download_result(result: DownloadResult) -> None:
    v = result.version
    console.print(
        f"[green]Saved[/green] {v.language or display_name(v.lang)} subtitle "
        f"(by {v.author or '?'}) for [bold]{v.slug}[/bold]"
    )
    if result.zip_path:
        console.print(f"  zip: {result.zip_path} ({result.bytes_downloaded:,} bytes)")
    for p in result.srt_paths:
        console.print(f"  saved: {p}")


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

@app.command()
def search(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="Show or movie title to look for."),
    lang: Optional[str] = typer.Option(
        None, "--lang", "-l", help="Only titles having subtitles in this language (default: config defaults.language)."
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON instead of a table."),
) -> None:
    """Search subzone.ir for movie/series titles."""
    client = _client(ctx.obj)
    try:
        results = client.search(query, lang=lang or _cfg(ctx.obj, "defaults.language", None))
    except TitleNotFoundError:
        if json_out:
            _emit([], True)
        raise typer.Exit(_fail(f"No title matches {query!r}.", EXIT_NOT_FOUND))
    except SubZoneError as exc:
        raise typer.Exit(_fail(str(exc), EXIT_ERROR))

    if json_out:
        _emit([r.as_dict() for r in results], True)
        return

    table = Table(title=f"Search results for {query!r}", box=box.SIMPLE_HEAVY)
    table.add_column("section", style="dim")
    table.add_column("title")
    table.add_column("year", justify="right")
    table.add_column("subs", justify="right")
    table.add_column("slug", style="dim")
    for r in results:
        style = SECTION_STYLE.get(r.section, "dim")
        table.add_row(
            f"[{style}]{r.section}[/]",
            r.name,
            str(r.year or "-"),
            str(r.subtitle_count if r.subtitle_count is not None else "-"),
            r.slug,
        )
    console.print(table)
    console.print("[dim]Next: subz versions <slug>[/dim]")


# ---------------------------------------------------------------------------
# versions
# ---------------------------------------------------------------------------

@app.command()
def versions(
    ctx: typer.Context,
    title: str = typer.Argument(..., help="Title slug (dexter-resurrection-season-2) or a show name."),
    lang: Optional[str] = typer.Option(
        None, "--lang", "-l", help="Only list versions in this language (default: config defaults.language)."
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON instead of a table."),
) -> None:
    """List subtitle versions available for a title."""
    client = _client(ctx.obj)
    try:
        resolved = _resolve_title_arg(client, title)
        version_list = client.title_versions(resolved.slug, lang or _cfg(ctx.obj, "defaults.language", None))
    except (TitleNotFoundError, NoSubtitleFoundError) as exc:
        raise typer.Exit(_fail(str(exc), EXIT_NOT_FOUND))
    except SubZoneError as exc:
        raise typer.Exit(_fail(str(exc), EXIT_ERROR))

    if json_out:
        _emit([v.as_dict() for v in version_list], True)
        return

    heading = f"Subtitles for {resolved.name}"
    if resolved.year:
        heading += f" ({resolved.year})"
    table = Table(title=heading, box=box.SIMPLE_HEAVY)
    table.add_column("language")
    table.add_column("rate")
    table.add_column("author")
    table.add_column("releases", overflow="fold")
    table.add_column("ref", style="dim", overflow="fold")
    for v in version_list:
        rate = {"positive": "[green]good[/green]", "negative": "[red]bad[/red]"}.get(v.rate, "·")
        releases = "; ".join(r[:42] for r in v.releases[:2])
        if len(v.releases) > 2:
            releases += f" (+{len(v.releases) - 2} more)"
        table.add_row(v.language or display_name(v.lang), rate, v.author, releases, v.ref)
    console.print(table)
    console.print("[dim]Next: subz download <ref>[/dim]")


# ---------------------------------------------------------------------------
# title
# ---------------------------------------------------------------------------

@app.command("title")
def title_cmd(
    ctx: typer.Context,
    title: str = typer.Argument(..., help="Title slug (dexter-resurrection-season-2) or a show/movie name."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON instead of a table."),
) -> None:
    """Show a title's details and which languages have subtitles."""
    client = _client(ctx.obj)
    try:
        resolved = _resolve_title_arg(client, title)
        page = client.title(resolved.slug)
    except (TitleNotFoundError, NoSubtitleFoundError) as exc:
        raise typer.Exit(_fail(str(exc), EXIT_NOT_FOUND))
    except SubZoneError as exc:
        raise typer.Exit(_fail(str(exc), EXIT_ERROR))

    if json_out:
        payload = page.as_dict()
        payload["url"] = f"{client.base_url}/subtitles/{page.slug}"
        _emit(payload, True)
        return

    heading = f"{page.name}"
    if page.year:
        heading += f" ({page.year})"
    meta = Table(title=heading, box=box.SIMPLE_HEAVY, show_header=False)
    meta.add_column("field", style="dim")
    meta.add_column("value", overflow="fold")
    meta.add_row("title page", f"{client.base_url}/subtitles/{page.slug}")
    if page.imdb_id:
        meta.add_row("imdb", page.imdb_id)
    meta.add_row("versions", str(len(page.versions)))
    console.print(meta)

    lang_table = Table(title="Languages", box=box.SIMPLE_HEAVY)
    lang_table.add_column("language")
    lang_table.add_column("slug", style="dim")
    lang_table.add_column("subs", justify="right")
    for tab in page.languages:
        lang_table.add_row(
            display_name(tab["slug"]),
            tab["slug"],
            str(tab.get("count", "-")),
        )
    console.print(lang_table)
    console.print("[dim]Next: subz versions <slug> -l <slug>[/dim]")


# ---------------------------------------------------------------------------
# download / url
# ---------------------------------------------------------------------------

def _download_impl(
    client: SubZoneClient,
    target: str,
    lang: Optional[str],
    limit: int,
    out: Path,
    save_zip: bool,
    lang_suffix: Optional[str],
    no_suffix: bool,
) -> List[DownloadResult]:
    """Shared logic for `download` and `url`: resolve target -> downloads."""
    kwargs = dict(
        dest_dir=out,
        save_mode="zip" if save_zip else "extract",
        lang_suffix=lang_suffix,
        no_suffix=no_suffix,
    )
    stripped = target.strip().strip("/")
    parts = stripped.split("/")

    is_url = stripped.startswith(("http://", "https://", "/subtitles"))
    if is_url:
        slug, url_lang, sub_id = parse_subtitles_url(stripped)
    elif len(parts) == 3 and parts[2].isdigit():
        slug, url_lang, sub_id = parts[0], parts[1], parts[2]
    elif len(parts) >= 1 and _is_slug_like(stripped):
        slug, url_lang, sub_id = stripped, None, None
    else:
        raise ValueError(
            "Target must be a '<slug>/<lang>/<id>' ref, a subzone.ir URL, "
            "or a title slug."
        )

    if sub_id:
        return [client.download(f"{slug}/{url_lang}/{sub_id}", **kwargs)]
    # title-level target: grab the newest versions of one language
    effective_lang = lang or url_lang or DEFAULT_LANGUAGE
    return client.download_url(f"/subtitles/{slug}", lang=effective_lang, limit=limit, **kwargs)


@app.command()
def download(
    ctx: typer.Context,
    target: str = typer.Argument(..., help="'<slug>/<lang>/<id>' ref, any subzone.ir URL, or a title slug."),
    lang: Optional[str] = typer.Option(None, "--lang", "-l", help="Language for title slugs/URLs (default: config defaults.language or farsi_persian)."),
    limit: int = typer.Option(1, "--limit", "-n", help="How many versions to grab from title slugs/URLs (0 = all)."),
    out: Optional[Path] = typer.Option(None, "--out", "-o", help="Output directory (default: current directory, or config defaults.out)."),
    save_zip: Optional[bool] = typer.Option(None, "--zip/--no-zip", help="Keep the downloaded zip instead of extracting it (default: config defaults.zip)."),
    lang_suffix: Optional[str] = typer.Option(None, "--lang-suffix", help="Suffix for extracted files, e.g. 'fa' (default: derived from language)."),
    no_suffix: Optional[bool] = typer.Option(None, "--no-suffix/--with-suffix", help="Name files 'Movie.srt' instead of 'Movie.fa.srt' (default: config defaults.no-suffix)."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Download a subtitle by ref, URL or title slug."""
    cfg = ctx.obj.config
    out = out if out is not None else Path(_cfg(ctx.obj, "defaults.out", ".") or ".")
    save_zip = bool(save_zip if save_zip is not None else cfg.get("defaults.zip", False))
    lang_suffix = lang_suffix if lang_suffix is not None else (cfg.get("defaults.lang-suffix") or None)
    no_suffix = bool(no_suffix if no_suffix is not None else cfg.get("defaults.no-suffix", False))
    lang = lang or _cfg(ctx.obj, "defaults.language", None)
    client = _client(ctx.obj)
    try:
        results = _download_impl(client, target, lang, limit, out, save_zip, lang_suffix, no_suffix)
    except (NoSubtitleFoundError, TitleNotFoundError) as exc:
        raise typer.Exit(_fail(str(exc), EXIT_NOT_FOUND))
    except (SubZoneError, ValueError) as exc:
        raise typer.Exit(_fail(str(exc), EXIT_ERROR))

    if json_out:
        _emit([r.as_dict() for r in results], True)
        return
    for r in results:
        _print_download_result(r)


@app.command()
def url(
    ctx: typer.Context,
    url_value: str = typer.Argument(..., help="Any subzone.ir subtitles URL (version or title page)."),
    lang: Optional[str] = typer.Option(None, "--lang", "-l", help="Language for title URLs (default: config defaults.language or farsi_persian)."),
    limit: int = typer.Option(1, "--limit", "-n", help="How many versions to grab from title URLs (0 = all)."),
    out: Optional[Path] = typer.Option(None, "--out", "-o", help="Output directory (default: current directory, or config defaults.out)."),
    save_zip: Optional[bool] = typer.Option(None, "--zip/--no-zip", help="Keep the downloaded zip instead of extracting it (default: config defaults.zip)."),
    lang_suffix: Optional[str] = typer.Option(None, "--lang-suffix", help="Suffix for extracted files, e.g. 'fa'."),
    no_suffix: Optional[bool] = typer.Option(None, "--no-suffix/--with-suffix", help="Name files 'Movie.srt' instead of 'Movie.fa.srt' (default: config defaults.no-suffix)."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """'URL grab': paste any subzone.ir subtitles URL and download it."""
    cfg = ctx.obj.config
    out = out if out is not None else Path(_cfg(ctx.obj, "defaults.out", ".") or ".")
    save_zip = bool(save_zip if save_zip is not None else cfg.get("defaults.zip", False))
    lang_suffix = lang_suffix if lang_suffix is not None else (cfg.get("defaults.lang-suffix") or None)
    no_suffix = bool(no_suffix if no_suffix is not None else cfg.get("defaults.no-suffix", False))
    lang = lang or _cfg(ctx.obj, "defaults.language", None)
    client = _client(ctx.obj)
    try:
        results = _download_impl(client, url_value, lang, limit, out, save_zip, lang_suffix, no_suffix)
    except (NoSubtitleFoundError, TitleNotFoundError) as exc:
        raise typer.Exit(_fail(str(exc), EXIT_NOT_FOUND))
    except (SubZoneError, ValueError) as exc:
        raise typer.Exit(_fail(str(exc), EXIT_ERROR))

    if json_out:
        _emit([r.as_dict() for r in results], True)
        return
    for r in results:
        _print_download_result(r)


# ---------------------------------------------------------------------------
# filename
# ---------------------------------------------------------------------------

@app.command("filename")
def filename(
    ctx: typer.Context,
    file: str = typer.Argument(..., help="Video file path or release name."),
    lang: Optional[str] = typer.Option(None, "--lang", "-l", help="Subtitle language (default: config defaults.language or farsi_persian)."),
    out: Optional[Path] = typer.Option(None, "--out", "-o", help="Output folder (relative: inside the video's folder; absolute: as-is; default: beside the video, or config defaults.out)."),
    out_cwd: bool = typer.Option(False, "--out-cwd", help="Resolve a relative --out against the current directory instead of the video's folder."),
    save_zip: Optional[bool] = typer.Option(None, "--zip/--no-zip", help="Keep the downloaded zip instead of extracting it (default: config defaults.zip)."),
    lang_suffix: Optional[str] = typer.Option(None, "--lang-suffix", help="Suffix for extracted files, e.g. 'fa'."),
    no_suffix: Optional[bool] = typer.Option(None, "--no-suffix/--with-suffix", help="Name files 'Movie.srt' instead of 'Movie.fa.srt' (default: config defaults.no-suffix)."),
    no_download: bool = typer.Option(False, "--no-download", help="Only show matching candidates."),
    min_score: Optional[int] = typer.Option(None, "--min-score", help="Minimum match score 0-100 to accept the best version (default: config defaults.min-score, 0 = accept every best match)."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Find the best subtitle for one video file / release name."""
    from .matcher import guess_season_episode, guess_title

    client = _client(ctx.obj)
    cfg = ctx.obj.config
    stem = Path(file).stem
    query = guess_title(stem)
    lang_slug = resolve(lang or _cfg(ctx.obj, "defaults.language", None)) or DEFAULT_LANGUAGE
    real_file = Path(file).is_file()
    # Relative -o paths are created inside the video's folder; absolute paths
    # are used as-is; --out-cwd restores current-directory-relative behavior.
    source = Path(file).parent if real_file else None
    out_value = out if out is not None else (Path(cfg["defaults.out"]) if cfg.get("defaults.out") else Path("."))
    dest_dir = _resolve_out(out_value, source, out_cwd) or (source or Path("."))
    min_score = int(min_score if min_score is not None else cfg.get("defaults.min-score", 0))
    save_zip = bool(save_zip if save_zip is not None else cfg.get("defaults.zip", False))
    lang_suffix = lang_suffix if lang_suffix is not None else (cfg.get("defaults.lang-suffix") or None)
    no_suffix = bool(no_suffix if no_suffix is not None else cfg.get("defaults.no-suffix", False))
    if not json_out:
        console.print(
            f"[dim]query:[/dim] [bold]{query}[/bold]   "
            f"[dim]language:[/dim] {display_name(lang_slug)}"
        )

    try:
        title, best, score = client.best_for_file(stem, lang=lang_slug)
        page = client.title(f"{title.slug}/{lang_slug}")
        ranked = rank_versions(stem, page.versions)[:5]
    except (NoSubtitleFoundError, TitleNotFoundError) as exc:
        raise typer.Exit(_fail(str(exc), EXIT_NOT_FOUND))
    except SubZoneError as exc:
        raise typer.Exit(_fail(str(exc), EXIT_ERROR))

    if json_out:
        payload: Dict[str, Any] = {
            "query": query,
            "title": title.as_dict(),
            "best": {**best.as_dict(), "score": score},
            "candidates": [{"score": s, **v.as_dict()} for s, v in ranked],
        }
        if not no_download and score < min_score:
            payload["skipped"] = (
                f"best score {score} is below min-score {min_score}"
            )
            _emit(payload, True)
            raise typer.Exit(EXIT_NOT_FOUND)
        elif not no_download:
            result = client.download(
                best,
                dest_dir=dest_dir,
                save_mode="zip" if save_zip else "extract",
                lang_suffix=lang_suffix,
                no_suffix=no_suffix,
                target_video=file if real_file else None,
            )
            payload["download"] = result.as_dict()
        _emit(payload, True)
        return

    table = Table(title="Top matching versions", box=box.SIMPLE_HEAVY)
    table.add_column("score", justify="right", style="bold cyan")
    table.add_column("language")
    table.add_column("author")
    table.add_column("releases", overflow="fold")
    table.add_column("ref", style="dim")
    for s, v in ranked:
        mark = " [green]<- best[/green]" if v.ref == best.ref else ""
        table.add_row(
            str(s),
            v.language or display_name(v.lang),
            v.author,
            ", ".join(v.releases[:2]) or "-",
            f"{v.ref}{mark}",
        )
    console.print(table)

    if no_download:
        return
    if score < min_score:
        raise typer.Exit(
            _fail(
                f"best score {score} is below min-score {min_score}; nothing downloaded "
                "(lower --min-score to accept this match)",
                EXIT_NOT_FOUND,
            )
        )
    try:
        result = client.download(
            best,
            dest_dir=dest_dir,
            save_mode="zip" if save_zip else "extract",
            lang_suffix=lang_suffix,
            no_suffix=no_suffix,
            target_video=file if real_file else None,
        )
    except (SubZoneError, ValueError) as exc:
        raise typer.Exit(_fail(str(exc), EXIT_ERROR))
    _print_download_result(result)


# ---------------------------------------------------------------------------
# auto
# ---------------------------------------------------------------------------

@app.command()
def auto(
    ctx: typer.Context,
    path: Path = typer.Argument(..., help="Video file or a folder containing videos."),
    lang: Optional[str] = typer.Option(None, "--lang", "-l", help="Subtitle language (default: config defaults.language or farsi_persian)."),
    out: Optional[Path] = typer.Option(None, "--out", "-o", help="Output folder for subtitles (relative: inside the videos folder; absolute: as-is; default: beside each video, or config defaults.out)."),
    out_cwd: bool = typer.Option(False, "--out-cwd", help="Resolve a relative --out against the current directory instead of the videos folder."),
    save_zip: Optional[bool] = typer.Option(None, "--zip/--no-zip", help="Keep the downloaded zips instead of extracting them (default: config defaults.zip)."),
    lang_suffix: Optional[str] = typer.Option(None, "--lang-suffix", help="Suffix for extracted files, e.g. 'fa'."),
    no_suffix: Optional[bool] = typer.Option(None, "--no-suffix/--with-suffix", help="Name files 'Movie.srt' instead of 'Movie.fa.srt' (default: config defaults.no-suffix)."),
    min_score: Optional[int] = typer.Option(None, "--min-score", help="Minimum match score 0-100; lower-scoring videos are skipped (default: config defaults.min-score, 0 = accept every best match)."),
    parallel: Optional[int] = typer.Option(None, "--parallel", "-w", help="Download for this many videos concurrently, 1-8 (default: config defaults.workers)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Only show the matches, download nothing."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    map_json: Optional[str] = typer.Option(None, "--map-json", metavar="[PATH]", help="Write the per-video result map as JSON to PATH ('-' = stdout; shape: results + summary)."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Auto-match every video in PATH and download fitting subtitles."""
    from .matcher import find_videos

    cfg = ctx.obj.config
    lang_slug = resolve(lang or _cfg(ctx.obj, "defaults.language", None)) or DEFAULT_LANGUAGE
    out_value = out if out is not None else (Path(cfg["defaults.out"]) if cfg.get("defaults.out") else None)
    min_score = int(min_score if min_score is not None else cfg.get("defaults.min-score", 0))
    workers = int(parallel if parallel is not None else cfg.get("defaults.workers", 1))
    workers = max(1, min(8, workers))
    save_zip = bool(save_zip if save_zip is not None else cfg.get("defaults.zip", False))
    lang_suffix = lang_suffix if lang_suffix is not None else (cfg.get("defaults.lang-suffix") or None)
    no_suffix = bool(no_suffix if no_suffix is not None else cfg.get("defaults.no-suffix", False))
    try:
        videos = find_videos(path)
    except (ValueError, FileNotFoundError) as exc:
        raise typer.Exit(_fail(str(exc), EXIT_ERROR))
    if not videos:
        raise typer.Exit(_fail(f"No video files found under {path}", EXIT_NOT_FOUND))

    if not json_out and map_json != "-":
        console.print(f"[bold]{len(videos)} video file(s)[/bold] under {path}")
        for v in videos:
            console.print(f"  [dim]-[/dim] {v.name}")
        note = "  [dim](dry run)[/dim]" if dry_run else ""
        if workers > 1:
            note += f"  [dim](parallel x{workers})[/dim]"
        console.print(f"[dim]language: {display_name(lang_slug)}{note}[/dim]")
        if not dry_run and not yes and sys.stdin.isatty():
            if not typer.confirm("Fetch and save subtitles for these files?"):
                return

    client = _client(ctx.obj)

    def _match_one(video: Path) -> Dict[str, Any]:
        row: Dict[str, Any] = {"video": str(video), "title": None, "status": "ok"}
        try:
            title, best, score = client.best_for_file(video.stem, lang=lang_slug)
            row.update({"title": title.name, "ref": best.ref, "score": score})
            if score < min_score:
                row["status"] = "skipped"
                row["error"] = f"score {score} is below min-score {min_score}"
            elif dry_run:
                row["status"] = "dry-run"
            else:
                dest = _resolve_out(out_value, video.parent, out_cwd) or video.parent
                result = client.download(
                    best,
                    dest_dir=dest,
                    save_mode="zip" if save_zip else "extract",
                    lang_suffix=lang_suffix,
                    no_suffix=no_suffix,
                    target_video=video,
                )
                row["saved"] = result.srt_paths or result.zip_path
        except (NoSubtitleFoundError, TitleNotFoundError) as exc:
            row["status"] = "not found"
            row["error"] = str(exc)
        except Exception as exc:  # noqa: BLE001 - one bad video must not stop the batch
            row["status"] = "error"
            row["error"] = str(exc)
        return row

    progress = Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
        disable=json_out or map_json == "-",
    )
    rows: List[Dict[str, Any]] = [None] * len(videos)  # type: ignore[list-item]
    with progress:
        task = progress.add_task("matching", total=len(videos))
        if workers > 1:
            from concurrent.futures import ThreadPoolExecutor, as_completed

            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(_match_one, video): i for i, video in enumerate(videos)}
                for future in as_completed(futures):
                    rows[futures[future]] = future.result()
                    progress.advance(task)
        else:
            for i, video in enumerate(videos):
                progress.update(task, description=f"[cyan]{video.name[:40]}")
                rows[i] = _match_one(video)
                progress.advance(task)

    map_payload: Optional[Dict[str, Any]] = None
    if map_json is not None:
        counts: Dict[str, int] = {}
        for row in rows:
            counts[row["status"]] = counts.get(row["status"], 0) + 1
        map_payload = {
            "path": str(path),
            "language": lang_slug,
            "min_score": min_score,
            "workers": workers,
            "dry_run": dry_run,
            "results": rows,
            "summary": {"total": len(rows), **counts},
        }

    if map_json == "-":
        # machine-readable stdout: no tables, no progress noise
        print(json.dumps(map_payload, ensure_ascii=False, indent=2))
    elif json_out:
        _emit(rows, True)
    else:
        summary = Table(title="Auto-match summary", box=box.SIMPLE_HEAVY)
        summary.add_column("video")
        summary.add_column("title")
        summary.add_column("score", justify="right")
        summary.add_column("status")
        summary.add_column("saved", overflow="fold")
        for row in rows:
            style = {"ok": "green", "dry-run": "cyan", "not found": "yellow", "skipped": "yellow"}.get(
                row["status"], "red"
            )
            saved = ", ".join(row.get("saved") or []) or row.get("error", "") or "-"
            summary.add_row(
                Path(row["video"]).name,
                row.get("title") or "-",
                str(row.get("score", "-")),
                f"[{style}]{row['status']}[/{style}]",
                saved,
            )
        console.print(summary)
        if map_payload is not None:
            map_path = Path(map_json).expanduser()
            map_path.parent.mkdir(parents=True, exist_ok=True)
            map_path.write_text(
                json.dumps(map_payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            console.print(f"[green]Wrote[/green] result map -> {map_path}")

    saw_error = any(row["status"] == "error" for row in rows)
    saw_missing = any(row["status"] in ("not found", "skipped") for row in rows)
    if saw_error:
        raise typer.Exit(EXIT_ERROR)
    if saw_missing:
        raise typer.Exit(EXIT_NOT_FOUND)


# ---------------------------------------------------------------------------
# popular / latest
# ---------------------------------------------------------------------------

@app.command()
def popular(
    ctx: typer.Context,
    mode: str = typer.Option("popular", "--mode", help="popular or latest."),
    kind: str = typer.Option("all", "--type", "-t", help="all, film, series or music."),
    page: int = typer.Option(1, "--page", "-p", help="Page number."),
    lang: Optional[str] = typer.Option(None, "--lang", "-l", help="Only rows in this language."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON instead of a table."),
) -> None:
    """Browse the popular/latest subtitle uploads."""
    client = _client(ctx.obj)
    try:
        entries = client.browse(mode=mode, kind=kind, page=page, lang=lang)
    except ValueError as exc:
        raise typer.Exit(_fail(str(exc), EXIT_ERROR))
    except SubZoneError as exc:
        raise typer.Exit(_fail(str(exc), EXIT_ERROR))

    if json_out:
        _emit([e.as_dict() for e in entries], True)
        return
    table = Table(
        title=f"{mode.title()} uploads ({kind}) - page {page}",
        box=box.SIMPLE_HEAVY,
    )
    table.add_column("title")
    table.add_column("language")
    table.add_column("type")
    table.add_column("owner", style="dim")
    table.add_column("date", style="dim")
    table.add_column("ref", style="dim")
    for e in entries:
        table.add_row(
            e.title_name,
            e.version.language,
            e.media_type,
            e.owner,
            e.date,
            e.version.ref,
        )
    console.print(table)
    console.print("[dim]Next: subz download <ref>[/dim]")


# ---------------------------------------------------------------------------
# languages
# ---------------------------------------------------------------------------

@app.command("languages")
def languages_cmd(
    json_out: bool = typer.Option(False, "--json", help="Emit JSON instead of a table."),
) -> None:
    """List every subtitle language the engine understands (with aliases)."""
    from .languages import DISPLAY_NAMES, codes_for, known_languages

    slugs = known_languages()
    if json_out:
        _emit(
            [{"slug": slug, "name": DISPLAY_NAMES[slug], "codes": codes_for(slug)} for slug in slugs],
            True,
        )
        return
    table = Table(title=f"{len(slugs)} known languages", box=box.SIMPLE_HEAVY)
    table.add_column("language")
    table.add_column("slug", style="bold cyan")
    table.add_column("short codes / aliases", style="dim")
    for slug in slugs:
        codes = ", ".join(codes_for(slug))
        table.add_row(DISPLAY_NAMES[slug], slug, codes or "-")
    console.print(table)
    console.print(
        "[dim]Use any slug, name or code with -l/--lang, e.g.:[/dim] "
        "[bold]subz versions dexter-resurrection-season-2 -l fa[/bold]"
    )


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------

config_app = typer.Typer(
    help="Read and write the config file whose values every command uses as defaults.",
    no_args_is_help=True,
    add_completion=False,
)
app.add_typer(config_app, name="config")


@config_app.command("path")
def config_path_cmd() -> None:
    """Print the config file location."""
    console.print(config_path())


@config_app.command("show")
def config_show() -> None:
    """Show the config file location and every value stored in it."""
    path = config_path()
    try:
        cfg = load_config(strict=True)
    except ValueError as exc:
        raise typer.Exit(_fail(str(exc), EXIT_ERROR))
    if not cfg:
        console.print(
            f"[dim]config file:[/dim] {path}\n"
            "[dim](empty - every option keeps its built-in default)\n"
            "[dim]set values with:[/dim] subz config set <key> <value>"
        )
        return
    table = Table(title=f"Config: {path}", box=box.SIMPLE_HEAVY)
    table.add_column("key", style="bold cyan")
    table.add_column("value", overflow="fold")
    table.add_column("type", style="dim")
    table.add_column("description", overflow="fold", style="dim")
    for key in sorted(cfg):
        spec = CONFIG_SPEC.get(key)
        table.add_row(key, str(cfg[key]), type(cfg[key]).__name__, spec[2] if spec else "-")
    console.print(table)


@config_app.command("set")
def config_set(
    key: str = typer.Argument(..., help="Dotted key, e.g. defaults.language."),
    value: str = typer.Argument(..., help="Value to store (true/false, numbers, text)."),
) -> None:
    """Store one key in the config file."""
    try:
        typed = set_config_value(key, value)
    except KeyError as exc:
        raise typer.Exit(_fail(str(exc), EXIT_ERROR))
    except ValueError as exc:
        raise typer.Exit(_fail(str(exc), EXIT_ERROR))
    console.print(f"[green]Saved[/green] [bold]{key}[/bold] = {typed!r} -> {config_path()}")


@config_app.command("unset")
def config_unset(
    key: str = typer.Argument(..., help="Dotted key to remove."),
) -> None:
    """Remove one key so it falls back to the built-in default."""
    try:
        removed = unset_config_value(key)
    except KeyError as exc:
        raise typer.Exit(_fail(str(exc), EXIT_ERROR))
    if removed:
        console.print(f"[green]Removed[/green] {key} -> {config_path()}")
    else:
        console.print(f"[yellow]{key} was not set in {config_path()}[/yellow]")


# ---------------------------------------------------------------------------
# tui
# ---------------------------------------------------------------------------

@app.command()
def tui(ctx: typer.Context) -> None:
    """Launch the interactive Textual interface."""
    from .tui import SubZoneTUI

    client = _client(ctx.obj)
    SubZoneTUI(client).run()


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

@app.callback()
def _callback(
    ctx: typer.Context,
    base_url: Optional[str] = typer.Option(None, "--base-url", help="Site base URL (default: config network.base-url)."),
    user_agent: Optional[str] = typer.Option(None, "--user-agent", help="User-Agent header (default: config network.user-agent)."),
    timeout: Optional[float] = typer.Option(None, "--timeout", help="HTTP timeout in seconds (default: config network.timeout)."),
    retries: Optional[int] = typer.Option(None, "--retries", help="Retries per request (default: config network.retries)."),
    min_interval: Optional[float] = typer.Option(None, "--min-interval", help="Minimum seconds between HTTP requests - politeness rate limit (default: config network.min-interval, 0 = off)."),
    cache_ttl: Optional[float] = typer.Option(None, "--cache-ttl", help="Seconds to cache fetched pages in memory (default: config network.cache-ttl, 0 = off)."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Log HTTP requests."),
) -> None:
    """[bold]subz[/bold] - search and download subtitles from subzone.ir."""
    cfg = load_config()
    ctx.obj = Settings(
        base_url=base_url or cfg.get("network.base-url") or DEFAULT_BASE_URL,
        user_agent=user_agent or cfg.get("network.user-agent") or DEFAULT_USER_AGENT,
        timeout=float(timeout if timeout is not None else cfg.get("network.timeout", 20.0)),
        retries=int(retries if retries is not None else cfg.get("network.retries", 3)),
        verbose=verbose or bool(cfg.get("network.verbose", False)),
        config=cfg,
        min_interval=float(min_interval if min_interval is not None else cfg.get("network.min-interval", 0.0)),
        cache_ttl=float(cache_ttl if cache_ttl is not None else cfg.get("network.cache-ttl", 0.0)),
    )


def main() -> None:
    """Console entry point."""
    app()


if __name__ == "__main__":
    main()
