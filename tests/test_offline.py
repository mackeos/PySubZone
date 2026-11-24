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


# ---------------------------------------------------------------------------
# pack-member selection + --prefer-complete (v0.4.4)
# (an E01 video matched to an E01-E03 pack used to write THREE files all
#  named after the video: <stem>.en.2.srt held episode 2's lines, etc.)
# ---------------------------------------------------------------------------

def _zip_payload_with_contents(members):
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in members.items():
            zf.writestr(name, content)
    return buf.getvalue()


def test_extract_filters_pack_member_for_episode_video(tmp_path):
    client = _offline_client()
    version = SubtitleVersion(slug="show", lang="english", sub_id="42")
    payload = _zip_payload_with_contents({
        "Show.S02E02.en.srt": "E02 lines",
        "Show.S02E01.en.srt": "E01 lines",
        "Show.S02E03.en.srt": "E03 lines",
    })
    written = client._extract_subtitles(
        payload, tmp_path, version, "en",
        target_video="Show.S02E01.1080p.WEBRip.x265.mkv",
    )
    assert [p.name for p in written] == ["Show.S02E01.1080p.WEBRip.x265.en.srt"]
    assert written[0].read_text(encoding="utf-8") == "E01 lines"


def test_extract_member_filter_word_form_video(tmp_path):
    client = _offline_client()
    version = SubtitleVersion(slug="show", lang="english", sub_id="42")
    payload = _zip_payload_with_contents({
        "Show.S02E01.en.srt": "E01 lines",
        "Show.S02E02.en.srt": "E02 lines",
    })
    written = client._extract_subtitles(
        payload, tmp_path, version, "en",
        target_video="Show Season 2 Episode 2 1080p.mkv",
    )
    assert [p.name for p in written] == ["Show Season 2 Episode 2 1080p.en.srt"]
    assert written[0].read_text(encoding="utf-8") == "E02 lines"


def test_extract_filter_falls_back_when_members_untagged(tmp_path):
    client = _offline_client()
    version = SubtitleVersion(slug="show", lang="farsi_persian", sub_id="42")
    payload = _zip_payload(["cd1.srt", "cd2.srt"])
    written = client._extract_subtitles(
        payload, tmp_path, version, "fa", target_video="Arcane.S02E01.mkv"
    )
    assert sorted(p.name for p in written) == [
        "Arcane.S02E01.fa.1.srt",
        "Arcane.S02E01.fa.2.srt",
    ]


def test_extract_no_filter_for_movie_or_season_video(tmp_path):
    client = _offline_client()
    version = SubtitleVersion(slug="show", lang="english", sub_id="42")
    payload = _zip_payload([
        "Show.S02E01.en.srt", "Show.S02E02.en.srt", "Show.S02E03.en.srt",
    ])
    written = client._extract_subtitles(
        payload, tmp_path, version, "en",
        target_video="Some.Movie.2023.1080p.BluRay.mkv",
    )
    assert len(written) == 3  # movies pin no episode -> numbered, unchanged
    written = client._extract_subtitles(
        payload, tmp_path, version, "en",
        target_video="Show.S02.1080p.WEBRip.mkv",
    )
    assert len(written) == 3  # season-folder video pins no episode either


def test_extract_wrong_season_member_not_selected(tmp_path):
    client = _offline_client()
    version = SubtitleVersion(slug="show", lang="english", sub_id="42")
    payload = _zip_payload_with_contents({
        "Show.S01E01.en.srt": "S1E01 lines",
        "Show.S01E02.en.srt": "S1E02 lines",
        "Show.S02E01.en.srt": "S2E01 lines",
    })
    written = client._extract_subtitles(
        payload, tmp_path, version, "en",
        target_video="Show.S02E01.1080p.WEBRip.mkv",
    )
    assert [p.name for p in written] == ["Show.S02E01.1080p.WEBRip.en.srt"]
    assert written[0].read_text(encoding="utf-8") == "S2E01 lines"


def test_zip_mode_keeps_full_pack(tmp_path, monkeypatch):
    from pysubzone.client import SubZoneClient

    class _FakeResponse:
        headers: dict = {}

        def iter_content(self, chunk_size=None):
            yield _zip_payload([
                "Show.S02E01.en.srt", "Show.S02E02.en.srt", "Show.S02E03.en.srt",
            ])

    client = SubZoneClient()
    monkeypatch.setattr(client, "_get", lambda *a, **k: _FakeResponse())
    version = SubtitleVersion(
        slug="show", lang="english", sub_id="42",
        releases=["Show.S02E01-E03.1080p.WEBRip"],
    )
    result = client.download(
        version, dest_dir=tmp_path, save_mode="zip",
        target_video="Show.S02E01.1080p.WEBRip.mkv",
    )
    import zipfile as _zipfile

    with _zipfile.ZipFile(result.zip_path) as zf:
        assert sorted(zf.namelist()) == [
            "Show.S02E01.en.srt", "Show.S02E02.en.srt", "Show.S02E03.en.srt",
        ]


def test_covers_episodes_rules():
    from pysubzone.scoring import covers_episodes

    season_pack = _ver("100", ["Arcane.Season2.COMPLETE.1080p.WEB-DL.DDP5.1.Atmos.x264"])
    span_pack = _ver("101", ["Arcane.S02E01-E09.1080p.WEB-DL"])
    act_pack = _ver("102", ["Arcane.S02E01-E03.1080p.WEBRip"])
    s1_pack = _ver("103", ["Arcane.SEASON.01.S01.COMPLETE.1080p.WEBRip"])
    untagged = _ver("104", ["Show.1080p.WEBRip"])

    ep5 = "Show.S02E05.1080p.WEB-DL"
    assert covers_episodes(ep5, season_pack)   # same-season COMPLETE pack
    assert covers_episodes(ep5, span_pack)     # span contains the episode
    assert not covers_episodes(ep5, act_pack)  # span stops at E03
    assert not covers_episodes(ep5, s1_pack)   # wrong season
    assert not covers_episodes(ep5, untagged)  # no season, no episodes
    assert not covers_episodes("Some.Movie.2023.1080p.BluRay", season_pack)
    assert not covers_episodes("Show.S02.1080p.WEBRip", season_pack)  # no pinned episode


def test_best_covering_version_picks_one_pack_for_all():
    from pysubzone.scoring import best_covering_version

    season_pack = _ver("100", [
        "Arcane.Season2.COMPLETE.1080p.WEB-DL.DDP5.1.Atmos.x264",
        "Arcane.Season2.COMPLETE.720p.WEB-DL",
    ])
    span_pack = _ver("101", ["Arcane.S02E01-E09.1080p.WEB-DL"])
    act_pack = _ver("102", ["Arcane.S02E01-E03.1080p.WEBRip"])
    s1_pack = _ver("103", ["Arcane.SEASON.01.S01.COMPLETE.1080p.WEBRip"])
    stems = [f"Show.S02E0{i}.1080p.WEB-DL" for i in (1, 2, 3)]
    best = best_covering_version(stems, [act_pack, s1_pack, span_pack, season_pack])
    assert best is not None
    assert best[1].sub_id in {"100", "101"}
    assert best_covering_version(["Some.Movie.2023.1080p.BluRay"], [season_pack]) is None
    assert best_covering_version([], [season_pack]) is None


