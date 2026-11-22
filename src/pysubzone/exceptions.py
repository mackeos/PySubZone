"""Exception hierarchy for PySubZone.

All library errors derive from :class:`SubZoneError` so callers can catch a
single type.  The CLI maps them to stable exit codes (0 ok, 1 not found,
2 error), the same contract PyAddic7 uses.
"""

from __future__ import annotations


class SubZoneError(Exception):
    """Base class for every PySubZone error."""


class NetworkError(SubZoneError):
    """HTTP request failed (connection, timeout, HTTP status >= 400)."""


class ParseError(SubZoneError):
    """The site answered but the HTML could not be understood."""


class TitleNotFoundError(SubZoneError):
    """No title matched the query."""


class NoSubtitleFoundError(SubZoneError):
    """A title exists but has no subtitle for the requested language."""


class DownloadError(SubZoneError):
    """The subtitle file could not be fetched or unpacked."""


class InvalidURLError(SubZoneError, ValueError):
    """The given URL is not a subzone.ir subtitles URL."""
