"""Unit tests for conduit.storage."""

import os
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit]

TABLE = "test-conduit-feeds"

# Patch DYNAMODB_TABLE before importing storage
os.environ.setdefault("DYNAMODB_TABLE", TABLE)


@pytest.fixture()
def mock_client() -> MagicMock:
    with patch("conduit.storage._client") as m:
        yield m


def test_add_feed_put_item_key_shape(mock_client: MagicMock) -> None:
    """add_feed constructs correct PK/SK and calls PutItem."""
    from conduit.storage import add_feed

    mock_client.put_item.return_value = {}
    add_feed("alice", "https://example.com/feed.xml")

    mock_client.put_item.assert_called_once()
    call_kwargs = mock_client.put_item.call_args
    item = call_kwargs.kwargs["Item"] if call_kwargs.kwargs else call_kwargs[1]["Item"]

    assert item["PK"]["S"] == "user#alice"
    assert item["SK"]["S"] == "feed#https://example.com/feed.xml"
    assert item["url"]["S"] == "https://example.com/feed.xml"
    assert item["lastFetched"]["S"] == ""
    assert item["etag"]["S"] == ""
    assert "addedAt" in item


def test_add_feed_with_label(mock_client: MagicMock) -> None:
    """add_feed stores label when provided."""
    from conduit.storage import add_feed

    mock_client.put_item.return_value = {}
    add_feed("alice", "https://example.com/feed.xml", label="My Feed")

    item = mock_client.put_item.call_args.kwargs["Item"]
    assert item["label"]["S"] == "My Feed"


def test_add_feed_without_label(mock_client: MagicMock) -> None:
    """add_feed omits label key when not provided."""
    from conduit.storage import add_feed

    mock_client.put_item.return_value = {}
    add_feed("alice", "https://example.com/feed.xml")

    item = mock_client.put_item.call_args.kwargs["Item"]
    assert "label" not in item


def test_add_feed_reserved_fields(mock_client: MagicMock) -> None:
    """add_feed stores lastFetched and etag as empty strings."""
    from conduit.storage import add_feed

    mock_client.put_item.return_value = {}
    add_feed("alice", "https://example.com/feed.xml")

    item = mock_client.put_item.call_args.kwargs["Item"]
    assert item["lastFetched"]["S"] == ""
    assert item["etag"]["S"] == ""


def test_remove_feed_delete_item(mock_client: MagicMock) -> None:
    """remove_feed calls DeleteItem with correct key."""
    from conduit.storage import remove_feed

    mock_client.delete_item.return_value = {}
    remove_feed("alice", "https://example.com/feed.xml")

    mock_client.delete_item.assert_called_once_with(
        TableName=TABLE,
        Key={
            "PK": {"S": "user#alice"},
            "SK": {"S": "feed#https://example.com/feed.xml"},
        },
    )


def test_list_feeds_query_and_return_shape(mock_client: MagicMock) -> None:
    """list_feeds queries by PK and returns correctly shaped FeedRecord list."""
    from conduit.storage import list_feeds

    mock_client.query.return_value = {
        "Items": [
            {
                "PK": {"S": "user#alice"},
                "SK": {"S": "feed#https://example.com/feed.xml"},
                "url": {"S": "https://example.com/feed.xml"},
                "label": {"S": "Example"},
                "addedAt": {"S": "2024-01-01T00:00:00+00:00"},
                "lastFetched": {"S": ""},
                "etag": {"S": ""},
            }
        ]
    }

    results = list_feeds("alice")

    mock_client.query.assert_called_once()
    call_kwargs = mock_client.query.call_args.kwargs
    assert call_kwargs["ExpressionAttributeValues"][":pk"]["S"] == "user#alice"

    assert len(results) == 1
    r = results[0]
    assert r["pk"] == "user#alice"
    assert r["sk"] == "feed#https://example.com/feed.xml"
    assert r["url"] == "https://example.com/feed.xml"
    assert r["label"] == "Example"
    assert r["addedAt"] == "2024-01-01T00:00:00+00:00"
    assert r["lastFetched"] == ""
    assert r["etag"] == ""


def test_list_feeds_empty(mock_client: MagicMock) -> None:
    """list_feeds returns empty list when no items found."""
    from conduit.storage import list_feeds

    mock_client.query.return_value = {"Items": []}
    assert list_feeds("alice") == []


def test_get_feed_returns_record_when_found(mock_client: MagicMock) -> None:
    """get_feed returns FeedRecord when item exists."""
    from conduit.storage import get_feed

    mock_client.get_item.return_value = {
        "Item": {
            "PK": {"S": "user#alice"},
            "SK": {"S": "feed#https://example.com/feed.xml"},
            "url": {"S": "https://example.com/feed.xml"},
            "addedAt": {"S": "2024-01-01T00:00:00+00:00"},
            "lastFetched": {"S": ""},
            "etag": {"S": ""},
        }
    }

    result = get_feed("alice", "https://example.com/feed.xml")

    assert result is not None
    assert result["url"] == "https://example.com/feed.xml"
    assert result["label"] is None


def test_get_feed_returns_none_when_not_found(mock_client: MagicMock) -> None:
    """get_feed returns None when item does not exist."""
    from conduit.storage import get_feed

    mock_client.get_item.return_value = {}

    result = get_feed("alice", "https://example.com/feed.xml")
    assert result is None


def test_get_feed_correct_key(mock_client: MagicMock) -> None:
    """get_feed uses correct key construction."""
    from conduit.storage import get_feed

    mock_client.get_item.return_value = {}
    get_feed("bob", "https://feeds.example.org/rss")

    mock_client.get_item.assert_called_once_with(
        TableName=TABLE,
        Key={
            "PK": {"S": "user#bob"},
            "SK": {"S": "feed#https://feeds.example.org/rss"},
        },
    )