def _auto_pack_stub_client():
    from pysubzone.models import DownloadResult, TitleResult

    downloads = []

    class _StubClient:
        def plan_prefer_complete(self, videos, lang=None):
            # the planner only calls best_for_file/title_versions, so the
            # real SubZoneClient implementation runs against this stub
            from pysubzone.client import SubZoneClient

            return SubZoneClient.plan_prefer_complete(self, videos, lang=lang)

        def find_complete_packs(self, videos, lang=None):
            from pysubzone.client import SubZoneClient

            return SubZoneClient.find_complete_packs(self, videos, lang=lang)

        def best_for_file(self, stem, lang=None):
            return (
                TitleResult(name="Show", slug="show-season-2", url=""),
                SubtitleVersion(
                    slug="show-season-2", lang="english", sub_id="7",
                    releases=["Show.S02E01-E03.1080p.WEBRip"], author="t",
                ),
                55,
            )

        def title_versions(self, slug, lang):
            return [
                SubtitleVersion(
                    slug="show-season-2", lang="english", sub_id="7",
                    releases=["Show.S02E01-E03.1080p.WEBRip"], author="t",
                ),
                SubtitleVersion(
                    slug="show-season-2", lang="english", sub_id="9",
                    releases=["Show.Season2.COMPLETE.1080p.WEB-DL"], author="t",
                ),
            ]

        def download(self, target, **kwargs):
            downloads.append((target, kwargs))
            name = Path(kwargs["target_video"]).stem
            return DownloadResult(
                zip_path=None,
                srt_paths=[f"/tmp/{name}.en.srt"],
                version=target, bytes_downloaded=100,
            )

    return _StubClient(), downloads


def test_cli_auto_prefer_complete_uses_pack_for_episodes(tmp_path, config_env, monkeypatch):
    import json as _json

    from pysubzone.cli import app

    (tmp_path / "Show.S02E01.1080p.WEBRip.mkv").touch()
    (tmp_path / "Show.S02E02.1080p.WEBRip.mkv").touch()
    (tmp_path / "Show.S02E05.1080p.WEBRip.mkv").touch()
    (tmp_path / "Some.Movie.2023.1080p.BluRay.mkv").touch()
    stub, downloads = _auto_pack_stub_client()
    monkeypatch.setattr("pysubzone.cli._client", lambda settings: stub)

    result = _runner().invoke(
        app,
        ["auto", str(tmp_path), "-y", "--prefer-complete", "--map-json", "-"],
    )
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    rows = {Path(r["video"]).name: r for r in data["results"]}
    ep1 = rows["Show.S02E01.1080p.WEBRip.mkv"]
    ep2 = rows["Show.S02E02.1080p.WEBRip.mkv"]
    ep5 = rows["Show.S02E05.1080p.WEBRip.mkv"]
    movie = rows["Some.Movie.2023.1080p.BluRay.mkv"]
    # the E01-E03 act pack cannot cover E05, so the COMPLETE pack wins for all
    # episode videos (sub_id 9)
    assert ep1["mode"] == "complete-pack" and ep1["ref"].endswith("/9")
    assert ep2["mode"] == "complete-pack" and ep2["ref"].endswith("/9")
    assert ep5["mode"] == "complete-pack" and ep5["ref"].endswith("/9")
    # the movie keeps its own per-video best (sub_id 7), no mode marker
    assert "mode" not in movie and movie["ref"].endswith("/7")
    assert {d[0].sub_id for d in downloads} == {"7", "9"}
    assert len(downloads) == 4


def test_cli_auto_prefer_complete_via_config(tmp_path, config_env, monkeypatch):
    import json as _json

    from pysubzone.cli import app

    (tmp_path / "Show.S02E01.1080p.WEBRip.mkv").touch()
    stub, _downloads = _auto_pack_stub_client()
    monkeypatch.setattr("pysubzone.cli._client", lambda settings: stub)

    runner = _runner()
    set_result = runner.invoke(app, ["config", "set", "defaults.prefer-complete", "true"])
    assert set_result.exit_code == 0, set_result.output

    result = runner.invoke(app, ["auto", str(tmp_path), "-y", "--map-json", "-"])
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["results"][0]["mode"] == "complete-pack"


def test_cli_auto_without_flag_keeps_per_video_best(tmp_path, config_env, monkeypatch):
    import json as _json

    from pysubzone.cli import app

    (tmp_path / "Show.S02E01.1080p.WEBRip.mkv").touch()
    (tmp_path / "Show.S02E02.1080p.WEBRip.mkv").touch()
    stub, _downloads = _auto_pack_stub_client()
    monkeypatch.setattr("pysubzone.cli._client", lambda settings: stub)

    result = _runner().invoke(app, ["auto", str(tmp_path), "-y", "--map-json", "-"])
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    for row in data["results"]:
        assert "mode" not in row
        assert row["ref"].endswith("/7")


# ---------------------------------------------------------------------------
# prefer-complete planner + TUI checkbox (v0.4.5)
# ---------------------------------------------------------------------------

def test_client_plan_prefer_complete_maps_group_to_one_pack(tmp_path):
    """SubZoneClient.plan_prefer_complete (shared engine of the CLI flag and
    the TUI checkbox): episode videos that resolve to one title are planned
    onto a single covering pack; movies and season-folder videos are not."""
    from pysubzone.client import SubZoneClient

    stub, _downloads = _auto_pack_stub_client()
    client = SubZoneClient()
    # real planner code path, offline transport: point the two network
    # methods at the stub
    client.best_for_file = stub.best_for_file
    client.title_versions = stub.title_versions

    names = (
        "Show.S02E01.1080p.WEBRip.mkv",
        "Show.S02E02.1080p.WEBRip.mkv",
        "Show.S02E05.1080p.WEBRip.mkv",
        "Show.S02.1080p.WEBRip.mkv",  # season folder: season but no episode
        "Some.Movie.2023.1080p.BluRay.mkv",
    )
    videos = [tmp_path / n for n in names]
    planned = client.plan_prefer_complete(videos, lang="english")

    # every episode is planned; the season folder and the movie are not
    assert set(planned) == {v for v in videos if "S02E0" in v.name}
    # the E01-E03 act pack cannot cover E05, so the COMPLETE pack (sub_id 9)
    # wins for the whole group
    assert {v[1].sub_id for v in planned.values()} == {"9"}
    # planned tuples are (title, pack version, score)
    title, version, score = planned[videos[0]]
    assert title.slug == "show-season-2" and score > 0


def test_client_plan_prefer_complete_returns_empty_without_cover(tmp_path):
    """When nothing covers the whole group the planner maps nothing - the
    normal per-video best match stays in charge."""
    from pysubzone.client import SubZoneClient
    from pysubzone.models import TitleResult

    class _ActPackOnlyClient:
        def best_for_file(self, stem, lang=None):
            return TitleResult(name="Show", slug="show-season-2", url=""), None, 50

        def title_versions(self, slug, lang=None):
            return [
                SubtitleVersion(
                    slug="show-season-2", lang="english", sub_id="7",
                    releases=["Show.S02E01-E02.1080p.WEBRip"], author="t",
                ),
            ]

    client = SubZoneClient()
    client.best_for_file = _ActPackOnlyClient().best_for_file
    client.title_versions = _ActPackOnlyClient().title_versions

    videos = [
        tmp_path / "Show.S02E01.1080p.WEBRip.mkv",
        tmp_path / "Show.S02E05.1080p.WEBRip.mkv",
    ]
    assert client.plan_prefer_complete(videos, lang="english") == {}


def test_tui_pack_picker_escape_falls_back_to_per_video_best(tmp_path, config_env):
    """Esc in the bulk pack picker must reject the pack for that group: every
    video then keeps its normal per-video best match (v0.5.0 bulk flow)."""
    from pysubzone.tui import BulkScreen, PackPickerScreen, SubZoneTUI
    from textual.widgets import Button, Input

    for name in ("Show.S02E01.1080p.WEBRip.mkv", "Show.S02E05.1080p.WEBRip.mkv"):
        (tmp_path / name).touch()
    client = _BulkTuiClient(with_pack=True)

    async def scenario():
        app = SubZoneTUI(client)
        async with app.run_test(size=(110, 46)) as pilot:
            await pilot.press("ctrl+p")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, BulkScreen)
            screen.query_one("#bulk-folder", Input).value = str(tmp_path)
            await pilot.pause()
            screen.query_one("#bulk-extract", Button).press()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert isinstance(app.screen, PackPickerScreen)

            # Esc = per-video best for this group
            await pilot.press("escape")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert screen.matches, screen.matches
            assert all(m["mode"] == "per-video" for m in screen.matches)
            assert all(m["status"] == "saved" for m in screen.matches)
            assert client.pack_calls == []
            assert len(client.video_calls) == 2
            assert all(v.sub_id == "3" for v in client.video_versions)

    _run_async(scenario())


