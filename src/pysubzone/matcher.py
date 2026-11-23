"""Video filename parsing and folder scanning for the auto-match mode.

``guess_release`` derives a search query and episode markers from a video
file name in the way release scenes name files:

* ``Dexter.Resurrection.S02E01.Risky.Business.1080p.AMZN.WEB-DL.DDP5.1.H.264-NTb.mkv``
  -> title query "Dexter Resurrection", season 2, episode 1
* ``The.Odyssey.2026.1080p.AMZN.WEB-DL.DDP5.1.H.264-NTb.mkv``
  -> title query "The Odyssey" (year kept separate)
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Tuple

VIDEO_EXTENSIONS = {
    ".mkv", ".mp4", ".avi", ".mov", ".m4v", ".mpg", ".mpeg", ".ts", ".wmv",
    ".flv", ".webm", ".ogv", ".vob", ".rmvb",
}

_CUT_RE = re.compile(
    r"\b(?:"
    r"s\d{1,2}[\s._-]*e\d{1,3}"          # SxxEyy
    r"|\b\d{1,2}x\d{1,3}\b"                # 2x05
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
