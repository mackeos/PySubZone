"""Offline tests: parsers, matcher and scoring run against saved HTML fixtures."""

from pathlib import Path
from typing import List, Optional

import pytest

from pysubzone.matcher import find_videos, guess_season_episode, guess_title
from pysubzone.parsing import (
    parse_browse,
    parse_search,
    parse_subtitles_url,
    parse_subtitle_page,
    parse_title,
)
from pysubzone.languages import resolve
from pysubzone.models import SubtitleVersion
from pysubzone.scoring import pick_best, score_version

FIXTURES = Path(__file__).parent / "fixtures"


def read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# url parsing
# ---------------------------------------------------------------------------

def test_parse_subtitles_url_variants():
    assert parse_subtitles_url("/subtitles/dexter-resurrection-season-2") == (
        "dexter-resurrection-season-2",
        None,
        None,
    )
    assert parse_subtitles_url(
        "https://subzone.ir/subtitles/the-odyssey-2026/farsi_persian/3768757"
    ) == ("the-odyssey-2026", "farsi_persian", "3768757")
    assert parse_subtitles_url(
        "https://subzone.ir/subtitles/the-odyssey-2026/farsi_persian/3768757/download"
    ) == ("the-odyssey-2026", "farsi_persian", "3768757")


def test_parse_subtitles_url_language_page():
    """Title+language URLs (as copied from the browser) resolve to the language."""
    assert parse_subtitles_url(
        "https://subzone.ir/subtitles/arcane-league-of-legends-first-season/farsi_persian"
    ) == ("arcane-league-of-legends-first-season", "farsi_persian", None)
    assert parse_subtitles_url("/subtitles/show-season-1/english/") == (
        "show-season-1",
        "english",
        None,
    )


def test_parse_subtitles_url_rejects_garbage():
    with pytest.raises(ValueError):
        parse_subtitles_url("https://example.com/other/page")


# ---------------------------------------------------------------------------
# search page
# ---------------------------------------------------------------------------

def test_parse_search_dexter():
    results = parse_search(read("search_dexter.html"))
    assert results, "no results parsed"
    first = results[0]
    assert first.name == "Dexter: Resurrection - Season 2"
    assert first.slug == "dexter-resurrection-season-2"
    assert first.year == 2025
    assert first.subtitle_count == 1
    assert first.section == "exact"
    dexter_lab = next(r for r in results if "Laboratory" in r.name)
    assert dexter_lab.year == 1996
    assert dexter_lab.section == "exact"


# ---------------------------------------------------------------------------
# title page
# ---------------------------------------------------------------------------

def test_parse_title_page():
    page = parse_title(read("title_dexter.html"), "dexter-resurrection-season-2")
    assert page.name == "Dexter: Resurrection - Season 2"
    assert page.year == 2025
    assert page.imdb_id == "tt33043892"
    counts = page.language_counts()
    assert counts.get("farsi_persian") == 22
    assert counts.get("english") == 13
    assert len(page.versions) > 50
    farsi = page.versions_for("farsi_persian")
    assert farsi and len(farsi) == 22
    some = farsi[0]
    assert some.lang == "farsi_persian"
    assert some.sub_id.isdigit()
    assert some.url.startswith("/subtitles/dexter-resurrection-season-2/farsi_persian/")
    assert some.author  # author parsed
    assert any("Dexter.Resurrection" in r for r in some.releases)


# ---------------------------------------------------------------------------
# subtitle detail page
# ---------------------------------------------------------------------------

def test_parse_subtitle_page():
    version = SubtitleVersion(
        slug="dexter-resurrection-season-2",
        lang="farsi_persian",
        sub_id="3770024",
    )
    details = parse_subtitle_page(read("subtitle_page.html"), version)
    assert details.title_name == "Dexter: Resurrection - Season 2"
    assert details.version.sub_id == "3770024"
    assert any("S02E04" in r for r in details.releases)
    assert details.online == "2026-11-20"
    assert details.files.startswith("1")


# ---------------------------------------------------------------------------
# browse page
# ---------------------------------------------------------------------------

def test_parse_browse():
    entries = parse_browse(read("browse_popular.html"))
    assert len(entries) >= 10
    first = entries[0]
    assert first.title_name
    assert first.version.slug
    assert first.version.sub_id.isdigit()
    assert first.media_type in ("web", "bluray", "cam", "hdtv", "other")


# ---------------------------------------------------------------------------
# languages
# ---------------------------------------------------------------------------

def test_language_aliases():
    assert resolve("fa") == "farsi_persian"
    assert resolve("Farsi/Persian") == "farsi_persian"
    assert resolve("en") == "english"
    assert resolve("PT-BR") == "portuguese-br"
    assert resolve("made_up_lang") == "made_up_lang"
    assert resolve(None) is None
    assert resolve("") is None


# ---------------------------------------------------------------------------
# matcher
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "stem,expected",
    [
        (
            "Dexter.Resurrection.S02E01.Risky.Business.1080p.AMZN.WEB-DL.DDP5.1.H.264-NTb",
            "Dexter Resurrection",
        ),
        ("The.Odyssey.2026.1080p.AMZN.WEB-DL.DDP5.1.H.264-NTb", "The Odyssey"),
        ("1984.1956.1080p.BluRay.x264", "1984"),
        ("Breaking.Bad.S05E14.720p.BluRay.x264-DEMAND", "Breaking Bad"),
        ("House.of.Cards.S01E01.1080p.WEB.h264-CONVOY", "House of Cards"),
    ],
)
def test_guess_title(stem, expected):
    assert guess_title(stem) == expected


def test_guess_season_episode():
    assert guess_season_episode("Show.S02E01.1080p") == (2, 1)
    assert guess_season_episode("Show.2x05.720p") == (2, 5)
    assert guess_season_episode("Some.Movie.2020.1080p") is None


def test_find_videos(tmp_path):
    (tmp_path / "a.mkv").touch()
    (tmp_path / "b.mp4").touch()
    (tmp_path / "notes.txt").touch()
    videos = find_videos(tmp_path)
    assert sorted(v.name for v in videos) == ["a.mkv", "b.mp4"]
    single = find_videos(tmp_path / "a.mkv")
    assert len(single) == 1
    with pytest.raises(ValueError):
        find_videos(tmp_path / "notes.txt")


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------

def _version(releases, rate=""):
    return SubtitleVersion(
        slug="show-season-1", lang="farsi_persian", sub_id="1",
        releases=releases, rate=rate,
    )


def test_score_version_episode_match():
    good = _version(["Show.S02E01.1080p.AMZN.WEB-DL-NTb"])
    bad = _version(["Show.S02E04.1080p.WEB-CAKES"])
    pack = _version(["Show.S02.Complete.720p.BluRay"])
    video = "Show.S02E01.Risky.Business.1080p.AMZN.WEB-DL.DDP5.1.H.264-NTb"
    assert score_version(video, good) is not None and score_version(video, good) > 40
    assert score_version(video, bad) is None          # wrong episode -> reject
    assert 0 <= score_version(video, pack) <= 30      # season pack -> neutral


def test_pick_best_prefers_exact_release():
    versions = [
        _version(["Show.S01E05.720p.HDTV-KILLERS"]),
        _version(["Show.S01E05.1080p.WEB-DL.DDP5.1.H.264-NTb"], rate="positive"),
        _version(["Show.S01E03.1080p.WEB-DL-CAKES"]),
    ]
    video = "Show.S01E05.1080p.WEB-DL.DDP5.1.H.264-NTb"
    picked = pick_best(video, versions)
    assert picked
    score, version = picked
    assert version.releases[0].startswith("Show.S01E05.1080p")
    assert score >= 50