def test_tui_pack_picker_second_choice_is_used(tmp_path, config_env):
    """Selecting a non-first row in the bulk picker must serve the group from
    that exact pack."""
    from pysubzone.tui import BulkScreen, PackPickerScreen, SubZoneTUI
    from textual.widgets import Button, Input

    for name in ("Show.S02E01.1080p.WEBRip.mkv", "Show.S02E05.1080p.WEBRip.mkv"):
        (tmp_path / name).touch()
    client = _BulkTuiClient(with_pack=True, with_span=True)
    assert len(client.ranked) == 2, "both packs must cover the two episodes"
    second_id = client.ranked[1][1].sub_id

    async def scenario():
        app = SubZoneTUI(client)
        async with app.run_test(size=(110, 46)) as pilot:
            await pilot.press("ctrl+p")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, BulkScreen)
            screen.query_one("#bulk-folder", Input).value = str(tmp_path)
            await pilot.pause()
            screen.query_one("#bulk-extract", Button).press()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert isinstance(app.screen, PackPickerScreen)

            # move to the second row and take it
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert screen.matches
            assert all(m["mode"] == "complete-pack" for m in screen.matches)
            assert all(m["status"] == "saved" for m in screen.matches)
            assert len(client.pack_calls) == 1
            assert client.pack_versions[0].sub_id == second_id
            assert client.video_calls == []

    _run_async(scenario())


def test_scoring_covering_versions_ranks_all_candidates():
    """covering_versions: every pack covering the whole group, best first,
    consistent with best_covering_version."""
    from pysubzone.scoring import best_covering_version, covering_versions

    def _ver(sub_id, lines):
        return SubtitleVersion(
            slug="show-season-2", lang="english", sub_id=str(sub_id),
            releases=lines, author="t",
        )

    season_pack = _ver("100", ["Arcane.Season2.COMPLETE.1080p.WEB-DL.DDP5.1.Atmos.x264"])
    span_pack = _ver("101", ["Arcane.S02E01-E09.1080p.WEB-DL"])
    act_pack = _ver("102", ["Arcane.S02E01-E03.1080p.WEBRip"])
    s1_pack = _ver("103", ["Arcane.SEASON.01.S01.COMPLETE.1080p.WEBRip"])
    stems = [f"Show.S02E0{i}.1080p.WEB-DL" for i in (1, 2, 3)]

    ranked = covering_versions(stems, [act_pack, s1_pack, span_pack, season_pack])
    ids = [v.sub_id for _total, v in ranked]
    # E01-E03 act pack covers these three stems too (E05 would not);
    # the S1 pack never qualifies (wrong season)
    assert set(ids) == {"100", "101", "102"}, ids
    # descending by total score
    totals = [total for total, _v in ranked]
    assert totals == sorted(totals, reverse=True)
    # the top entry IS best_covering_version's answer
    best = best_covering_version(stems, [act_pack, s1_pack, span_pack, season_pack])
    assert best is not None and best[1].sub_id == ranked[0][1].sub_id
    # empty inputs and no-cover cases
    assert covering_versions([], [season_pack]) == []
    assert covering_versions(["Some.Movie.2023.1080p.BluRay"], [season_pack]) == []


def test_client_find_complete_packs_groups_and_ranks(tmp_path):
    """find_complete_packs: one group per matched title, ranked covering
    packs, movies and season-folder videos excluded."""
    from pysubzone.client import SubZoneClient

    stub, _downloads = _auto_pack_stub_client()
    client = SubZoneClient()
    client.best_for_file = stub.best_for_file
    client.title_versions = stub.title_versions

    names = (
        "Show.S02E01.1080p.WEBRip.mkv",
        "Show.S02E05.1080p.WEBRip.mkv",
        "Show.S02.1080p.WEBRip.mkv",  # season folder: no episode
        "Some.Movie.2023.1080p.BluRay.mkv",
    )
    videos = [tmp_path / n for n in names]
    groups = client.find_complete_packs(videos, lang="english")

    assert len(groups) == 1
    title, group, ranked = groups[0]
    assert title.slug == "show-season-2"
    assert {p.name for p in group} == {
        "Show.S02E01.1080p.WEBRip.mkv",
        "Show.S02E05.1080p.WEBRip.mkv",
    }
    # both versions cover the two episodes (act pack spans 1-3? no - E05 is
    # outside, so only the COMPLETE pack qualifies)
    assert [v.sub_id for _t, v in ranked] == ["9"]


def _auto_multi_pack_stub_client():
    """The two-pack stub plus a spanning act pack, for pick tests."""
    stub, downloads = _auto_pack_stub_client()
    span = SubtitleVersion(
        slug="show-season-2", lang="english", sub_id="8",
        releases=["Show.S02E01-E05.1080p.WEBRip"], author="t",
    )
    base_versions = stub.title_versions("show-season-2", "english")
    stub.title_versions = lambda slug, lang=None: [*base_versions, span]
    return stub, downloads


def test_cli_auto_prefer_complete_lists_packs_and_uses_best(tmp_path, config_env, monkeypatch):
    """Non-interactive runs still show the ranked pack list and use the best
    pack automatically."""
    import json as _json

    from pysubzone.cli import app
    from pysubzone.scoring import covering_versions

    (tmp_path / "Show.S02E01.1080p.WEBRip.mkv").touch()
    (tmp_path / "Show.S02E02.1080p.WEBRip.mkv").touch()
    (tmp_path / "Show.S02E05.1080p.WEBRip.mkv").touch()
    (tmp_path / "Some.Movie.2023.1080p.BluRay.mkv").touch()
    stub, downloads = _auto_multi_pack_stub_client()
    monkeypatch.setattr("pysubzone.cli._client", lambda settings: stub)

    result = _runner().invoke(
        app, ["auto", str(tmp_path), "-y", "--prefer-complete", "--map-json", "-"],
    )
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    rows = {Path(r["video"]).name: r for r in data["results"]}

    stems = [
        "Show.S02E01.1080p.WEBRip.mkv",
        "Show.S02E02.1080p.WEBRip.mkv",
        "Show.S02E05.1080p.WEBRip.mkv",
    ]
    versions = stub.title_versions("show-season-2", "english")
    ranked = covering_versions(stems, versions)
    best_ref = ranked[0][1].ref
    assert len(ranked) == 2  # COMPLETE pack and E01-E05 span pack qualify

    for name in stems:
        row = rows[name]
        assert row["mode"] == "complete-pack"
        assert row["ref"] == best_ref
    movie = rows["Some.Movie.2023.1080p.BluRay.mkv"]
    assert "mode" not in movie and movie["ref"].endswith("/7")


