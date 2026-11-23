"""HTML parsing for the Subf2m engine running on subzone.ir.

The site templates are stable (they are shared with subf2m.co), so parsers
are written against the actual markup:

* search page  : ``div.byTitle`` > ``div.search-result`` > ``h2`` + ``li``
                 (``div.title > a`` = name/year link, ``div.subtle.count`` = count)
* title page   : ``div.header > h2`` = name + Imdb link, ``li > strong`` "Year:",
                 ``ul.tabbar`` = language tabs, ``li.item`` = one subtitle upload
                 (``span.language``, ``span.rate``, ``ul.scrolllist > li`` releases,
                 ``div.comment-col`` author/comment, ``a.download`` version link)
* version page : ``h1 > span[itemprop=name]``, ``li.release > div`` releases,
                 ``li.author`` commentary, ``a#downloadButton``, ``div.details ul li``
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag

from .models import BrowseEntry, SubtitleDetails, SubtitleVersion, TitlePage, TitleResult

_TITLE_URL_RE = re.compile(r"^/subtitles/([A-Za-z0-9_\-]+)(?:/([a-z_\-]+))?/?$")
_VERSION_URL_RE = re.compile(r"^/subtitles/([A-Za-z0-9_\-]+)/([a-z_\-]+)/(\d+)/?(?:download)?/?$")
_YEAR_RE = re.compile(r"\((\d{4})\)\s*$")
_COUNT_RE = re.compile(r"(\d[\d\s]*)")

SECTION_ALIASES = {
    "exact": "exact",
    "tv-series": "tv_series",
    "tv series": "tv_series",
    "close": "close",
    "popular": "popular",
}


def make_soup(html: str) -> BeautifulSoup:
    """Parse HTML with lxml, falling back to html.parser."""
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:  # lxml missing/broken at runtime
        return BeautifulSoup(html, "html.parser")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


# --------------------------------------------------------------------------
# URLs
# --------------------------------------------------------------------------

def parse_subtitles_url(url: str) -> Tuple[str, Optional[str], Optional[str]]:
    """Classify a subzone.ir URL.

    Returns ``(slug, lang, sub_id)``:

    * ``/subtitles/<slug>``                -> ``(slug, None, None)``
    * ``/subtitles/<slug>/<lang>``         -> ``(slug, lang, None)``
    * ``/subtitles/<slug>/<lang>/<id>``    -> ``(slug, lang, id)``
      (an optional trailing ``/download`` is accepted)

    Raises ValueError for anything else.
    """
    path = urlparse(url).path.rstrip("/") if "://" in url else url.rstrip("/")
    version = _VERSION_URL_RE.match(path)
    if version:
        slug, lang, sub_id = version.groups()
        return slug, lang, sub_id
    title = _TITLE_URL_RE.match(path)
    if title:
        return title.group(1), title.group(2), None
    raise ValueError(f"Not a subzone.ir subtitles URL: {url!r}")


# --------------------------------------------------------------------------
# search page
# --------------------------------------------------------------------------

def parse_search(html: str) -> List[TitleResult]:
    """Parse ``/subtitles/searchbytitle`` into a flat list of TitleResult."""
    soup = make_soup(html)
    results: List[TitleResult] = []
    container = soup.select_one("div.byTitle") or soup
    sections = container.select("div.search-result")
    blocks = sections if sections else [container]
    for block in blocks:
        heading = block.find("h2")
        section = SECTION_ALIASES.get(
            (heading.get_text(" ").strip().lower() if heading else ""), "exact"
        )
        for li in block.find_all("li"):
            link = li.find("a", href=True)
            if not link:
                continue
            href = link["href"].strip()
            m = _TITLE_URL_RE.match(href)
            if not m:
                continue
            slug = m.group(1)
            raw_name = _clean(link.get_text(" "))
            year = None
            ym = _YEAR_RE.search(raw_name)
            if ym:
                year = int(ym.group(1))
                raw_name = raw_name[: ym.start()].strip()
            count = None
            count_tag = li.find(class_="count") or li.find("div", class_="subtle")
            if count_tag:
                cm = _COUNT_RE.search(count_tag.get_text(" "))
                if cm:
                    count = int(cm.group(1).replace(" ", ""))
            results.append(
                TitleResult(
                    name=raw_name,
                    slug=slug,
                    year=year,
                    subtitle_count=count,
                    section=section,
                    url=f"/subtitles/{slug}",
                )
            )
    return results


# --------------------------------------------------------------------------
# title page
# --------------------------------------------------------------------------

def parse_title(html: str, slug: str) -> TitlePage:
    """Parse ``/subtitles/<slug>`` (optionally ``/<lang>``) into a TitlePage."""
    soup = make_soup(html)
    page = TitlePage(slug=slug, name=slug)

    header = soup.select_one("div.header")
    if header:
        h2 = header.find("h2")
        if h2:
            name = ""
            for child in h2.contents:
                if isinstance(child, Tag):
                    if child.name == "a" and "imdb" in (child.get("class") or []):
                        page.imdb_id = (child.get("href") or "").rstrip("/").split("/")[-1]
                else:
                    name += str(child)
            page.name = _clean(re.sub(r"<[^>]+>", "", name)) or slug
        for li in header.find_all("li"):
            strong = li.find("strong")
            if strong and "year" in strong.get_text(" ").lower():
                year_text = _clean(li.get_text(" ").replace("Year:", ""))
                m = re.search(r"(\d{4})", year_text)
                if m:
                    page.year = int(m.group(1))

    # language tabs
    for a in soup.select("ul.tabbar a[href]"):
        href = a["href"].strip()
        m = re.match(r"^/subtitles/[A-Za-z0-9_\-]+/([a-z_\-]+)/?$", href)
        if not m:
            continue
        count_tag = a.find(class_="count")
        count = 0
        if count_tag:
            cm = _COUNT_RE.search(count_tag.get_text())
            if cm:
                count = int(cm.group(1).replace(" ", ""))
        page.languages.append({"slug": m.group(1), "count": count, "url": href})

    # subtitle items
    for li in soup.select("li.item"):
        version = _parse_item(li, slug)
        if version:
            page.versions.append(version)

    return page


def _parse_item(li: Tag, slug: str) -> Optional[SubtitleVersion]:
    link = li.find("a", class_="download", href=True)
    if not link:
        # some templates use "icon-download" alone
        link = li.find("a", href=re.compile(r"^/subtitles/.+/\d+$"))
    if not link:
        return None
    m = _VERSION_URL_RE.match(link["href"].strip())
    if not m:
        return None
    v_slug, lang, sub_id = m.groups()

    lang_span = li.find(class_="language")
    language = _clean(lang_span.get_text(" ")) if lang_span else ""

    rate = ""
    rate_span = li.find(class_="rate")
    if rate_span:
        classes = " ".join(rate_span.get("class") or [])
        for candidate in ("positive", "negative", "good", "bad", "not rated"):
            if candidate in classes:
                rate = {"not rated": "neutral", "good": "positive", "bad": "negative"}.get(
                    candidate, candidate
                )
                break

    releases = [
        _clean(el.get_text(" "))
        for el in li.select("ul.scrolllist li")
        if _clean(el.get_text(" "))
    ]

    author, comment = "", ""
    comment_col = li.find(class_="comment-col")
    if comment_col:
        b_tag = comment_col.find("b")
        if b_tag:
            b_text = _clean(b_tag.get_text(" "))
            author = re.sub(r"^By\s*", "", b_text, flags=re.I)
        p_tag = comment_col.find("p")
        if p_tag:
            comment = _clean(p_tag.get_text(" "))

    return SubtitleVersion(
        slug=v_slug,
        lang=lang,
        sub_id=sub_id,
        language=language,
        rate=rate,
        releases=releases,
        author=author,
        comment=comment,
    )


# --------------------------------------------------------------------------
# version detail page
# --------------------------------------------------------------------------

def parse_subtitle_page(html: str, version: SubtitleVersion) -> SubtitleDetails:
    """Parse ``/subtitles/<slug>/<lang>/<id>`` into SubtitleDetails."""
    soup = make_soup(html)
    details = SubtitleDetails(version=version, releases=list(version.releases))

    h1 = soup.find("h1")
    if h1:
        span = h1.find(itemprop="name")
        if span:
            details.title_name = _clean(span.get_text(" "))

    for li in soup.select("li.release"):
        divs = [d for d in li.find_all("div") if _clean(d.get_text(" "))]
        if divs:
            details.releases = [_clean(d.get_text(" ")) for d in divs]
            break

    author_li = soup.find("li", class_="author")
    if author_li and not version.author:
        a = author_li.find("a", href=re.compile(r"^/u/"))
        if a:
            version.author = _clean(a.get_text(" "))
        if not version.comment:
            text = _clean(author_li.get_text(" "))
            text = re.sub(r"^A commentary by\s*", "", text, flags=re.I)
            text = re.sub(r"\s*Combined subtitle rating.*$", "", text)
            version.comment = text

    dl_button = soup.find(id="downloadButton")
    if dl_button and dl_button.get("href"):
        m = _VERSION_URL_RE.match(str(dl_button["href"]).strip())
        if m:
            version.slug, version.lang, version.sub_id = m.groups()

    detail_box = soup.select_one("div.details")
    if detail_box:
        for li in detail_box.find_all("li"):
            strong = li.find("strong")
            if not strong:
                continue
            label = _clean(strong.get_text(" ")).rstrip(":").lower()
            value = _clean(li.get_text(" ").replace(strong.get_text(" "), "", 1))
            if label == "online":
                details.online = value.split()[0] if value else ""
            elif label == "hearing impaired":
                details.hearing_impaired = bool(value)
            elif label == "foreign parts":
                details.foreign_parts = bool(value)
            elif label == "framerate":
                details.framerate = value
            elif label == "files":
                details.files = value
            elif label == "production type":
                details.production_type = value
            elif label == "release type":
                details.release_type = value
            elif label == "downloads":
                dm = _COUNT_RE.search(value)
                if dm:
                    details.downloads = int(dm.group(1).replace(" ", ""))

    return details


# --------------------------------------------------------------------------
# browse pages (latest / popular)
# --------------------------------------------------------------------------

def parse_browse(html: str) -> List[BrowseEntry]:
    """Parse ``/browse/{latest,popular}/{all,film,series,music}/<page>``."""
    soup = make_soup(html)
    entries: List[BrowseEntry] = []
    for tr in soup.select("tbody tr"):
        link = tr.find("a", href=_VERSION_URL_RE)
        if not link:
            continue
        m = _VERSION_URL_RE.match(link["href"].strip())
        if not m:
            continue
        slug, lang, sub_id = m.groups()

        language = ""
        title_name = ""
        lang_span = link.find(class_="l")
        if lang_span:
            language = _clean(lang_span.get_text(" "))
        title_span = link.find(class_="new")
        if title_span:
            title_name = _clean(title_span.get_text(" "))

        cells = tr.find_all("td")
        media_type = _clean(cells[1].get_text(" ")) if len(cells) > 1 else ""
        hi = bool(_clean(cells[2].get_text(" "))) if len(cells) > 2 else False
        owner = ""
        if len(cells) > 3:
            owner_a = cells[3].find("a")
            owner = _clean(owner_a.get_text(" ")) if owner_a else _clean(cells[3].get_text(" "))
        date = _clean(cells[4].get_text(" ")) if len(cells) > 4 else ""
        downloads = None
        if len(cells) > 5:
            dm = _COUNT_RE.search(_clean(cells[5].get_text(" ")))
            if dm:
                downloads = int(dm.group(1).replace(" ", ""))

        entries.append(
            BrowseEntry(
                title_name=title_name,
                version=SubtitleVersion(
                    slug=slug,
                    lang=lang,
                    sub_id=sub_id,
                    language=language,
                ),
                media_type=media_type.lower(),
                hearing_impaired=hi,
                owner=owner,
                date=date,
                downloads=downloads,
            )
        )
    return entries