# ---------------------------------------------------------------------------
# download save locations
# (regression: -o/--out must always be honored, even with target_video)
# ---------------------------------------------------------------------------

def _zip_payload(members):
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name in members:
            zf.writestr(
                name, "1\n00:00:01,000 --> 00:00:02,000\noffline test line\n"
            )
    return buf.getvalue()


def _offline_client():
    from pysubzone.client import SubZoneClient

    return SubZoneClient()


def test_extract_saves_into_dest_dir_even_with_target_video(tmp_path):
    client = _offline_client()
    version = SubtitleVersion(slug="show", lang="farsi_persian", sub_id="42")
    payload = _zip_payload(["Show.S02.1080p.WEB-DL.srt"])
    out = tmp_path / "subs"
    written = client._extract_subtitles(
        payload, out, version, "fa",
        target_video=tmp_path / "Movies" / "Arcane.S02E01.1080p.x265-HODL.mkv",
    )
    assert len(written) == 1
    # regression: the file must land in the dest folder, NOT next to the video
    assert written[0].parent == out
    assert written[0].name == "Arcane.S02E01.1080p.x265-HODL.fa.srt"


def test_extract_without_target_video_uses_slug_names(tmp_path):
    client = _offline_client()
    version = SubtitleVersion(slug="show-season-1", lang="farsi_persian", sub_id="42")
    payload = _zip_payload(["sub.srt"])
    written = client._extract_subtitles(
        payload, tmp_path, version, "fa", target_video=None
    )
    assert written[0].parent == tmp_path
    assert written[0].name == "show-season-1-farsi_persian-42.fa.srt"


def test_extract_multiple_members_get_indexes(tmp_path):
    client = _offline_client()
    version = SubtitleVersion(slug="show", lang="farsi_persian", sub_id="42")
    payload = _zip_payload(["cd1.srt", "cd2.srt"])
    written = client._extract_subtitles(
        payload, tmp_path, version, "fa", target_video="Arcane.S02E01.mkv"
    )
    names = sorted(p.name for p in written)
    assert names == ["Arcane.S02E01.fa.1.srt", "Arcane.S02E01.fa.2.srt"]


# ---------------------------------------------------------------------------
# -o/--out resolution (auto / filename)
# (relative -o must land INSIDE the videos folder; --out-cwd = old behavior)
# ---------------------------------------------------------------------------

def test_resolve_out_relative_goes_inside_source(tmp_path):
    from pysubzone.cli import _resolve_out

    source = tmp_path / "shows" / "Arcane.S02"
    dest = _resolve_out(Path("subs"), source, out_cwd=False)
    assert dest == (source / "subs").resolve()


def test_resolve_out_absolute_path_untouched(tmp_path):
    from pysubzone.cli import _resolve_out

    absolute = tmp_path / "absolute" / "subs"
    assert _resolve_out(absolute, tmp_path, out_cwd=False) == absolute


def test_resolve_out_none_means_no_request():
    from pysubzone.cli import _resolve_out

    assert _resolve_out(None, Path("/anywhere"), out_cwd=False) is None


def test_resolve_out_cwd_flag_restores_old_behavior(tmp_path):
    from pysubzone.cli import _resolve_out

    assert _resolve_out(Path("subs"), tmp_path, out_cwd=True) == Path("subs")


def test_resolve_out_without_source_stays_cwd_relative():
    from pysubzone.cli import _resolve_out

    assert _resolve_out(Path("subs"), None, out_cwd=False) == Path("subs")


# ---------------------------------------------------------------------------
# config file (defaults for every command)
# ---------------------------------------------------------------------------

@pytest.fixture
def config_env(tmp_path, monkeypatch):
    """Redirect the config directory into tmp_path via PYSUBZONE_CONFIG."""
    monkeypatch.setenv("PYSUBZONE_CONFIG", str(tmp_path))
    return tmp_path


def test_config_missing_file_returns_empty(config_env):
    from pysubzone.config import load_config

    assert load_config() == {}


def test_config_roundtrip_and_coercion(config_env):
    from pysubzone.config import load_config, set_config_value

    assert set_config_value("defaults.language", "tr") == "tr"
    assert set_config_value("defaults.min-score", "60") == 60
    assert set_config_value("defaults.zip", "yes") is True
    assert set_config_value("defaults.no-suffix", "off") is False
    assert set_config_value("defaults.workers", "4") == 4
    assert set_config_value("network.timeout", "7.5") == 7.5
    flat = load_config()
    assert flat["defaults.language"] == "tr"
    assert flat["defaults.min-score"] == 60
    assert flat["defaults.zip"] is True
    assert flat["defaults.workers"] == 4
    assert flat["network.timeout"] == 7.5


def test_config_string_values_are_quoted_and_survive_reload(config_env):
    from pysubzone.config import load_config, set_config_value

    tricky = 'C:/dir "quoted"'
    set_config_value("defaults.out", tricky)
    assert load_config()["defaults.out"] == tricky


def test_config_unknown_key_rejected(config_env):
    from pysubzone.config import set_config_value

    with pytest.raises(KeyError):
        set_config_value("defaults.nope", "1")


def test_config_bad_value_and_bounds_rejected(config_env):
    from pysubzone.config import set_config_value

    with pytest.raises(ValueError):
        set_config_value("defaults.min-score", "abc")
    with pytest.raises(ValueError):  # bounds 0..100
        set_config_value("defaults.min-score", "150")
    with pytest.raises(ValueError):  # bounds 1..8
        set_config_value("defaults.workers", "0")
    with pytest.raises(ValueError):
        set_config_value("defaults.zip", "maybe")


def test_config_unset_removes_key(config_env):
    from pysubzone.config import load_config, set_config_value, unset_config_value

    set_config_value("defaults.language", "fa")
    assert unset_config_value("defaults.language") is True
    assert load_config() == {}
    assert unset_config_value("defaults.language") is False


def test_config_broken_toml_lenient_vs_strict(config_env):
    from pysubzone.config import load_config

    (config_env / "config.toml").write_text("this is not [valid toml", encoding="utf-8")
    assert load_config() == {}            # commands keep working
    with pytest.raises(ValueError):       # subz config show surfaces it
        load_config(strict=True)


def test_tui_settings_roundtrip(config_env):
    from pysubzone.config import load_tui_settings, save_tui_settings

    assert load_tui_settings() == {}
    save_tui_settings({"dest": "/tmp/subs", "lang": "fa"})
    save_tui_settings({"dest": "/tmp/other"})  # merge, not replace
    assert load_tui_settings() == {"dest": "/tmp/other", "lang": "fa"}


# ---------------------------------------------------------------------------
# CLI: config commands + new options (no network touched)
# ---------------------------------------------------------------------------

def _runner():
    from typer.testing import CliRunner

    return CliRunner()


def test_cli_config_set_show_unset(config_env):
    from pysubzone.cli import app

    runner = _runner()
    result = runner.invoke(app, ["config", "set", "defaults.min-score", "65"])
    assert result.exit_code == 0, result.output
    assert (config_env / "config.toml").is_file()

    result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0, result.output
    assert "defaults.min-score" in result.output and "65" in result.output

    result = runner.invoke(app, ["config", "unset", "defaults.min-score"])
    assert result.exit_code == 0, result.output

    result = runner.invoke(app, ["config", "set", "nope.key", "1"])
    assert result.exit_code == 2


