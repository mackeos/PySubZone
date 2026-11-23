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
    ) -> None:
        self.base_url = base_url
        self.user_agent = user_agent
        self.timeout = timeout
        self.retries = retries
        self.verbose = verbose


def _client(settings: Settings) -> SubZoneClient:
    return SubZoneClient(
        base_url=settings.base_url,
        user_agent=settings.user_agent,
        timeout=settings.timeout,
        max_retries=settings.retries,
        verbose=settings.verbose,
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
        None, "--lang", "-l", help="Only titles having subtitles in this language (fa, en, tr, ...)."
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON instead of a table."),
) -> None:
    """Search subzone.ir for movie/series titles."""
    client = _client(ctx.obj)
    try:
        results = client.search(query, lang=lang)
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
        None, "--lang", "-l", help="Only list versions in this language."
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON instead of a table."),
) -> None:
    """List subtitle versions available for a title."""
    client = _client(ctx.obj)
    try:
        resolved = _resolve_title_arg(client, title)
        version_list = client.title_versions(resolved.slug, lang)
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


def _shared_download_options(out: Path = None):
    return {
        "out": typer.Option(Path("."), "--out", "-o", help="Output directory (default: current directory)."),
        "save_zip": typer.Option(False, "--zip", help="Keep the downloaded zip instead of extracting it."),
        "lang_suffix": typer.Option(None, "--lang-suffix", help="Suffix for extracted files, e.g. 'fa' (default: derived from language)."),
        "no_suffix": typer.Option(False, "--no-suffix", help="Name files 'Movie.srt' instead of 'Movie.fa.srt'."),
    }


