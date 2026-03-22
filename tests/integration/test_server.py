"""Integration tests for conduit.server against DynamoDB Local.

All tests run with AUTH_DISABLED=true (configured in conftest.py).  Each test
uses a unique user ID — injected by patching conduit.server._get_user_id — so
tests do not interfere with each other in the shared DynamoDB Local table.

feeds.validate_feed, feeds.fetch_items, and feeds.fetch_all_items are mocked
throughout.  The integration boundary under test is server-tool <-> DynamoDB.
"""

import asyncio
import os
import uuid
from unittest.mock import AsyncMock, patch

import pytest

import conduit.storage as storage

pytestmark = [pytest.mark.integration]

# Guard: env vars must be set before conduit imports.  conftest.py sets them
# first, but pytest may import this module before conftest in some edge cases.
os.environ.setdefault("DYNAMODB_TABLE", "conduit-feeds")
os.environ.setdefault("AUTH_DISABLED", "true")

from conduit.feeds import FeedItem  # noqa: E402
from conduit.server import (  # noqa: E402
    add_feed,
    get_all_items,
    get_feed_items,
    list_feeds,
    remove_feed,
)

# ---------------------------------------------------------------------------
# add_feed
# ---------------------------------------------------------------------------


def test_add_feed_end_to_end() -> None:
    """add_feed stores a subscription retrievable via storage.get_feed."""
    user_id = str(uuid.uuid4())
    url = "https://example.com/feed.xml"

    with (
        patch("conduit.server._get_user_id", return_value=user_id),
        patch("conduit.server.feeds.validate_feed", new_callable=AsyncMock),
    ):
        asyncio.run(add_feed(url, label="Example Feed"))

    record = storage.get_feed(user_id, url)
    assert record is not None
    assert record["url"] == url
    assert record["label"] == "Example Feed"


def test_add_feed_duplicate_is_idempotent() -> None:
    """Calling add_feed twice with the same URL leaves exactly one record."""
    user_id = str(uuid.uuid4())
    url = "https://example.com/feed-dupe.xml"

    with (
        patch("conduit.server._get_user_id", return_value=user_id),
        patch("conduit.server.feeds.validate_feed", new_callable=AsyncMock),
    ):
        asyncio.run(add_feed(url))
        asyncio.run(add_feed(url))

    records = storage.list_feeds(user_id)
    assert len(records) == 1
    assert records[0]["url"] == url


# ---------------------------------------------------------------------------
# remove_feed
# ---------------------------------------------------------------------------


def test_remove_feed_end_to_end() -> None:
    """add_feed then remove_feed leaves no record in DynamoDB."""
    user_id = str(uuid.uuid4())
    url = "https://example.com/removable.xml"

    with (
        patch("conduit.server._get_user_id", return_value=user_id),
        patch("conduit.server.feeds.validate_feed", new_callable=AsyncMock),
    ):
        asyncio.run(add_feed(url))

    assert storage.get_feed(user_id, url) is not None

    with patch("conduit.server._get_user_id", return_value=user_id):
        asyncio.run(remove_feed(url))

    assert storage.get_feed(user_id, url) is None


def test_remove_feed_nonexistent_does_not_raise() -> None:
    """remove_feed on a URL that was never added must not raise."""
    user_id = str(uuid.uuid4())
    url = "https://example.com/never-added.xml"

    with patch("conduit.server._get_user_id", return_value=user_id):
        asyncio.run(remove_feed(url))  # must not raise


# ---------------------------------------------------------------------------
# list_feeds
# ---------------------------------------------------------------------------


def test_list_feeds_end_to_end() -> None:
    """Add two feeds; list_feeds returns both with the expected shape."""
    user_id = str(uuid.uuid4())
    url_a = "https://feed-a.example.com/rss"
    url_b = "https://feed-b.example.com/atom"

    with (
        patch("conduit.server._get_user_id", return_value=user_id),
        patch("conduit.server.feeds.validate_feed", new_callable=AsyncMock),
    ):
        asyncio.run(add_feed(url_a, label="Feed A"))
        asyncio.run(add_feed(url_b))

    with patch("conduit.server._get_user_id", return_value=user_id):
        result = asyncio.run(list_feeds())

    assert len(result) == 2
    by_url = {item["url"]: item for item in result}
    assert set(by_url.keys()) == {url_a, url_b}
    assert by_url[url_a]["label"] == "Feed A"
    assert by_url[url_b]["label"] is None
    for item in result:
        assert "addedAt" in item


