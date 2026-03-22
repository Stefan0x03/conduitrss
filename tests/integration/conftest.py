"""Fixtures shared by all integration tests.

Sets required environment variables before any conduit imports occur, creates
the conduit-feeds table in DynamoDB Local once per session, and redirects the
storage module's already-initialised boto3 client to the local endpoint.
"""

import os
from collections.abc import Generator

import boto3
import pytest
from mypy_boto3_dynamodb import DynamoDBClient

# Must be set before any conduit imports:
#   - storage.py reads DYNAMODB_TABLE at module level (fails if absent)
#   - server.py evaluates AUTH_DISABLED at module level (skips Cognito wiring)
os.environ.setdefault("DYNAMODB_TABLE", "conduit-feeds")
os.environ.setdefault("AUTH_DISABLED", "true")

_DYNAMODB_ENDPOINT: str = "http://localhost:8001"
_TABLE_NAME: str = "conduit-feeds"


def _make_local_client() -> DynamoDBClient:
    """Return a boto3 DynamoDB client pointed at DynamoDB Local.

    Explicit dummy credentials are required — boto3 raises NoCredentialsError
    if none are present, even though DynamoDB Local does not validate them.
    """
    return boto3.client(
        "dynamodb",
        endpoint_url=_DYNAMODB_ENDPOINT,
        region_name="us-east-1",
        aws_access_key_id="local",
        aws_secret_access_key="local",
    )


@pytest.fixture(scope="session", autouse=True)
def dynamodb_table() -> Generator[None, None, None]:
    """Create the conduit-feeds table in DynamoDB Local for the test session.

    Also patches ``conduit.storage._client`` to point at DynamoDB Local.
    ``storage.py`` calls ``boto3.client("dynamodb")`` at import time, so
    environment variables cannot redirect it after the fact — we must
    overwrite the module attribute directly.

    The table is deleted during teardown so each test run starts clean.
    """
    import conduit.storage

    local_client = _make_local_client()

    # Redirect the module-level client to DynamoDB Local.
    conduit.storage._client = local_client

    local_client.create_table(
        TableName=_TABLE_NAME,
        KeySchema=[
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )

    yield

    local_client.delete_table(TableName=_TABLE_NAME)
