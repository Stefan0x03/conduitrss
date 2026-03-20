"""Unit tests for conduit.feeds."""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from conduit.feeds import (
    AggregatedFeedItem,
    FeedItem,
    fetch_all_items,
    fetch_items,
    validate_feed,
)

pytestmark = [pytest.mark.unit]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_parsed(
    entries: list[Any],
    *,
    bozo: bool = False,
    bozo_exception: Exception | None = None,
) -> MagicMock:
    """Return a mock feedparser result."""
    parsed = MagicMock()
    parsed.bozo = bozo
    parsed.bozo_exception = bozo_exception
    parsed.entries = entries
    return parsed


def _make_entry(**kwargs: Any) -> MagicMock:
    """Return a mock feedparser entry with the given attributes set."""
    entry = MagicMock()
    for key, val in kwargs.items():
        setattr(entry, key, val)
    return entry


# ---------------------------------------------------------------------------
# FeedItem normalization — RSS 2.0
# ---------------------------------------------------------------------------


def test_rss_normalization_uses_published_and_summary() -> None:
    """fetch_items normalizes RSS 2.0 entries using published and summary."""
    entry = _make_entry(
        title="Article Title",
        link="https://example.com/article",
        published="Mon, 01 Jan 2024 00:00:00 +0000",
        summary="Article summary text",
    )
    parsed = _make_parsed([entry])

    with patch("feedparser.parse", return_value=parsed):
        items = asyncio.run(fetch_items("https://example.com/feed.xml"))

    assert len(items) == 1
    assert items[0]["title"] == "Article Title"
    assert items[0]["link"] == "https://example.com/article"
    assert items[0]["published"] == "Mon, 01 Jan 2024 00:00:00 +0000"
    assert items[0]["summary"] == "Article summary text"


# ---------------------------------------------------------------------------
# FeedItem normalization — Atom (updated + content fallbacks)
# ---------------------------------------------------------------------------


def test_atom_normalization_falls_back_to_updated_and_content() -> None:
    """fetch_items falls back to updated for published and content[0].value for summary."""
    content_item = MagicMock()
    content_item.value = "Full content value"
    entry = _make_entry(
        title="Atom Article",
        link="https://atom.example.com/1",
        updated="2024-06-15T12:00:00Z",
    )
    # No published attribute (MagicMock auto-creates it, but we override to empty string
    # so _str_field skips it and falls through to updated)
    entry.published = ""
    # No summary — MagicMock will have it, but set to empty to trigger content fallback
    entry.summary = ""
    entry.content = [content_item]

    parsed = _make_parsed([entry])

    with patch("feedparser.parse", return_value=parsed):
        items = asyncio.run(fetch_items("https://atom.example.com/feed"))

    assert len(items) == 1
    assert items[0]["published"] == "2024-06-15T12:00:00Z"
    assert items[0]["summary"] == "Full content value"


# ---------------------------------------------------------------------------
# FeedItem normalization — all fields absent → empty strings
# ---------------------------------------------------------------------------


def test_normalization_falls_back_to_empty_strings() -> None:
    """fetch_items returns empty strings when no recognized fields are present."""
    entry = _make_entry(
        title="",
        link="",
        published="",
        updated="",
        summary="",
    )
    # No content list — MagicMock will be a non-list object, not triggering fallback
    entry.content = None

    parsed = _make_parsed([entry])

    with patch("feedparser.parse", return_value=parsed):
        items = asyncio.run(fetch_items("https://example.com/sparse.xml"))

    assert len(items) == 1
    assert items[0]["title"] == ""
    assert items[0]["link"] == ""
    assert items[0]["published"] == ""
    assert items[0]["summary"] == ""


# ---------------------------------------------------------------------------
# validate_feed — fatal bozo raises ValueError
# ---------------------------------------------------------------------------