def test_cli_config_path(config_env):
    from pysubzone.cli import app

    result = _runner().invoke(app, ["config", "path"])
    assert result.exit_code == 0, result.output
    assert str(config_env / "config.toml") in result.output


def test_cli_auto_and_filename_expose_new_options(config_env):
    from pysubzone.cli import app

    runner = _runner()
    auto_help = runner.invoke(app, ["auto", "--help"]).output
    for option in ("--min-score", "--parallel", "--out-cwd", "--json"):
        assert option in auto_help, f"auto missing {option}"
    filename_help = runner.invoke(app, ["filename", "--help"]).output
    assert "--min-score" in filename_help
    download_help = runner.invoke(app, ["download", "--help"]).output
    assert "--no-suffix" in download_help and "--no-zip" in download_help


def test_cli_filename_min_score_gate_offline(tmp_path, config_env, monkeypatch):
    """--min-score rejects a low-scoring best match before any download."""
    from types import SimpleNamespace

    from typer.testing import CliRunner

    from pysubzone.cli import app

    class _StubClient:
        def best_for_file(self, stem, lang=None):
            version = SubtitleVersion(
                slug="show-season-1", lang="farsi_persian", sub_id="7",
                releases=["Show.S01E05.720p"], author="tester",
            )
            from pysubzone.models import TitleResult

            return (TitleResult(name="Show", slug="show-season-1", url="/subtitles/show-season-1"),
                    version, 25)

        def title(self, slug):
            return SimpleNamespace(versions=[])

        def download(self, *a, **k):
            raise AssertionError("min-score gate must prevent downloads")

    monkeypatch.setattr("pysubzone.cli._client", lambda settings: _StubClient())
    result = CliRunner().invoke(
        app, ["filename", "Show.S01E05.720p.mkv", "--min-score", "50", "--json"]
    )
    assert result.exit_code == 1, result.output
    assert '"skipped"' in result.output


# ---------------------------------------------------------------------------
# download_many + base_name (offline via monkeypatched download/_get)
# ---------------------------------------------------------------------------

def _result_for(ref: str):
    from pysubzone.models import DownloadResult, SubtitleVersion

    slug, lang, sub_id = str(ref).split("/")
    return DownloadResult(
        zip_path=None,
        srt_paths=[f"/tmp/{slug}.{sub_id}.srt"],
        version=SubtitleVersion(slug=slug, lang=lang, sub_id=sub_id),
        bytes_downloaded=10,
        target_video=None,
    )


def test_download_many_returns_per_item_errors_in_order():
    from pysubzone.client import SubZoneClient
    from pysubzone.exceptions import DownloadError
    from pysubzone.models import DownloadResult

    client = SubZoneClient()

    def fake_download(target, **kwargs):
        if "bad" in str(target):
            raise DownloadError("boom")
        return _result_for(target)

    client.download = fake_download  # type: ignore[method-assign]
    results = client.download_many(["a/b/1", "bad/c/2", "d/e/3"])
    assert isinstance(results[0], DownloadResult)
    assert isinstance(results[1], DownloadError)
    assert isinstance(results[2], DownloadResult)
    assert [str(r.version.sub_id) for r, _ in [(results[0], 0), (results[2], 1)]] == ["1", "3"]


def test_download_many_parallel_keeps_target_order():
    import time

    from pysubzone.client import SubZoneClient
    from pysubzone.models import DownloadResult

    client = SubZoneClient()

    def fake_download(target, **kwargs):
        time.sleep(0.05 - 0.01 * int(str(target).split("/")[2]))
        return _result_for(target)

    client.download = fake_download  # type: ignore[method-assign]
    results = client.download_many(["a/b/1", "a/b/2", "a/b/3", "a/b/4"], max_workers=4)
    assert all(isinstance(r, DownloadResult) for r in results)
    assert [r.version.sub_id for r in results] == ["1", "2", "3", "4"]


def test_download_many_rejects_bad_workers():
    from pysubzone.client import SubZoneClient

    client = SubZoneClient()
    with pytest.raises(ValueError):
        client.download_many(["a/b/1"], max_workers=0)


def test_download_many_on_result_callback_fires_for_each_item():
    from pysubzone.client import SubZoneClient
    from pysubzone.exceptions import DownloadError

    client = SubZoneClient()

    def fake_download(target, **kwargs):
        if str(target) == "bad/c/2":
            raise DownloadError("boom")
        return _result_for(target)

    client.download = fake_download  # type: ignore[method-assign]
    seen = []
    client.download_many(["a/b/1", "bad/c/2"], on_result=lambda i, r: seen.append((i, type(r).__name__)))
    assert seen == [(0, "DownloadResult"), (1, "DownloadError")]


def test_base_name_controls_extract_names(tmp_path):
    from pysubzone.client import SubZoneClient

    client = SubZoneClient()
    version = SubtitleVersion(slug="show", lang="farsi_persian", sub_id="42")
    written = client._extract_subtitles(
        _zip_payload(["sub.srt"]), tmp_path, version, "fa",
        target_video=None, base_name="My Show S01E01",
    )
    assert written[0].name == "My Show S01E01.fa.srt"


def test_base_name_strips_path_traversal(tmp_path):
    from pysubzone.client import SubZoneClient

    client = SubZoneClient()
    version = SubtitleVersion(slug="show", lang="farsi_persian", sub_id="42")
    written = client._extract_subtitles(
        _zip_payload(["sub.srt"]), tmp_path, version, "fa",
        target_video=None, base_name="../evil/renamed",
    )
    assert written[0].parent == tmp_path
    assert written[0].name == "renamed.fa.srt"


def test_base_name_controls_zip_name(tmp_path, monkeypatch):
    from pysubzone.client import SubZoneClient

    class _FakeResponse:
        headers: dict = {}

        def iter_content(self, chunk_size=None):
            yield _zip_payload(["Show.S01E01.srt"])

    client = SubZoneClient()
    monkeypatch.setattr(client, "_get", lambda *a, **k: _FakeResponse())
    # the version has no releases/author -> download() prefetches metadata;
    # make that lookup fail cleanly so the zip path is still exercised
    from pysubzone.exceptions import DownloadError

    def _boom(*a, **k):
        raise DownloadError("offline test")

    monkeypatch.setattr(client, "get_version", _boom)
    version = SubtitleVersion(slug="show", lang="farsi_persian", sub_id="42")
    result = client.download(
        version, dest_dir=tmp_path, save_mode="zip", base_name="../Custom Name"
    )
    assert Path(result.zip_path).name == "Custom Name.zip"
    assert (tmp_path / "Custom Name.zip").is_file()


# ---------------------------------------------------------------------------
# TUI smoke test (headless, no network)
# ---------------------------------------------------------------------------

class _FakeClient:
    """Every method raises, so no test ever touches the network."""

    def search(self, *a, **k):
        from pysubzone.exceptions import TitleNotFoundError

        raise TitleNotFoundError("offline test")

    def browse(self, *a, **k):
        from pysubzone.exceptions import SubZoneError

        raise SubZoneError("offline test")

    def title_versions(self, *a, **k):
        from pysubzone.exceptions import SubZoneError

        raise SubZoneError("offline test")

    def best_for_file(self, *a, **k):
        from pysubzone.exceptions import NoSubtitleFoundError

        raise NoSubtitleFoundError("no match offline")

    def download(self, *a, **k):
        from pysubzone.exceptions import SubZoneError

        raise SubZoneError("offline test")


