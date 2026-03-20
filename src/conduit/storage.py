"""DynamoDB CRUD for Conduit feed subscriptions."""

import os
from datetime import UTC, datetime
from typing import TypedDict

import boto3
from mypy_boto3_dynamodb import DynamoDBClient
from mypy_boto3_dynamodb.type_defs import AttributeValueTypeDef

DYNAMODB_TABLE: str = os.environ["DYNAMODB_TABLE"]

_client: DynamoDBClient = boto3.client("dynamodb")


class FeedRecord(TypedDict):
    pk: str
    sk: str
    url: str
    label: str | None
    addedAt: str
    lastFetched: str
    etag: str


def _pk(user_id: str) -> str:
    return f"user#{user_id}"


def _sk(feed_url: str) -> str:
    return f"feed#{feed_url}"


def add_feed(user_id: str, url: str, label: str | None = None) -> None:
    """Add a feed subscription for the given user."""
    item: dict[str, AttributeValueTypeDef] = {
        "pk": {"S": _pk(user_id)},
        "sk": {"S": _sk(url)},
        "url": {"S": url},
        "addedAt": {"S": datetime.now(UTC).isoformat()},
        "lastFetched": {"S": ""},
        "etag": {"S": ""},
    }
    if label is not None:
        item["label"] = {"S": label}
    _client.put_item(TableName=DYNAMODB_TABLE, Item=item)


def remove_feed(user_id: str, url: str) -> None:
    """Remove a feed subscription for the given user."""
    _client.delete_item(
        TableName=DYNAMODB_TABLE,
        Key={"pk": {"S": _pk(user_id)}, "sk": {"S": _sk(url)}},
    )


def list_feeds(user_id: str) -> list[FeedRecord]:
    """Return all feed subscriptions for the given user."""
    response = _client.query(
        TableName=DYNAMODB_TABLE,
        KeyConditionExpression="pk = :pk",
        ExpressionAttributeValues={":pk": {"S": _pk(user_id)}},
    )
    records: list[FeedRecord] = []
    for item in response.get("Items", []):
        records.append(_item_to_record(item))
    return records


def get_feed(user_id: str, url: str) -> FeedRecord | None:
    """Return a single feed subscription, or None if not found."""
    response = _client.get_item(
        TableName=DYNAMODB_TABLE,
        Key={"pk": {"S": _pk(user_id)}, "sk": {"S": _sk(url)}},
    )
    item = response.get("Item")
    if item is None:
        return None
    return _item_to_record(item)


def _item_to_record(item: dict[str, AttributeValueTypeDef]) -> FeedRecord:
    label_attr = item.get("label")
    return FeedRecord(
        pk=item["pk"]["S"],
        sk=item["sk"]["S"],
        url=item["url"]["S"],
        label=label_attr["S"] if label_attr is not None else None,
        addedAt=item["addedAt"]["S"],
        lastFetched=item["lastFetched"]["S"],
        etag=item["etag"]["S"],
    )
