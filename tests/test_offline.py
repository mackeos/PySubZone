"""Offline tests: parsers, matcher and scoring run against saved HTML fixtures."""

from pathlib import Path

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