# ---------------------------------------------------------------------------
# get_feed_items
# ---------------------------------------------------------------------------


def test_get_feed_items_rejects_unsubscribed_url() -> None:
    """get_feed_items raises ValueError when the user is not subscribed."""
    user_id = str(uuid.uuid4())
    url = "https://not-subscribed.example.com/rss"

    with (
        patch("conduit.server._get_user_id", return_value=user_id),
        pytest.raises(ValueError, match="Not subscribed"),
    ):
        asyncio.run(get_feed_items(url))


def test_get_feed_items_end_to_end() -> None:
    """After add_feed, get_feed_items calls fetch_items with correct args."""
    user_id = str(uuid.uuid4())
    url = "https://feed-items.example.com/rss"
    mock_items: list[FeedItem] = [
        FeedItem(
            title="Post 1",
            link="https://feed-items.example.com/post-1",
            published="2024-01-01",
            summary="Summary 1",
        )
    ]

    with (
        patch("conduit.server._get_user_id", return_value=user_id),
        patch("conduit.server.feeds.validate_feed", new_callable=AsyncMock),
    ):
        asyncio.run(add_feed(url))

    with (
        patch("conduit.server._get_user_id", return_value=user_id),
        patch(
            "conduit.server.feeds.fetch_items",
            new_callable=AsyncMock,
            return_value=mock_items,
        ) as mock_fetch,
    ):
        result = asyncio.run(get_feed_items(url, limit=10))

    mock_fetch.assert_awaited_once_with(url, 10)
    assert result == mock_items


# ---------------------------------------------------------------------------
# get_all_items
# ---------------------------------------------------------------------------


def test_get_all_items_end_to_end() -> None:
    """Add three feeds; get_all_items calls fetch_all_items with all three URLs."""
    user_id = str(uuid.uuid4())
    urls = [
        "https://all-1.example.com/rss",
        "https://all-2.example.com/rss",
        "https://all-3.example.com/rss",
    ]

    with (
        patch("conduit.server._get_user_id", return_value=user_id),
        patch("conduit.server.feeds.validate_feed", new_callable=AsyncMock),
    ):
        for url in urls:
            asyncio.run(add_feed(url))

    with (
        patch("conduit.server._get_user_id", return_value=user_id),
        patch(
            "conduit.server.feeds.fetch_all_items",
            new_callable=AsyncMock,
            return_value=[],
        ) as mock_fetch,
    ):
        asyncio.run(get_all_items())

    mock_fetch.assert_awaited_once()
    called_urls: list[str] = list(mock_fetch.call_args.args[0])
    assert set(called_urls) == set(urls)


def test_get_all_items_respects_per_feed_cap() -> None:
    """limit=100 across 4 feeds gives per_feed_limit=25 (100 // 4)."""
    user_id = str(uuid.uuid4())
    urls = [
        "https://cap-1.example.com/rss",
        "https://cap-2.example.com/rss",
        "https://cap-3.example.com/rss",
        "https://cap-4.example.com/rss",
    ]

    with (
        patch("conduit.server._get_user_id", return_value=user_id),
        patch("conduit.server.feeds.validate_feed", new_callable=AsyncMock),
    ):
        for url in urls:
            asyncio.run(add_feed(url))

    with (
        patch("conduit.server._get_user_id", return_value=user_id),
        patch(
            "conduit.server.feeds.fetch_all_items",
            new_callable=AsyncMock,
            return_value=[],
        ) as mock_fetch,
    ):
        asyncio.run(get_all_items(limit=100))

    mock_fetch.assert_awaited_once()
    per_feed_limit: int = mock_fetch.call_args.args[1]
    assert per_feed_limit == 25  # 100 // 4