def test_cli_auto_prefer_complete_interactive_pick(tmp_path, config_env, monkeypatch):
    """With a TTY and more than one covering pack, the user can pick one;
    choosing pack 2 must plan exactly that pack."""
    import json as _json

    from pysubzone.cli import app
    from pysubzone.scoring import covering_versions

    (tmp_path / "Show.S02E01.1080p.WEBRip.mkv").touch()
    (tmp_path / "Show.S02E02.1080p.WEBRip.mkv").touch()
    (tmp_path / "Show.S02E05.1080p.WEBRip.mkv").touch()
    (tmp_path / "Some.Movie.2023.1080p.BluRay.mkv").touch()
    stub, _downloads = _auto_multi_pack_stub_client()
    monkeypatch.setattr("pysubzone.cli._client", lambda settings: stub)
    monkeypatch.setattr("pysubzone.cli._stdin_isatty", lambda: True)

    stems = [
        "Show.S02E01.1080p.WEBRip.mkv",
        "Show.S02E02.1080p.WEBRip.mkv",
        "Show.S02E05.1080p.WEBRip.mkv",
    ]
    versions = stub.title_versions("show-season-2", "english")
    ranked = covering_versions(stems, versions)
    second_ref = ranked[1][1].ref

    result = _runner().invoke(
        app,
        ["auto", str(tmp_path), "--prefer-complete", "--map-json", str(tmp_path / "map.json")],
        input="2\n",
    )
    assert result.exit_code == 0, result.output
    data = _json.loads((tmp_path / "map.json").read_text(encoding="utf-8"))
    rows = {Path(r["video"]).name: r for r in data["results"]}
    for name in stems:
        row = rows[name]
        assert row["mode"] == "complete-pack"
        assert row["ref"] == second_ref
    # the movie is never part of any pack group
    assert "mode" not in rows["Some.Movie.2023.1080p.BluRay.mkv"]
    # the ranking was printed for the user
    assert "covering pack(s):" in result.output


def test_cli_auto_prefer_complete_interactive_skip(tmp_path, config_env, monkeypatch):
    """Answering 0 keeps the per-video best for the whole group."""
    import json as _json

    from pysubzone.cli import app

    (tmp_path / "Show.S02E01.1080p.WEBRip.mkv").touch()
    (tmp_path / "Show.S02E05.1080p.WEBRip.mkv").touch()
    stub, _downloads = _auto_multi_pack_stub_client()
    monkeypatch.setattr("pysubzone.cli._client", lambda settings: stub)
    monkeypatch.setattr("pysubzone.cli._stdin_isatty", lambda: True)

    result = _runner().invoke(
        app,
        ["auto", str(tmp_path), "--prefer-complete", "--map-json", str(tmp_path / "map.json")],
        input="0\n",
    )
    assert result.exit_code == 0, result.output
    data = _json.loads((tmp_path / "map.json").read_text(encoding="utf-8"))
    for row in data["results"]:
        assert "mode" not in row
        assert row["ref"].endswith("/7")
    assert "per-video best for these videos" in result.output


def test_cli_auto_prefer_complete_interactive_invalid_then_valid(tmp_path, config_env, monkeypatch):
    """Out-of-range answers re-prompt instead of crashing or mis-planning."""
    import json as _json

    from pysubzone.cli import app
    from pysubzone.scoring import covering_versions

    (tmp_path / "Show.S02E01.1080p.WEBRip.mkv").touch()
    (tmp_path / "Show.S02E05.1080p.WEBRip.mkv").touch()
    stub, _downloads = _auto_multi_pack_stub_client()
    monkeypatch.setattr("pysubzone.cli._client", lambda settings: stub)
    monkeypatch.setattr("pysubzone.cli._stdin_isatty", lambda: True)

    stems = ["Show.S02E01.1080p.WEBRip.mkv", "Show.S02E05.1080p.WEBRip.mkv"]
    ranked = covering_versions(stems, stub.title_versions("show-season-2", "english"))
    second_ref = ranked[1][1].ref

    result = _runner().invoke(
        app,
        ["auto", str(tmp_path), "--prefer-complete", "--map-json", str(tmp_path / "map.json")],
        input="9\n2\n",
    )
    assert result.exit_code == 0, result.output
    assert "Enter a number between 0 and 2" in result.output
    data = _json.loads((tmp_path / "map.json").read_text(encoding="utf-8"))
    for row in data["results"]:
        if row["video"].endswith(".mkv") and "S02E0" in row["video"]:
            assert row["ref"] == second_ref


def test_cli_auto_prefer_complete_dry_run_never_prompts(tmp_path, config_env, monkeypatch):
    """Dry runs list the packs but never prompt, even on a TTY."""
    from pysubzone.cli import app
    from pysubzone.scoring import covering_versions

    (tmp_path / "Show.S02E01.1080p.WEBRip.mkv").touch()
    (tmp_path / "Show.S02E05.1080p.WEBRip.mkv").touch()
    stub, downloads = _auto_multi_pack_stub_client()
    monkeypatch.setattr("pysubzone.cli._client", lambda settings: stub)
    monkeypatch.setattr("pysubzone.cli._stdin_isatty", lambda: True)

    result = _runner().invoke(
        app, ["auto", str(tmp_path), "--prefer-complete", "--dry-run"],
    )
    assert result.exit_code == 0, result.output
    assert "covering pack(s):" in result.output
    assert "-> pack 1" in result.output
    assert downloads == []  # dry run downloads nothing

    # -y also skips the prompt (scripting stays non-interactive)
    ranked = covering_versions(
        ["Show.S02E01.1080p.WEBRip.mkv", "Show.S02E05.1080p.WEBRip.mkv"],
        stub.title_versions("show-season-2", "english"),
    )
    best_ref = ranked[0][1].ref
    result = _runner().invoke(
        app, ["auto", str(tmp_path), "-y", "--prefer-complete", "--map-json", "-"],
        input="",
    )
    assert result.exit_code == 0, result.output
    import json as _json
    data = _json.loads(result.output)
    for row in data["results"]:
        if "S02E0" in row["video"]:
            assert row["ref"] == best_ref


# ---------------------------------------------------------------------------
# SxxEyy x-form member tags + pack-mode strictness (v0.4.7)
# (the .ASS.thetic COMPLETE season packs name their zip members
#  "Arcane [2021] - S02xE01 Heavy is the Crown.ass"; the x form parsed as
#  untagged, so EVERY video of the folder got all 9 members extracted and
#  renamed after itself: 9 videos x 9 members = 81 files)
# ---------------------------------------------------------------------------

_XFORM_PACK_MEMBERS = {
    "Arcane [2021] - S02xE01 Heavy is the Crown.ass": "E01 lines",
    "Arcane [2021] - S02xE02 Watch It All Burn.ass": "E02 lines",
    "Arcane [2021] - S02xE03 Finally Got the Name Right.ass": "E03 lines",
    "Arcane [2021] - S02xE04 Paint the Town Blue.ass": "E04 lines",
    "Arcane [2021] - S02xE05 Blisters and Bedrock.ass": "E05 lines",
    "Arcane [2021] - S02xE06 The Message Hidden Within the Pattern.ass": "E06 lines",
    "Arcane [2021] - S02xE07 Pretend Like It's the First Time.ass": "E07 lines",
    "Arcane [2021] - S02xE08 Killing is a Cycle.ass": "E08 lines",
    "Arcane [2021] - S02xE09 The Dirt Under Your Nails.ass": "E09 lines",
}


def test_season_info_parses_x_form_episode_tags():
    from pysubzone.scoring import season_info

    info = season_info("Arcane [2021] - S02xE01 Heavy is the Crown.ass")
    assert (info.season, info.episodes) == (2, (1, 1))
    info = season_info("Show.S02xE12.720p.WEBRip")
    assert (info.season, info.episodes) == (2, (12, 12))
    info = season_info("show s02xe07-e09 1080p")  # x-form act-pack span
    assert (info.season, info.episodes) == (2, (7, 9))
    # nothing new parses where nothing parsed before
    assert season_info("Some.Movie.2023.1080p.BluRay.x265").episodes is None
    assert season_info("Show.S02.1080p.WEBRip.x265-HODL").episodes is None
    assert season_info("Show.S02.1080p.WEB-DL.DDP5.1.x265.HEVC").episodes is None


