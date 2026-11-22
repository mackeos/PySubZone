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
