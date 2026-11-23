"""Best-version selection for the auto-match mode.

Given a video release name and a list of subtitle versions, score how well
each version fits the video: seasons must agree, the video's episode must
fall inside the subtitle's explicit episode span (``S02E01-E03`` covers
episodes 1-3), and resolution / source / release-group tokens earn bonus
points.  The design mirrors the "scoreBestSubVersions" idea of PyAddic7
but is tuned to the Subf2m layout, where a version exposes one or more
*release info* lines instead of a single distribution string.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .models import SubtitleVersion

# --- release name tokens -------------------------------------------------

_RESOLUTIONS = ("2160p", "1080p", "1080i", "720p", "576p", "480p")
_SOURCES = (
    "web-dl", "webdl", "webrip", "web", "bluray", "blu-ray", "bdrip", "brrip",
    "dvdrip", "hdtv", "pdtv", "hdrip", "dvdscr", "remux", "amzn", "nf", "atv",
    "hulu", "dsnp", "max", "hbo", "itunes", "cthp", "skst",
)
_GROUP_RE = re.compile(r"-([A-Za-z0-9]+)(?:\.[a-z]{2})?$")
_SEASON_EPISODE_RE = re.compile(
    r"\bs(\d{1,2})[\s._-]*e(\d{1,3})\b", re.IGNORECASE
)
_EPISODE_ONLY_RE = re.compile(r"\b(?:e|ep)[\s._-]*(\d{1,3})\b", re.IGNORECASE)
_SEASON_ONLY_RE = re.compile(r"\bs(\d{1,2})\b(?![\s._-]*e)", re.IGNORECASE)

# --- season / episode-range awareness (0.4.3) ------------------------------

_SEASON_TOKEN_RE = re.compile(r"\bs(\d{1,2})\b", re.IGNORECASE)
_SEASON_WORD_NUM_RE = re.compile(r"\bseason[\s._-]*(\d{1,2})\b", re.IGNORECASE)
_SEASON_ORDINALS = {
    "first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3,
    "fourth": 4, "4th": 4, "fifth": 5, "5th": 5, "sixth": 6, "6th": 6,
    "seventh": 7, "7th": 7, "eighth": 8, "8th": 8, "ninth": 9, "9th": 9,
    "tenth": 10, "10th": 10,
}
_SEASON_WORD_ORD_RE = re.compile(
    r"\b(first|1st|second|2nd|third|3rd|fourth|4th|fifth|5th|sixth|6th|"
    r"seventh|7th|eighth|8th|ninth|9th|tenth|10th)[\s._-]+season\b"
    r"|\bseason[\s._-]+"
    r"(first|1st|second|2nd|third|3rd|fourth|4th|fifth|5th|sixth|6th|"
    r"seventh|7th|eighth|8th|ninth|9th|tenth|10th)\b",
    re.IGNORECASE,
)
_SEASON_FA_RE = re.compile(
    r"فصل[\s._\u200c-]*(اول|دوم|سوم|چهارم|پنجم|ششم|هفتم|هشتم|نهم|دهم|\d{1,2})"
)
_SEASON_FA_ORDINALS = {
    "اول": 1, "دوم": 2, "سوم": 3, "چهارم": 4, "پنجم": 5,
    "ششم": 6, "هفتم": 7, "هشتم": 8, "نهم": 9, "دهم": 10,
}
# S02E01-E03 / S01E07~E09 / S02E04-E05-E06 / S02EP01 -> (season, first..last)
# Range tails must carry an explicit E/EP prefix, so release-group tokens
# like "-4chan" or "-2CH" are never mistaken for episode numbers.
_EPISODE_RANGE_RE = re.compile(
    r"\bs(\d{1,2})[\s._-]*ep?(\d{1,3})(?!\d)"
    r"((?:\s*[-–—~]\s*ep?\d{1,3}(?!\d))*)",
    re.IGNORECASE,
)
_EPISODE_WORD_RE = re.compile(r"\be(?:pisode)?[\s._-]*(\d{1,3})(?!\d)", re.IGNORECASE)
_ALT_SE_RE = re.compile(r"\b(\d{1,2})x(\d{1,3})\b", re.IGNORECASE)

SOURCE_ALIASES = {
    "webdl": "web-dl",
    "web": "web-dl",
    "blu-ray": "bluray",
}


# --- configurable weights ---------------------------------------------------

@dataclass(frozen=True)
class ScoreWeights:
    """Tunable points for every component of the release-match score.

    Defaults reproduce the built-in ranking exactly.  Pass a modified
    instance to :func:`pick_best` / :meth:`SubZoneClient.best_for_file` to
    emphasize what matters for your library (e.g. resolution over release
    group).  ``score_release`` rejects a pair outright when the seasons
    disagree or the video's episode falls outside the subtitle's explicit
    episode span - those hard rules are not weights and always apply.
    """

    resolution_match: int = 25       # same resolution token on both sides
    resolution_mismatch: int = -15   # both sides name a resolution, different
    resolution_unspecified: int = 2  # video has one, subtitle side silent
    source_match: int = 20           # same source (web-dl, bluray, ...)
    source_mismatch: int = -8
    source_unspecified: int = 2
    group_match: int = 15            # same release group (-NTb, -HODL, ...)
    token_overlap: int = 20          # max bonus, scaled by overlap ratio
    episode_pin_bonus: int = 10      # version pins the exact episode
    positive_rate_bonus: int = 8     # site users rated the upload positive
    season_pack_floor: int = 0       # lower bound for season-pack fallbacks


DEFAULT_WEIGHTS = ScoreWeights()


def _effective(weights: Optional["ScoreWeights"]) -> "ScoreWeights":
    return weights if weights is not None else DEFAULT_WEIGHTS


def normalize(name: str) -> str:
    """Lower-case and tokenize a release name ('.' '_' '-' -> space)."""
    cleaned = re.sub(r"[\._]+", " ", name.lower())
    cleaned = re.sub(r"[()\[\]]", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def tokens(name: str) -> List[str]:
    return normalize(name).split()


def _canonical_source(tok: str) -> str:
    return SOURCE_ALIASES.get(tok, tok)


def extract_season_episode(name: str) -> Optional[Tuple[int, int]]:
    """Return (season, episode) for names like S02E05, else None."""
    m = _SEASON_EPISODE_RE.search(name)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


def extract_episode(name: str) -> Optional[int]:
    """Episode number for names that only carry E## (e.g. subf2m releases)."""
    m = _EPISODE_ONLY_RE.search(name)
    if m:
        return int(m.group(1))
    return None