def test_extract_xform_pack_one_file_per_video(tmp_path):
    """The user's exact scenario: a 9-member S02xE0N pack planned onto nine
    S02E0N videos must write exactly one correctly named file per video."""
    client = _offline_client()
    version = SubtitleVersion(
        slug="arcane-first-season", lang="english", sub_id="3561361"
    )
    payload = _zip_payload_with_contents(_XFORM_PACK_MEMBERS)
    for episode in range(1, 10):
        video = (
            f"Arcane.S02E{episode:02d}.Heavy.Is.the.Crown."
            "1080p.WEBRip.10bit.DDP5.1.x265-HODL.mkv"
        )
        written = client._extract_subtitles(
            payload, tmp_path, version, "en", target_video=video, pack=True,
        )
        assert len(written) == 1, (episode, written)
        assert written[0].name == (
            f"Arcane.S02E{episode:02d}.Heavy.Is.the.Crown."
            "1080p.WEBRip.10bit.DDP5.1.x265-HODL.en.ass"
        )
        assert written[0].read_text(encoding="utf-8") == f"E{episode:02d} lines"
    assert len(list(tmp_path.iterdir())) == 9  # nine files total, not 81


def test_extract_xform_filter_works_without_pack_flag(tmp_path):
    """The regex fix alone also repairs plain downloads of an x-form pack."""
    client = _offline_client()
    version = SubtitleVersion(slug="arcane-first-season", lang="english", sub_id="1")
    payload = _zip_payload_with_contents(_XFORM_PACK_MEMBERS)
    written = client._extract_subtitles(
        payload, tmp_path, version, "en",
        target_video="Arcane.S02E05.Blisters.and.Bedrock.1080p.mkv",
    )
    assert [p.name for p in written] == [
        "Arcane.S02E05.Blisters.and.Bedrock.1080p.en.ass"
    ]
    assert written[0].read_text(encoding="utf-8") == "E05 lines"


def test_pack_mode_errors_when_no_member_matches(tmp_path):
    """pack=True must never dump every member renamed after the video:
    an archive without a matching member is a clean per-row error, while a
    regular download keeps the old numbered fall-back."""
    from pysubzone.exceptions import DownloadError

    client = _offline_client()
    version = SubtitleVersion(slug="show", lang="farsi_persian", sub_id="42")
    payload = _zip_payload(["cd1.srt", "cd2.srt"])
    with pytest.raises(DownloadError, match="none is tagged with episode 1"):
        client._extract_subtitles(
            payload, tmp_path, version, "fa",
            target_video="Arcane.S02E01.mkv", pack=True,
        )
    written = client._extract_subtitles(
        payload, tmp_path, version, "fa",
        target_video="Arcane.S02E01.mkv", pack=False,
    )
    assert sorted(p.name for p in written) == [
        "Arcane.S02E01.fa.1.srt", "Arcane.S02E01.fa.2.srt",
    ]


def test_pack_mode_errors_when_episode_outside_member_tags(tmp_path):
    from pysubzone.exceptions import DownloadError

    client = _offline_client()
    version = SubtitleVersion(slug="show", lang="english", sub_id="42")
    payload = _zip_payload_with_contents({
        "Show.S02xE01.en.srt": "E01 lines",
        "Show.S02xE02.en.srt": "E02 lines",
        "Show.S02xE03.en.srt": "E03 lines",
    })
    with pytest.raises(DownloadError, match="none is tagged with episode 5"):
        client._extract_subtitles(
            payload, tmp_path, version, "en",
            target_video="Show.S02E05.1080p.WEBRip.mkv", pack=True,
        )


def test_pack_mode_keeps_variant_members_numbered(tmp_path):
    """All members matching the video's episode are legit variants (1080p /
    720p tracks): pack mode keeps them, numbered like a regular download."""
    client = _offline_client()
    version = SubtitleVersion(slug="show", lang="english", sub_id="42")
    payload = _zip_payload_with_contents({
        "Show.S02E01.1080p.en.srt": "1080p lines",
        "Show.S02E01.720p.en.srt": "720p lines",
    })
    written = client._extract_subtitles(
        payload, tmp_path, version, "en",
        target_video="Show.S02E01.1080p.WEBRip.mkv", pack=True,
    )
    assert sorted(p.name for p in written) == [
        "Show.S02E01.1080p.WEBRip.en.1.srt",
        "Show.S02E01.1080p.WEBRip.en.2.srt",
    ]


def test_xform_release_lines_score_and_cover():
    """x-form release lines guard seasons/episodes like the E form."""
    from pysubzone.scoring import covers_episodes, score_release

    season_pack = SubtitleVersion(
        slug="show", lang="english", sub_id="200",
        releases=["Arcane.SEASON.02.S02.COMPLETE.1080p.10bit.WEBRip.6CH.x265.HEVC-PSA"],
    )
    assert covers_episodes(
        "Arcane.S02E01.Heavy.Is.the.Crown.1080p.WEBRip.10bit.DDP5.1.x265-HODL",
        season_pack,
    )
    # x-form act packs are now properly scoped
    assert score_release(
        "Show.S02E01.1080p.WEBRip", "Show.S02xE01-E03.1080p.WEBRip"
    ) == (score_release("Show.S02E01.1080p.WEBRip", "Show.S02E01-E03.1080p.WEBRip")[0], True)
    assert score_release(
        "Show.S02E05.1080p.WEBRip", "Show.S02xE01-E03.1080p.WEBRip"
    )[0] == -100
    # movies are still never covered
    assert not covers_episodes("Some.Movie.2023.1080p.BluRay.x265", season_pack)


def test_cli_auto_passes_pack_flag_to_download(tmp_path, config_env, monkeypatch):
    """Pack-planned downloads carry pack=True; per-video downloads don't."""
    import json as _json

    from pysubzone.cli import app

    (tmp_path / "Show.S02E01.1080p.WEBRip.mkv").touch()
    (tmp_path / "Show.S02E05.1080p.WEBRip.mkv").touch()
    (tmp_path / "Some.Movie.2023.1080p.BluRay.mkv").touch()
    stub, downloads = _auto_pack_stub_client()
    monkeypatch.setattr("pysubzone.cli._client", lambda settings: stub)

    result = _runner().invoke(
        app,
        ["auto", str(tmp_path), "-y", "--prefer-complete", "--map-json", "-"],
    )
    assert result.exit_code == 0, result.output
    _json.loads(result.output)  # output stays valid JSON
    assert len(downloads) == 3
    packs = {
        Path(kwargs["target_video"]).name: kwargs.get("pack")
        for _target, kwargs in downloads
    }
    assert packs["Show.S02E01.1080p.WEBRip.mkv"] is True
    assert packs["Show.S02E05.1080p.WEBRip.mkv"] is True
    assert packs["Some.Movie.2023.1080p.BluRay.mkv"] is False


# ---------------------------------------------------------------------------
# 0.5.0: bulk folder mode (season pack first, one zip per season, per-video
# fallback when no pack covers the group)
# ---------------------------------------------------------------------------

def _bulk_client(monkeypatch, versions, pack_payload=None, pack_error=None):
    """A real client whose search/title/fetch methods are stubbed offline."""
    from pysubzone.client import SubZoneClient
    from pysubzone.models import TitleResult

    client = SubZoneClient()
    title = TitleResult(name="Arcane", slug="arcane", url="")
    calls = {"search": 0, "title": 0, "fetch": 0}

    def search_exact(query, lang=None):
        calls["search"] += 1
        return title

    def title_versions(slug, lang=None):
        calls["title"] += 1
        return versions

    def _fetch_zip_payload(version, on_progress=None):
        calls["fetch"] += 1
        if pack_error is not None:
            raise pack_error
        return pack_payload

    client.search_exact = search_exact
    client.title_versions = title_versions
    client._fetch_zip_payload = _fetch_zip_payload
    client._bulk_calls = calls
    return client


