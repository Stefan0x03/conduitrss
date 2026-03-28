"""FastMCP app, OAuth wiring, and tool registration.

Module responsibilities:
- Instantiate the FastMCP app
- Wire the Cognito OAuth adapter (skipped when AUTH_DISABLED=true)
- Register /healthz health-check route for the ALB
- Seed OPML feeds at startup in local dev mode (via lifespan)
- Register the five MCP tools (no business logic — delegates to feeds/storage)
"""

import logging
import os
import xml.etree.ElementTree as ET
from collections.abc import AsyncIterator
from typing import Any

import uvicorn
from fastmcp import FastMCP
from fastmcp.server.auth.providers.aws import AWSCognitoProvider
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.lifespan import Lifespan
from starlette.requests import Request
from starlette.responses import JSONResponse

from conduit import feeds, storage

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Auth-disabled flag — evaluated once at import time.
# AUTH_DISABLED=true must NEVER be set in production.
# ---------------------------------------------------------------------------

AUTH_DISABLED: bool = os.environ.get("AUTH_DISABLED", "").lower() == "true"
LOCAL_USER_ID: str = "local-dev-user"


# ---------------------------------------------------------------------------
# Identity helper
# ---------------------------------------------------------------------------


def _get_user_id() -> str:
    """Return the authenticated user's ID.

    In AUTH_DISABLED mode returns the hardcoded LOCAL_USER_ID.
    Otherwise extracts the ``sub`` claim from the Cognito JWT via FastMCP's
    request-scoped dependency.

    Raises:
        ValueError: If no access token is present or the ``sub`` claim is
            missing / not a string.
    """
    if AUTH_DISABLED:
        return LOCAL_USER_ID

    token = get_access_token()
    if token is None:
        raise ValueError("No access token — authentication required")

    sub: object = token.claims.get("sub")
    if not isinstance(sub, str) or not sub:
        raise ValueError("JWT missing or invalid 'sub' claim")

    return sub


# ---------------------------------------------------------------------------
# OPML seed loader
# ---------------------------------------------------------------------------


def _seed_feeds_from_opml() -> None:
    """Parse seeds/feeds.opml and pre-populate DynamoDB for LOCAL_USER_ID.

    Uses :mod:`xml.etree.ElementTree` (stdlib only — no new dependencies).
    Logs a warning and returns on any error; never raises.
    """
    seeds_dir = os.path.join(os.path.dirname(__file__), "..", "..", "seeds")
    opml_path = os.path.abspath(os.path.join(seeds_dir, "feeds.opml"))

    try:
        tree = ET.parse(opml_path)
    except FileNotFoundError:
        logger.warning("OPML seed file not found: %s", opml_path)
        return
    except ET.ParseError as exc:
        logger.warning("Failed to parse OPML seed file %s: %s", opml_path, exc)
        return

    root = tree.getroot()
    body = root.find("body")
    if body is None:
        logger.warning("OPML seed file has no <body> element: %s", opml_path)
        return

    for outline in body.iter("outline"):
        url: str | None = outline.get("xmlUrl") or outline.get("url")
        if not url:
            continue
        label: str | None = outline.get("text") or outline.get("title")
        try:
            storage.add_feed(LOCAL_USER_ID, url, label)
            logger.info("Seeded feed: %s (%s)", label, url)
        except Exception as exc:  # broad catch — seed errors must never crash startup
            logger.warning("Failed to seed feed %s: %s", url, exc)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


async def _conduit_lifespan(
    server: FastMCP[Any],  # Any: FastMCP is generic over LifespanResultT
) -> AsyncIterator[dict[str, Any] | None]:
    """Startup hook: seed OPML feeds in local dev mode."""
    if AUTH_DISABLED:
        _seed_feeds_from_opml()
    yield None


# ---------------------------------------------------------------------------
# Auth provider (skipped in local dev)
# ---------------------------------------------------------------------------