class _WorkingClient:
    """best_for_file + download succeed (or fail on demand), offline only."""

    def __init__(self, download_error: Optional[Exception] = None) -> None:
        self.download_error = download_error
        self.downloads: List[dict] = []

    def best_for_file(self, stem, lang=None, **k):
        from pysubzone.models import SubtitleVersion, TitleResult

        title = TitleResult(name="Show", slug="show", year=2024, subtitle_count=5)
        version = SubtitleVersion(
            slug="show",
            lang="farsi_persian",
            sub_id="7",
            releases=[stem],
            author="tester",
        )
        return title, version, 88

    def download(self, version, dest_dir=".", save_mode="extract", target_video=None, **k):
        from pysubzone.models import DownloadResult

        if self.download_error is not None:
            raise self.download_error
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        out = dest / f"{Path(target_video).with_suffix('').name}.fa.srt"
        out.write_text("dummy", encoding="utf-8")
        self.downloads.append({"dest": dest, "mode": save_mode, "video": target_video})
        return DownloadResult(zip_path=None, srt_paths=[str(out)], version=version)


def _run_async(coro):
    import asyncio

    return asyncio.run(coro)


def test_tui_screens_switch_and_auto_dry_run(tmp_path, config_env):
    from pysubzone.tui import (
        AutoScreen,
        BrowseScreen,
        DownloadDialog,
        SearchScreen,
        SubZoneTUI,
    )
    from textual.widgets import Button, DataTable, Input

    (tmp_path / "Show.S01E01.1080p.mkv").touch()

    async def scenario():
        app = SubZoneTUI(_FakeClient())
        async with app.run_test(size=(110, 44)) as pilot:
            assert isinstance(app.screen, SearchScreen)

            await pilot.press("ctrl+b")
            await pilot.pause()
            assert isinstance(app.screen, BrowseScreen)
            await app.workers.wait_for_complete()
            await pilot.pause()

            await pilot.press("ctrl+a")
            await pilot.pause()
            assert isinstance(app.screen, AutoScreen)
            app.screen.query_one("#auto-folder", Input).value = str(tmp_path)
            await pilot.pause()
            app.screen.query_one("#auto-dry", Button).press()
            await app.workers.wait_for_complete()
            await pilot.pause()
            table = app.screen.query_one("#auto-table", DataTable)
            assert table.row_count == 1
            dry_button = app.screen.query_one("#auto-dry", Button)
            assert not dry_button.disabled  # re-enabled after the run

            # Enter on the matched row opens the title's VersionsScreen
            await pilot.press("enter")
            await pilot.pause()

    _run_async(scenario())


def test_tui_auto_match_extract_saves_files(tmp_path, config_env):
    """Regression for v0.4.2: 'Match & extract' failed on every video because
    the TUI called the CLI-only _resolve_out (NameError -> row status 'error').
    A relative output folder must be created inside the videos folder, exactly
    like `subz auto -o subs` does."""
    from pysubzone.tui import AutoScreen, SubZoneTUI
    from textual.widgets import Button, Input

    (tmp_path / "Show.S01E01.1080p.mkv").touch()
    client = _WorkingClient()

    async def scenario():
        app = SubZoneTUI(client)
        async with app.run_test(size=(110, 44)) as pilot:
            # Ctrl+A must reach the auto-match screen straight from search
            await pilot.press("ctrl+a")
            await pilot.pause()
            assert isinstance(app.screen, AutoScreen)
            screen = app.screen
            screen.query_one("#auto-folder", Input).value = str(tmp_path)
            screen.query_one("#auto-out", Input).value = "subs"
            await pilot.pause()
            screen.query_one("#auto-extract", Button).press()
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert screen.matches[0]["status"] == "saved", screen.matches[0]
            assert client.downloads and client.downloads[0]["mode"] == "extract"
            # relative output resolved inside the videos folder, like the CLI
            assert client.downloads[0]["dest"] == (tmp_path / "subs").resolve()
            assert (tmp_path / "subs" / "Show.S01E01.1080p.fa.srt").is_file()

    _run_async(scenario())


def test_tui_auto_match_failed_row_shows_reason_on_enter(tmp_path, config_env):
    """A failed download must not stay a bare 'error' - Enter on the failed
    row prints the underlying message in the status line and keeps the user
    on the auto screen."""
    from pysubzone.tui import AutoScreen, SubZoneTUI
    from textual.widgets import Button, Input, Static

    (tmp_path / "Show.S01E01.1080p.mkv").touch()
    client = _WorkingClient(download_error=RuntimeError("boom offline"))

    async def scenario():
        app = SubZoneTUI(client)
        async with app.run_test(size=(110, 44)) as pilot:
            await pilot.press("ctrl+a")
            await pilot.pause()
            screen = app.screen
            screen.query_one("#auto-folder", Input).value = str(tmp_path)
            await pilot.pause()
            screen.query_one("#auto-extract", Button).press()
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert screen.matches[0]["status"] == "error"
            await pilot.press("enter")
            await pilot.pause()
            status_text = str(screen.query_one("#auto-status", Static).content)
            assert "boom offline" in status_text
            # stayed on the auto screen, no version list pushed
            assert isinstance(app.screen, AutoScreen)

    _run_async(scenario())


def test_tui_ctrl_a_from_search_screen_keeps_typed_text(config_env):
    """Ctrl+A must open auto-match straight from the search screen (not via
    Browse first), and pressing it again must not wipe what was typed."""
    from pysubzone.tui import AutoScreen, SearchScreen, SubZoneTUI
    from textual.widgets import Input

    async def scenario():
        app = SubZoneTUI(_FakeClient())
        async with app.run_test(size=(110, 44)) as pilot:
            assert isinstance(app.screen, SearchScreen)
            await pilot.press("ctrl+a")
            await pilot.pause()
            assert isinstance(app.screen, AutoScreen)
            app.screen.query_one("#auto-folder", Input).value = "typed-folder"
            await pilot.pause()
            await pilot.press("ctrl+a")
            await pilot.pause()
            assert isinstance(app.screen, AutoScreen)
            assert app.screen.query_one("#auto-folder", Input).value == "typed-folder"

    _run_async(scenario())


def test_tui_title_bar_shows_version(config_env):
    """The header must show the running version next to the tagline."""
    from pysubzone import __version__
    from pysubzone.tui import SubZoneTUI

    async def scenario():
        app = SubZoneTUI(_FakeClient())
        async with app.run_test(size=(80, 24)):
            assert app.title == "PySubZone"
            assert app.sub_title == f"subzone.ir subtitle downloader — v{__version__}"

    _run_async(scenario())


def test_resolve_out_shared_by_cli_and_tui():
    """The v0.4.1 regression happened because the TUI re-implemented the
    CLI's output resolution; both must use the very same helper."""
    from pysubzone.cli import _resolve_out
    from pysubzone.matcher import resolve_out
    from pysubzone.tui import resolve_out

    assert _resolve_out is resolve_out
    assert _resolve_out is resolve_out


