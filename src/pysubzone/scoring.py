"""Best-version selection for the auto-match mode.

Given a video release name and a list of subtitle versions, score how well
each version fits the video: episode numbers must match, and resolution /
source / release-group tokens earn bonus points.  The design mirrors the
"scoreBestSubVersions" idea of PyAddic7 but is tuned to the Subf2m layout,
where a version exposes one or more *release info* lines instead of a
single distribution string.
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
    group).  ``score_release`` rejects a pair outright when both sides carry
    explicit season/episode numbers that disagree - that hard rule is not a
    weight and always applies.
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


# --- scoring ---------------------------------------------------------------

def score_release(
    video_release: str,
    sub_release: str,
    weights: Optional[ScoreWeights] = None,
) -> Tuple[int, bool]:
    """Score one (video release, subtitle release) pair.

    Returns ``(score, episode_matched)``.  *episode_matched* is False when
    both sides carry explicit S/E numbers that disagree - the pair must be
    rejected in that case.  Point values come from *weights*
    (:class:`ScoreWeights`, defaults used when ``None``).
    """
    w = _effective(weights)
    v_se = extract_season_episode(video_release)
    s_se = extract_season_episode(sub_release)
    if v_se and s_se:
        episode_ok = v_se == s_se
        if not episode_ok:
            return -100, False
    else:
        episode_ok = True

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

    ``None`` means "hard reject": every release line of the version carries
    an explicit season/episode that disagrees with the video.  Otherwise the
    best-scoring release line wins; versions with season-pack style releases
    (no explicit episode) are kept as low-ranked fallbacks - they still
    cover every episode of the season.
    """
    w = _effective(weights)
    lines = version.releases or [version.ref]
    best: Optional[int] = None
    any_episode_line = False
    for line in lines:
        s, ep_ok = score_release(video_release, line, w)
        if extract_season_episode(line) or extract_episode(line):
            any_episode_line = True
        if not ep_ok:
            continue
        if best is None or s > best:
            best = s
    if not any_episode_line:
        # season pack / movie release: usable fallback, floored by weights
        return max(w.season_pack_floor, best) if best is not None else w.season_pack_floor
    if best is None:
        return None  # explicit S/E everywhere, none matched
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