def extract_season(name: str) -> Optional[int]:
    m = _SEASON_ONLY_RE.search(name)
    if m:
        return int(m.group(1))
    return None


@dataclass(frozen=True)
class SeasonInfo:
    """Season / episode span parsed from one release name."""

    season: Optional[int]                        # None = unknown / ambiguous
    episodes: Optional[Tuple[int, int]] = None   # inclusive (first, last); None = no explicit episodes


def season_info(name: str) -> SeasonInfo:
    """Extract the season and explicit episode span of a release name.

    Numeric season tokens (``S02``, ``SEASON.01``, the ``Sxx`` of ``S02E05``)
    take precedence over word forms ("First Season", "Complete Season 1",
    ``فصل دوم``); several conflicting numeric tokens (``S01-S02``) make the
    season *unknown*, which disables the guard instead of guessing.
    ``episodes`` is an inclusive ``(first, last)`` span: single pins such as
    ``S02E05`` or ``2x05`` give ``(5, 5)``, multi-episode packs give real
    ranges, and lines without explicit episode tokens give ``None``.
    """
    episodes: Optional[Tuple[int, int]] = None
    season_from_range: Optional[int] = None
    m = _EPISODE_RANGE_RE.search(name)
    if m:
        eps = [int(m.group(2))]
        for extra in re.finditer(r"(\d{1,3})(?!\d)", m.group(3) or ""):
            eps.append(int(extra.group(1)))
        episodes = (min(eps), max(eps))
        season_from_range = int(m.group(1))
    if episodes is None:
        se = extract_season_episode(name)
        if se:
            episodes = (se[1], se[1])
    if episodes is None:
        m = _ALT_SE_RE.search(name)
        if m:
            episodes = (int(m.group(2)), int(m.group(2)))
            season_from_range = int(m.group(1))
    if episodes is None:
        e = extract_episode(name)
        if e is not None:
            episodes = (e, e)
    if episodes is None:
        m = _EPISODE_WORD_RE.search(name)
        if m:
            episodes = (int(m.group(1)), int(m.group(1)))

    numeric = {int(x) for x in _SEASON_TOKEN_RE.findall(name)}
    numeric |= {int(x) for x in _SEASON_WORD_NUM_RE.findall(name)}
    if season_from_range is not None:
        numeric.add(season_from_range)
    season: Optional[int] = None
    if len(numeric) == 1:
        season = next(iter(numeric))
    elif not numeric:
        words: set = set()
        m = _SEASON_WORD_ORD_RE.search(name)
        if m:
            for g in m.groups():
                if g:
                    words.add(_SEASON_ORDINALS[g.lower()])
                    break
        m = _SEASON_FA_RE.search(name)
        if m:
            tok = m.group(1)
            if tok in _SEASON_FA_ORDINALS:
                words.add(_SEASON_FA_ORDINALS[tok])
            else:
                words.add(int(tok))
        if len(words) == 1:
            season = next(iter(words))
    return SeasonInfo(season=season, episodes=episodes)


# --- scoring ---------------------------------------------------------------