_auth: AWSCognitoProvider | None = None
if not AUTH_DISABLED:
    _auth = AWSCognitoProvider(
        user_pool_id=os.environ["COGNITO_USER_POOL_ID"],
        client_id=os.environ["COGNITO_APP_CLIENT_ID"],
        client_secret=os.environ["COGNITO_CLIENT_SECRET"],
        aws_region=os.environ.get("AWS_REGION", "us-east-1"),
        base_url=f"https://{os.environ['DOMAIN']}",
    )

# ---------------------------------------------------------------------------
# FastMCP app
# ---------------------------------------------------------------------------

mcp: FastMCP[Any] = FastMCP(  # Any: lifespan yields None → LifespanResultT = None
    "Conduit",
    auth=_auth,
    lifespan=Lifespan(_conduit_lifespan),
)


# ---------------------------------------------------------------------------
# Health check endpoint for the ALB
# ---------------------------------------------------------------------------


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(request: Request) -> JSONResponse:
    """ALB health check — always returns HTTP 200 with {"status": "ok"}."""
    return JSONResponse({"status": "ok"})


# ---------------------------------------------------------------------------
# Tools — all business logic delegated to feeds.py and storage.py
# ---------------------------------------------------------------------------


@mcp.tool()
async def add_feed(url: str, label: str | None = None) -> dict[str, str | None]:
    """Add a feed subscription for the authenticated user.

    Validates that *url* is a reachable RSS/Atom feed before storing.
    Propagates :exc:`ValueError` from :func:`feeds.validate_feed` if the URL
    is unreachable or not a valid feed; in that case storage is never touched.
    """
    user_id = _get_user_id()
    await feeds.validate_feed(url)
    storage.add_feed(user_id, url, label)
    return {"url": url, "label": label, "status": "added"}


@mcp.tool()
async def remove_feed(url: str) -> dict[str, str]:
    """Remove a feed subscription for the authenticated user."""
    user_id = _get_user_id()
    storage.remove_feed(user_id, url)
    return {"url": url, "status": "removed"}


@mcp.tool()
async def list_feeds() -> list[dict[str, str | None]]:
    """Return all feed subscriptions for the authenticated user.

    Each item includes ``url``, ``label``, and ``addedAt``.
    """
    user_id = _get_user_id()
    records = storage.list_feeds(user_id)
    return [
        {"url": r["url"], "label": r["label"], "addedAt": r["addedAt"]} for r in records
    ]


@mcp.tool()
async def get_feed_items(url: str, limit: int = 50) -> list[feeds.FeedItem]:
    """Fetch live items from a single subscribed feed.

    Args:
        url: Feed URL.  Must be an active subscription for this user.
        limit: Maximum number of items to return.

    Raises:
        ValueError: If the authenticated user is not subscribed to *url*.
    """
    user_id = _get_user_id()
    record = storage.get_feed(user_id, url)
    if record is None:
        raise ValueError(f"Not subscribed to feed: {url!r}")
    return await feeds.fetch_items(url, limit)


@mcp.tool()
async def get_all_items(limit: int = 200) -> list[feeds.AggregatedFeedItem]:
    """Fetch a headline index across all subscribed feeds (no summaries).

    Returns title, link, published, and feed_url for each item — summaries are
    intentionally omitted to keep the payload small.  Use this tool to scan
    and filter headlines, then call get_feed_items for full content on feeds
    of interest.

    *limit* is divided evenly across feeds (``per_feed_limit = limit //
    len(feeds)``).  Returns an empty list if no feeds are subscribed.  Sort
    and filter by ``published`` timestamp on the LLM side.
    """
    user_id = _get_user_id()
    records = storage.list_feeds(user_id)
    if not records:
        return []
    per_feed_limit = limit // len(records)
    urls = [r["url"] for r in records]
    return await feeds.fetch_all_items(urls, per_feed_limit)


# ---------------------------------------------------------------------------
# ASGI application and entry point
# ---------------------------------------------------------------------------

app = mcp.http_app()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
