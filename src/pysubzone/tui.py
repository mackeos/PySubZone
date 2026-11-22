"""Textual TUI for PySubZone.

Interactive flow:

    [SearchScreen]  type a query, Enter -> results table
         | Enter on a row
         v
    [VersionsScreen]  all uploads of that title, language filter,
         | Enter on a row        refresh with 'r'
         v
    [DownloadDialog]  pick destination folder, extract or keep zip,
         progress bar while downloading, notification when done

All network work runs in thread workers, so the UI never blocks.
Launch with ``subz tui``.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    ProgressBar,
    Static,
)

from .client import SubZoneClient
from .exceptions import NoSubtitleFoundError, SubZoneError, TitleNotFoundError
from .languages import DEFAULT_LANGUAGE, display_name, resolve
from .models import TitleResult


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
        ("escape", "app.pop_screen", "Back to results"),
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
        self._load(None)

    def action_refresh(self) -> None:
        lang_input = self.query_one("#lang", Input)
        self._load(resolve(lang_input.value.strip()))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "lang":
            value = event.value.strip()
            self._load(resolve(value) if value else None)

    @work(thread=True, exclusive=True)
    def _load(self, lang_slug: Optional[str]) -> None:
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
        self.app.call_from_thread(self._fill_table, table, versions, label)

    def _fill_table(self, table: DataTable, versions, label: str) -> None:
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
            Horizontal(
                Button("Extract subtitles", id="extract", variant="primary"),
                Button("Keep zip", id="zip", variant="default"),
                id="dl-buttons",
            ),
            ProgressBar(total=100, show_eta=False, id="dl-progress"),
            Static("", id="dl-status"),
            id="dl-box",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        dest = Path(self.query_one("#dest", Input).value.strip() or "subs")
        mode = "zip" if event.button.id == "zip" else "extract"
        # prevent double submits
        for button_id in ("#extract", "#zip"):
            self.query_one(button_id, Button).disabled = True
        self._download(dest, mode)

    @work(thread=True, exclusive=True)
    def _download(self, dest: Path, mode: str) -> None:
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

        try:
            result = self.client.download(
                self.version,
                dest_dir=dest,
                save_mode=mode,
                on_progress=on_progress,
            )
        except SubZoneError as exc:
            self.app.call_from_thread(status.update, f"[red]{exc}[/red]")
            return

        saved = result.zip_path or ", ".join(result.srt_paths)
        self.app.call_from_thread(
            status.update,
            f"[green]Saved:[/green] {saved}   [dim](Esc closes)[/dim]",
        )
        self.app.call_from_thread(
            self.app.notify, f"Subtitle saved: {saved}", title="Download complete"
        )


class SubZoneTUI(App):
    """PySubZone interactive interface."""

    CSS = """
    #banner {
        padding: 0 1;
        background: $primary;
        color: $text;
        text-style: bold;
    }
    #query, #lang {
        margin: 1 1;
    }
    #results, #versions {
        height: 1fr;
        margin: 0 1;
    }
    #status {
        padding: 0 1;
        color: $accent;
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
    #dest {
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
    ]

    def __init__(self, client: SubZoneClient) -> None:
        super().__init__()
        self.client = client

    def on_mount(self) -> None:
        self.title = "PySubZone"
        self.sub_title = "subzone.ir subtitle downloader"
        self.push_screen(SearchScreen(self.client))