def score_release(
    video_release: str,
    sub_release: str,
    weights: Optional[ScoreWeights] = None,
    season_guard: bool = True,
) -> Tuple[int, bool]:
    """Score one (video release, subtitle release) pair.

    Returns ``(score, episode_matched)``.  The pair is rejected (``-100``)
    when the seasons disagree (word forms count too, e.g. a Season 1 pack
    against a Season 2 video) or when both sides carry explicit episodes
    and the video's episode lies outside the subtitle's span - an act pack
    ``S02E01-E03`` matches episodes 1-3 and is rejected for episode 4.
    Point values come from *weights* (:class:`ScoreWeights`, defaults used
    when ``None``).

    ``season_guard=False`` skips the season check; it is used for synthetic
    ``ref`` fallback lines, whose title slug season is not trustworthy
    (a site page named "...-first-season" can host uploads of any season).
    """
    w = _effective(weights)
    v_info = season_info(video_release)
    s_info = season_info(sub_release)
    if (
        season_guard
        and v_info.season is not None
        and s_info.season is not None
        and v_info.season != s_info.season
    ):
        return -100, False
    episode_ok = True
    if v_info.episodes and s_info.episodes:
        if v_info.episodes[1] < s_info.episodes[0] or v_info.episodes[0] > s_info.episodes[1]:
            return -100, False

    v_tokens = set(tokens(video_release))
    s_tokens = set(tokens(sub_release))
    if not v_tokens or not s_tokens:
        return 0, episode_ok

    score = 0

    # resolution
    v_res = next((t for t in _RESOLUTIONS if t in v_tokens), None)
    s_res = next((t for t in _RESOLUTIONS if t in s_tokens), None)
    if v_res and s_res:
        score += w.resolution_match if v_res == s_res else w.resolution_mismatch
    elif v_res:
        score += w.resolution_unspecified

    # source
    v_src = next((t for t in v_tokens if t in _SOURCES), None)
    s_src = next((t for t in s_tokens if t in _SOURCES), None)
    if v_src and s_src:
        score += w.source_match if _canonical_source(v_src) == _canonical_source(s_src) else w.source_mismatch
    elif v_src:
        score += w.source_unspecified

    # release group
    v_group = _group_of(video_release)
    s_group = _group_of(sub_release)
    if v_group and s_group:
        score += w.group_match if v_group.lower() == s_group.lower() else 0

    # plain token overlap (episode titles, DDP5.1, H.264 variants ...)
    v_extra = v_tokens - {t for t in _RESOLUTIONS} - {t for t in _SOURCES}
    s_extra = s_tokens - {t for t in _RESOLUTIONS} - {t for t in _SOURCES}
    if v_extra and s_extra:
        overlap = len(v_extra & s_extra) / min(len(v_extra), len(s_extra))
        score += int(w.token_overlap * overlap)

    return score, episode_ok


def _group_of(release: str) -> Optional[str]:
    m = _GROUP_RE.search(release.replace(" ", "."))
    return m.group(1) if m else None


def score_version(
    video_release: str,
    version: SubtitleVersion,
    weights: Optional[ScoreWeights] = None,
) -> Optional[int]:
    """Score a subtitle version against a video release name.

    ``None`` means "hard reject": every release line of the version was
    rejected (season or episode conflict).  Otherwise the best-scoring
    release line wins; versions with season-pack style releases (no
    explicit episode) are kept as low-ranked fallbacks - they still cover
    every episode of their season, provided that season matches.
    """
    w = _effective(weights)
    trusted = bool(version.releases)  # ref fallback carries the page slug,
    lines = version.releases or [version.ref]  # whose season is not reliable
    best: Optional[int] = None
    any_episode_line = False
    for line in lines:
        s, ep_ok = score_release(video_release, line, w, season_guard=trusted)
        if season_info(line).episodes is not None:
            any_episode_line = True
        if not ep_ok:
            continue
        if best is None or s > best:
            best = s
    if best is None:
        return None  # every line rejected (season / episode conflict)
    if not any_episode_line:
        # season pack / movie release: usable fallback, floored by weights
        return max(w.season_pack_floor, best)
    best += w.episode_pin_bonus  # reward versions that pin the exact episode
    if version.rate == "positive":
        best += w.positive_rate_bonus
    return best


def rank_versions(
    video_release: str,
    versions: List[SubtitleVersion],
    weights: Optional[ScoreWeights] = None,
) -> List[Tuple[int, SubtitleVersion]]:
    """Return (score, version) pairs sorted best-first, rejects dropped."""
    scored = []
    for v in versions:
        s = score_version(video_release, v, weights)
        if s is not None and s >= 0:
            scored.append((s, v))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored


def pick_best(
    video_release: str,
    versions: List[SubtitleVersion],
    weights: Optional[ScoreWeights] = None,
) -> Optional[Tuple[int, SubtitleVersion]]:
    """Single best version or ``None``."""
    ranked = rank_versions(video_release, versions, weights)
    return ranked[0] if ranked else None