def test_tui_download_dialog_has_rename_input(tmp_path, config_env):
    from pysubzone.tui import DownloadDialog, SubZoneTUI
    from textual.widgets import Button, Input

    async def scenario():
        app = SubZoneTUI(_FakeClient())
        async with app.run_test(size=(110, 44)) as pilot:
            version = SubtitleVersion(
                slug="show-season-1", lang="farsi_persian", sub_id="9",
                releases=["Show.S01E01.1080p"], author="tester",
            )
            app.push_screen(DownloadDialog(app.client, version))
            await pilot.pause()
            assert isinstance(app.screen, DownloadDialog)
            app.screen.query_one("#saveas", Input).value = "Renamed.Show"
            await pilot.pause()
            extract = app.screen.query_one("#extract", Button)
            extract.press()
            await app.workers.wait_for_complete()
            await pilot.pause()
            # fake client raises -> error is shown, buttons re-enabled
            assert not app.screen.query_one("#extract", Button).disabled

    _run_async(scenario())


# ---------------------------------------------------------------------------
# 0.4.0: ScoreWeights (configurable scoring)
# ---------------------------------------------------------------------------

def _match_versions():
    return [
        SubtitleVersion(
            slug="show-season-1", lang="farsi_persian", sub_id="1",
            releases=["Show.S01E01.1080p.WEB-DL-AAA"], author="a",
        ),
        SubtitleVersion(
            slug="show-season-1", lang="farsi_persian", sub_id="2",
            releases=["Show.S01E01.720p.WEB-DL-NTb"], author="b",
        ),
    ]


def test_score_weights_defaults_reproduce_builtin_ranking():
    from pysubzone.scoring import DEFAULT_WEIGHTS, pick_best

    video = "Show.S01E01.1080p.WEB-DL-NTb"
    assert pick_best(video, _match_versions()) == pick_best(
        video, _match_versions(), DEFAULT_WEIGHTS
    )
    assert pick_best(video, _match_versions())[1].sub_id == "1"


def test_score_weights_change_ranking():
    from pysubzone.scoring import ScoreWeights, pick_best

    video = "Show.S01E01.1080p.WEB-DL-NTb"
    weights = ScoreWeights(
        resolution_match=0,
        resolution_mismatch=0,
        resolution_unspecified=0,
        source_match=20,
        group_match=100,
    )
    picked = pick_best(video, _match_versions(), weights)
    assert picked[1].sub_id == "2"  # release group now dominates


def test_score_weights_season_pack_floor():
    from pysubzone.scoring import ScoreWeights, score_version

    pack = SubtitleVersion(
        slug="show-season-1", lang="farsi_persian", sub_id="3",
        releases=["Show.S01.1080p.WEB-DL"], author="c",
    )
    # res 25 + source 20 + group 15 + token overlap 10, no pin/rate bonus
    assert score_version("Show.S01E01.1080p.WEB-DL", pack) == 70
    weights = ScoreWeights(season_pack_floor=-1)
    assert score_version("Show.S01E01.1080p.WEB-DL", pack, weights) >= 0


def test_best_for_file_forwards_weights(tmp_path, monkeypatch):
    from pysubzone.client import SubZoneClient
    from pysubzone.models import TitleResult
    from pysubzone.scoring import ScoreWeights

    client = SubZoneClient()
    seen = {}

    def fake_pick_best(stem, versions, weights=None):
        seen["weights"] = weights
        return (10, versions[0])

    monkeypatch.setattr("pysubzone.client.pick_best", fake_pick_best)
    monkeypatch.setattr(
        client, "search_exact",
        lambda query, lang=None: TitleResult(name="Show", slug="show-season-1", url=""),
    )
    monkeypatch.setattr(
        client, "title_versions", lambda slug, lang=None: _match_versions()
    )
    client.best_for_file("Show.S01E01.1080p", weights=ScoreWeights(group_match=99))
    assert seen["weights"].group_match == 99
    client.best_for_file("Show.S01E01.1080p")
    assert seen["weights"] is None  # untouched default passes through


# ---------------------------------------------------------------------------
# 0.4.0: rate limiting + page cache
# ---------------------------------------------------------------------------

class _FakeResp:
    def __init__(self, url):
        self.status_code = 200
        self.text = f"page:{url}"


class _FakeSession:
    def __init__(self):
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        return _FakeResp(url)


def test_rate_limit_sleeps_between_requests(monkeypatch):
    import pysubzone.client as client_mod
    from pysubzone.client import SubZoneClient

    sleeps = []
    monkeypatch.setattr(client_mod.time, "sleep", lambda s: sleeps.append(s))
    client = SubZoneClient(min_request_interval=0.4)
    client.session = _FakeSession()

    client._get("/first")
    assert sleeps == []  # first request is never delayed
    client._get("/second")
    assert len(sleeps) == 1
    assert 0 < sleeps[0] <= 0.4


def test_page_cache_hits_session_once():
    from pysubzone.client import SubZoneClient

    client = SubZoneClient(cache_ttl=60)
    fake = _FakeSession()
    client.session = fake
    r1 = client._get("/subtitles/show")
    r2 = client._get("/subtitles/show")
    assert len(fake.calls) == 1
    assert r1 is r2 and r1.text == "page:https://subzone.ir/subtitles/show"
    client._get("/subtitles/show", params={"query": "x"})
    assert len(fake.calls) == 2  # different query -> different cache key


def test_page_cache_expires_after_ttl(monkeypatch):
    import pysubzone.client as client_mod
    from pysubzone.client import SubZoneClient

    clock = {"t": 100.0}
    monkeypatch.setattr(client_mod.time, "monotonic", lambda: clock["t"])
    client = SubZoneClient(cache_ttl=10)
    fake = _FakeSession()
    client.session = fake

    client._get("/a")
    clock["t"] += 5
    client._get("/a")
    assert len(fake.calls) == 1  # still fresh
    clock["t"] += 6  # 11s > ttl of 10s
    client._get("/a")
    assert len(fake.calls) == 2  # expired -> refetched


def test_cache_never_caches_stream_downloads():
    from pysubzone.client import SubZoneClient

    client = SubZoneClient(cache_ttl=60)
    fake = _FakeSession()
    client.session = fake
    client._get("/download", stream=True)
    client._get("/download", stream=True)
    assert len(fake.calls) == 2


def test_clear_cache_forces_refetch():
    from pysubzone.client import SubZoneClient

    client = SubZoneClient(cache_ttl=60)
    fake = _FakeSession()
    client.session = fake
    client._get("/a")
    client.clear_cache()
    client._get("/a")
    assert len(fake.calls) == 2


def test_parse_ref_raises_invalid_url_error():
    from pysubzone.client import SubZoneClient
    from pysubzone.exceptions import InvalidURLError

    with pytest.raises(InvalidURLError):
        SubZoneClient._parse_ref("not-a-ref")
    with pytest.raises(InvalidURLError):
        SubZoneClient._parse_ref("slug/lang/notanumber")


# ---------------------------------------------------------------------------
# 0.4.0: py.typed + async client
# ---------------------------------------------------------------------------

def test_py_typed_marker_ships():
    import pysubzone

    assert (Path(pysubzone.__file__).parent / "py.typed").is_file()


