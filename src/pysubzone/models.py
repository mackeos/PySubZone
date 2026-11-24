"""Data models returned by the PySubZone client.

Every model implements ``as_dict()`` so the CLI can emit stable JSON for
other programs (mpv scripts, cron jobs, ...), mirroring the PyAddic7
contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .languages import display_name


@dataclass
class TitleResult:
    """A movie/series title returned by the site search."""

    name: str
    slug: str
    year: Optional[int] = None
    subtitle_count: Optional[int] = None
    section: str = "exact"  # exact | tv_series | close | popular
    url: str = ""

    @property
    def is_series(self) -> bool:
        return "season" in self.slug

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "slug": self.slug,
            "year": self.year,
            "subtitle_count": self.subtitle_count,
            "section": self.section,
            "url": self.url,
            "is_series": self.is_series,
        }


@dataclass
class SubtitleVersion:
    """One subtitle upload of a title (a row on the title page)."""

    slug: str          # title slug
    lang: str          # language slug
    sub_id: str        # numeric id of the upload
    language: str = ""  # display name as shown by the site
    rate: str = ""      # positive | neutral | negative | "" (not rated)
    releases: List[str] = field(default_factory=list)
    author: str = ""
    comment: str = ""

    @property
    def url(self) -> str:
        return f"/subtitles/{self.slug}/{self.lang}/{self.sub_id}"

    @property
    def ref(self) -> str:
        """Compact reference usable in ``subz download``."""
        return f"{self.slug}/{self.lang}/{self.sub_id}"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ref": self.ref,
            "url": self.url,
            "language": self.language or display_name(self.lang),
            "lang": self.lang,
            "rate": self.rate,
            "releases": self.releases,
            "author": self.author,
            "comment": self.comment,
        }


@dataclass
class TitlePage:
    """A title detail page: metadata, language tabs and versions."""

    slug: str
    name: str
    year: Optional[int] = None
    imdb_id: Optional[str] = None
    languages: List[Dict[str, Any]] = field(default_factory=list)  # {slug,count,url}
    versions: List[SubtitleVersion] = field(default_factory=list)

    def versions_for(self, lang: Optional[str] = None) -> List[SubtitleVersion]:
        """Versions filtered by language slug (all versions if None)."""
        if not lang:
            return list(self.versions)
        return [v for v in self.versions if v.lang == lang]

    def language_counts(self) -> Dict[str, int]:
        return {tab["slug"]: tab["count"] for tab in self.languages}

    def as_dict(self) -> Dict[str, Any]:
        return {
            "slug": self.slug,
            "name": self.name,
            "year": self.year,
            "imdb_id": self.imdb_id,
            "languages": self.languages,
            "versions": [v.as_dict() for v in self.versions],
        }


@dataclass
class BrowseEntry:
    """One row of the /browse pages (latest / popular subtitle uploads)."""

    title_name: str
    version: SubtitleVersion
    media_type: str = ""      # web | bluray | cam | ...
    hearing_impaired: bool = False
    owner: str = ""
    date: str = ""
    downloads: Optional[int] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title_name,
            "media_type": self.media_type,
            "hearing_impaired": self.hearing_impaired,
            "owner": self.owner,
            "date": self.date,
            "downloads": self.downloads,
            "version": self.version.as_dict(),
        }


@dataclass
class SubtitleDetails:
    """Full metadata of one subtitle (the version detail page)."""

    version: SubtitleVersion
    title_name: str = ""
    releases: List[str] = field(default_factory=list)
    online: str = ""
    hearing_impaired: bool = False
    foreign_parts: bool = False
    framerate: str = ""
    files: str = ""
    production_type: str = ""
    release_type: str = ""
    downloads: Optional[int] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            **self.version.as_dict(),
            "title": self.title_name,
            "releases": self.releases,
            "online": self.online,
            "hearing_impaired": self.hearing_impaired,
            "foreign_parts": self.foreign_parts,
            "framerate": self.framerate,
            "files": self.files,
            "production_type": self.production_type,
            "release_type": self.release_type,
            "downloads": self.downloads,
        }


@dataclass
class BulkGroupPlan:
    """One same-title group of videos produced by the bulk planner.

    ``ranked_packs`` holds every version whose release lines cover every
    video of the group (best first) - usually one COMPLETE season zip plus
    act packs.  ``best_by_video`` carries each video's own best match so a
    group without a covering pack (or a video the pack archive cannot
    serve) falls back to the per-video match without extra searches.
    ``error`` is set when the group's title resolution failed, in which
    case every video of the group reports that failure.
    """

    title: Optional[TitleResult]                 # None when the search failed
    videos: List[Path]
    ranked_packs: List[Tuple[int, "SubtitleVersion"]] = field(default_factory=list)
    best_by_video: Dict[Path, Tuple["SubtitleVersion", int]] = field(default_factory=dict)
    chosen_pack: Optional["SubtitleVersion"] = None  # None -> per-video best
    pinned: bool = False                          # every video pins season AND episode
    error: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title.as_dict() if self.title else None,
            "videos": [str(v) for v in self.videos],
            "ranked_packs": [
                {"score": score, **version.as_dict()}
                for score, version in self.ranked_packs
            ],
            "best_by_video": {
                str(video): {**version.as_dict(), "score": score}
                for video, (version, score) in self.best_by_video.items()
            },
            "chosen_pack": self.chosen_pack.as_dict() if self.chosen_pack else None,
            "pinned": self.pinned,
            "error": self.error,
        }


@dataclass
class BulkPlan:
    """The complete bulk-mode plan for a folder scan."""

    groups: List[BulkGroupPlan] = field(default_factory=list)

    @property
    def videos(self) -> List[Path]:
        return [video for group in self.groups for video in group.videos]

    def as_dict(self) -> Dict[str, Any]:
        return {"groups": [group.as_dict() for group in self.groups]}


@dataclass
class DownloadResult:
    """Outcome of a subtitle download."""

    zip_path: Optional[str]          # set when saved as zip
    srt_paths: List[str]             # extracted subtitle files (if extracted)
    version: SubtitleVersion
    bytes_downloaded: int = 0
    target_video: Optional[str] = None  # set by auto-match mode

    def as_dict(self) -> Dict[str, Any]:
        return {
            "zip_path": self.zip_path,
            "srt_paths": self.srt_paths,
            "version": self.version.as_dict(),
            "bytes": self.bytes_downloaded,
            "video": self.target_video,
        }
