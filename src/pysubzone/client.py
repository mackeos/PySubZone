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
from typing import Callable, List, Optional, Sequence, Tuple, Union

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
from .matcher import guess_title, guess_season_episode
from .models import DownloadResult, SubtitleDetails, SubtitleVersion, TitlePage, TitleResult
from .parsing import parse_search, parse_subtitles_url, parse_subtitle_page, parse_title
from .scoring import ScoreWeights, pick_best

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

    def search_exact(self, query: str, lang: Optional[str] = None) -> TitleResult:
        """First exact match, falling back to the closest fuzzy match."""
        results = self.search(query, lang=lang)
        normalized = re.sub(r"[^a-z0-9]+", "", query.lower())
        for section in ("exact", "tv_series"):
            for result in results:
                if result.section != section:
                    continue
                cand = re.sub(r"[^a-z0-9]+", "", result.name.lower())
                if cand == normalized:
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
    ) -> Tuple[TitleResult, SubtitleVersion, int]:
        """Find the best subtitle version for a video release name.

        ``filename_stem`` is the video file name without extension.  The
        title is resolved via site search (query derived from the file name
        unless *title_query* is given), then versions are scored by release
        similarity (episode numbers must agree).  *weights* optionally
        tunes the scoring via :class:`~.scoring.ScoreWeights`.
        """
        query = title_query or guess_title(filename_stem)
        if not query:
            raise TitleNotFoundError(f"cannot derive a title from {filename_stem!r}")
        title = self.search_exact(query, lang=lang)
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

    # ------------------------------------------------------------------
    # downloading
    # ------------------------------------------------------------------

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
    ) -> DownloadResult:
        """Download one subtitle.

        *target* may be a :class:`SubtitleVersion`, a :class:`SubtitleDetails`
        or a reference ``"<slug>/<lang>/<id>"``.  ``save_mode='extract'``
        unpacks the subtitle files (named after *target_video* when given),
        ``save_mode='zip'`` keeps the original archive.  *base_name* renames
        the output regardless: files become ``<base_name>.<suffix>.srt``
        (or ``<base_name>.zip``); path parts are stripped for safety.
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
        response = self._get(
            f"/subtitles/{version.slug}/{version.lang}/{version.sub_id}/download",
            stream=True,
            referer=f"/subtitles/{version.slug}/{version.lang}/{version.sub_id}",
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
            payload, dest, version, suffix, target_video, base_name=base_name
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
        * title URL (``/subtitles/<slug>``) - the newest *limit* versions in
          *lang* (``limit=0`` grabs every version of that language)
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
        return [self.download(v, **download_kwargs) for v in versions]

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

    def _extract_subtitles(
        self,
        payload: bytes,
        dest: Path,
        version: SubtitleVersion,
        suffix: str,
        target_video: Optional[Union[str, Path]],
        base_name: Optional[str] = None,
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
        for index, member in enumerate(sorted(members), start=1):
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