def test_async_client_delegates_to_sync_client():
    import asyncio

    from pysubzone.async_client import AsyncSubZoneClient

    async def scenario():
        client = AsyncSubZoneClient()
        calls = []
        client._client.search = (
            lambda query, lang=None: calls.append(("search", query, lang)) or ["r"]
        )
        client._client.best_for_file = (
            lambda stem, lang=None, title_query=None, weights=None:
            calls.append(("bff", stem, lang)) or ("t", "v", 1)
        )
        client._client.download = (
            lambda *a, **k: calls.append(("dl", a[0], a[1])) or "dl-ok"
        )
        assert await client.search("dexter", lang="fa") == ["r"]
        assert await client.best_for_file("Show.S01E01") == ("t", "v", 1)
        assert await client.download("a/b/1", dest_dir="/tmp/x") == "dl-ok"
        await client.close()
        return calls

    calls = asyncio.run(scenario())
    assert ("search", "dexter", "fa") in calls
    assert ("bff", "Show.S01E01", "farsi_persian") in calls
    assert ("dl", "a/b/1", "/tmp/x") in calls


def test_async_client_context_manager():
    import asyncio

    from pysubzone.async_client import AsyncSubZoneClient

    async def scenario():
        async with AsyncSubZoneClient(cache_ttl=5) as client:
            assert client.sync.cache_ttl == 5
            assert client.sync.min_request_interval == 0

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 0.4.0: CLI languages / title / --map-json / network options
# ---------------------------------------------------------------------------

def test_cli_languages_lists_known_languages(config_env):
    import json as _json

    from pysubzone.cli import app

    result = _runner().invoke(app, ["languages"])
    assert result.exit_code == 0, result.output
    assert "farsi_persian" in result.output
    assert "Farsi/Persian" in result.output
    assert "fa" in result.output

    result = _runner().invoke(app, ["languages", "--json"])
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    fa = next(row for row in data if row["slug"] == "farsi_persian")
    assert fa["name"] == "Farsi/Persian" and "fa" in fa["codes"]
    assert len(data) > 50


def test_cli_title_shows_language_tabs(config_env, monkeypatch):
    import json as _json

    from pysubzone.cli import app
    from pysubzone.models import TitlePage

    class _StubClient:
        base_url = "https://subzone.ir"

        def title(self, slug):
            return TitlePage(
                slug="show-season-1", name="Show", year=2024, imdb_id="tt1234567",
                languages=[
                    {"slug": "farsi_persian", "count": 12, "url": "/x"},
                    {"slug": "english", "count": 40, "url": "/y"},
                ],
                versions=[
                    SubtitleVersion(slug="show-season-1", lang="farsi_persian", sub_id="1")
                ],
            )

    monkeypatch.setattr("pysubzone.cli._client", lambda settings: _StubClient())

    result = _runner().invoke(app, ["title", "show-season-1", "--json"])
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["slug"] == "show-season-1" and data["year"] == 2024
    assert len(data["languages"]) == 2
    assert data["url"].endswith("/subtitles/show-season-1")

    result = _runner().invoke(app, ["title", "show-season-1"])
    assert result.exit_code == 0, result.output
    assert "Farsi/Persian" in result.output and "tt1234567" in result.output


def _auto_stub_client():
    from pysubzone.models import DownloadResult, TitleResult

    class _StubClient:
        def best_for_file(self, stem, lang=None):
            return (
                TitleResult(name="Show", slug="show-season-1", url=""),
                SubtitleVersion(
                    slug="show-season-1", lang="farsi_persian", sub_id="7",
                    releases=["Show.S01E01.1080p"], author="t",
                ),
                55,
            )

        def download(self, target, **kwargs):
            return DownloadResult(
                zip_path=None, srt_paths=["/tmp/Show.S01E01.fa.srt"],
                version=target, bytes_downloaded=100,
            )

    return _StubClient()


def test_cli_auto_map_json_writes_file(tmp_path, config_env, monkeypatch):
    import json as _json

    from pysubzone.cli import app

    (tmp_path / "Show.S01E01.1080p.mkv").touch()
    monkeypatch.setattr("pysubzone.cli._client", lambda settings: _auto_stub_client())
    map_path = tmp_path / "out" / "map.json"

    result = _runner().invoke(app, ["auto", str(tmp_path), "-y", "--map-json", str(map_path)])
    assert result.exit_code == 0, result.output
    data = _json.loads(map_path.read_text())
    assert data["summary"] == {"total": 1, "ok": 1}
    assert data["results"][0]["status"] == "ok"
    assert data["results"][0]["saved"] == ["/tmp/Show.S01E01.fa.srt"]
    assert data["language"] == "farsi_persian"
    assert "Wrote" in result.output


def test_cli_auto_map_json_stdout_is_clean_json(tmp_path, config_env, monkeypatch):
    import json as _json

    from pysubzone.cli import app

    (tmp_path / "Show.S01E01.1080p.mkv").touch()
    monkeypatch.setattr("pysubzone.cli._client", lambda settings: _auto_stub_client())

    result = _runner().invoke(app, ["auto", str(tmp_path), "-y", "--map-json", "-"])
    assert result.exit_code == 0
    data = _json.loads(result.output)  # nothing but JSON on stdout
    assert data["summary"]["total"] == 1


def test_cli_auto_map_json_dry_run_and_min_score(tmp_path, config_env, monkeypatch):
    import json as _json

    from pysubzone.cli import app

    (tmp_path / "Show.S01E01.1080p.mkv").touch()
    monkeypatch.setattr("pysubzone.cli._client", lambda settings: _auto_stub_client())

    result = _runner().invoke(
        app, ["auto", str(tmp_path), "-y", "--dry-run", "--map-json", "-"]
    )
    data = _json.loads(result.output)
    assert data["results"][0]["status"] == "dry-run"
    assert data["dry_run"] is True

    result = _runner().invoke(
        app, ["auto", str(tmp_path), "-y", "--min-score", "90", "--map-json", "-"]
    )
    data = _json.loads(result.output)
    assert data["results"][0]["status"] == "skipped"
    assert data["min_score"] == 90


def test_cli_new_commands_and_network_options_listed(config_env):
    from pysubzone.cli import app

    help_text = _runner().invoke(app, ["--help"]).output
    for name in ("title", "languages"):
        assert name in help_text, f"missing command {name}"
    for option in ("--min-interval", "--cache-ttl"):
        assert option in help_text, f"missing option {option}"

    auto_help = _runner().invoke(app, ["auto", "--help"]).output
    assert "--map-json" in auto_help


def test_config_network_rate_keys(config_env):
    from pysubzone.config import load_config, set_config_value

    assert set_config_value("network.min-interval", "0.5") == 0.5
    assert set_config_value("network.cache-ttl", "30") == 30.0
    flat = load_config()
    assert flat["network.min-interval"] == 0.5
    assert flat["network.cache-ttl"] == 30.0
    with pytest.raises(ValueError):
        set_config_value("network.min-interval", "999")  # out of bounds


def test_cli_url_accepts_language_page_url(config_env, monkeypatch):
    """`subz url <title>/<lang>` grabs that language without -l (v0.4.1)."""
    from pysubzone.cli import app

    recorded = {}

    class _StubClient:
        base_url = "https://subzone.ir"

        def download_url(self, url, lang=None, limit=1, **kwargs):
            recorded["url"] = url
            recorded["lang"] = lang
            recorded["limit"] = limit
            return []

    monkeypatch.setattr("pysubzone.cli._client", lambda settings: _StubClient())

    result = _runner().invoke(
        app,
        ["url", "https://subzone.ir/subtitles/show-season-1/farsi_persian", "-n", "2"],
    )
    assert result.exit_code == 0, result.output
    assert recorded["url"] == "/subtitles/show-season-1"
    assert recorded["lang"] == "farsi_persian"  # taken from the URL itself
    assert recorded["limit"] == 2

    # an explicit -l still wins over the URL language (passed through raw;
    # client.download_url resolves aliases like "en" -> "english" itself)
    result = _runner().invoke(
        app,
        [
            "url",
            "https://subzone.ir/subtitles/show-season-1/farsi_persian",
            "-l",
            "en",
        ],
    )
    assert result.exit_code == 0, result.output
    assert recorded["lang"] == "en"


