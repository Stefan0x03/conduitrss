"""Live HTTP fetch and parse via feedparser."""

import asyncio
import logging
from typing import TypedDict

import feedparser
import trafilatura
from feedparser.exceptions import CharacterEncodingOverride

logger = logging.getLogger(__name__)


class FeedItem(TypedDict):
    title: str
    link: str
    published: str
    summary: str


class AggregatedFeedItem(TypedDict):
    title: str
    link: str
    published: str
    feed_url: str


class ArticleContent(TypedDict):
    url: str
    title: str  # empty string if not extractable
    author: str  # empty string if not extractable
    published: str  # empty string if not extractable
    content: str  # empty string on error
    truncated: bool
    error: str  # empty string on success


MAX_CONTENT_CHARS = 100_000


def _str_field(entry: object, *attrs: str) -> str:
    """Return the first non-empty string attribute from entry, or empty string."""
    for attr in attrs:
        val: object = getattr(entry, attr, None)
        if isinstance(val, str) and val:
            return val
    return ""


def _summary_field(entry: object) -> str:
    """Return summary, falling back to content[0].value, then empty string."""
    val: object = getattr(entry, "summary", None)
    if isinstance(val, str) and val:
        return val
    # Atom content fallback
    content: object = getattr(entry, "content", None)
    if not isinstance(content, list) or len(content) == 0:
        return ""
    first: object = content[0]
    cv: object = getattr(first, "value", None)
    return cv if isinstance(cv, str) else ""


def _normalize_entry(entry: object) -> FeedItem:
    """Normalize a feedparser entry into a FeedItem."""
    return FeedItem(
        title=_str_field(entry, "title"),
        link=_str_field(entry, "link"),
        published=_str_field(entry, "published", "updated"),
        summary=_summary_field(entry),
    )


def _is_malformed(parsed: object) -> bool:
    """Return True if parsed has a fatal bozo exception.

    CharacterEncodingOverride is not treated as fatal — it indicates a minor
    encoding mismatch on an otherwise valid feed.
    """
    bozo: object = getattr(parsed, "bozo", False)
    if not bozo:
        return False
    exc: object = getattr(parsed, "bozo_exception", None)
    if exc is None:
        return False
    return not isinstance(exc, CharacterEncodingOverride)


async def validate_feed(url: str) -> None:
    """Confirm URL is reachable and parses as a valid RSS/Atom feed.

    Raises ValueError if the feed is malformed. A feed with zero entries is
    valid. CharacterEncodingOverride bozo exceptions are not treated as fatal.
    """
    loop = asyncio.get_running_loop()
    parsed: object = await loop.run_in_executor(None, feedparser.parse, url)
    if _is_malformed(parsed):
        exc: object = getattr(parsed, "bozo_exception", None)
        raise ValueError(f"Feed at {url!r} is malformed: {exc}")


async def fetch_items(url: str, limit: int = 50) -> list[FeedItem]:
    """Fetch and return up to `limit` items from a single feed.

    Returns an empty list if the feed is unreachable, malformed, or raises
    any exception during fetching; logs a warning in all failure cases.
    """
    loop = asyncio.get_running_loop()
    try:
        parsed: object = await loop.run_in_executor(None, feedparser.parse, url)
    except Exception as exc:
        logger.warning("Failed to fetch feed %r: %s — returning empty list", url, exc)
        return []
    if _is_malformed(parsed):
        exc2: object = getattr(parsed, "bozo_exception", None)
        logger.warning("Malformed feed %r: %s — returning empty list", url, exc2)
        return []
    entries: object = getattr(parsed, "entries", [])
    if not isinstance(entries, list):
        return []
    return [_normalize_entry(entry) for entry in entries[:limit]]


def _do_fetch_article(url: str) -> ArticleContent:
    """Synchronous article fetch and extraction — intended for executor use.

    Uses trafilatura to download the page and extract plain-text content.
    Returns an :class:`ArticleContent` dict; never raises — all failures are
    captured in the ``error`` field with ``content`` set to an empty string.
    """
    empty = ArticleContent(
        url=url, title="", author="", published="", content="", truncated=False, error=""
    )

    html: object = trafilatura.fetch_url(url)
    if not isinstance(html, str):
        return {**empty, "error": "Failed to fetch URL"}

    # Extract metadata (title, author, date) separately so it is available
    # even when content extraction fails.
    title = ""
    author = ""
    published = ""
    metadata: object = trafilatura.extract_metadata(html)
    if metadata is not None:
        raw_title: object = getattr(metadata, "title", None)
        if isinstance(raw_title, str):
            title = raw_title

        raw_author: object = getattr(metadata, "author", None)
        if isinstance(raw_author, list):
            author = ", ".join(str(a) for a in raw_author if a)
        elif isinstance(raw_author, str):
            author = raw_author

        raw_date: object = getattr(metadata, "date", None)
        if isinstance(raw_date, str):
            published = raw_date

    content: object = trafilatura.extract(html, output_format="txt", include_comments=False)
    if not isinstance(content, str):
        return {**empty, "title": title, "author": author, "published": published, "error": "No extractable content found"}

    truncated = len(content) > MAX_CONTENT_CHARS
    return ArticleContent(
        url=url,
        title=title,
        author=author,
        published=published,
        content=content[:MAX_CONTENT_CHARS],
        truncated=truncated,
        error="",
    )


async def fetch_article_content(url: str) -> ArticleContent:
    """Fetch and extract the full plain-text content of an article.

    Offloads the blocking trafilatura calls to a thread pool executor,
    mirroring the pattern used by :func:`fetch_items`.

    Returns an :class:`ArticleContent` dict on both success and failure.
    On failure ``error`` is non-empty and ``content`` is an empty string.
    Never raises.
    """
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, _do_fetch_article, url)
    except Exception as exc:
        logger.warning("Unexpected error fetching article %r: %s", url, exc)
        return ArticleContent(
            url=url, title="", author="", published="", content="", truncated=False, error=str(exc)
        )


async def fetch_all_items(
    urls: list[str], per_feed_limit: int
) -> list[AggregatedFeedItem]:
    """Fetch items from all URLs concurrently and attach feed_url to each item."""
    per_feed_results: list[list[FeedItem]] = list(
        await asyncio.gather(*[fetch_items(url, per_feed_limit) for url in urls])
    )
    aggregated: list[AggregatedFeedItem] = []
    for url, items in zip(urls, per_feed_results):
        for item in items:
            aggregated.append(
                AggregatedFeedItem(
                    title=item["title"],
                    link=item["link"],
                    published=item["published"],
                    feed_url=url,
                )
            )
    return aggregated
