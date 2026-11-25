"""HTTP client for subzone.ir (the Subf2m subtitle database engine).

Public API mirrors PyAddic7's client style:

    client = SubZoneClient()
    results = client.search("dexter resurrection")
    page = client.title(results[0].slug)
    version = page.versions_for("farsi_persian")[0]
    result = client.download(version, dest_dir=".", save_mode="extract")

All methods raise :class:`SubZoneError` subclasses, so callers can catch one
type.  Downloads stream through a signed CDN redirect; the zip magic bytes
are verified because stale uploads return an S3 "NoSuchKey" XML body with
HTTP 200.
"""

from __future__ import annotations

import io
import logging
import re
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .exceptions import (
    DownloadError,
    InvalidURLError,
    NetworkError,
    NoSubtitleFoundError,
    SubZoneError,
    TitleNotFoundError,
)
from .languages import DEFAULT_LANGUAGE, display_name, resolve
from .matcher import (
    flatten_name,
    guess_title,
    guess_season_episode,
    is_specials_folder,
    special_query_parts,
)
from .models import (
    BulkGroupPlan,
    BulkPlan,
    DownloadResult,
    SubtitleDetails,
    SubtitleVersion,
    TitlePage,
    TitleResult,
)
from .parsing import parse_search, parse_subtitles_url, parse_subtitle_page, parse_title
from .scoring import ScoreWeights, best_covering_version, covering_versions, pick_best, score_version, season_info

DEFAULT_BASE_URL = "https://subzone.ir"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

SubtitleRef = Union[str, SubtitleVersion, SubtitleDetails]
ProgressCallback = Callable[[int, Optional[int]], None]

logger = logging.getLogger("pysubzone")

SUBTITLE_EXTS = {".srt", ".ass", ".ssa", ".sub", ".vtt", ".srt3d"}


def _slugify_lang_suffix(lang_slug: str) -> str:
    """Short suffix used when naming extracted subtitle files."""
    short = {
        "farsi_persian": "fa",
        "english": "en",
        "arabic": "ar",
        "turkish": "tr",
        "indonesian": "id",
        "vietnamese": "vi",
        "thai": "th",
        "bengali": "bn",
        "italian": "it",
        "polish": "pl",
        "portuguese": "pt",
        "portuguese-br": "pt-br",
        "spanish": "es",
        "french": "fr",
        "german": "de",
        "dutch": "nl",
        "russian": "ru",
        "korean": "ko",
        "japanese": "ja",
        "chinese": "zh",
        "hindi": "hi",
        "urdu": "ur",
    }
    return short.get(lang_slug, re.sub(r"[^a-z0-9]+", "-", lang_slug).strip("-"))


def _sanitize_base_name(base_name: str) -> str:
    """Keep only the final path part of a requested output base name."""
    cleaned = Path(str(base_name).strip()).name
    return cleaned or "subtitle"


_flatten = flatten_name  # historical name, see matcher.flatten_name


_SEASON_TAIL_RE = re.compile(
    r"[\s\-–—:_.]*"
    r"(?:"
    r"\b(?:season|فصل)[\s._-]*"
    r"(?:first|1st|second|2nd|third|3rd|fourth|4th|fifth|5th|sixth|6th|"
    r"seventh|7th|eighth|8th|ninth|9th|tenth|10th|\d{1,2})\b"
    r"|\b(?:first|1st|second|2nd|third|3rd|fourth|4th|fifth|5th|sixth|6th|"
    r"seventh|7th|eighth|8th|ninth|9th|tenth|10th)\b[\s._-]*season\b"
    r"|\bs\d{1,2}\b"
    r")\s*$",
    re.IGNORECASE,
)


def _strip_season_phrase(name: str) -> str:
    """Entry name without its trailing season qualifier.

    ``Prison Break - Second Season`` -> ``Prison Break`` so a per-season
    site entry can be matched back to the plain show name of a video
    folder; separators dangling after the strip are trimmed too.
    """
    stripped = name.strip()
    while True:
        candidate = _SEASON_TAIL_RE.sub("", stripped).strip(" \t-–—:_.")
        if candidate == stripped:
            return stripped
        stripped = candidate


def _season_hint(stem: str, parent_name: str = "") -> Optional[int]:
    """Season of one video: file name first, parent folder as fallback.

    ``Show.S02E05.mkv`` pins season 2 in its name; ``E05.mkv`` inside a
    ``Season 2`` folder inherits the folder's season.  A ``Specials``
    folder means season 0 - a specials file is never an episode of the
    show's numbered seasons.  Folder names that merely carry noise
    (``New folder (6)``) yield ``None`` - the same parser that reads
    release names reads folder names, so word forms (``Season Two``,
    ``Show - First Season``) work too.
    """
    info = season_info(stem)
    if info.season is not None:
        return info.season
    if is_specials_folder(parent_name):
        return 0
    return season_info(parent_name).season