def test_download_cdn_404_raises_friendly_error(tmp_path, monkeypatch):
    """A 404 from the signed CDN link becomes the stale-upload error, not NetworkError."""
    from pysubzone.client import SubZoneClient
    from pysubzone.exceptions import DownloadError

    client = SubZoneClient()
    monkeypatch.setattr(client, "_get", lambda *a, **k: None)  # _get 404 -> None
    # releases set -> download() skips the metadata prefetch that also uses _get
    version = SubtitleVersion(
        slug="show", lang="farsi_persian", sub_id="42", releases=["Show.S01E01.1080p"]
    )
    with pytest.raises(DownloadError) as excinfo:
        client.download(version, dest_dir=tmp_path)
    assert "missing on the site's CDN" in str(excinfo.value)
    assert "try another version" in str(excinfo.value)


def test_download_url_skips_stale_uploads_but_keeps_the_rest(tmp_path, monkeypatch):
    """One dead CDN link no longer aborts a multi-version URL grab (v0.4.1)."""
    from pysubzone.client import SubZoneClient
    from pysubzone.exceptions import DownloadError, NoSubtitleFoundError
    from pysubzone.models import SubtitleVersion

    client = SubZoneClient()
    versions = [
        SubtitleVersion(slug="show", lang="farsi_persian", sub_id=str(i), releases=["Show.S01E01"])
        for i in (1, 2, 3)
    ]
    monkeypatch.setattr(client, "title_versions", lambda slug, lang=None: versions)

    def fake_download(target, **kwargs):
        if target.sub_id == "2":
            raise DownloadError("subtitle file missing on the site's CDN (HTTP 404)")
        return _result_for(target.ref)

    monkeypatch.setattr(client, "download", fake_download)
    results = client.download_url("/subtitles/show", limit=3)
    assert [r.version.sub_id for r in results] == ["1", "3"]  # dead upload skipped

    # every version dead -> the first error is re-raised
    def all_dead(target, **kwargs):
        raise DownloadError("subtitle file missing on the site's CDN (HTTP 404)")

    monkeypatch.setattr(client, "download", all_dead)
    with pytest.raises(DownloadError):
        client.download_url("/subtitles/show", limit=3)


# ---------------------------------------------------------------------------
# season guard + episode ranges (v0.4.3)
# (regression: the Arcane S2 run downloaded a Season 1 COMPLETE pack for six
#  of nine episodes because season-only releases were never rejected, and
#  act packs "S02E01-E03" were hard-rejected for every episode but the first)
# ---------------------------------------------------------------------------

def _ver(sub_id, releases, slug="arcane-league-of-legends-first-season"):
    return SubtitleVersion(slug=slug, lang="english", sub_id=sub_id, releases=releases)


def test_season_info_numeric_and_word_forms():
    from pysubzone.scoring import season_info

    assert season_info("Show.S02E05.1080p.WEB-DL").season == 2
    assert season_info("Show.S02E05.1080p.WEB-DL").episodes == (5, 5)
    assert season_info("Show.SEASON.01.S01.COMPLETE.1080p").season == 1
    assert season_info("Show - Complete Season 1 - NF WEBRip").season == 1
    assert season_info("Show - The Complete First Season").season == 1
    assert season_info("Show - The Complete Second Season").season == 2
    assert season_info("فصل دوم کامل").season == 2
    assert season_info("فصل اول کامل").season == 1
    # conflicting season tokens are ambiguous -> guard disabled, not guessed
    assert season_info("Show.S01-S02.COMPLETE").season is None
    # movies carry no season/episode tokens at all
    movie = season_info("The.Odyssey.2026.1080p.AMZN.WEB-DL.DDP5.1.H.264-NTb")
    assert movie.season is None and movie.episodes is None


def test_season_info_ranges_and_alt_forms():
    from pysubzone.scoring import season_info

    assert season_info("Show.S02E01-E03.1080p.WEBRip").episodes == (1, 3)
    assert season_info("Show.S01E07~E09.1080p.WEB-DL").episodes == (7, 9)
    assert season_info("Show.S02E04-E05-E06.NF.WEBRip").episodes == (4, 6)
    assert season_info("Show.S02EP01.LEAKED.AAC.H.264-4chan").episodes == (1, 1)
    # a digit-leading release group after a dash is NOT an episode range
    assert season_info("Show.S02E01.1080p.x265-2CH").episodes == (1, 1)
    assert season_info("Show.S02E01.1080p.x265-4chan").episodes == (1, 1)
    assert season_info("Show 2x05 1080p WEBRip").season == 2
    assert season_info("Show 2x05 1080p WEBRip").episodes == (5, 5)
    assert season_info("Show Season 2 Episode 5 1080p").season == 2
    assert season_info("Show Season 2 Episode 5 1080p").episodes == (5, 5)


def test_season_guard_rejects_s1_pack_for_s2_video():
    from pysubzone.scoring import score_release, score_version

    video = "Arcane.S02E02.Watch.It.All.Burn.1080p.WEBRip.10bit.DDP5.1.x265-HODL"
    psa_lines = [
        "Arcane.SEASON.01.S01.COMPLETE.1080p.10bit.WEBRip.6CH.x265.HEVC-PSA",
        "Arcane.SEASON.01.S01.COMPLETE.720p.10bit.WEBRip.2CH.x265.HEVC-PSA",
    ]
    score, ok = score_release(video, psa_lines[0])
    assert score == -100 and ok is False
    assert score_version(video, _ver("3561362", psa_lines)) is None


def test_season_guard_keeps_same_season_pack():
    from pysubzone.scoring import score_version

    version = _ver("3561361", [
        "Arcane.SEASON.02.S02.COMPLETE.1080p.10bit.WEBRip.6CH.x265.HEVC-PSA",
    ])
    video = "Arcane.S01E03.1080p.WEBRip.10bit.DDP5.1.x265-HODL".replace("S01E03", "S02E03")
    assert score_version(video, version) >= 40  # matching season pack stays a fallback


def test_season_guard_word_and_persian_forms():
    from pysubzone.scoring import score_release

    video = "Show.S02E05.1080p.WEB-DL"
    assert score_release(video, "Show - The Complete First Season")[0] == -100
    assert score_release(video, "Show - Complete Season 1 - NF WEBRip")[0] == -100
    assert score_release(video, "فصل اول کامل 1080p")[0] == -100
    assert score_release(video, "Show.SEASON.02.COMPLETE.1080p")[0] >= 0
    assert score_release("Show.S01E05.1080p.WEB-DL", "فصل اول کامل 1080p")[0] >= 0


def test_season_guard_ambiguous_range_not_rejected():
    from pysubzone.scoring import score_release

    both = "Show.S01-S02.COMPLETE.1080p.WEB-DL"
    assert score_release("Show.S01E05.1080p.WEB-DL", both)[0] >= 0
    assert score_release("Show.S02E05.1080p.WEB-DL", both)[0] >= 0


