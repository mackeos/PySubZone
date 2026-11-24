"""Async convenience wrapper around :class:`~.client.SubZoneClient`.

The scraping logic (parsing, retrying, caching, rate limiting) lives in the
synchronous client.  :class:`AsyncSubZoneClient` reuses every bit of it by
offloading each blocking call to a worker thread with
:func:`asyncio.to_thread`, so calling it never blocks the event loop while
still honoring the client's rate limit and cache - there is no duplicated
site logic to drift out of sync.

Example::

    import asyncio
    from pysubzone import AsyncSubZoneClient

    async def main():
        async with AsyncSubZoneClient(min_request_interval=0.5) as client:
            results = await client.search("dexter")
            page = await client.title(results[0].slug)
            versions = await client.title_versions(results[0].slug, "fa")
            await client.download(versions[0], dest_dir="./subs")

    asyncio.run(main())

Every method mirrors its synchronous counterpart one-to-one and accepts the
same arguments.  ``await client.download_many(...)`` fires its
``on_result`` callback from worker threads, just like the sync version.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple, Union

from .client import (
    DEFAULT_BASE_URL,
    DEFAULT_USER_AGENT,
    ProgressCallback,
    SubtitleRef,
    SubZoneClient,
)
from .models import (
    BrowseEntry,
    DownloadResult,
    SubtitleDetails,
    SubtitleVersion,
    TitlePage,
    TitleResult,
)
from .languages import DEFAULT_LANGUAGE
from .scoring import ScoreWeights


class AsyncSubZoneClient:
    """Async facade over :class:`SubZoneClient` (thread-offloading)."""

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
        self._client = SubZoneClient(
            base_url=base_url,
            user_agent=user_agent,
            timeout=timeout,
            max_retries=max_retries,
            verbose=verbose,
            min_request_interval=min_request_interval,
            cache_ttl=cache_ttl,
            cache_size=cache_size,
        )

    @property
    def sync(self) -> SubZoneClient:
        """The underlying synchronous client (rate limit + cache live here)."""
        return self._client

    # -- pages -------------------------------------------------------------

    async def search(self, query: str, lang: Optional[str] = None) -> List[TitleResult]:
        return await asyncio.to_thread(self._client.search, query, lang)

    async def search_exact(self, query: str, lang: Optional[str] = None) -> TitleResult:
        return await asyncio.to_thread(self._client.search_exact, query, lang)

    async def title(self, slug: str) -> TitlePage:
        return await asyncio.to_thread(self._client.title, slug)

    async def title_versions(
        self, slug: str, lang: Optional[str] = None
    ) -> List[SubtitleVersion]:
        return await asyncio.to_thread(self._client.title_versions, slug, lang)

    async def get_version(self, slug: str, lang: str, sub_id: str) -> SubtitleDetails:
        return await asyncio.to_thread(self._client.get_version, slug, lang, sub_id)

    async def get_version_from_url(self, url: str) -> SubtitleDetails:
        return await asyncio.to_thread(self._client.get_version_from_url, url)

    # -- matching ----------------------------------------------------------

    async def best_for_file(
        self,
        filename_stem: str,
        lang: Optional[str] = DEFAULT_LANGUAGE,
        title_query: Optional[str] = None,
        weights: Optional[ScoreWeights] = None,
    ) -> Tuple[TitleResult, SubtitleVersion, int]:
        return await asyncio.to_thread(
            self._client.best_for_file, filename_stem, lang, title_query, weights
        )

    async def auto_match(
        self,
        video_stem: str,
        lang: Optional[str] = None,
    ) -> Tuple[TitleResult, SubtitleVersion, int]:
        return await asyncio.to_thread(self._client.auto_match, video_stem, lang)

    # -- downloading -------------------------------------------------------

    async def download(
        self,
        target: SubtitleRef,
        dest_dir: Union[str, Path] = ".",
        save_mode: str = "extract",
        lang_suffix: Optional[str] = None,
        no_suffix: bool = False,
        target_video: Optional[Union[str, Path]] = None,
        base_name: Optional[str] = None,
        on_progress: Optional[ProgressCallback] = None,
        pack: bool = False,
    ) -> DownloadResult:
        return await asyncio.to_thread(
            self._client.download,
            target,
            dest_dir,
            save_mode,
            lang_suffix,
            no_suffix,
            target_video,
            base_name,
            on_progress,
            pack=pack,
        )

    async def download_url(
        self,
        url: str,
        lang: Optional[str] = None,
        limit: int = 1,
        **download_kwargs,
    ) -> List[DownloadResult]:
        return await asyncio.to_thread(
            self._client.download_url, url, lang, limit, **download_kwargs
        )

    async def grab_url(self, url: str, **download_kwargs) -> List[DownloadResult]:
        return await asyncio.to_thread(self._client.grab_url, url, **download_kwargs)

    async def download_many(
        self,
        targets: Sequence[SubtitleRef],
        dest_dir: Union[str, Path] = ".",
        save_mode: str = "extract",
        lang_suffix: Optional[str] = None,
        no_suffix: bool = False,
        base_name: Optional[str] = None,
        max_workers: int = 1,
        on_progress: Optional[ProgressCallback] = None,
        on_result: Optional[Callable] = None,
    ) -> List[Union[DownloadResult, Exception]]:
        return await asyncio.to_thread(
            self._client.download_many,
            targets,
            dest_dir,
            save_mode,
            lang_suffix,
            no_suffix,
            base_name,
            max_workers,
            on_progress,
            on_result,
        )

    # -- browse ------------------------------------------------------------

    async def browse(
        self,
        mode: str = "popular",
        kind: str = "all",
        page: int = 1,
        lang: Optional[str] = None,
    ) -> List[BrowseEntry]:
        return await asyncio.to_thread(self._client.browse, mode, kind, page, lang)

    # -- lifecycle ---------------------------------------------------------

    async def clear_cache(self) -> None:
        await asyncio.to_thread(self._client.clear_cache)

    async def close(self) -> None:
        await asyncio.to_thread(self._client.close)

    async def __aenter__(self) -> "AsyncSubZoneClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()
