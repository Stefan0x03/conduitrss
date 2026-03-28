"""Unit tests for conduit.server.

Mocks both conduit.server.storage and conduit.server.feeds throughout —
no real AWS or HTTP calls are made.

AUTH_DISABLED=true is set before importing conduit.server so that:
  - The Cognito provider block is skipped (no COGNITO_* env vars needed)
  - _get_user_id() returns LOCAL_USER_ID without consulting a JWT
  - The lifespan OPML seeding path is exercised in isolation
"""

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit]

# Must be set before importing conduit.server, which imports conduit.storage
# (DYNAMODB_TABLE is required at module level in storage.py).
os.environ.setdefault("DYNAMODB_TABLE", "test-conduit-feeds")
os.environ.setdefault("AUTH_DISABLED", "true")

from conduit.feeds import ArticleContent  # noqa: E402
from conduit.server import (  # noqa: E402
    LOCAL_USER_ID,
    _get_user_id,
    add_feed,
    get_all_items,
    get_article_content,
    get_feed_items,
    list_feeds,
    remove_feed,
)
from conduit.storage import FeedRecord  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_feed_record(url: str, label: str | None = None) -> FeedRecord:
    return FeedRecord(
        pk=f"user#{LOCAL_USER_ID}",
        sk=f"feed#{url}",
        url=url,
        label=label,
        addedAt="2024-01-01T00:00:00+00:00",
        lastFetched="",
        etag="",
    )


# ---------------------------------------------------------------------------
# AUTH_DISABLED identity injection
# ---------------------------------------------------------------------------


def test_auth_disabled_injects_local_user() -> None:
    """With AUTH_DISABLED=true, _get_user_id returns 'local-dev-user'."""
    assert _get_user_id() == LOCAL_USER_ID
    assert LOCAL_USER_ID == "local-dev-user"


# ---------------------------------------------------------------------------
# add_feed
# ---------------------------------------------------------------------------


def test_add_feed_calls_validate_before_storage() -> None:
    """add_feed calls validate_feed then storage.add_feed with correct args."""
    with (
        patch(
            "conduit.server.feeds.validate_feed", new_callable=AsyncMock
        ) as mock_validate,
        patch("conduit.server.storage.add_feed") as mock_add,
    ):
        mock_validate.return_value = None
        mock_add.return_value = None

        asyncio.run(add_feed("https://example.com/feed.xml", label="Example"))

        mock_validate.assert_awaited_once_with("https://example.com/feed.xml")
        mock_add.assert_called_once_with(
            LOCAL_USER_ID, "https://example.com/feed.xml", "Example"
        )


def test_add_feed_does_not_call_storage_when_validate_raises() -> None:
    """If validate_feed raises, storage.add_feed must never be called."""
    with (
        patch(
            "conduit.server.feeds.validate_feed", new_callable=AsyncMock
        ) as mock_validate,
        patch("conduit.server.storage.add_feed") as mock_add,
    ):
        mock_validate.side_effect = ValueError("Malformed feed")

        with pytest.raises(ValueError, match="Malformed feed"):
            asyncio.run(add_feed("https://bad-feed.example.com/rss"))

        mock_add.assert_not_called()


# ---------------------------------------------------------------------------
# remove_feed
# ---------------------------------------------------------------------------


def test_remove_feed_delegates_to_storage() -> None:
    """remove_feed calls storage.remove_feed with the correct user and url."""
    with patch("conduit.server.storage.remove_feed") as mock_remove:
        mock_remove.return_value = None
        result = asyncio.run(remove_feed("https://example.com/feed.xml"))

        mock_remove.assert_called_once_with(
            LOCAL_USER_ID, "https://example.com/feed.xml"
        )
        assert result["status"] == "removed"


# ---------------------------------------------------------------------------
# list_feeds
# ---------------------------------------------------------------------------