def _fake_per_video_download(writer):
    """Replace client.download with a writer that produces the srt file."""

    def download(self, version, dest_dir=".", save_mode="extract", target_video=None, **kwargs):
        from pysubzone.models import DownloadResult

        video = Path(target_video)
        dest = Path(dest_dir) if dest_dir else video.parent
        dest.mkdir(parents=True, exist_ok=True)
        out = dest / f"{video.with_suffix('').name}.en.srt"
        out.write_text("per-video lines", encoding="utf-8")
        return DownloadResult(
            zip_path=None, srt_paths=[str(out)], version=version,
            bytes_downloaded=10, target_video=str(video),
        )

    return download


def test_bulk_download_serves_season_with_one_zip(tmp_path, monkeypatch):
    """THE 0.5.0 regression: a 9-episode folder + one COMPLETE pack zip must
    produce exactly 9 subtitle files (one per video, each video's own
    episode) from exactly ONE zip download."""
    from pysubzone.models import SubtitleVersion

    videos = []
    for ep in range(1, 10):
        video = tmp_path / f"Arcane.S02E{ep:02d}.1080p.WEB-DL.mkv"
        video.touch()
        videos.append(video)
    season_pack = SubtitleVersion(
        slug="arcane", lang="english", sub_id="100",
        releases=["Arcane.Season.2.COMPLETE.1080p.WEB-DL"], author="ASS.thetic",
    )
    payload = _zip_payload_with_contents({
        f"Arcane.S02E{ep:02d}.en.srt": f"LINES-E{ep:02d}" for ep in range(1, 10)
    })
    client = _bulk_client(monkeypatch, versions=[season_pack], pack_payload=payload)

    rows = client.bulk_download(videos, lang="english")

    assert client._bulk_calls["fetch"] == 1, "the pack zip must be downloaded exactly once"
    assert len(rows) == 9
    assert all(r["status"] == "saved" for r in rows), rows
    assert all(r["mode"] == "complete-pack" for r in rows)
    # rows carry the pack ref and the per-video score (map JSON usefulness)
    assert all(r.get("ref") == season_pack.ref for r in rows)
    assert all(isinstance(r.get("score"), int) and r["score"] >= 0 for r in rows)
    for ep in range(1, 10):
        out = tmp_path / f"Arcane.S02E{ep:02d}.1080p.WEB-DL.en.srt"
        assert out.is_file(), f"missing subtitle for E{ep:02d}"
        assert out.read_text(encoding="utf-8") == f"LINES-E{ep:02d}"
    assert len(list(tmp_path.glob("*.srt"))) == 9  # and nothing else


def test_bulk_pack_missing_episode_falls_back_for_that_video(tmp_path, monkeypatch):
    """A pack archive that lacks one episode keeps the other pack rows and
    recovers the missing one via its per-video best."""
    from pysubzone.models import SubtitleVersion

    videos = []
    for ep in range(1, 4):
        video = tmp_path / f"Arcane.S02E{ep:02d}.1080p.WEB-DL.mkv"
        video.touch()
        videos.append(video)
    season_pack = SubtitleVersion(
        slug="arcane", lang="english", sub_id="100",
        releases=["Arcane.Season.2.COMPLETE.1080p.WEB-DL"], author="ASS.thetic",
    )
    payload = _zip_payload_with_contents({
        "Arcane.S02E01.en.srt": "LINES-E01",
        "Arcane.S02E02.en.srt": "LINES-E02",
        # E03 is missing from the archive on purpose
    })
    client = _bulk_client(monkeypatch, versions=[season_pack], pack_payload=payload)
    monkeypatch.setattr(
        type(client), "download", _fake_per_video_download(None), raising=False
    )

    rows = client.bulk_download(videos, lang="english")

    assert len(rows) == 3
    modes = {Path(r["video"]).name: r["mode"] for r in rows}
    statuses = {Path(r["video"]).name: r["status"] for r in rows}
    assert modes["Arcane.S02E01.1080p.WEB-DL.mkv"] == "complete-pack"
    assert modes["Arcane.S02E02.1080p.WEB-DL.mkv"] == "complete-pack"
    assert modes["Arcane.S02E03.1080p.WEB-DL.mkv"] == "pack-fallback"
    assert all(status == "saved" for status in statuses.values()), statuses
    assert len(list(tmp_path.glob("*.srt"))) == 3


def test_bulk_download_falls_back_per_video_without_pack(tmp_path, monkeypatch):
    """No covering pack -> pick_pack sees an empty ranking, the group is
    served per-video, and the run still completes."""
    from pysubzone.models import SubtitleVersion

    videos = []
    for ep in (1, 2):
        video = tmp_path / f"Arcane.S02E0{ep}.1080p.WEB-DL.mkv"
        video.touch()
        videos.append(video)
    versions = [
        SubtitleVersion(slug="arcane", lang="english", sub_id="1",
                        releases=["Arcane.S02E01.1080p.WEB-DL"], author="a"),
        SubtitleVersion(slug="arcane", lang="english", sub_id="2",
                        releases=["Arcane.S02E02.1080p.WEB-DL"], author="b"),
    ]
    client = _bulk_client(monkeypatch, versions=versions, pack_payload=b"PK-unused")
    monkeypatch.setattr(
        type(client), "download", _fake_per_video_download(None), raising=False
    )
    seen_rankings = []

    def pick_pack(title, group, ranked):
        seen_rankings.append(list(ranked))
        return None

    rows = client.bulk_download(videos, lang="english", pick_pack=pick_pack)

    assert seen_rankings == [[]], "the callback must be told there is no pack"
    assert len(rows) == 2
    assert all(r["mode"] == "per-video" and r["status"] == "saved" for r in rows)
    assert (tmp_path / "Arcane.S02E01.1080p.WEB-DL.en.srt").is_file()
    assert (tmp_path / "Arcane.S02E02.1080p.WEB-DL.en.srt").is_file()


def test_bulk_movies_never_get_packs(tmp_path, monkeypatch):
    """Movies pin no season/episode: pack machinery never touches them."""
    from pysubzone.models import SubtitleVersion

    movies = [
        tmp_path / "Some.Movie.2023.1080p.BluRay.mkv",
        tmp_path / "Another.Movie.2024.720p.WEBRip.mkv",
    ]
    for movie in movies:
        movie.touch()
    season_pack = SubtitleVersion(
        slug="arcane", lang="english", sub_id="100",
        releases=["Arcane.Season.2.COMPLETE.1080p.WEB-DL"], author="x",
    )
    client = _bulk_client(
        monkeypatch, versions=[season_pack],
        pack_payload=_zip_payload(["Arcane.S02E01.en.srt"]),
    )
    monkeypatch.setattr(
        type(client), "download", _fake_per_video_download(None), raising=False
    )

    rows = client.bulk_download(movies, lang="english")

    assert client._bulk_calls["fetch"] == 0  # pack zip never fetched
    assert all(r["mode"] == "per-video" and r["status"] == "saved" for r in rows)


def test_plan_bulk_searches_once_per_title(tmp_path, monkeypatch):
    """A nine-episode folder of one show costs ONE search + ONE title fetch."""
    from pysubzone.models import SubtitleVersion

    videos = [tmp_path / f"Arcane.S02E{ep:02d}.1080p.mkv" for ep in range(1, 10)]
    videos.append(tmp_path / "Other.Show.S01E01.720p.mkv")
    season_pack = SubtitleVersion(slug="arcane", lang="english", sub_id="100",
                                  releases=["Arcane.Season.2.COMPLETE"])
    client = _bulk_client(monkeypatch, versions=[season_pack], pack_payload=b"PK")

    plan = client.plan_bulk(videos, lang="english")

    assert client._bulk_calls["search"] == 2  # "Arcane" + "Other Show"
    assert client._bulk_calls["title"] == 2
    assert len(plan.groups) == 2
    big = next(g for g in plan.groups if len(g.videos) == 9)
    assert big.pinned is True
    assert big.ranked_packs  # the COMPLETE pack covers all nine


