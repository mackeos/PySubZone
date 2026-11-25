"""Video filename parsing and folder scanning for the auto-match mode.

``guess_release`` derives a search query and episode markers from a video
file name in the way release scenes name files:

* ``Dexter.Resurrection.S02E01.Risky.Business.1080p.AMZN.WEB-DL.DDP5.1.H.264-NTb.mkv``
  -> title query "Dexter Resurrection", season 2, episode 1
* ``Arcane Season 2 Episode 1 1080p NF WEB-DL.mkv``
  -> title query "Arcane" (word-form season/episode tokens are cut too)
* ``The.Odyssey.2026.1080p.AMZN.WEB-DL.DDP5.1.H.264-NTb.mkv``
  -> title query "The Odyssey" (year kept separate)
* ``Prison Break S00E03 The Final Break 1080p.mkv``
  -> special query "Prison Break The Final Break" (season-0 names keep the
  part after the marker - the site lists specials as their own entry)
* ``The Final Break.mkv`` inside ``Show/Specials/``
  -> special query "Show The Final Break" (folder is the marker, stem the
  special's own name)
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Tuple

from .scoring import season_info

VIDEO_EXTENSIONS = {
    ".mkv", ".mp4", ".avi", ".mov", ".m4v", ".mpg", ".mpeg", ".ts", ".wmv",
    ".flv", ".webm", ".ogv", ".vob", ".rmvb",
}

_CUT_RE = re.compile(
    r"\b(?:"
    r"s\d{1,2}[\s._-]*e\d{1,3}"          # SxxEyy
    r"|\b\d{1,2}x\d{1,3}\b"                # 2x05
    r"|\bseason[\s._-]*\d{1,2}\b"          # "Season 2" / "SEASON.01" word form
    r"|(?:19|20)\d{2}"                     # year
    r"|\b(?:2160p|1080[pi]|720p|576p|480p)\b"
    r"|\b(?:web-?dl|webdl|webrip|web|bluray|blu-ray|bdrip|brrip|dvdrip|hdrip|"
    r"hdtv|pdtv|remux|dvdscr|amzn|nf|atv|hulu|dsnp|hbo|max|itunes|cthp|skst)\b"
    r"|\b(?:aac|ac3|eac3|ddp?\d(?:[\._]\d)?|dts(?:-hd)?|truehd|atmos|flac|opus)\b"
    r"|\b(?:x264|x265|h\.?264|h\.?265|hevc|avc|av1|xvid|divx)\b"
    r"|\b(?:proper|repack|extended|unrated|remastered|complete|internal)\b"
    r")",
    re.IGNORECASE,
)

_SEASON_EPISODE_RE = re.compile(r"\bs(\d{1,2})[\s._-]*e(\d{1,3})\b", re.IGNORECASE)
_ALT_EPISODE_RE = re.compile(r"\b(\d{1,2})x(\d{1,3})\b", re.IGNORECASE)
_GROUP_TAIL_RE = re.compile(r"\s*-[A-Za-z0-9]{2,}$")
_LEADING_YEAR_RE = re.compile(r"^(?:19|20)\d{2}(?=$|[\._\s])")

# season-0 (specials) markers: S00E03 / S0E3 / "Season 0" / a bare S00
_SEASON_ZERO_RE = re.compile(
    r"\bs0{1,2}[\s._-]*e\d{1,3}\b"
    r"|\bseason[\s._-]*0\b"
    r"|\bs0{1,2}\b(?![\s._-]*e\d)",
    re.IGNORECASE,
)
# folder names that hold a show's specials ("Specials", "S00", "Season 0")
_SPECIALS_FOLDER_RE = re.compile(
    r"^(?:specials?(?:[ ._-]?extras)?|extras|season[ ._-]*0+|s0+)$",
    re.IGNORECASE,
)
# episode/special index tokens that hide a special's own name ("E03 ...")
_INDEX_TOKEN_RE = re.compile(
    r"\b(?:\d{1,2}x\d{1,3}|s\d{1,2}\b(?:[\s._-]*e\d{1,3})?|(?:e|ep)[\s._-]*\d{1,3})\b[\s._-]*",
    re.IGNORECASE,
)


def guess_release(stem: str) -> str:
    """Video file stem cleaned of extension noise (dots kept for scoring)."""
    return stem.strip()


def guess_title(stem: str) -> str:
    """Best-effort show/movie name from a release file stem."""
    cut = _CUT_RE.search(stem)
    raw = stem[: cut.start()] if cut else stem
    cleaned = raw.replace(".", " ").replace("_", " ").replace("'", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -")
    # drop a dangling year that survived the cut
    cleaned = re.sub(r"\s+(?:19|20)\d{2}$", "", cleaned)
    # strip a trailing scene group ("... -NTb")
    cleaned = _GROUP_TAIL_RE.sub("", cleaned).strip()
    if not cleaned:
        # titles that ARE a year ("1984", "2012") or single-token stems
        if _LEADING_YEAR_RE.match(stem):
            return stem.split(".")[0]
        first = re.split(r"[\._]", stem)[0].strip()
        return first if re.search(r"[A-Za-z0-9]", first) else ""
    return cleaned


def guess_season_episode(stem: str) -> Optional[Tuple[int, int]]:
    m = _SEASON_EPISODE_RE.search(stem)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = _ALT_EPISODE_RE.search(stem)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


def _name_fragment(raw: str) -> str:
    """Readable name from a release-name fragment (guess_title's cleanup)."""
    cleaned = raw.replace(".", " ").replace("_", " ").replace("'", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -")
    cut = _CUT_RE.search(cleaned)
    if cut:
        cleaned = cleaned[: cut.start()].strip(" -")
    cleaned = re.sub(r"\s+(?:19|20)\d{2}$", "", cleaned)
    cleaned = _GROUP_TAIL_RE.sub("", cleaned).strip()
    return re.sub(r"\s+", " ", cleaned).strip()


def flatten_name(name: str) -> str:
    """Letters and digits only, lower-cased (comparison key for titles)."""
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def split_special(stem: str) -> Tuple[str, str]:
    """(show, special name) carried by a season-0 release name.

    ``Prison Break S00E03 The Final Break`` -> ``('Prison Break',
    'The Final Break')`` - season 0 has no site entry of its own and the
    part after the marker is the special's actual name.  Both parts are
    empty when the name carries no season-0 marker (``S00Eyy`` / ``S0Eyy``
    / ``Season 0`` / a bare ``S00``), so it is not a special.
    """
    if season_info(stem).season != 0:
        return "", ""
    m = _SEASON_ZERO_RE.search(stem)
    if not m:
        return "", ""
    return _name_fragment(stem[: m.start()]), _name_fragment(stem[m.end():])


def is_specials_folder(name: str) -> bool:
    """True for folder names that hold a show's specials.

    ``Specials``, ``Special``, ``Extras``, ``Season 0``, ``S00`` - the
    case-insensitive, release-noise-tolerant spellings seen in the wild.
    """
    return bool(_SPECIALS_FOLDER_RE.match((name or "").strip()))


def special_query_parts(
    stem: str, parent_name: str = "", grandparent_name: str = ""
) -> Optional[Tuple[str, str]]:
    """(search query, show-only query) for a season-0 (specials) video.

    Marker form: ``Prison Break S00E03 The Final Break`` - the site lists
    the special as its own entry (``Prison Break: The Final Break``), so
    the query keeps both the show part and the part after the marker:
    ``Prison Break The Final Break``.  The second element is the show-only
    query, used as a fallback when the site does not know the special.

    Episode-only name inside a Specials folder: ``The Final Break.mkv`` in
    ``Show/Specials/`` - the folder is the marker, the stem is the
    special's own name and the show comes from the folder above it.

    ``None`` when the name is not a special (no season-0 marker and not
    inside a Specials folder) - callers keep plain ``guess_title``.
    """
    show, tail = split_special(stem)
    folder = is_specials_folder(parent_name)
    if not show and not tail:
        # no season-0 marker in the stem: a Specials *folder* makes the
        # stem the special's own name ("The Final Break.mkv" in
        # "Show/Specials/"); a stem that pins a real season is an episode
        # and never a special
        stem_season = season_info(stem).season
        if stem_season is not None and stem_season != 0:
            return None
        if not folder:
            return None
        tail = _INDEX_TOKEN_RE.sub("", _name_fragment(stem)).strip(" -")
    elif not tail and folder:
        # "The Final Break S00E03" inside Specials: the name sits before
        # the marker, so the show part comes from the folder above
        grand_show = _name_fragment(grandparent_name)
        if grand_show and flatten_name(show) != flatten_name(grand_show):
            tail, show = show, grand_show
    if not show and folder:
        # the show of a Specials-folder video is the folder above Specials
        show = _name_fragment(grandparent_name)
    query = " ".join(part for part in (show, tail) if part)
    if not query or not tail:
        # an un-nameable special ("Show S00E03", "E03") keeps guess_title
        return None
    return query, show


def guess_special_query(
    stem: str, parent_name: str = "", grandparent_name: str = ""
) -> Optional[str]:
    """Search query for a season-0 (specials) video, or ``None``.

    Convenience wrapper around :func:`special_query_parts` returning only
    the full query.
    """
    parts = special_query_parts(stem, parent_name, grandparent_name)
    return parts[0] if parts else None


def is_video_file(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTENSIONS


def find_videos(path: Path) -> List[Path]:
    """Video files at *path*: itself, or scanned recursively in a folder."""
    if path.is_file():
        if not is_video_file(path):
            raise ValueError(f"Not a video file: {path}")
        return [path]
    if path.is_dir():
        videos = sorted(
            p for p in path.rglob("*") if p.is_file() and is_video_file(p)
        )
        return videos
    raise FileNotFoundError(path)


def resolve_out(out: Optional[Path], source: Optional[Path], out_cwd: bool = False) -> Optional[Path]:
    """Resolve an output folder for the auto-match flows (CLI and TUI).

    A relative *out* is created inside *source* (the videos folder), so that
    ``-o subs`` / an output of ``subs`` collects subtitles in a ``subs`` folder
    next to the videos.  Absolute paths are used unchanged, and
    ``out_cwd=True`` resolves against the current directory instead.
    ``None`` means no output folder was requested (caller falls back to its
    own default).
    """
    if out is None:
        return None
    if out.is_absolute() or out_cwd or source is None:
        return out
    return (source / out).resolve()


def subtitle_target(video: Path) -> Path:
    """Default subtitle file path for a video (same dir, .fa.srt)."""
    return video.with_suffix(".fa.srt")


def safe_title_from_slug(slug: str) -> str:
    """Human-friendly fallback title from a URL slug."""
    return re.sub(r"[\-_]+", " ", slug).strip().title()