def test_list_feeds_delegates_to_storage() -> None:
    """list_feeds returns the mapped records from storage.list_feeds."""
    records = [
        _make_feed_record("https://feed1.com/rss", label="Feed One"),
        _make_feed_record("https://feed2.com/rss"),
    ]
    with patch("conduit.server.storage.list_feeds", return_value=records):
        result = asyncio.run(list_feeds())

    assert len(result) == 2
    assert result[0] == {
        "url": "https://feed1.com/rss",
        "label": "Feed One",
        "addedAt": "2024-01-01T00:00:00+00:00",
    }
    assert result[1]["label"] is None


# ---------------------------------------------------------------------------
# get_feed_items
# ---------------------------------------------------------------------------


def test_get_feed_items_raises_when_not_subscribed() -> None:
    """get_feed_items raises ValueError when storage.get_feed returns None."""
    with (
        patch("conduit.server.storage.get_feed", return_value=None) as mock_get,
        patch(
            "conduit.server.feeds.fetch_items", new_callable=AsyncMock
        ) as mock_fetch,
    ):
        with pytest.raises(ValueError, match="Not subscribed"):
            asyncio.run(get_feed_items("https://example.com/feed.xml"))

        mock_get.assert_called_once_with(
            LOCAL_USER_ID, "https://example.com/feed.xml"
        )
        mock_fetch.assert_not_called()


def test_get_feed_items_fetches_when_subscribed() -> None:
    """get_feed_items calls fetch_items when the user is subscribed."""
    record = _make_feed_record("https://example.com/feed.xml")
    mock_items: list[MagicMock] = [MagicMock()]

    with (
        patch("conduit.server.storage.get_feed", return_value=record),
        patch(
            "conduit.server.feeds.fetch_items",
            new_callable=AsyncMock,
            return_value=mock_items,
        ) as mock_fetch,
    ):
        result = asyncio.run(
            get_feed_items("https://example.com/feed.xml", limit=10)
        )

        mock_fetch.assert_awaited_once_with("https://example.com/feed.xml", 10)
        assert result == mock_items


# ---------------------------------------------------------------------------
# get_all_items
# ---------------------------------------------------------------------------


def test_get_all_items_computes_correct_per_feed_limit() -> None:
    """get_all_items divides limit evenly: per_feed_limit = limit // len(feeds)."""
    records = [
        _make_feed_record("https://feed1.com/rss"),
        _make_feed_record("https://feed2.com/rss"),
        _make_feed_record("https://feed3.com/rss"),
        _make_feed_record("https://feed4.com/rss"),
    ]
    with (
        patch("conduit.server.storage.list_feeds", return_value=records),
        patch(
            "conduit.server.feeds.fetch_all_items",
            new_callable=AsyncMock,
            return_value=[],
        ) as mock_fetch,
    ):
        asyncio.run(get_all_items(limit=200))

        mock_fetch.assert_awaited_once_with(
            [
                "https://feed1.com/rss",
                "https://feed2.com/rss",
                "https://feed3.com/rss",
                "https://feed4.com/rss",
            ],
            50,  # 200 // 4
        )


def test_get_all_items_returns_empty_for_zero_feeds() -> None:
    """get_all_items returns [] without dividing by zero when no feeds exist."""
    with patch("conduit.server.storage.list_feeds", return_value=[]):
        result = asyncio.run(get_all_items(limit=200))

    assert result == []


# ---------------------------------------------------------------------------
# get_article_content
# ---------------------------------------------------------------------------


def test_get_article_content_delegates_to_feeds() -> None:
    """get_article_content calls feeds.fetch_article_content and returns its result."""
    expected: ArticleContent = ArticleContent(
        url="https://example.com/article",
        title="Test Title",
        author="Test Author",
        published="2024-06-01",
        content="Full article text.",
        truncated=False,
        error="",
    )

    with patch(
        "conduit.server.feeds.fetch_article_content",
        new_callable=AsyncMock,
        return_value=expected,
    ) as mock_fetch:
        result = asyncio.run(get_article_content("https://example.com/article"))

        mock_fetch.assert_awaited_once_with("https://example.com/article")
        assert result == expected