def test_bulk_download_dry_run_and_min_score(tmp_path, monkeypatch):
    from pysubzone.models import SubtitleVersion

    videos = []
    for ep in (1, 2):
        video = tmp_path / f"Arcane.S02E0{ep}.1080p.WEB-DL.mkv"
        video.touch()
        videos.append(video)
    season_pack = SubtitleVersion(slug="arcane", lang="english", sub_id="100",
                                  releases=["Arcane.Season.2.COMPLETE.1080p.WEB-DL"])
    payload = _zip_payload(["Arcane.S02E01.en.srt", "Arcane.S02E02.en.srt"])
    client = _bulk_client(monkeypatch, versions=[season_pack], pack_payload=payload)

    rows = client.bulk_download(videos, lang="english", dry_run=True)
    assert client._bulk_calls["fetch"] == 0
    assert all(r["status"] == "dry-run" and r["mode"] == "complete-pack" for r in rows)

    rows = client.bulk_download(videos, lang="english", min_score=10000)
    assert client._bulk_calls["fetch"] == 0
    assert all(r["status"] == "skipped" for r in rows)


def test_download_pack_once_zip_mode_writes_single_zip(tmp_path, monkeypatch):
    from pysubzone.models import SubtitleVersion

    videos = [tmp_path / f"Arcane.S02E0{ep}.mkv" for ep in (1, 2, 3)]
    for video in videos:
        video.touch()
    version = SubtitleVersion(slug="arcane", lang="english", sub_id="100",
                              releases=["Arcane.Season.2.COMPLETE"])
    payload = _zip_payload(["Arcane.S02E01.en.srt"])
    client = _bulk_client(monkeypatch, versions=[version], pack_payload=payload)

    results = client.download_pack_once(version, videos, save_mode="zip")

    assert len(results) == 3
    zips = list(tmp_path.glob("*.zip"))
    assert len(zips) == 1  # the archive is written exactly once
    assert all(isinstance(r, tuple) or True for r in results)
    first_video, first_result = results[0]
    assert first_result.zip_path == str(zips[0])
    assert first_result.bytes_downloaded > 0
    assert all(r.bytes_downloaded == 0 for _v, r in results[1:])


# --- bulk CLI ---------------------------------------------------------------

class _BulkCliStub:
    """Offline stub backing the `subz bulk` command tests."""

    def __init__(self, ranked=True):
        from pysubzone.models import SubtitleVersion, TitleResult

        self.title = TitleResult(name="Show", slug="show", url="")
        self.pack = SubtitleVersion(slug="show", lang="english", sub_id="9",
                                    releases=["Show.S02.COMPLETE.1080p"], author="x")
        self.per_video = SubtitleVersion(slug="show", lang="english", sub_id="3",
                                         releases=["Show.S02E01.1080p"], author="y")
        self.ranked = ranked
        self.pack_calls = []
        self.video_calls = []
        self.kwargs_log = []

    def plan_bulk(self, videos, lang=None):
        from pysubzone.models import BulkGroupPlan, BulkPlan

        ranked_packs = [(30, self.pack)] if self.ranked else []
        group = BulkGroupPlan(
            title=self.title, videos=list(videos), ranked_packs=ranked_packs,
            best_by_video={v: (self.per_video, 55) for v in videos}, pinned=True,
        )
        return BulkPlan(groups=[group])

    def download_pack_once(self, version, videos, dest_dir=None, save_mode="extract", **kwargs):
        from pysubzone.models import DownloadResult

        self.pack_calls.append(list(videos))
        out = []
        for index, video in enumerate(videos):
            dest = Path(dest_dir) if dest_dir else video.parent
            dest.mkdir(parents=True, exist_ok=True)
            f = dest / f"{video.with_suffix('').name}.en.srt"
            f.write_text("pack lines", encoding="utf-8")
            out.append((video, DownloadResult(
                zip_path=None, srt_paths=[str(f)], version=version,
                bytes_downloaded=1000 if index == 0 else 0, target_video=str(video),
            )))
        return out

    def download(self, version, dest_dir=".", save_mode="extract", target_video=None, **kwargs):
        from pysubzone.models import DownloadResult

        self.video_calls.append(target_video)
        video = Path(target_video)
        dest = Path(dest_dir) if dest_dir else video.parent
        dest.mkdir(parents=True, exist_ok=True)
        f = dest / f"{video.with_suffix('').name}.en.srt"
        f.write_text("per-video lines", encoding="utf-8")
        return DownloadResult(zip_path=None, srt_paths=[str(f)], version=version,
                              bytes_downloaded=10, target_video=str(video))

    def bulk_download(self, videos, **kwargs):
        from pysubzone.client import SubZoneClient

        self.kwargs_log.append(kwargs)
        return SubZoneClient.bulk_download(self, videos, **kwargs)


def test_cli_bulk_pack_serves_whole_folder(tmp_path, config_env, monkeypatch):
    """`subz bulk` with a covering pack: one download_pack_once call for the
    whole folder, every row served from the pack, clean JSON out."""
    import json as _json

    from pysubzone.cli import app

    for ep in (1, 2, 3):
        (tmp_path / f"Show.S02E0{ep}.1080p.mkv").touch()
    stub = _BulkCliStub(ranked=True)
    monkeypatch.setattr("pysubzone.cli._client", lambda settings: stub)

    result = _runner().invoke(app, ["bulk", str(tmp_path), "-y", "--json"])
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"] == {"total": 3, "saved": 3}
    assert all(r["mode"] == "complete-pack" for r in data["results"])
    assert len(stub.pack_calls) == 1  # ONE pack download for the whole folder
    assert len(stub.pack_calls[0]) == 3


def test_cli_bulk_fallback_without_pack(tmp_path, config_env, monkeypatch):
    """Without covering packs the CLI falls back per-video and reports it."""
    import json as _json

    from pysubzone.cli import app

    (tmp_path / "Show.S02E01.1080p.mkv").touch()
    (tmp_path / "Show.S02E02.1080p.mkv").touch()
    stub = _BulkCliStub(ranked=False)
    monkeypatch.setattr("pysubzone.cli._client", lambda settings: stub)

    result = _runner().invoke(app, ["bulk", str(tmp_path), "-y", "--map-json", "-"])
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)  # stdout stays pure JSON
    assert data["prefer_complete"] is True
    assert data["summary"] == {"total": 2, "saved": 2}
    assert all(r["mode"] == "per-video" for r in data["results"])
    assert len(stub.video_calls) == 2
    assert len(stub.pack_calls) == 0


def test_cli_bulk_no_packs_flag_forces_per_video(tmp_path, config_env, monkeypatch):
    from pysubzone.cli import app

    (tmp_path / "Show.S02E01.1080p.mkv").touch()
    stub = _BulkCliStub(ranked=True)
    monkeypatch.setattr("pysubzone.cli._client", lambda settings: stub)

    result = _runner().invoke(app, ["bulk", str(tmp_path), "-y", "--no-packs", "--json"])
    assert result.exit_code == 0, result.output
    assert stub.kwargs_log[0]["prefer_complete"] is False

    stub.kwargs_log.clear()
    result = _runner().invoke(app, ["bulk", str(tmp_path), "-y", "--json"])
    assert result.exit_code == 0, result.output
    assert stub.kwargs_log[0]["prefer_complete"] is True


