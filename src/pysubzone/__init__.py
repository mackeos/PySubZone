"""PySubZone - search and download subtitles from subzone.ir.

Python API, CLI and TUI for the Subf2m subtitle database engine running on
subzone.ir, in the spirit of PyAddic7 (addic7ed.com).

Quick start::

    from pysubzone import SubZoneClient

    client = SubZoneClient()
    title = client.search_exact("Dexter Resurrection")
    versions = client.title_versions(title.slug, "farsi_persian")
    client.download(versions[0], dest_dir=".", save_mode="extract")
"""

from .client import SubZoneClient
from .async_client import AsyncSubZoneClient
from .exceptions import (
    DownloadError,
    InvalidURLError,
    NetworkError,
    NoSubtitleFoundError,
    ParseError,
    SubZoneError,
    TitleNotFoundError,
)
from .models import (
    BrowseEntry,
    DownloadResult,
    SubtitleDetails,
    SubtitleVersion,
    TitlePage,
    TitleResult,
)
from .scoring import DEFAULT_WEIGHTS, ScoreWeights

__version__ = "0.4.3"

__all__ = [
    "SubZoneClient",
    "AsyncSubZoneClient",
    "SubZoneError",
    "NetworkError",
    "ParseError",
    "TitleNotFoundError",
    "NoSubtitleFoundError",
    "DownloadError",
    "InvalidURLError",
    "TitleResult",
    "TitlePage",
    "SubtitleVersion",
    "SubtitleDetails",
    "DownloadResult",
    "BrowseEntry",
    "ScoreWeights",
    "DEFAULT_WEIGHTS",
    "__version__",
]
