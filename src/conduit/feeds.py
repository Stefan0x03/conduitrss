"""Live HTTP fetch and parse via feedparser."""

import asyncio
import logging
from typing import TypedDict

import feedparser
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
    summary: str
    feed_url: str


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

    Returns an empty list if the feed is malformed; logs a warning.
    """
    loop = asyncio.get_running_loop()
    parsed: object = await loop.run_in_executor(None, feedparser.parse, url)
    if _is_malformed(parsed):
        exc: object = getattr(parsed, "bozo_exception", None)
        logger.warning("Malformed feed %r: %s — returning empty list", url, exc)
        return []
    entries: object = getattr(parsed, "entries", [])
    if not isinstance(entries, list):
        return []
    return [_normalize_entry(entry) for entry in entries[:limit]]


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
                    summary=item["summary"],
                    feed_url=url,
                )
            )
    return aggregated