def test_cli_bulk_dry_run_downloads_nothing(tmp_path, config_env, monkeypatch):
    import json as _json

    from pysubzone.cli import app

    (tmp_path / "Show.S02E01.1080p.mkv").touch()
    stub = _BulkCliStub(ranked=True)
    monkeypatch.setattr("pysubzone.cli._client", lambda settings: stub)

    result = _runner().invoke(app, ["bulk", str(tmp_path), "--dry-run", "--json"])
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"] == {"total": 1, "dry-run": 1}
    assert data["dry_run"] is True
    assert stub.pack_calls == [] and stub.video_calls == []


def test_cli_bulk_lists_bulk_in_help(config_env):
    from pysubzone.cli import app

    help_text = _runner().invoke(app, ["--help"]).output
    assert "bulk" in help_text
    bulk_help = _runner().invoke(app, ["bulk", "--help"]).output
    for option in ("--no-packs", "--map-json", "--dry-run", "--min-score"):
        assert option in bulk_help, f"missing {option}"


def test_cli_politeness_defaults_are_on():
    """v0.5.0: every command paces requests (1s) and caches pages (300s)."""
    from pysubzone.cli import DEFAULT_CACHE_TTL, DEFAULT_MIN_INTERVAL

    assert DEFAULT_MIN_INTERVAL == 1.0
    assert DEFAULT_CACHE_TTL == 300.0


# --- bulk TUI ---------------------------------------------------------------

class _BulkTuiClient:
    """plan_bulk / download_pack_once / download succeed, offline only."""

    def __init__(self, with_pack: bool = True, with_span: bool = False) -> None:
        from pysubzone.models import SubtitleVersion, TitleResult

        self.title = TitleResult(name="Show", slug="show", url="")
        self.pack = SubtitleVersion(slug="show", lang="english", sub_id="9",
                                    releases=["Show.S02.COMPLETE.1080p"], author="x")
        self.span = SubtitleVersion(slug="show", lang="english", sub_id="8",
                                    releases=["Show.S02E01-E05.1080p.WEBRip"], author="y")
        self.per_video = SubtitleVersion(slug="show", lang="english", sub_id="3",
                                         releases=["Show.S02E01.1080p"], author="y")
        self.with_pack = with_pack
        self.with_span = with_span
        self.pack_calls: List[List[Path]] = []
        self.pack_versions: List[SubtitleVersion] = []
        self.video_calls: List[Path] = []
        self.video_versions: List[SubtitleVersion] = []

    @property
    def ranked(self):
        packs = [(30, self.pack)]
        if self.with_span:
            packs.append((20, self.span))
        return packs

    def plan_bulk(self, videos, lang=None):
        from pysubzone.models import BulkGroupPlan, BulkPlan

        ranked = self.ranked if self.with_pack else []
        group = BulkGroupPlan(
            title=self.title, videos=list(videos), ranked_packs=ranked,
            best_by_video={v: (self.per_video, 55) for v in videos}, pinned=bool(ranked),
        )
        return BulkPlan(groups=[group])

    def download_pack_once(self, version, videos, dest_dir=None, save_mode="extract", **k):
        from pysubzone.models import DownloadResult

        self.pack_calls.append(list(videos))
        self.pack_versions.append(version)
        out = []
        for index, video in enumerate(videos):
            dest = Path(dest_dir) if dest_dir else video.parent
            dest.mkdir(parents=True, exist_ok=True)
            f = dest / f"{video.with_suffix('').name}.en.srt"
            f.write_text("pack lines", encoding="utf-8")
            out.append((video, DownloadResult(
                zip_path=None, srt_paths=[str(f)], version=version,
                bytes_downloaded=100 if index == 0 else 0, target_video=str(video),
            )))
        return out

    def download(self, version, dest_dir=".", save_mode="extract", target_video=None, **k):
        from pysubzone.models import DownloadResult

        self.video_calls.append(Path(target_video))
        self.video_versions.append(version)
        video = Path(target_video)
        dest = Path(dest_dir) if dest_dir else video.parent
        dest.mkdir(parents=True, exist_ok=True)
        f = dest / f"{video.with_suffix('').name}.en.srt"
        f.write_text("per-video lines", encoding="utf-8")
        return DownloadResult(zip_path=None, srt_paths=[str(f)], version=version,
                              bytes_downloaded=10, target_video=str(video))


def test_tui_ctrl_p_opens_bulk_screen(config_env):
    from pysubzone.tui import BulkScreen, SubZoneTUI

    async def scenario():
        app = SubZoneTUI(_FakeClient())
        async with app.run_test(size=(110, 44)) as pilot:
            await pilot.press("ctrl+p")
            await pilot.pause()
            assert isinstance(app.screen, BulkScreen)

    _run_async(scenario())


def test_tui_auto_screen_has_no_pack_checkbox():
    """v0.5.0: prefer-complete moved out of auto-match into the bulk screen."""
    import inspect

    from pysubzone.tui import AutoScreen

    source = inspect.getsource(AutoScreen)
    assert "Checkbox" not in source
    assert "prefer" not in source.lower()


def test_tui_bulk_screen_dry_run_then_pack_extract(tmp_path, config_env):
    """Bulk flow: dry run plans, 'Bulk & extract' downloads the pack once."""
    from pysubzone.tui import BulkScreen, PackPickerScreen, SubZoneTUI
    from textual.widgets import Button, Input

    for ep in (1, 2):
        (tmp_path / f"Show.S02E0{ep}.1080p.mkv").touch()
    client = _BulkTuiClient(with_pack=True)

    async def scenario():
        app = SubZoneTUI(client)
        async with app.run_test(size=(110, 46)) as pilot:
            await pilot.press("ctrl+p")
            await pilot.pause()
            assert isinstance(app.screen, BulkScreen)
            screen = app.screen
            screen.query_one("#bulk-folder", Input).value = str(tmp_path)
            await pilot.pause()

            # dry run: picker appears, Enter accepts the best pack
            screen.query_one("#bulk-dry", Button).press()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert isinstance(app.screen, PackPickerScreen)
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert all(r["status"] == "dry run" for r in screen.matches), screen.matches
            assert all(r["mode"] == "complete-pack" for r in screen.matches)
            assert client.pack_calls == []  # dry run downloads nothing

            # real run: picker again, Enter, pack extracted for both videos
            screen.query_one("#bulk-extract", Button).press()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert isinstance(app.screen, PackPickerScreen)
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert all(r["status"] == "saved" for r in screen.matches), screen.matches
            assert len(client.pack_calls) == 1  # ONE pack download
            assert len(client.pack_calls[0]) == 2
            assert client.video_calls == []  # no per-video downloads needed
            for ep in (1, 2):
                out = tmp_path / f"Show.S02E0{ep}.1080p.en.srt"
                assert out.is_file()

    _run_async(scenario())


def test_tui_bulk_screen_falls_back_without_pack(tmp_path, config_env):
    """No covering packs: no picker at all, per-video fallback serves both."""
    from pysubzone.tui import BulkScreen, SubZoneTUI
    from textual.widgets import Button, Input

    for ep in (1, 2):
        (tmp_path / "Show.S02E01.1080p.mkv" if ep == 1 else tmp_path / "Show.S02E02.1080p.mkv").touch()
    client = _BulkTuiClient(with_pack=False)

    async def scenario():
        app = SubZoneTUI(client)
        async with app.run_test(size=(110, 46)) as pilot:
            await pilot.press("ctrl+p")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, BulkScreen)
            screen.query_one("#bulk-folder", Input).value = str(tmp_path)
            screen.query_one("#bulk-extract", Button).press()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert all(r["mode"] == "per-video" for r in screen.matches)
            assert all(r["status"] == "saved" for r in screen.matches)
            assert len(client.video_calls) == 2
            assert client.pack_calls == []

    _run_async(scenario())