class SubZoneClient:
    """Scrape-and-download client for subzone.ir."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: float = 20.0,
        max_retries: int = 3,
        verbose: bool = False,
        min_request_interval: float = 0.0,
        cache_ttl: float = 0.0,
        cache_size: int = 256,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.verbose = verbose
        # politeness rate limit: minimum seconds between two HTTP requests
        self.min_request_interval = max(0.0, float(min_request_interval))
        self._next_allowed = 0.0
        self._rate_lock = threading.Lock()
        # in-memory page cache: {cache_key: (monotonic_ts, response)};
        # 0 disables it, stream=True downloads are never cached
        self.cache_ttl = max(0.0, float(cache_ttl))
        self.cache_size = max(0, int(cache_size))
        self._cache = {}
        self._cache_lock = threading.Lock()
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": self.base_url + "/",
            }
        )
        retry = Retry(
            total=max_retries,
            connect=max_retries,
            read=max_retries,
            status=max_retries,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET", "HEAD"]),
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    # ------------------------------------------------------------------
    # transport
    # ------------------------------------------------------------------

    def _url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return f"{self.base_url}{path if path.startswith('/') else '/' + path}"

    @staticmethod
    def _cache_key(url: str, params: Optional[dict]) -> str:
        if not params:
            return url
        query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        return f"{url}?{query}"

    def _throttle(self) -> None:
        """Sleep just long enough to honor min_request_interval (global)."""
        if self.min_request_interval <= 0:
            return
        with self._rate_lock:
            wait = self._next_allowed - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._next_allowed = time.monotonic() + self.min_request_interval

    def _cache_get(self, key: str) -> Optional[requests.Response]:
        if self.cache_ttl <= 0:
            return None
        with self._cache_lock:
            entry = self._cache.get(key)
        if entry is None:
            return None
        ts, response = entry
        if time.monotonic() - ts > self.cache_ttl:
            with self._cache_lock:
                self._cache.pop(key, None)
            return None
        if self.verbose:
            logger.info("cache hit %s", key)
        return response

    def _cache_put(self, key: str, response: requests.Response) -> None:
        if self.cache_ttl <= 0 or self.cache_size <= 0:
            return
        with self._cache_lock:
            self._cache[key] = (time.monotonic(), response)
            while len(self._cache) > self.cache_size:
                self._cache.pop(next(iter(self._cache)))

    def clear_cache(self) -> None:
        """Drop every cached page (rate limiting is unaffected)."""
        with self._cache_lock:
            self._cache.clear()

    def _get(
        self,
        path: str,
        *,
        params: Optional[dict] = None,
        stream: bool = False,
        referer: Optional[str] = None,
        not_found_ok: bool = False,
    ) -> Optional[requests.Response]:
        url = self._url(path)
        cacheable = not stream and self.cache_ttl > 0
        key = self._cache_key(url, params) if cacheable else None
        if key is not None:
            cached = self._cache_get(key)
            if cached is not None:
                return cached
        self._throttle()
        headers = {"Referer": referer} if referer else None
        if self.verbose:
            logger.info("GET %s params=%s", url, params)
        try:
            response = self.session.get(
                url, params=params, timeout=self.timeout, stream=stream, headers=headers
            )
        except requests.RequestException as exc:
            raise NetworkError(f"request failed: {url} ({exc})") from exc
        if response.status_code == 404 and not_found_ok:
            return None
        if response.status_code >= 400:
            raise NetworkError(f"HTTP {response.status_code} for {url}")
        if key is not None:
            self._cache_put(key, response)
        return response

    # ------------------------------------------------------------------
    # pages
    # ------------------------------------------------------------------

    def search(self, query: str, lang: Optional[str] = None) -> List[TitleResult]:
        """Search titles by keyword; *lang* narrows to one language."""
        params = {"query": query}
        lang_slug = resolve(lang)
        if lang_slug:
            params["l"] = lang_slug
        response = self._get("/subtitles/searchbytitle", params=params)
        results = parse_search(response.text)
        if not results:
            raise TitleNotFoundError(f"no title matches {query!r}")
        return results

    def search_exact(
        self, query: str, lang: Optional[str] = None, season: Optional[int] = None
    ) -> TitleResult:
        """First exact match, falling back to the closest fuzzy match.

        With *season* set (and no exact name match), the site's per-season
        entries are understood: an entry named ``Show - Second Season``
        whose show part equals *query* wins when the requested season is 2,
        and a season-less generic entry is preferred over a wrong-season
        one.  Only when nothing matches does the first search result win -
        the previous, season-blind behaviour.
        """
        results = self.search(query, lang=lang)
        return self.select_title(results, query, season=season)

    @staticmethod
    def select_title(
        results: List[TitleResult], query: str, season: Optional[int] = None
    ) -> TitleResult:
        """Pick the entry that answers *query* (and *season*) from *results*.

        Priority: exact name match in the exact/TV sections, then - for
        season 0 (specials) - an entry whose name ends with the special's
        own name, then a season-qualified entry whose show name and season
        both match, then a season-less entry with the query's own name (a
        generic page that hosts every season), and finally the first
        result - the historic fallback, kept so the never-empty contract
        of ``search_exact`` is unchanged.
        """
        normalized = _flatten(query)
        for section in ("exact", "tv_series"):
            for result in results:
                if result.section != section:
                    continue
                if _flatten(result.name) == normalized:
                    return result
        if season == 0:
            # specials live on their own entry ("Prison Break: The Final
            # Break"); when the query is the special's own name and no
            # entry carries it exactly, the entry ENDING with the name is
            # the show+special page ("The Final Break" -> "Prison Break:
            # The Final Break").  Multi-word queries only, so a stray
            # single token cannot hijack the pick.
            if len(query.split()) >= 2:
                for result in results:
                    flat = _flatten(result.name)
                    if flat and flat != normalized and flat.endswith(normalized):
                        return result
        if season is not None:
            # the site splits one show into per-season entries ("Show -
            # Second Season"); match the show part AND the season number -
            # slugs lie (a "sequel-first-season" slug can be named "Fifth
            # Season"), entry names do not
            for result in results:
                if season_info(result.name).season != season:
                    continue
                base = _strip_season_phrase(result.name)
                if base and _flatten(base) == normalized:
                    return result
            # no per-season entry for *season*: a generic entry (no season
            # in its name) hosts every season on one page
            for result in results:
                if season_info(result.name).season is not None:
                    continue
                if _flatten(result.name) == normalized:
                    return result
        return results[0]

    def title(self, slug: str) -> TitlePage:
        """Fetch a title page with all versions and language tabs."""
        response = self._get(f"/subtitles/{slug}", not_found_ok=True)
        if response is None:
            raise TitleNotFoundError(f"no title page at /subtitles/{slug}")
        page = parse_title(response.text, slug)
        if not page.versions and not page.languages:
            raise TitleNotFoundError(f"title page has no subtitles: /subtitles/{slug}")
        return page

    def title_versions(self, slug: str, lang: Optional[str] = None) -> List[SubtitleVersion]:
        """Versions of a title, optionally filtered by language.

        Raises :class:`NoSubtitleFoundError` when the language filter yields
        nothing, so callers never silently download a different language.
        """
        lang_slug = resolve(lang)
        if lang_slug:
            try:
                page = self.title(f"{slug}/{lang_slug}")
            except (NetworkError, TitleNotFoundError):
                page = self.title(slug)
            versions = page.versions_for(lang_slug)
            if versions:
                return versions
            raise NoSubtitleFoundError(
                f"no subtitles for {slug!r} in {display_name(lang_slug)}"
            )
        return self.title(slug).versions

    def get_version(self, slug: str, lang: str, sub_id: str) -> SubtitleDetails:
        """Fetch the detail page of one subtitle upload."""
        version = SubtitleVersion(slug=slug, lang=lang, sub_id=str(sub_id))
        response = self._get(f"/subtitles/{slug}/{lang}/{sub_id}")
        return parse_subtitle_page(response.text, version)

    def get_version_from_url(self, url: str) -> SubtitleDetails:
        slug, lang, sub_id = parse_subtitles_url(url)
        if not (lang and sub_id):
            raise ValueError(f"URL does not point to a single subtitle: {url}")
        return self.get_version(slug, lang, sub_id)

    # ------------------------------------------------------------------
    # matching
    # ------------------------------------------------------------------

    def best_for_file(
        self,
        filename_stem: str,
        lang: Optional[str] = DEFAULT_LANGUAGE,
        title_query: Optional[str] = None,
        weights: Optional[ScoreWeights] = None,
        *,
        parent_name: str = "",
        grandparent_name: str = "",
    ) -> Tuple[TitleResult, SubtitleVersion, int]:
        """Find the best subtitle version for a video release name.

        ``filename_stem`` is the video file name without extension.  The
        title is resolved via site search (query derived from the file name
        unless *title_query* is given), then versions are scored by release
        similarity (episode numbers must agree).  *weights* optionally
        tunes the scoring via :class:`~.scoring.ScoreWeights`.

        Season-0 (specials) names are routed to the special's own site
        entry: ``Prison Break S00E03 The Final Break`` searches ``Prison
        Break The Final Break`` - the part after the ``S00Eyy`` marker is
        the special's actual name - and lands on the dedicated ``Prison
        Break: The Final Break`` entry instead of falling into a numbered
        season.  An episode-only stem inside a ``Specials`` folder (its
        *parent_name*) composes the query the same way, taking the show
        name from the folder above.  A special the site does not know
        falls back to the show's own search instead of erroring.
        """
        special = (
            special_query_parts(filename_stem, parent_name, grandparent_name)
            if title_query is None
            else None
        )
        query = (
            title_query
            or (special[0] if special else None)
            or guess_title(filename_stem)
        )
        if not query:
            raise TitleNotFoundError(f"cannot derive a title from {filename_stem!r}")
        # a file that pins a season points at that season's site entry -
        # search results put any season first, not the requested one
        season = season_info(filename_stem).season
        if season is None and special is not None:
            season = _season_hint(filename_stem, parent_name)
        try:
            title = self.search_exact(query, lang=lang, season=season)
        except TitleNotFoundError:
            # unknown special: retry the show's own search before giving up
            if special is not None and special[1] and _flatten(special[1]) != _flatten(query):
                title = self.search_exact(special[1], lang=lang, season=season)
            else:
                raise
        versions = self.title_versions(title.slug, lang)
        if not versions:
            raise NoSubtitleFoundError(
                f"no subtitles for {title.name!r} in {display_name(resolve(lang) or '')}"
            )
        picked = pick_best(filename_stem, versions, weights)
        if not picked:
            raise NoSubtitleFoundError(
                f"no version of {title.name!r} matches {filename_stem!r}"
            )
        return title, picked[1], picked[0]

    def auto_match(
        self,
        video_stem: str,
        lang: Optional[str] = DEFAULT_LANGUAGE,
    ) -> Tuple[TitleResult, SubtitleVersion, int]:
        """Alias of :meth:`best_for_file` kept for API symmetry."""
        return self.best_for_file(video_stem, lang=lang)

    def season_episode_of(self, version: SubtitleDetails) -> Optional[Tuple[int, int]]:
        """S/E of a subtitle version taken from its release lines."""
        for line in version.releases:
            se = guess_season_episode(line)
            if se:
                return se
        return None

    def plan_prefer_complete(
        self,
        videos: List[Path],
        lang: Optional[str] = None,
    ) -> Dict[Path, Tuple[TitleResult, SubtitleVersion, int]]:
        """Map episode videos onto one same-season pack that covers them all.

        Engine behind ``subz auto --prefer-complete`` and the TUI
        auto-match checkbox: videos whose file name pins a season AND an
        episode are grouped by their matched title; when one version's
        release lines cover every video of a group (a COMPLETE season zip,
        or an act pack spanning them all), each video of the group is
        planned onto that pack - the per-video download then extracts the
        video's own episode out of the pack archive.

        Movies and videos without a pinned season/episode never appear in
        the returned mapping and keep their normal per-video best match.
        Raises whatever ``best_for_file``/``title_versions`` raise -
        callers wrap this in try/except so planning stays best-effort and
        a failure never breaks the normal flow.
        """
        planned: Dict[Path, Tuple[TitleResult, SubtitleVersion, int]] = {}
        for title, group, ranked in self.find_complete_packs(videos, lang=lang):
            if not ranked:
                continue
            _total, pack = ranked[0]
            for video in group:
                s = score_version(video.name, pack)
                if s is not None:
                    planned[video] = (title, pack, s)
        return planned

    def find_complete_packs(
        self,
        videos: List[Path],
        lang: Optional[str] = None,
    ) -> List[Tuple[TitleResult, List[Path], List[Tuple[int, SubtitleVersion]]]]:
        """Group episode videos per title with their ranked covering packs.

        The interactive engine behind ``subz auto --prefer-complete`` and
        the TUI pack picker: every group of same-title episode videos is
        returned together with **all** versions whose release lines cover
        every video of the group, ranked best-first by total score (see
        :func:`~.scoring.covering_versions`).  A group with no covering
        pack comes back with an empty ranking so callers can fall back to
        the per-video best.

        Movies and videos without a pinned season/episode never appear in
        the groups.  Raises whatever ``best_for_file``/``title_versions``
        raise - callers wrap this in try/except so planning stays
        best-effort and a failure never breaks the normal flow.
        """
        episode_videos = [
            v for v in videos
            if season_info(v.name).episodes is not None
            and season_info(v.name).season is not None
        ]
        grouped: Dict[str, List[Path]] = {}
        titles_by_slug: Dict[str, Any] = {}
        for video in episode_videos:
            title, _best, _score = self.best_for_file(video.stem, lang=lang)
            grouped.setdefault(title.slug, []).append(video)
            titles_by_slug[title.slug] = title
        groups: List[Tuple[TitleResult, List[Path], List[Tuple[int, SubtitleVersion]]]] = []
        for slug, group in grouped.items():
            versions = self.title_versions(slug, lang)
            ranked = covering_versions([v.name for v in group], versions)
            groups.append((titles_by_slug[slug], group, ranked))
        return groups

    # ------------------------------------------------------------------
    # bulk folder mode (v0.5.0)
    # ------------------------------------------------------------------

    def plan_bulk(
        self,
        videos: Sequence[Union[str, Path]],
        lang: Optional[str] = None,
    ) -> BulkPlan:
        """Plan a bulk folder run: group videos per title+season with packs.

        Videos are grouped locally by their guessed title AND season (no
        network), so every distinct group costs one search lookup; the
        search itself runs once per distinct title and is reused for that
        show's every season - a four-season folder costs ONE search request
        plus one title page per season.  The season decides WHICH site
        entry is used: the site lists each season as its own entry (``Show
        - Second Season``) and puts any of them first in the search
        results, so a season-blind first-result pick downloads the wrong
        season's subtitles.  The season comes from the video file name,
        falling back to the parent folder name (``.../Show/Season 2/``).
        Each group carries every covering pack ranked best-first (see
        :func:`~.scoring.covering_versions`) plus each video's own best
        match, so a group without a covering pack falls back to the
        per-video match without extra searches.

        Videos whose names do not pin both a season and an episode (movies)
        never get packs - they form their own per-video groups.  Season-0
        (specials) videos form their own groups under the special's own
        query (``Prison Break S00E03 The Final Break`` -> "Prison Break The
        Final Break"), so they land on the dedicated entry instead of a
        numbered season; an episode-only stem inside a ``Specials`` folder
        composes the query from the folder above it.  A group whose title
        search fails comes back with ``title=None`` and ``error`` set;
        callers report its videos as "not found" instead of raising, so
        one unmatchable file never aborts the whole folder.  An unknown
        special retries the show's own search before the group is given up.
        """
        by_key: Dict[Tuple[str, Optional[int]], List[Path]] = {}
        special_fallback: Dict[Tuple[str, Optional[int]], str] = {}
        for raw in videos:
            video = Path(raw)
            special = special_query_parts(
                video.stem, video.parent.name, video.parent.parent.name
            )
            query = (
                (special[0] if special else None)
                or guess_title(video.stem)
                or video.stem
            )
            key = (query, _season_hint(video.stem, video.parent.name))
            by_key.setdefault(key, []).append(video)
            if special and special[1]:
                special_fallback[key] = special[1]
        groups: List[BulkGroupPlan] = []
        search_cache: Dict[str, List[TitleResult]] = {}
        select_queries: Dict[str, str] = {}
        search_errors: Dict[str, str] = {}
        for (query, season), group in by_key.items():
            fallback_query = special_fallback.get((query, season))
            if query in search_errors:  # one failed lookup serves the show's every season
                groups.append(
                    BulkGroupPlan(
                        title=None, videos=group, error=search_errors[query], season=season
                    )
                )
                continue
            results = search_cache.get(query)
            if results is None:
                try:
                    results = self.search(query, lang=lang)
                    select_queries[query] = query
                except SubZoneError as exc:
                    # an unknown special retries the show's own search
                    # before the whole group is given up
                    retried = False
                    if (
                        fallback_query
                        and _flatten(fallback_query) != _flatten(query)
                    ):
                        try:
                            results = self.search(fallback_query, lang=lang)
                            select_queries[query] = fallback_query
                            retried = True
                        except SubZoneError:
                            pass
                    if not retried:
                        search_errors[query] = str(exc)
                        groups.append(
                            BulkGroupPlan(title=None, videos=group, error=str(exc), season=season)
                        )
                        continue
                search_cache[query] = results
            title = self.select_title(
                results, select_queries.get(query, query), season=season
            )
            try:
                versions = self.title_versions(title.slug, lang)
            except SubZoneError as exc:
                groups.append(
                    BulkGroupPlan(title=title, videos=group, error=str(exc), season=season)
                )
                continue
            best_by_video: Dict[Path, Tuple[SubtitleVersion, int]] = {}
            for video in group:
                picked = pick_best(video.name, versions)
                if picked is not None:
                    best_by_video[video] = (picked[1], picked[0])
            ranked = covering_versions([v.name for v in group], versions)
            pinned = all(
                season_info(video.name).season is not None
                and season_info(video.name).episodes is not None
                for video in group
            )
            groups.append(
                BulkGroupPlan(
                    title=title,
                    videos=group,
                    ranked_packs=ranked,
                    best_by_video=best_by_video,
                    pinned=pinned,
                    season=season,
                )
            )
        return BulkPlan(groups=groups)

    def download_pack_once(
        self,
        target: SubtitleRef,
        videos: Sequence[Union[str, Path]],
        dest_dir: Union[str, Path, None] = None,   # None -> beside each video
        save_mode: str = "extract",                # extract | zip
        lang_suffix: Optional[str] = None,
        no_suffix: bool = False,
        on_progress: Optional[ProgressCallback] = None,
    ) -> List[Tuple[Path, Union[DownloadResult, Exception]]]:
        """Download ONE pack archive and extract each video's own episode.

        The bulk-mode workhorse behind :meth:`bulk_download`: the zip is
        fetched exactly once and every video of the group gets its matching
        member extracted and renamed after the video itself (strict pack
        semantics - a video whose episode the archive does not carry yields
        an exception for that video instead of wrong files, so the caller
        can fall back to the per-video best).  A failure to fetch the
        archive itself raises.

        Returns ``(video, result_or_exception)`` pairs aligned with
        *videos*.  ``save_mode='zip'`` writes the archive once (into
        *dest_dir*, or the first video's folder) and every row references
        it; ``dest_dir=None`` extracts beside each video.
        """
        if isinstance(target, SubtitleDetails):
            version = target.version
        elif isinstance(target, SubtitleVersion):
            version = target
        else:
            slug, lang, sub_id = self._parse_ref(target)
            version = SubtitleVersion(slug=slug, lang=lang, sub_id=sub_id)
        video_paths = [Path(v) for v in videos]
        if not video_paths:
            return []
        if not version.author and not version.releases:
            # bare ref: metadata fetch only improves naming; never fatal
            try:
                details = self.get_version(version.slug, version.lang, version.sub_id)
                version = details.version
            except SubZoneError:
                pass

        payload = self._fetch_zip_payload(version, on_progress)
        results: List[Tuple[Path, Union[DownloadResult, Exception]]] = []
        if save_mode == "zip":
            dest = Path(dest_dir) if dest_dir is not None else video_paths[0].parent
            dest.mkdir(parents=True, exist_ok=True)
            zip_path = dest / f"{version.slug}-{version.lang}-{version.sub_id}.zip"
            zip_path.write_bytes(payload)
            for index, video in enumerate(video_paths):
                results.append(
                    (
                        video,
                        DownloadResult(
                            zip_path=str(zip_path),
                            srt_paths=[],
                            version=version,
                            bytes_downloaded=len(payload) if index == 0 else 0,
                            target_video=str(video),
                        ),
                    )
                )
            return results
        suffix = "" if no_suffix else (lang_suffix or _slugify_lang_suffix(version.lang))
        for index, video in enumerate(video_paths):
            try:
                dest = Path(dest_dir) if dest_dir is not None else video.parent
                srts = self._extract_subtitles(
                    payload, dest, version, suffix, target_video=video, pack=True,
                )
                results.append(
                    (
                        video,
                        DownloadResult(
                            zip_path=None,
                            srt_paths=[str(p) for p in srts],
                            version=version,
                            bytes_downloaded=len(payload) if index == 0 else 0,
                            target_video=str(video),
                        ),
                    )
                )
            except Exception as exc:  # noqa: BLE001 - per-video fallback is the caller's job
                results.append((video, exc))
        return results

    def bulk_download(
        self,
        path: Union[str, Path, Sequence[Union[str, Path]]],
        lang: Optional[str] = None,
        dest_dir: Union[str, Path, None] = None,   # None -> beside each video
        save_mode: str = "extract",                # extract | zip
        lang_suffix: Optional[str] = None,
        no_suffix: bool = False,
        prefer_complete: bool = True,
        pick_pack: Optional[
            "Callable[[TitleResult, List[Path], List[Tuple[int, SubtitleVersion]]], Optional[SubtitleVersion]]"
        ] = None,
        min_score: int = 0,
        dry_run: bool = False,
        on_row: Optional["Callable[[Dict[str, Any]], None]"] = None,
    ) -> List[Dict[str, Any]]:
        """Bulk folder mode: one pack per season folder, per-video otherwise.

        Plans *path* (a folder scanned for video files, or an explicit
        video list) with :meth:`plan_bulk`, serves every episode group from
        the covering pack the *pick_pack* callback chooses (the best-ranked
        pack when the callback is ``None``), and downloads each chosen pack
        exactly ONCE - every video still receives its own episode extracted
        and renamed after the video file itself.  A group without a
        covering pack, a video whose episode is missing from the chosen
        archive, and plain movies all fall back to their per-video best
        match.  That fallback is the bulk answer to "no season pack found":
        the folder is still served completely.

        *pick_pack* receives ``(title, videos, ranked_packs)`` and returns
        the :class:`SubtitleVersion` to use, or ``None`` to keep the
        per-video best for that group.  *on_row* is called with each result
        row (same shape as the ``subz auto`` map rows, plus ``mode``) as
        soon as it is ready.  Raises whatever ``find_videos`` raises for an
        unusable *path*; per-video failures stay inside the rows.
        """
        if isinstance(path, (str, Path)):
            from .matcher import find_videos

            video_paths = find_videos(Path(path))
        else:
            video_paths = [Path(v) for v in path]
        if not video_paths:
            raise ValueError(f"no video files found under {path}")
        lang_slug = resolve(lang) or DEFAULT_LANGUAGE
        plan = self.plan_bulk(video_paths, lang=lang_slug)
        rows: List[Dict[str, Any]] = []

        def emit(row: Dict[str, Any]) -> None:
            rows.append(row)
            if on_row is not None:
                try:
                    on_row(row)
                except Exception:  # noqa: BLE001 - UI callbacks must not break the batch
                    pass

        def dest_for(video: Path) -> Path:
            return Path(dest_dir) if dest_dir is not None else video.parent

        def per_video_row(video: Path, group: BulkGroupPlan, mode: str) -> Dict[str, Any]:
            return {
                "video": str(video),
                "title": group.title.name if group.title else None,
                "mode": mode,
                "status": "ok",
            }

        def save_row(video: Path, group: BulkGroupPlan, mode: str, result: DownloadResult) -> Dict[str, Any]:
            return {
                "video": str(video),
                "title": group.title.name if group.title else None,
                "mode": mode,
                "status": "saved",
                "saved": result.srt_paths or ([result.zip_path] if result.zip_path else []),
            }

        def fallback_row(
            video: Path, group: BulkGroupPlan, pack_error: str
        ) -> None:
            """Serve one video the pack archive could not, per-video best."""
            best = group.best_by_video.get(video)
            row = per_video_row(video, group, "pack-fallback")
            if best is None:
                row.update({"status": "error", "error": str(pack_error)})
                emit(row)
                return
            row.update({"ref": best[0].ref, "score": best[1]})
            if best[1] < min_score:
                row.update(
                    {
                        "status": "skipped",
                        "error": f"score {best[1]} is below min-score {min_score}",
                    }
                )
                emit(row)
                return
            try:
                result = self.download(
                    best[0],
                    dest_dir=dest_for(video),
                    save_mode=save_mode,
                    lang_suffix=lang_suffix,
                    no_suffix=no_suffix,
                    target_video=video,
                )
                row.update(save_row(video, group, "pack-fallback", result))
            except Exception as exc:  # noqa: BLE001 - one bad video must not stop the batch
                row.update({"status": "error", "error": f"pack: {pack_error}; per-video: {exc}"})
            emit(row)

        for group in plan.groups:
            if group.title is None or group.error:
                # search failed (title=None) or the title page failed
                # (error set) - either way the group is served per-video
                # only, and the rows carry the planning error
                for video in group.videos:
                    row = per_video_row(video, group, "per-video")
                    row.update({"status": "not found", "error": group.error})
                    emit(row)
                continue
            chosen: Optional[SubtitleVersion] = None
            if prefer_complete:
                # pick_pack is invoked for EVERY group (even without covering
                # packs) so callers can surface the "no pack found - falling
                # back to per-video" notice; returning None keeps the fallback.
                if pick_pack is not None:
                    chosen = pick_pack(group.title, group.videos, group.ranked_packs)
                elif group.ranked_packs:
                    chosen = group.ranked_packs[0][1]
            group.chosen_pack = chosen

            if chosen is not None:
                todo: List[tuple] = []
                for video in group.videos:
                    row = per_video_row(video, group, "complete-pack")
                    score = score_version(video.name, chosen)
                    row.update({"ref": chosen.ref, "score": score})
                    if score is None:
                        row.update(
                            {"status": "not found", "error": "pack does not cover this video"}
                        )
                        emit(row)
                        continue
                    if dry_run:
                        row["status"] = "dry-run"
                        emit(row)
                        continue
                    if score < min_score:
                        row.update(
                            {
                                "status": "skipped",
                                "error": f"score {score} is below min-score {min_score}",
                            }
                        )
                        emit(row)
                        continue
                    todo.append((video, row))
                if not todo:
                    continue
                pack_results = self.download_pack_once(
                    chosen,
                    [v for v, _row in todo],
                    dest_dir=dest_dir,
                    save_mode=save_mode,
                    lang_suffix=lang_suffix,
                    no_suffix=no_suffix,
                )
                for (video, row), (_v, result) in zip(todo, pack_results):
                    if isinstance(result, Exception):
                        fallback_row(video, group, str(result))
                    else:
                        # the todo row already carries ref + score; keep them
                        row.update(
                            {
                                "status": "saved",
                                "saved": result.srt_paths
                                or ([result.zip_path] if result.zip_path else []),
                            }
                        )
                        emit(row)
            else:
                for video in group.videos:
                    row = per_video_row(video, group, "per-video")
                    best = group.best_by_video.get(video)
                    if best is None:
                        row.update({"status": "not found", "error": "no matching subtitle"})
                        emit(row)
                        continue
                    row.update({"ref": best[0].ref, "score": best[1]})
                    if dry_run:
                        row["status"] = "dry-run"
                        emit(row)
                        continue
                    if best[1] < min_score:
                        row.update(
                            {
                                "status": "skipped",
                                "error": f"score {best[1]} is below min-score {min_score}",
                            }
                        )
                        emit(row)
                        continue
                    try:
                        result = self.download(
                            best[0],
                            dest_dir=dest_for(video),
                            save_mode=save_mode,
                            lang_suffix=lang_suffix,
                            no_suffix=no_suffix,
                            target_video=video,
                        )
                        row.update(save_row(video, group, "per-video", result))
                    except Exception as exc:  # noqa: BLE001 - one bad video must not stop the batch
                        row.update({"status": "error", "error": str(exc)})
                    emit(row)
        return rows

    # ------------------------------------------------------------------
    # downloading
    # ------------------------------------------------------------------

    def _fetch_zip_payload(
        self,
        version: SubtitleVersion,
        on_progress: Optional[ProgressCallback] = None,
    ) -> bytes:
        """Stream one subtitle zip through the signed CDN link.

        A 404 on the redirect target means the file is gone from the CDN
        even though the site still issues the link; a stale upload comes
        back as an S3 "NoSuchKey" XML body with HTTP 200.  Both surface as
        :class:`DownloadError` telling the user to try another version.
        """
        response = self._get(
            f"/subtitles/{version.slug}/{version.lang}/{version.sub_id}/download",
            stream=True,
            referer=f"/subtitles/{version.slug}/{version.lang}/{version.sub_id}",
            not_found_ok=True,
        )
        if response is None:
            raise DownloadError(
                "subtitle file missing on the site's CDN (HTTP 404); "
                "try another version"
            )
        total = response.headers.get("Content-Length")
        total_bytes = int(total) if total and total.isdigit() else None

        chunks: List[bytes] = []
        received = 0
        try:
            for chunk in response.iter_content(chunk_size=65536):
                if chunk:
                    chunks.append(chunk)
                    received += len(chunk)
                    if on_progress:
                        on_progress(received, total_bytes)
        except requests.RequestException as exc:
            raise NetworkError(f"download interrupted: {exc}") from exc
        payload = b"".join(chunks)

        if not payload.startswith(b"PK"):
            snippet = payload[:200].decode("utf-8", "replace")
            if "NoSuchKey" in snippet or "<Error>" in snippet:
                raise DownloadError(
                    "subtitle file missing on the site's CDN (stale upload); "
                    "try another version"
                )
            raise DownloadError(
                f"expected a zip archive, got {len(payload)} bytes of "
                f"unexpected content: {snippet[:80]!r}"
            )
        return payload

    def download(
        self,
        target: SubtitleRef,
        dest_dir: Union[str, Path] = ".",
        save_mode: str = "extract",           # extract | zip
        lang_suffix: Optional[str] = None,    # None -> auto ("fa" ...)
        no_suffix: bool = False,
        target_video: Optional[Union[str, Path]] = None,
        base_name: Optional[str] = None,      # rename output (overrides other naming)
        on_progress: Optional[ProgressCallback] = None,
        pack: bool = False,                   # archive is a pack planned by prefer-complete
    ) -> DownloadResult:
        """Download one subtitle.

        *target* may be a :class:`SubtitleVersion`, a :class:`SubtitleDetails`
        or a reference ``"<slug>/<lang>/<id>"``.  ``save_mode='extract'``
        unpacks the subtitle files (named after *target_video* when given),
        ``save_mode='zip'`` keeps the original archive.  *base_name* renames
        the output regardless: files become ``<base_name>.<suffix>.srt``
        (or ``<base_name>.zip``); path parts are stripped for safety.

        ``pack=True`` marks a download that prefer-complete planned onto a
        covering pack: when the video pins an episode and no archive member
        carries that episode's tag, a :class:`DownloadError` is raised
        instead of the old fall-back of writing every member renamed after
        the video (which turned one 9-episode pack into 9 wrong files per
        video).  Regular downloads keep that fall-back for multi-file
        archives (CD variants, SDH/forced tracks).
        """
        if isinstance(target, SubtitleDetails):
            version = target.version
        elif isinstance(target, SubtitleVersion):
            version = target
        else:
            slug, lang, sub_id = self._parse_ref(target)
            version = SubtitleVersion(slug=slug, lang=lang, sub_id=sub_id)

        if not version.author and not version.releases:
            # version was built from a bare ref: fetch metadata for nicer
            # output; failures must never break the download itself
            try:
                details = self.get_version(version.slug, version.lang, version.sub_id)
                version = details.version
            except SubZoneError:
                pass

        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)

        if base_name:
            zip_name = f"{_sanitize_base_name(base_name)}.zip"
        elif target_video is not None:
            zip_name = f"{Path(target_video).with_suffix('').name}.zip"
        else:
            zip_name = f"{version.slug}-{version.lang}-{version.sub_id}.zip"
        zip_path = dest / zip_name
        # A 404 here can come from the signed CDN link itself (the site still
        # issues the redirect, but the file is gone) - _fetch_zip_payload
        # surfaces it as the same "stale upload" problem users already know
        # from the NoSuchKey case.
        payload = self._fetch_zip_payload(version, on_progress)

        if save_mode == "zip":
            zip_path.write_bytes(payload)
            return DownloadResult(
                zip_path=str(zip_path),
                srt_paths=[],
                version=version,
                bytes_downloaded=len(payload),
                target_video=str(target_video) if target_video else None,
            )

        suffix = ""
        if not no_suffix:
            suffix = lang_suffix or _slugify_lang_suffix(version.lang)
        srts = self._extract_subtitles(
            payload, dest, version, suffix, target_video,
            base_name=base_name, pack=pack,
        )
        return DownloadResult(
            zip_path=None,
            srt_paths=[str(p) for p in srts],
            version=version,
            bytes_downloaded=len(payload),
            target_video=str(target_video) if target_video else None,
        )

    def download_url(
        self,
        url: str,
        lang: Optional[str] = DEFAULT_LANGUAGE,
        limit: int = 1,
        **download_kwargs,
    ) -> List[DownloadResult]:
        """'URL grab': download from any subzone.ir subtitles URL.

        * version URL (``/subtitles/<slug>/<lang>/<id>``) - exactly one file
        * title URL (``/subtitles/<slug>`` or ``/subtitles/<slug>/<lang>``)
          - the newest *limit* versions in *lang* (``limit=0`` grabs every
          version of that language)

        When several versions are grabbed, one with a dead CDN link (stale
        upload) is skipped with a logged warning instead of aborting the
        grab; the first error is re-raised only when every version failed.
        """
        slug, url_lang, sub_id = parse_subtitles_url(url)
        if lang and sub_id is None:
            lang_slug = resolve(lang) or DEFAULT_LANGUAGE
        elif sub_id is None and url_lang:
            lang_slug = resolve(url_lang)
        else:
            lang_slug = url_lang

        if sub_id:
            version = SubtitleVersion(slug=slug, lang=url_lang, sub_id=sub_id)
            return [self.download(version, **download_kwargs)]

        versions = self.title_versions(slug, lang_slug)
        if limit and limit > 0:
            versions = versions[:limit]
        if not versions:
            raise NoSubtitleFoundError(
                f"no subtitles for {slug!r} in {display_name(lang_slug or '')}"
            )
        results: List[DownloadResult] = []
        first_error: Optional[DownloadError] = None
        for version in versions:
            try:
                results.append(self.download(version, **download_kwargs))
            except DownloadError as exc:
                # dead CDN link / stale upload: keep grabbing the rest
                logger.warning("skipping %s: %s", version.ref, exc)
                if first_error is None:
                    first_error = exc
        if not results:
            if first_error is not None:
                raise first_error
            raise NoSubtitleFoundError(
                f"no subtitles for {slug!r} in {display_name(lang_slug or '')}"
            )
        return results

    def grab_url(self, url: str, **download_kwargs) -> List[DownloadResult]:
        """Alias of :meth:`download_url` (URL grab command)."""
        return self.download_url(url, **download_kwargs)

    def download_many(
        self,
        targets: Sequence[SubtitleRef],
        dest_dir: Union[str, Path] = ".",
        save_mode: str = "extract",
        lang_suffix: Optional[str] = None,
        no_suffix: bool = False,
        base_name: Optional[str] = None,
        max_workers: int = 1,
        on_progress: Optional[ProgressCallback] = None,
        on_result: Optional["Callable[[int, Union[DownloadResult, Exception]], None]"] = None,
    ) -> List[Union[DownloadResult, Exception]]:
        """Download several subtitles as one batch.

        *targets* is an iterable of refs / versions / details, exactly like
        :meth:`download` accepts.  Returns a list aligned with *targets*
        holding a :class:`DownloadResult` per success and an
        :class:`Exception` instance per failure - one bad ref never aborts
        the rest of the batch.

        ``max_workers=1`` (default) downloads sequentially; values above 1
        download concurrently in a thread pool (capped to the number of
        targets).  *on_progress* is forwarded to every item; *on_result*
        is called with ``(index, result_or_exception)`` from worker threads
        as items complete, so callers can update UIs/progress incrementally.
        """
        target_list = list(targets)
        if max_workers < 1:
            raise ValueError("max_workers must be >= 1")
        workers = min(max_workers, len(target_list)) or 1
        download_kwargs = dict(
            dest_dir=dest_dir,
            save_mode=save_mode,
            lang_suffix=lang_suffix,
            no_suffix=no_suffix,
            base_name=base_name,
            on_progress=on_progress,
        )
        results: List[Union[DownloadResult, Exception]] = [None] * len(target_list)  # type: ignore[list-item]

        def _one(index: int, target: SubtitleRef) -> None:
            try:
                results[index] = self.download(target, **download_kwargs)
            except Exception as exc:  # noqa: BLE001 - batch keeps per-item errors
                results[index] = exc
            if on_result is not None:
                try:
                    on_result(index, results[index])
                except Exception:  # noqa: BLE001 - user callbacks must not break the batch
                    pass

        if workers == 1:
            for index, target in enumerate(target_list):
                _one(index, target)
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                list(pool.map(_one, range(len(target_list)), target_list))
        return results

    def browse(
        self,
        mode: str = "popular",        # popular | latest
        kind: str = "all",            # all | film | series | music
        page: int = 1,
        lang: Optional[str] = None,
    ) -> List["BrowseEntry"]:
        """List subtitle uploads from the /browse pages."""
        from .models import BrowseEntry  # local import avoids cycle at module load
        from .parsing import parse_browse

        mode = mode.lower()
        kind = kind.lower()
        if mode not in ("popular", "latest"):
            raise ValueError(f"mode must be popular|latest, got {mode!r}")
        if kind not in ("all", "film", "series", "music"):
            raise ValueError(f"kind must be all|film|series|music, got {kind!r}")
        if page < 1:
            raise ValueError("page must be >= 1")
        response = self._get(f"/browse/{mode}/{kind}/{page}")
        entries = parse_browse(response.text)
        lang_slug = resolve(lang)
        if lang_slug:
            wanted = display_name(lang_slug)
            entries = [
                e for e in entries
                if e.version.lang == lang_slug
                or wanted.lower() in (e.version.language or "").lower()
            ]
        return entries

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_ref(ref: str) -> Tuple[str, str, str]:
        parts = str(ref).strip().strip("/").split("/")
        if len(parts) != 3:
            raise InvalidURLError(
                "reference must look like '<slug>/<lang>/<id>' "
                "(e.g. dexter-resurrection-season-2/farsi_persian/3770171)"
            )
        slug, lang, sub_id = parts
        if not sub_id.isdigit():
            raise InvalidURLError(f"subtitle id must be numeric, got {sub_id!r}")
        return slug, lang, sub_id

    @staticmethod
    def _select_episode_members(
        members: List[str],
        target_video: Optional[Union[str, Path]],
        pack: bool = False,
    ) -> List[str]:
        """Keep only the pack members that carry the video's own episode.

        Auto-match can hand a multi-episode pack (``S02E01-E03``) to a single
        episode video; extracting every member would name them all after the
        video (``<stem>.en.2.srt`` holding episode 2's lines).  When the
        video pins an episode (``SxxEyy``) and member names carry their own
        episode tags, only matching members are written.  Anything ambiguous
        - video without a pinned episode, untagged member names, no match -
        keeps the previous behaviour (all members, numbered) for regular
        downloads.

        ``pack=True`` (prefer-complete planned a whole folder onto one
        archive) is stricter: there the video's episode is explicitly
        pinned, so writing every member renamed after the video can only
        produce wrong files (one 9-episode pack once unpacked as 81 files).
        When no member matches, a :class:`DownloadError` is raised instead
        so the video shows up as a failed row and another pack (or the
        per-video best) can take over.
        """
        if target_video is None or len(members) <= 1:
            return members
        v_info = season_info(Path(target_video).name)
        if v_info.episodes is None:
            return members
        matched: List[str] = []
        for member in members:
            m_info = season_info(Path(member).name)
            if m_info.episodes is None:
                continue
            if (
                v_info.season is not None
                and m_info.season is not None
                and m_info.season != v_info.season
            ):
                continue
            if (
                m_info.episodes[0] <= v_info.episodes[1]
                and v_info.episodes[0] <= m_info.episodes[1]
            ):
                matched.append(member)
        if pack and not matched:
            first, last = v_info.episodes
            span = f"episode {first}" if first == last else f"episodes {first}-{last}"
            raise DownloadError(
                f"the pack archive holds {len(members)} subtitle file(s) but "
                f"none is tagged with {span} for {Path(target_video).name!r}; "
                "pick a different pack or keep the per-video best"
            )
        return matched if matched and len(matched) < len(members) else members

    def _extract_subtitles(
        self,
        payload: bytes,
        dest: Path,
        version: SubtitleVersion,
        suffix: str,
        target_video: Optional[Union[str, Path]],
        base_name: Optional[str] = None,
        pack: bool = False,
    ) -> List[Path]:
        try:
            archive = zipfile.ZipFile(io.BytesIO(payload))
        except zipfile.BadZipFile as exc:
            raise DownloadError(f"broken zip archive: {exc}") from exc

        members = [
            name
            for name in archive.namelist()
            if Path(name).suffix.lower() in SUBTITLE_EXTS and not name.endswith("/")
        ]
        if not members:
            raise DownloadError(
                "zip archive contains no subtitle files: "
                f"{', '.join(archive.namelist()[:6]) or 'empty archive'}"
            )

        members = self._select_episode_members(sorted(members), target_video, pack=pack)

        if base_name:
            base = _sanitize_base_name(base_name)
        elif target_video is not None:
            # Only the file NAME comes from the video; the folder is always
            # dest_dir (this is what makes ``subz auto -o DIR`` work).
            base = Path(target_video).with_suffix("").name
        else:
            base = f"{version.slug}-{version.lang}-{version.sub_id}"
        out_dir = dest
        out_dir.mkdir(parents=True, exist_ok=True)

        written: List[Path] = []
        for index, member in enumerate(members, start=1):
            ext = Path(member).suffix.lower()
            if len(members) == 1:
                stem = f"{base}.{suffix}" if suffix else str(base)
            else:
                stem = f"{base}.{suffix}.{index}" if suffix else f"{base}.{index}"
            out_path = out_dir / f"{stem}{ext}"
            out_path.write_bytes(archive.read(member))
            written.append(out_path)
        return written

    def close(self) -> None:
        self.clear_cache()
        self.session.close()

    def __enter__(self) -> "SubZoneClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