def test_season_guard_skipped_for_ref_fallback_line():
    from pysubzone.scoring import score_version

    # a version with no release lines falls back to "slug/lang/id"; the slug
    # season ("first-season") must not reject Season 2 videos
    version = SubtitleVersion(
        slug="arcane-league-of-legends-first-season", lang="english", sub_id="9",
        releases=[],
    )
    assert score_version("Arcane.S02E02.1080p.WEB-DL", version) is not None


def test_episode_range_act_pack_matches_covered_episodes_only():
    from pysubzone.scoring import score_release

    pack = "Show.S02E01-E03.1080p.WEBRip"
    for ep in ("S02E01", "S02E02", "S02E03"):
        assert score_release(f"Show.{ep}.1080p.WEBRip.x265", pack)[0] >= 0
    for ep in ("S02E04", "S02E05"):
        assert score_release(f"Show.{ep}.1080p.WEBRip.x265", pack)[0] == -100


def test_word_form_and_alt_form_videos_reject_wrong_season():
    from pysubzone.scoring import score_release

    video = "Arcane Season 2 Episode 1 1080p NF WEB-DL"
    assert score_release(video, "Arcane.S01E09.480p.WEB-DL.RMT.GM")[0] == -100
    assert score_release(video, "Arcane.S02E01.NF.WEBRip.1080p")[0] >= 0
    video2 = "Arcane 2x01 1080p NF WEB-DL"
    assert score_release(video2, "Arcane.S02E09.1080p.NF.WEB-DL")[0] == -100
    assert score_release(video2, "Arcane.S02E01.1080p.NF.WEBRip")[0] >= 0


def test_movie_matching_unaffected():
    from pysubzone.scoring import score_release, score_version

    video = "The.Odyssey.2026.1080p.AMZN.WEB-DL.DDP5.1.H.264-NTb"
    sub = "The.Odyssey.2026.1080p.AMZN.WEB-DL.DDP5.1.Atmos.H.264-NTb"
    assert score_release(video, sub)[0] >= 50
    version = _ver("1", [sub], slug="the-odyssey-2026")
    assert score_version(video, version) >= 50


def test_movie_with_episode_word_title_still_matches():
    from pysubzone.scoring import score_release

    video = "Star.Wars.Episode.8.The.Last.Jedi.2017.1080p.BluRay"
    sub = "Star.Wars.Episode.8.The.Last.Jedi.2017.2160p.BluRay"
    assert score_release(video, sub)[0] >= 0


def test_guess_title_cuts_word_form_season():
    assert guess_title("Arcane Season 2 Episode 1 1080p NF WEB-DL") == "Arcane"
    assert guess_title("Show.Name.SEASON.02.720p.WEBRip") == "Show Name"


def test_guess_title_keeps_season_word_without_digits():
    # "Season of the Witch" is a movie title - no season digits -> no cut
    assert guess_title("Season.of.the.Witch.2010.1080p.BluRay") == "Season of the Witch"


def test_arcane_s2_replay_never_picks_season_1():
    """The exact live-data scenario that shipped wrong-season ZIPs (v0.4.3).

    Release lines are the real upload rows of
    /subtitles/arcane-league-of-legends-first-season/english at release time.
    """
    from pysubzone.scoring import rank_versions

    versions = [
        _ver("3554502", [
            "Arcane.S02E01-E03.NF.WEBRip",
            "Arcane.S02E01-E03.1080p.NF.WEBRip.DDP5.1.Atmos.x264-SuccessfulCrab",
            "Arcane.S02E01-E03.720p.NF.WEB-DL.DDP5.1.Atmos.H.264",
        ]),
        _ver("3556509", [
            "Arcane.S02E04-E05-E06.NF.WEBRip",
            "Arcane.S02E04-E05-E06.1080p.NF.WEBRip.DDP5.1.Atmos.x264-SuccessfulCrab",
        ]),
        _ver("3558977", [
            "Arcane.S02E07-E08-E09.NF.WEBRip",
            "Arcane.S02E07-E08-E09.1080p.WEB.H264-SuccessfulCrab",
        ]),
        _ver("3559016", [
            "Arcane.S02.NF.WEBRip",
            "Arcane.S02.1080p.WEB.H264-SuccessfulCrab",
            "Arcane.S02.720p.NF.WEB-DL.DDP5.1.Atmos.H.264",
        ]),
        _ver("3561362", [
            "Arcane.SEASON.01.S01.COMPLETE.1080p.10bit.WEBRip.6CH.x265.HEVC-PSA",
            "Arcane.SEASON.01.S01.COMPLETE.720p.10bit.WEBRip.2CH.x265.HEVC-PSA",
        ]),
        _ver("3633501", ["Arcane - The Complete First Season"]),
        _ver("3026864", ["Arcane - Complete Season 1 - NF WEBRip"]),
        _ver("3547988", ["Arcane.League.of.Legends.S01.2021.ENGLISH.1080p.BluRay.H264.AAC-SWHA"]),
        _ver("2615457", [
            "Arcane.S01E01~E03.720p.NF.WEB-DL.DDP5.1.H.264-TEPES",
            "Arcane.S01E01~E03.1080p.NF.WEB-DL.DDP5.1.H.264-TEPES",
        ]),
        _ver("2620546", ["Arcane.S01E04~E06.720p.NF.WEB-DL.DDP5.1.H.264-TEPES"]),
        _ver("2625485", ["Arcane.S01E07~E09.720p.NF.WEB-DL.DDP5.1.H.264-TEPES"]),
        _ver("2804595", ["Arcane.S01E01 - Welcome to the Playground"]),
        _ver("2804599", ["Arcane.S01E05 - Everybody Wants to Be My Enemy"]),
    ]
    s2_ids = {"3554502", "3556509", "3558977", "3559016"}
    stems = [
        "Arcane.S02E01.Heavy.Is.the.Crown.1080p.WEBRip.10bit.DDP5.1.x265-HODL",
        "Arcane.S02E02.Watch.It.All.Burn.1080p.WEBRip.10bit.DDP5.1.x265-HODL",
        "Arcane.S02E03.Finally.Got.the.Name.Right.1080p.WEBRip.10bit.DDP5.1.x265-HODL",
        "Arcane.S02E04.Paint.the.Town.Blue.1080p.WEBRip.10bit.DDP5.1.x265-HODL",
        "Arcane.S02E05.Blisters.and.Bedrock.1080p.WEBRip.10bit.DDP5.1.x265-HODL",
        "Arcane.S02E06.The.Message.Hidden.Within.the.Pattern.1080p.WEBRip.10bit.DDP5.1.x265-HODL",
        "Arcane.S02E07.Pretend.Like.Its.the.First.Time.1080p.WEBRip.10bit.DDP5.1.x265-HODL",
        "Arcane.S02E08.Killing.Is.a.Cycle.1080p.WEBRip.10bit.DDP5.1.x265-HODL",
        "Arcane.S02E09.The.Dirt.Under.Your.Nails.1080p.WEBRip.10bit.DDP5.1.x265-HODL",
    ]
    for stem in stems:
        ranked = rank_versions(stem, versions)
        assert ranked, f"no candidate for {stem}"
        winner = ranked[0][1]
        assert winner.sub_id in s2_ids, f"{stem} -> season-1 upload {winner.sub_id}"