def test_validate_feed_raises_on_malformed_feed() -> None:
    """validate_feed raises ValueError when bozo_exception is a fatal exception."""
    exc = Exception("Connection refused")
    parsed = _make_parsed([], bozo=True, bozo_exception=exc)

    with patch("feedparser.parse", return_value=parsed):
        with pytest.raises(ValueError, match="malformed"):
            asyncio.run(validate_feed("https://not-a-feed.example.com/"))


# ---------------------------------------------------------------------------
# validate_feed — zero entries is valid
# ---------------------------------------------------------------------------


def test_validate_feed_accepts_empty_feed() -> None:
    """validate_feed does not raise for a valid feed with zero entries."""
    parsed = _make_parsed([])

    with patch("feedparser.parse", return_value=parsed):
        asyncio.run(validate_feed("https://example.com/empty.xml"))  # must not raise


# ---------------------------------------------------------------------------
# validate_feed — CharacterEncodingOverride is not fatal
# ---------------------------------------------------------------------------


def test_validate_feed_accepts_character_encoding_override() -> None:
    """validate_feed does not raise when bozo_exception is CharacterEncodingOverride."""
    from feedparser.exceptions import CharacterEncodingOverride

    enc_exc = CharacterEncodingOverride("encoding mismatch")
    entry = _make_entry(
        title="Valid Title",
        link="https://example.com/1",
        published="2024-01-01",
        summary="Valid summary",
    )
    parsed = _make_parsed([entry], bozo=True, bozo_exception=enc_exc)

    with patch("feedparser.parse", return_value=parsed):
        asyncio.run(validate_feed("https://example.com/enc-feed.xml"))  # must not raise


# ---------------------------------------------------------------------------
# fetch_items — malformed feed returns empty list without raising
# ---------------------------------------------------------------------------


def test_fetch_items_returns_empty_list_on_malformed_feed() -> None:
    """fetch_items returns [] and does not raise when the feed is malformed."""
    exc = Exception("Parse error")
    parsed = _make_parsed([], bozo=True, bozo_exception=exc)

    with patch("feedparser.parse", return_value=parsed):
        items = asyncio.run(fetch_items("https://broken.example.com/feed"))

    assert items == []


# ---------------------------------------------------------------------------
# fetch_items — limit is respected
# ---------------------------------------------------------------------------


def test_fetch_items_respects_limit() -> None:
    """fetch_items returns at most `limit` items."""
    entries = [
        _make_entry(title=f"T{i}", link=f"L{i}", published="2024-01-01", summary="S")
        for i in range(10)
    ]
    parsed = _make_parsed(entries)

    with patch("feedparser.parse", return_value=parsed):
        items = asyncio.run(fetch_items("https://example.com/feed.xml", limit=3))

    assert len(items) == 3
    assert items[0]["title"] == "T0"
    assert items[2]["title"] == "T2"


# ---------------------------------------------------------------------------
# fetch_all_items — concurrent fetch, feed_url attached
# ---------------------------------------------------------------------------


def test_fetch_all_items_attaches_feed_url() -> None:
    """fetch_all_items attaches feed_url to every item from each feed."""
    feed1_item = FeedItem(title="T1", link="L1", published="P1", summary="S1")
    feed2_item = FeedItem(title="T2", link="L2", published="P2", summary="S2")

    async def _side_effect(url: str, limit: int = 50) -> list[FeedItem]:
        if "feed1" in url:
            return [feed1_item]
        return [feed2_item]

    urls = ["https://feed1.example.com/rss", "https://feed2.example.com/rss"]

    with patch("conduit.feeds.fetch_items", new=AsyncMock(side_effect=_side_effect)):
        items = asyncio.run(fetch_all_items(urls, per_feed_limit=10))

    assert len(items) == 2

    by_feed: dict[str, list[AggregatedFeedItem]] = {}
    for item in items:
        by_feed.setdefault(item["feed_url"], []).append(item)

    assert by_feed["https://feed1.example.com/rss"][0]["title"] == "T1"
    assert by_feed["https://feed2.example.com/rss"][0]["title"] == "T2"
