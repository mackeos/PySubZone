"""Textual TUI for PySubZone.

Interactive screens (switch with Ctrl+S / Ctrl+B / Ctrl+A from anywhere,
quit with Ctrl+Q):

    [SearchScreen]    type a query, Enter -> results table
         | Enter on a row
         v
    [VersionsScreen]  all uploads of that title, language filter
         | Enter on a row        (remembered between sessions)
         v
    [DownloadDialog]  destination folder, optional "save as" rename,
         | extract or keep zip, progress bar, notification
         v
    saved - destination folder is remembered for next time

    [AutoScreen]      the `subz auto` flow: scan a folder, match every
                      video, dry-run or download; an optional
                      "Prefer complete season pack" checkbox (remembered
                      between sessions, default from
                      defaults.prefer-complete) finds same-season packs
                      covering every episode video - like the CLI's
                      --prefer-complete - and a picker screen lists them
                      ranked, so you choose which one serves the folder
                      (Esc = per-video best); each video still gets its
                      own episode extracted and renamed; Enter on a
                      matched row opens its VersionsScreen, Enter on a
                      failed row shows why it failed

    [BrowseScreen]    the `subz popular` flow: popular/latest uploads,
                      media-type + page filters; Enter opens the
                      download dialog for the selected upload

All network work runs in thread workers, so the UI never blocks.
The last destination folder and language filter are remembered in the
config directory (settings.json, next to config.toml).
Launch with ``subz tui``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Footer,
    Header,
    Input,
    ProgressBar,
    Static,
)

from . import __version__
from .client import SubZoneClient
from .config import load_config, load_tui_settings, save_tui_settings
from .exceptions import NoSubtitleFoundError, SubZoneError, TitleNotFoundError
from .languages import DEFAULT_LANGUAGE, display_name, resolve
from .matcher import find_videos, resolve_out
from .models import BrowseEntry, TitleResult
from .scoring import score_version


class SearchScreen(Screen):
    BINDINGS = [
        ("escape", "clear_results", "Clear results"),
    ]

    def __init__(self, client: SubZoneClient, **kwargs) -> None:
        super().__init__(**kwargs)
        self.client = client
        self.results: List[TitleResult] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("PySubZone - search subtitles on subzone.ir", id="banner")
        yield Input(placeholder="Show or movie title - press Enter to search", id="query")
        yield DataTable(id="results", cursor_type="row")
        yield Static("", id="status")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#results", DataTable)
        table.add_columns("#", "title", "year", "section", "subs", "slug")

    def action_clear_results(self) -> None:
        table = self.query_one("#results", DataTable)
        table.clear()
        self.results = []
        self.query_one("#status", Static).update("")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "query":
            self._search(event.value.strip())

    @work(thread=True, exclusive=True)
    def _search(self, query: str) -> None:
        status = self.query_one("#status", Static)
        if not query:
            return
        self.app.call_from_thread(status.update, f"Searching [bold]{query}[/bold] ...")
        try:
            results = self.client.search(query)
        except TitleNotFoundError:
            self.app.call_from_thread(status.update, f"[yellow]No title matches {query!r}[/yellow]")
            return
        except SubZoneError as exc:
            self.app.call_from_thread(status.update, f"[red]{exc}[/red]")
            return

        table = self.query_one("#results", DataTable)
        self.app.call_from_thread(self._fill_table, table, results, query)

    def _fill_table(self, table: DataTable, results: List[TitleResult], query: str) -> None:
        self.results = results
        table.clear()
        for index, r in enumerate(results, start=1):
            table.add_row(
                str(index),
                r.name,
                str(r.year or "-"),
                r.section,
                str(r.subtitle_count if r.subtitle_count is not None else "-"),
                r.slug,
            )
        self.query_one("#status", Static).update(
            f"{len(results)} titles for [bold]{query}[/bold] - Enter opens a title"
        )
        table.focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "results" and self.results:
            index = event.cursor_row
            if 0 <= index < len(self.results):
                self.app.push_screen(VersionsScreen(self.client, self.results[index]))


class VersionsScreen(Screen):
    BINDINGS = [
        ("escape", "app.pop_screen", "Back"),
        ("r", "refresh", "Refresh"),
    ]

    def __init__(self, client: SubZoneClient, title_info: TitleResult, **kwargs) -> None:
        super().__init__(**kwargs)
        self.client = client
        self.title_info = title_info
        self.versions = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(f"Subtitles for [bold]{self.title_info.name}[/bold]", id="banner")
        yield Input(
            placeholder=f"Language filter (fa, en, tr, ...) - Enter applies, empty = all (default {DEFAULT_LANGUAGE})",
            id="lang",
        )
        yield DataTable(id="versions", cursor_type="row")
        yield Static("", id="status")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#versions", DataTable)
        table.add_columns("language", "rate", "author", "releases", "comment", "ref")
        # remember destination / language settings are stored in settings.json
        remembered = load_tui_settings().get("lang")
        if remembered:
            self.query_one("#lang", Input).value = str(remembered)
        self._load(resolve(str(remembered)) if remembered else None, str(remembered or ""))

    def action_refresh(self) -> None:
        lang_input = self.query_one("#lang", Input)
        raw = lang_input.value.strip()
        self._load(resolve(raw) if raw else None, raw)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "lang":
            raw = event.value.strip()
            self._load(resolve(raw) if raw else None, raw)

    @work(thread=True, exclusive=True)
    def _load(self, lang_slug: Optional[str], raw_value: str = "") -> None:
        status = self.query_one("#status", Static)
        label = display_name(lang_slug) if lang_slug else "all languages"
        self.app.call_from_thread(status.update, f"Loading versions ({label}) ...")
        try:
            versions = self.client.title_versions(self.title_info.slug, lang_slug)
        except NoSubtitleFoundError as exc:
            self.app.call_from_thread(status.update, f"[yellow]{exc}[/yellow]")
            return
        except (TitleNotFoundError, SubZoneError) as exc:
            self.app.call_from_thread(status.update, f"[red]{exc}[/red]")
            return

        table = self.query_one("#versions", DataTable)
        self.app.call_from_thread(self._fill_table, table, versions, label, raw_value)

    def _fill_table(self, table: DataTable, versions, label: str, raw_value: str = "") -> None:
        self.versions = versions
        table.clear()
        for v in versions:
            rate = {"positive": "good", "negative": "bad"}.get(v.rate, "-")
            table.add_row(
                v.language or display_name(v.lang),
                rate,
                v.author,
                ", ".join(v.releases[:2]) or "-",
                v.comment[:40],
                v.ref,
            )
        self.query_one("#status", Static).update(
            f"{len(versions)} versions ({label}) - Enter downloads, r refreshes, Esc goes back"
        )
        table.focus()
        if raw_value:
            save_tui_settings({"lang": raw_value})

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "versions" and self.versions:
            index = event.cursor_row
            if 0 <= index < len(self.versions):
                self.app.push_screen(DownloadDialog(self.client, self.versions[index]))


class DownloadDialog(ModalScreen):
    BINDINGS = [("escape", "app.pop_screen", "Cancel")]

    def __init__(self, client: SubZoneClient, version, **kwargs) -> None:
        super().__init__(**kwargs)
        self.client = client
        self.version = version

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(
                f"Download [bold]{self.version.language or display_name(self.version.lang)}[/bold] "
                f"subtitle by [bold]{self.version.author or '?'}[/bold]",
                id="dl-title",
            ),
            Static(", ".join(self.version.releases[:2]) or "-", id="dl-releases"),
            Input(placeholder="Destination folder (default: ./subs)", id="dest"),
            Input(
                placeholder="Save as - optional name for the subtitle file, empty = automatic",
                id="saveas",
            ),
            Horizontal(
                Button("Extract subtitles", id="extract", variant="primary"),
                Button("Keep zip", id="zip", variant="default"),
                id="dl-buttons",
            ),
            ProgressBar(total=100, show_eta=False, id="dl-progress"),
            Static("", id="dl-status"),
            id="dl-box",
        )

    def on_mount(self) -> None:
        remembered = load_tui_settings().get("dest")
        if remembered:
            self.query_one("#dest", Input).value = str(remembered)

    def _set_buttons(self, enabled: bool) -> None:
        for button_id in ("#extract", "#zip"):
            self.query_one(button_id, Button).disabled = not enabled

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id not in ("extract", "zip"):
            return
        dest = Path(self.query_one("#dest", Input).value.strip() or "subs")
        saveas = self.query_one("#saveas", Input).value.strip() or None
        mode = "zip" if event.button.id == "zip" else "extract"
        # prevent double submits
        self._set_buttons(False)
        self._download(dest, mode, saveas)

    @work(thread=True, exclusive=True)
    def _download(self, dest: Path, mode: str, base_name: Optional[str]) -> None:
        status = self.query_one("#dl-status", Static)
        progress = self.query_one("#dl-progress", ProgressBar)
        self.app.call_from_thread(status.update, "Downloading ...")

        def on_progress(done: int, total: Optional[int]) -> None:
            if total:
                self.app.call_from_thread(
                    progress.update,
                    total=total,
                    progress=done,
                )

        def _set_buttons(enabled: bool) -> None:
            for button_id in ("#extract", "#zip"):
                self.query_one(button_id, Button).disabled = not enabled

        try:
            result = self.client.download(
                self.version,
                dest_dir=dest,
                save_mode=mode,
                base_name=base_name,
                on_progress=on_progress,
            )
        except SubZoneError as exc:
            self.app.call_from_thread(status.update, f"[red]{exc}[/red]")
            self.app.call_from_thread(_set_buttons, True)
            return

        saved = result.zip_path or ", ".join(result.srt_paths)
        self.app.call_from_thread(status.update, f"[green]Saved:[/green] {saved}   [dim](Esc closes)[/dim]")
        self.app.call_from_thread(_set_buttons, True)
        # remember the destination folder for the next dialog / auto run
        save_tui_settings({"dest": str(dest)})
        self.app.call_from_thread(
            self.app.notify, f"Subtitle saved: {saved}", title="Download complete"
        )


class PackPickerScreen(ModalScreen):
    """Pick one complete-season pack for a group of episode videos.

    Opened by the auto-match screen when the prefer-complete checkbox is on
    and covering packs were found (the TUI counterpart of the CLI pack
    prompt).  Enter (or selecting a row) uses the chosen pack for the whole
    group - each video still receives its own episode extracted and
    renamed; Esc falls back to the per-video best match.
    """

    BINDINGS = [("escape", "use_per_video_best", "Per-video best")]

    def __init__(self, title, group, ranked, dry: bool = False, **kwargs) -> None:
        super().__init__(**kwargs)
        self.title_result = title
        self.group = group
        self.ranked = ranked
        self.dry = dry

    def compose(self) -> ComposeResult:
        next_step = "dry run" if self.dry else "download"
        yield Vertical(
            Static(
                f"[bold]{len(self.ranked)}[/bold] complete-season pack(s) cover "
                f"[bold]{len(self.group)}[/bold] episode video(s) of "
                f"[bold]{self.title_result.name}[/bold] - best match first",
                id="picker-title",
            ),
            DataTable(id="picker-table", cursor_type="row"),
            Static(
                "Enter: use the selected pack - Esc: keep per-video best "
                f"- then {next_step}",
                id="picker-hint",
            ),
            id="picker-box",
        )

    def on_mount(self) -> None:
        table = self.query_one("#picker-table", DataTable)
        table.add_columns("#", "fit", "release", "author")
        for i, (total, version) in enumerate(self.ranked, 1):
            avg = round(total / len(self.group)) if self.group else total
            release = version.releases[0] if version.releases else version.ref
            table.add_row(str(i), str(avg), release, version.author or "-")
        table.focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id != "picker-table":
            return
        index = event.cursor_row
        if 0 <= index < len(self.ranked):
            self.dismiss(self.ranked[index][1])

    def action_use_per_video_best(self) -> None:
        self.dismiss(None)


class AutoScreen(Screen):
    """The `subz auto` flow: scan a folder and match every video."""

    BINDINGS = [
        ("escape", "app.show_search", "Search"),
        ("r", "rescan", "Re-scan"),
    ]

    def __init__(self, client: SubZoneClient, **kwargs) -> None:
        super().__init__(**kwargs)
        self.client = client
        self.matches: List[Dict[str, Any]] = []
        self.title_results: List[Optional[TitleResult]] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("Auto-match a folder - the [bold]subz auto[/bold] flow", id="banner")
        yield Input(placeholder="Folder with video files (or one video path) - Enter starts a dry run", id="auto-folder")
        yield Input(placeholder="Language (fa, en, tr, ...)", id="auto-lang")
        yield Input(
            placeholder="Output folder - optional; relative = inside the videos folder",
            id="auto-out",
        )
        yield Horizontal(
            Button("Dry run", id="auto-dry", variant="default"),
            Button("Match & extract", id="auto-extract", variant="primary"),
            Button("Match & zip", id="auto-zip", variant="default"),
            Checkbox("Prefer complete season pack", id="auto-prefer-complete"),
            id="auto-buttons",
        )
        yield DataTable(id="auto-table", cursor_type="row")
        yield ProgressBar(total=100, show_eta=False, id="auto-progress")
        yield Static("", id="auto-status")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#auto-table", DataTable)
        table.add_columns("video", "title", "score", "status")
        remembered = load_tui_settings()
        if remembered.get("lang"):
            self.query_one("#auto-lang", Input).value = str(remembered["lang"])
        if remembered.get("auto_out"):
            self.query_one("#auto-out", Input).value = str(remembered["auto_out"])
        # checkbox: last TUI choice wins, otherwise the config default
        # (defaults.prefer-complete), otherwise off
        checkbox = self.query_one("#auto-prefer-complete", Checkbox)
        if "prefer_complete" in remembered:
            checkbox.value = bool(remembered["prefer_complete"])
        else:
            try:
                checkbox.value = bool(load_config().get("defaults.prefer-complete", False))
            except Exception:  # noqa: BLE001 - a broken config must not break the screen
                checkbox.value = False

    def _set_buttons(self, enabled: bool) -> None:
        for button_id in ("#auto-dry", "#auto-extract", "#auto-zip"):
            self.query_one(button_id, Button).disabled = not enabled

    def action_rescan(self) -> None:
        """Start a dry run for the folder currently in the input."""
        self._start_run(dry=True, mode="extract")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "auto-folder":
            self._start_run(dry=True, mode="extract")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "auto-dry":
            self._start_run(dry=True, mode="extract")
        elif event.button.id == "auto-extract":
            self._start_run(dry=False, mode="extract")
        elif event.button.id == "auto-zip":
            self._start_run(dry=False, mode="zip")

    def _start_run(self, dry: bool, mode: str) -> None:
        folder = self.query_one("#auto-folder", Input).value.strip()
        status = self.query_one("#auto-status", Static)
        if not folder:
            status.update("[red]Enter a folder with video files first[/red]")
            return
        self._run_ctx: Dict[str, Any] = {
            "folder": folder,
            "lang_value": self.query_one("#auto-lang", Input).value.strip(),
            "out_value": self.query_one("#auto-out", Input).value.strip(),
            "prefer": self.query_one("#auto-prefer-complete", Checkbox).value,
            "dry": dry,
            "mode": mode,
            "status": status,
            "progress": self.query_one("#auto-progress", ProgressBar),
            "table": self.query_one("#auto-table", DataTable),
            "planner_note": "",
        }
        self._planned: Dict[Path, tuple] = {}
        self._pending_groups: List[tuple] = []
        self._set_buttons(False)
        self._plan()

    @work(thread=True, exclusive=True)
    def _plan(self) -> None:
        """Worker: scan the folder and find covering packs (best-effort)."""
        ctx = self._run_ctx

        def ui(fn, *args, **kwargs) -> None:
            self.app.call_from_thread(fn, *args, **kwargs)

        ui(ctx["status"].update, f"Scanning [bold]{ctx['folder']}[/bold] ...")
        lang_slug = resolve(ctx["lang_value"]) or DEFAULT_LANGUAGE
        try:
            videos = find_videos(Path(ctx["folder"]).expanduser())
        except (ValueError, FileNotFoundError) as exc:
            ui(ctx["status"].update, f"[red]{exc}[/red]")
            ui(self._set_buttons, True)
            return
        if not videos:
            ui(ctx["status"].update, f"[yellow]No video files found under {ctx['folder']}[/yellow]")
            ui(self._set_buttons, True)
            return
        ui(ctx["table"].clear)
        ui(ctx["progress"].update, total=len(videos), progress=0)
        ctx["videos"] = videos
        ctx["lang_slug"] = lang_slug

        if ctx["prefer"]:
            ui(ctx["status"].update, "Looking for complete-season packs ...")
            try:
                groups = self.client.find_complete_packs(videos, lang=lang_slug)
            except Exception as exc:  # noqa: BLE001 - planning is best-effort
                ctx["planner_note"] = f"prefer-complete planning skipped: {exc}"
                groups = []
            else:
                if groups and not any(ranked for _t, _g, ranked in groups):
                    ctx["planner_note"] = (
                        "no covering complete-season pack found - per-video best stays"
                    )
        else:
            groups = []
        ui(self._begin_pack_pick, groups)

    def _begin_pack_pick(self, groups: List[tuple]) -> None:
        """UI thread: chain the pack pickers, then run the per-video batch."""
        self._pending_groups = [g for g in groups if g[2]]
        self._open_next_picker()

    def _open_next_picker(self) -> None:
        """UI thread: prompt for the next group, or start the batch run."""
        if not self._pending_groups:
            self._run_matches()
            return
        title, group, ranked = self._pending_groups.pop(0)

        def picked(pack_version) -> None:
            if pack_version is not None:
                for video in group:
                    s = score_version(video.name, pack_version)
                    if s is not None:
                        self._planned[video] = (title, pack_version, s)
            self._open_next_picker()

        self.app.push_screen(
            PackPickerScreen(title, group, ranked, dry=self._run_ctx["dry"]),
            picked,
        )

    @work(thread=True, exclusive=True)
    def _run_matches(self) -> None:
        """Worker: match every video (pack-planned ones included) and save."""
        ctx = self._run_ctx
        planner_note = ctx["planner_note"]
        prefer = ctx["prefer"]

        def ui(fn, *args, **kwargs) -> None:
            self.app.call_from_thread(fn, *args, **kwargs)

        planned: Dict[Path, tuple] = self._planned if prefer else {}
        dry = ctx["dry"]
        mode = ctx["mode"]
        out_path = Path(ctx["out_value"]).expanduser() if ctx["out_value"] else None
        lang_slug = ctx["lang_slug"]
        videos = ctx["videos"]
        status = ctx["status"]
        progress = ctx["progress"]
        table = ctx["table"]
        self.matches = []
        self.title_results = []
        total = len(videos)
        for position, video in enumerate(videos, start=1):
            ui(status.update, f"[{position}/{total}] matching [bold]{video.name[:44]}[/bold] ...")
            row: Dict[str, Any] = {
                "video": video.name,
                "title": None,
                "score": None,
                "status": "",
                "detail": "",
            }
            title_result: Optional[TitleResult] = None
            try:
                if video in planned:
                    # planned onto the covering pack; download() with
                    # target_video extracts the video's own episode out of
                    # the pack archive (v0.4.4 pack-member awareness)
                    title, best, score = planned[video]
                    row.update({"title": title.name, "score": score, "pack": True})
                else:
                    title, best, score = self.client.best_for_file(video.stem, lang=lang_slug)
                    row.update({"title": title.name, "score": score})
                title_result = title
                if dry:
                    row["status"] = "dry run"
                else:
                    # a relative output folder lives inside the videos folder,
                    # exactly like `subz auto -o` (resolve_out is shared with the CLI)
                    dest = resolve_out(out_path, video.parent) or video.parent
                    result = self.client.download(
                        best,
                        dest_dir=dest,
                        save_mode=mode,
                        target_video=video,
                    )
                    row["status"] = "saved"
                    row["detail"] = str(result.zip_path or ", ".join(result.srt_paths))
            except (NoSubtitleFoundError, TitleNotFoundError) as exc:
                row["status"] = "not found"
                row["detail"] = str(exc)
            except Exception as exc:  # noqa: BLE001 - one bad video must not stop the batch
                row["status"] = "error"
                row["detail"] = str(exc)
            self.matches.append(row)
            self.title_results.append(title_result)
            ui(progress.advance, 1)

        def fill() -> None:
            table.clear()
            for r in self.matches:
                score = "-" if r["score"] is None else str(r["score"])
                status_text = r["status"] + (" (pack)" if r.get("pack") else "")
                table.add_row(r["video"][:40], r["title"] or "-", score, status_text)
            table.focus()

        ui(fill)
        failed = sum(1 for r in self.matches if r["status"] in ("error", "not found"))
        pack_rows = sum(1 for r in self.matches if r.get("pack"))
        if dry:
            summary = f"{total} video(s) matched (dry run)"
        else:
            saved = sum(1 for r in self.matches if r["status"] == "saved")
            summary = f"{saved}/{total} subtitle(s) saved"
        if pack_rows:
            summary += f", {pack_rows} via complete pack"
        if failed:
            summary += f", {failed} failed - Enter on a failed row shows why"
        if planner_note:
            summary += f" - [dim]{planner_note}[/dim]"
        ui(
            status.update,
            f"[green]{summary}[/green] - Enter opens a matched title, r re-scans, Esc goes back",
        )
        if ctx["lang_value"]:
            save_tui_settings({"lang": ctx["lang_value"]})
        if ctx["out_value"]:
            save_tui_settings({"auto_out": ctx["out_value"]})
        save_tui_settings({"prefer_complete": prefer})
        ui(self._set_buttons, True)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id != "auto-table":
            return
        index = event.cursor_row
        if not (0 <= index < len(self.matches)):
            return
        row = self.matches[index]
        if row["status"] in ("error", "not found"):
            # failed rows explain themselves instead of opening a version list
            self.query_one("#auto-status", Static).update(
                f"[red]{row['video']}: {row['status']}[/red]"
                f"{' - ' + row['detail'] if row['detail'] else ''}"
            )
            return
        if 0 <= index < len(self.title_results) and self.title_results[index]:
            self.app.push_screen(VersionsScreen(self.client, self.title_results[index]))


class BrowseScreen(Screen):
    """The `subz popular` flow: browse popular/latest uploads."""

    BINDINGS = [
        ("escape", "app.show_search", "Search"),
        ("p", "mode_popular", "Popular"),
        ("l", "mode_latest", "Latest"),
        ("left", "page_up", "Prev page"),
        ("right", "page_down", "Next page"),
    ]

    def __init__(self, client: SubZoneClient, **kwargs) -> None:
        super().__init__(**kwargs)
        self.client = client
        self.entries: List[BrowseEntry] = []
        self.mode = "popular"

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("Browse uploads - popular or latest, like [bold]subz popular[/bold]", id="banner")
        with Horizontal(id="browse-controls"):
            yield Button("Popular", id="btn-popular", variant="primary")
            yield Button("Latest", id="btn-latest", variant="default")
            yield Input(placeholder="type: all / film / series / music", id="browse-kind")
            yield Input(placeholder="page", id="browse-page")
        yield DataTable(id="browse-table", cursor_type="row")
        yield Static("", id="browse-status")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#browse-table", DataTable)
        table.add_columns("title", "language", "type", "owner", "date", "ref")
        self.query_one("#browse-page", Input).value = "1"
        self._load()

    def action_mode_popular(self) -> None:
        self.mode = "popular"
        self._load()

    def action_mode_latest(self) -> None:
        self.mode = "latest"
        self._load()

    def action_page_up(self) -> None:
        page_input = self.query_one("#browse-page", Input)
        try:
            page_input.value = str(max(1, int(page_input.value.strip() or "1") - 1))
        except ValueError:
            page_input.value = "1"
        self._load()

    def action_page_down(self) -> None:
        page_input = self.query_one("#browse-page", Input)
        try:
            page_input.value = str(max(1, int(page_input.value.strip() or "1")) + 1)
        except ValueError:
            page_input.value = "1"
        self._load()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-popular":
            self.action_mode_popular()
        elif event.button.id == "btn-latest":
            self.action_mode_latest()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id in ("browse-kind", "browse-page"):
            self._load()

    def _load(self) -> None:
        kind = self.query_one("#browse-kind", Input).value.strip() or "all"
        page_text = self.query_one("#browse-page", Input).value.strip() or "1"
        status = self.query_one("#browse-status", Static)
        try:
            page = int(page_text)
        except ValueError:
            status.update("[red]Page must be a number[/red]")
            return
        if page < 1:
            page = 1
            self.query_one("#browse-page", Input).value = "1"
        self._fetch(self.mode, kind, page, status, self.query_one("#browse-table", DataTable))

    @work(thread=True, exclusive=True)
    def _fetch(self, mode: str, kind: str, page: int, status: Static, table: DataTable) -> None:
        def ui(fn, *args) -> None:
            self.app.call_from_thread(fn, *args)

        ui(status.update, f"Loading {mode} {kind} uploads (page {page}) ...")
        try:
            entries = self.client.browse(mode=mode, kind=kind, page=page)
        except ValueError as exc:
            ui(status.update, f"[red]{exc}[/red]")
            return
        except SubZoneError as exc:
            ui(status.update, f"[red]{exc}[/red]")
            return

        self.entries = entries

        def fill() -> None:
            table.clear()
            for entry in entries:
                table.add_row(
                    entry.title_name[:48],
                    entry.version.language,
                    entry.media_type,
                    entry.owner,
                    entry.date,
                    entry.version.ref,
                )
            table.focus()

        ui(fill)
        ui(
            status.update,
            f"{len(entries)} {mode} uploads ({kind}, page {page}) - Enter downloads, "
            "p/l switch popular/latest, left/right change page, Esc goes back",
        )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id != "browse-table":
            return
        index = event.cursor_row
        if 0 <= index < len(self.entries):
            self.app.push_screen(DownloadDialog(self.client, self.entries[index].version))


class SubZoneTUI(App):
    """PySubZone interactive interface."""

    CSS = """
    #banner {
        padding: 0 1;
        background: $primary;
        color: $text;
        text-style: bold;
    }
    #query, #lang, #auto-folder, #auto-lang, #auto-out {
        margin: 1 1;
    }
    #results, #versions, #auto-table, #browse-table {
        height: 1fr;
        margin: 0 1;
    }
    #status, #auto-status, #browse-status {
        padding: 0 1;
        color: $accent;
    }
    #auto-buttons {
        height: auto;
        margin: 0 1 1 1;
    }
    #auto-buttons Button {
        margin-right: 1;
    }
    #auto-buttons Checkbox {
        margin-left: 2;
    }
    #picker-box {
        width: 90;
        max-width: 100%;
        padding: 1 2;
        background: $surface;
        border: tall $primary;
    }
    #picker-title {
        margin-bottom: 1;
    }
    #picker-table {
        height: auto;
        max-height: 14;
        margin-bottom: 1;
    }
    #picker-hint {
        color: $text-muted;
    }
    #browse-controls {
        height: auto;
        margin: 1 1;
    }
    #browse-controls Button {
        margin-right: 1;
    }
    #browse-controls Input {
        width: 1fr;
    }
    #dl-box {
        width: 72;
        padding: 1 2;
        background: $surface;
        border: tall $primary;
    }
    #dl-title {
        margin-bottom: 1;
    }
    #dl-releases {
        color: $text-muted;
        margin-bottom: 1;
    }
    #dest, #saveas {
        margin-bottom: 1;
    }
    #dl-buttons {
        height: auto;
        margin-bottom: 1;
    }
    #dl-buttons Button {
        margin-right: 1;
    }
    #dl-status {
        color: $accent;
    }
    """

    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
        ("ctrl+s", "show_search", "Search"),
        ("ctrl+b", "show_browse", "Browse"),
        # priority so the shortcut wins over the focused Input's own ctrl+a
        # binding (cursor-home) - Auto-match must work from the search screen
        # even while the query field has focus
        Binding("ctrl+a", "show_auto", "Auto-match", priority=True),
    ]

    def __init__(self, client: SubZoneClient) -> None:
        super().__init__()
        self.client = client

    def get_default_screen(self) -> Screen:
        return SearchScreen(self.client)

    def on_mount(self) -> None:
        self.title = "PySubZone"
        self.sub_title = f"subzone.ir subtitle downloader — v{__version__}"

    async def _swap(self, screen: Screen) -> None:
        """Replace the current top-level screen.

        switch_screen pops the current top screen, which fails while the
        stack still holds only the default screen - so the very first
        navigation pushes instead.
        """
        if len(self.screen_stack) > 1:
            await self.switch_screen(screen)
        else:
            await self.push_screen(screen)

    async def _goto(self, screen: Screen, target_type: type) -> None:
        """Swap to *screen* unless we are already there or a modal is open.

        Re-entering the same screen (e.g. Ctrl+A while typing in the
        auto-match screen) would otherwise recreate it and wipe whatever
        the user had typed; a modal such as the download dialog must never
        be yanked away by a screen switch either.
        """
        current = self.screen
        if isinstance(current, target_type) or isinstance(current, ModalScreen):
            return
        await self._swap(screen)

    async def action_show_search(self) -> None:
        await self._goto(SearchScreen(self.client), SearchScreen)

    async def action_show_browse(self) -> None:
        await self._goto(BrowseScreen(self.client), BrowseScreen)

    async def action_show_auto(self) -> None:
        await self._goto(AutoScreen(self.client), AutoScreen)