@app.command()
def download(
    ctx: typer.Context,
    target: str = typer.Argument(..., help="'<slug>/<lang>/<id>' ref, any subzone.ir URL, or a title slug."),
    lang: Optional[str] = typer.Option(None, "--lang", "-l", help="Language for title slugs/URLs (default: farsi_persian)."),
    limit: int = typer.Option(1, "--limit", "-n", help="How many versions to grab from title slugs/URLs (0 = all)."),
    out: Path = typer.Option(Path("."), "--out", "-o", help="Output directory (default: current directory)."),
    save_zip: bool = typer.Option(False, "--zip", help="Keep the downloaded zip instead of extracting it."),
    lang_suffix: Optional[str] = typer.Option(None, "--lang-suffix", help="Suffix for extracted files, e.g. 'fa' (default: derived from language)."),
    no_suffix: bool = typer.Option(False, "--no-suffix", help="Name files 'Movie.srt' instead of 'Movie.fa.srt'."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Download a subtitle by ref, URL or title slug."""
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
    lang: Optional[str] = typer.Option(None, "--lang", "-l", help="Language for title URLs (default: farsi_persian)."),
    limit: int = typer.Option(1, "--limit", "-n", help="How many versions to grab from title URLs (0 = all)."),
    out: Path = typer.Option(Path("."), "--out", "-o", help="Output directory (default: current directory)."),
    save_zip: bool = typer.Option(False, "--zip", help="Keep the downloaded zip instead of extracting it."),
    lang_suffix: Optional[str] = typer.Option(None, "--lang-suffix", help="Suffix for extracted files, e.g. 'fa'."),
    no_suffix: bool = typer.Option(False, "--no-suffix", help="Name files 'Movie.srt' instead of 'Movie.fa.srt'."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """'URL grab': paste any subzone.ir subtitles URL and download it."""
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
    lang: Optional[str] = typer.Option(DEFAULT_LANGUAGE, "--lang", "-l", help="Subtitle language."),
    out: Path = typer.Option(Path("."), "--out", "-o", help="Output folder (relative: inside the video's folder; absolute: as-is; default: beside the video)."),
    out_cwd: bool = typer.Option(False, "--out-cwd", help="Resolve a relative --out against the current directory instead of the video's folder."),
    save_zip: bool = typer.Option(False, "--zip", help="Keep the downloaded zip instead of extracting it."),
    lang_suffix: Optional[str] = typer.Option(None, "--lang-suffix", help="Suffix for extracted files, e.g. 'fa'."),
    no_suffix: bool = typer.Option(False, "--no-suffix", help="Name files 'Movie.srt' instead of 'Movie.fa.srt'."),
    no_download: bool = typer.Option(False, "--no-download", help="Only show matching candidates."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Find the best subtitle for one video file / release name."""
    from .matcher import guess_season_episode, guess_title

    client = _client(ctx.obj)
    stem = Path(file).stem
    query = guess_title(stem)
    lang_slug = resolve(lang) or DEFAULT_LANGUAGE
    real_file = Path(file).is_file()
    # Relative -o paths are created inside the video's folder; absolute paths
    # are used as-is; --out-cwd restores current-directory-relative behavior.
    source = Path(file).parent if real_file else None
    dest_dir = _resolve_out(out, source, out_cwd) or (source or Path("."))
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
        if not no_download:
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
    lang: Optional[str] = typer.Option(DEFAULT_LANGUAGE, "--lang", "-l", help="Subtitle language."),
    out: Optional[Path] = typer.Option(None, "--out", "-o", help="Output folder for subtitles (relative: inside the videos folder; absolute: as-is)."),
    out_cwd: bool = typer.Option(False, "--out-cwd", help="Resolve a relative --out against the current directory instead of the videos folder."),
    save_zip: bool = typer.Option(False, "--zip", help="Keep the downloaded zips instead of extracting them."),
    lang_suffix: Optional[str] = typer.Option(None, "--lang-suffix", help="Suffix for extracted files, e.g. 'fa'."),
    no_suffix: bool = typer.Option(False, "--no-suffix", help="Name files 'Movie.srt' instead of 'Movie.fa.srt'."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Only show the matches, download nothing."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Auto-match every video in PATH and download fitting subtitles."""
    from .matcher import find_videos

    lang_slug = resolve(lang) or DEFAULT_LANGUAGE
    try:
        videos = find_videos(path)
    except (ValueError, FileNotFoundError) as exc:
        raise typer.Exit(_fail(str(exc), EXIT_ERROR))
    if not videos:
        raise typer.Exit(_fail(f"No video files found under {path}", EXIT_NOT_FOUND))

    if not json_out:
        console.print(f"[bold]{len(videos)} video file(s)[/bold] under {path}")
        for v in videos:
            console.print(f"  [dim]-[/dim] {v.name}")
        note = "  [dim](dry run)[/dim]" if dry_run else ""
        console.print(f"[dim]language: {display_name(lang_slug)}{note}[/dim]")
        if not dry_run and not yes and sys.stdin.isatty():
            if not typer.confirm("Fetch and save subtitles for these files?"):
                return

    client = _client(ctx.obj)
    rows: List[Dict[str, Any]] = []
    saw_error = False
    saw_missing = False

    progress = Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
        disable=json_out,
    )
    with progress:
        task = progress.add_task("matching", total=len(videos))
        for video in videos:
            progress.update(task, description=f"[cyan]{video.name[:40]}")
            row: Dict[str, Any] = {"video": str(video), "title": None, "status": "ok"}
            try:
                title, best, score = client.best_for_file(video.stem, lang=lang_slug)
                row.update({"title": title.name, "ref": best.ref, "score": score})
                if dry_run:
                    row["status"] = "dry-run"
                else:
                    dest = _resolve_out(out, video.parent, out_cwd) or video.parent
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
                saw_missing = True
            except (SubZoneError, ValueError, OSError) as exc:
                row["status"] = "error"
                row["error"] = str(exc)
                saw_error = True
            rows.append(row)
            progress.advance(task)

    if json_out:
        _emit(rows, True)
    else:
        summary = Table(title="Auto-match summary", box=box.SIMPLE_HEAVY)
        summary.add_column("video")
        summary.add_column("title")
        summary.add_column("score", justify="right")
        summary.add_column("status")
        summary.add_column("saved", overflow="fold")
        for row in rows:
            style = {"ok": "green", "dry-run": "cyan", "not found": "yellow"}.get(
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
    base_url: str = typer.Option(DEFAULT_BASE_URL, "--base-url", help="Site base URL."),
    user_agent: str = typer.Option(DEFAULT_USER_AGENT, "--user-agent", help="User-Agent header."),
    timeout: float = typer.Option(20.0, "--timeout", help="HTTP timeout in seconds."),
    retries: int = typer.Option(3, "--retries", help="Retries per request."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Log HTTP requests."),
) -> None:
    """[bold]subz[/bold] - search and download subtitles from subzone.ir."""
    ctx.obj = Settings(base_url, user_agent, timeout, retries, verbose)


def main() -> None:
    """Console entry point."""
    app()


if __name__ == "__main__":
    main()
