# Conduit — Implementation Progress

## Status Key
- [ ] Not started
- [~] In progress
- [x] Complete

---

## Scaffolding

- [x] `pyproject.toml` — deps pinned (fastmcp==3.1.1), ruff + mypy config
- [x] `Dockerfile` — multi-stage, non-root user
- [x] `docker-compose.yml` — conduit + DynamoDB Local
- [x] `Makefile` — all targets
- [x] `.pre-commit-config.yaml` — gitleaks hook configured
- [x] `.gitignore`
- [x] `.claude/settings.json` — postToolUse ruff + mypy hooks
- [x] `infra/storage.yaml` — DynamoDB table (SSE, PITR, PAY_PER_REQUEST)
- [x] `infra/cognito.yaml` — User Pool + App Client
- [x] `infra/networking.yaml` — ALB, HTTPS listener, target group
- [x] `infra/ecs.yaml` — Fargate cluster, task def, service, IAM roles
- [x] `infra/params/dev.json` — placeholder values
- [x] `infra/params/prod.json` — placeholder values
- [x] `seeds/feeds.opml` — skeleton (replace with InoReader export)
- [x] `src/conduit/__init__.py`
- [x] `tests/{unit,integration}/__init__.py`
- [x] `.venv` created with Python 3.12.10
- [x] `pre-commit install` — activate gitleaks hook in local git repo
- [x] `[tool.pytest.ini_options]` added to `pyproject.toml` — separate unit/integration via markers

---

## Stage 1 — `storage.py` + unit tests

- [x] Define `FeedRecord` TypedDict: `pk`, `sk`, `url`, `label`, `addedAt`, `lastFetched`, `etag`
- [x] `DYNAMODB_TABLE` sourced from environment variable, fail fast if missing
- [x] All key construction (`user#{userId}`, `feed#{feedUrl}`) contained in this module
- [x] Implement `add_feed(user_id, url, label)` — PutItem with full key schema; `lastFetched` and `etag` stored as empty strings (reserved)
- [x] Implement `remove_feed(user_id, url)` — DeleteItem
- [x] Implement `list_feeds(user_id)` — Query by PK, return list of `FeedRecord`
- [x] Implement `get_feed(user_id, url)` — GetItem, return `FeedRecord | None` (used by server to verify subscription ownership)
- [x] Passes `mypy --strict` and `ruff`
- [x] `tests/unit/test_storage.py`
  - [x] Mock boto3 client
  - [x] Test key construction for `add_feed`
  - [x] Test `add_feed` — correct PutItem shape including reserved fields
  - [x] Test `remove_feed` — correct DeleteItem call
  - [x] Test `list_feeds` — correct Query and return shape
  - [x] Test `get_feed` — returns `FeedRecord` when found, `None` when not found

---

## Stage 2 — `feeds.py` + unit tests

- [x] Define `FeedItem` TypedDict: `title`, `link`, `published`, `summary`
- [x] Define `AggregatedFeedItem` TypedDict: `title`, `link`, `published`, `summary`, `feed_url`
- [x] Implement `validate_feed(url)` — confirm URL is reachable and parses as RSS/Atom; raise on failure
- [x] Implement `fetch_items(url, limit)` — live fetch single feed, return `list[FeedItem]`; run `feedparser.parse()` in thread pool executor
- [x] Implement `fetch_all_items(urls, per_feed_limit)` — concurrent fetch via `asyncio.gather`, return `list[AggregatedFeedItem]`
- [x] Normalize field differences between RSS 2.0 and Atom (`published` vs `updated`, `summary` vs `content`)
- [x] Handle `bozo` flag and malformed feeds gracefully (log warning, return empty list for that feed)
- [x] No AWS dependencies in this module
- [x] Passes `mypy --strict` and `ruff`
- [x] `tests/unit/test_feeds.py`
  - [x] Mock `feedparser.parse`
  - [x] Test `FeedItem` normalization for RSS 2.0 feed
  - [x] Test `FeedItem` normalization for Atom feed
  - [x] Test `validate_feed` rejects non-feed URLs
  - [x] Test `bozo` feed returns empty list without raising
  - [x] Test `fetch_all_items` calls `fetch_items` concurrently and attaches `feed_url`

---

## Stage 3 — `server.py` + unit tests

- [x] Inspect `fastmcp==3.1.1` source to confirm OAuth proxy API before writing
- [x] Instantiate `FastMCP` app
- [x] Wire Cognito OAuth adapter (authorization endpoint, token endpoint, JWKS)
- [x] Extract authenticated user `sub` claim from FastMCP `Context`
- [x] Implement `AUTH_DISABLED=true` branch — skip OAuth, inject `local-dev-user`
- [x] Health check endpoint (`/healthz`) for ALB
- [x] OPML seed loader — parse `seeds/feeds.opml` at startup when `AUTH_DISABLED=true`, pre-populate DynamoDB for `local-dev-user`
- [x] `if __name__ == "__main__"` entry point so `python -m conduit.server` works
- [x] Register `add_feed` tool — calls `feeds.validate_feed(url)` first, then `storage.add_feed()`
- [x] Register `remove_feed` tool — calls `storage.remove_feed()`
- [x] Register `list_feeds` tool — calls `storage.list_feeds()`
- [x] Register `get_feed_items` tool — calls `storage.get_feed()` to verify subscription, then `feeds.fetch_items()`
- [x] Register `get_all_items` tool — calls `storage.list_feeds()`, computes `per_feed_limit = limit // len(feeds)`, calls `feeds.fetch_all_items(urls, per_feed_limit)`
- [x] No business logic in this module
- [x] Passes `mypy --strict` and `ruff`
- [x] `tests/unit/test_server.py`
  - [x] Test `AUTH_DISABLED` identity injection
  - [x] Test `add_feed` calls `validate_feed` before `storage.add_feed`
  - [x] Test `get_feed_items` raises when user is not subscribed
  - [x] Test `get_all_items` computes correct `per_feed_limit`
  - [x] Test `get_all_items` handles zero subscribed feeds without dividing by zero

---

## Stage 4 — Integration Tests

- [x] `tests/integration/conftest.py` — fixtures: DynamoDB Local client, pre-created table, seeded `local-dev-user`
- [x] `tests/integration/test_server.py` against DynamoDB Local with `AUTH_DISABLED=true`
  - [x] `add_feed` end-to-end (validates feed, stores subscription)
  - [x] `add_feed` duplicate is idempotent
  - [x] `remove_feed` end-to-end
  - [x] `remove_feed` on non-existent feed is handled gracefully
  - [x] `list_feeds` end-to-end
  - [x] `get_feed_items` rejects unsubscribed URL
  - [x] `get_feed_items` end-to-end for subscribed feed
  - [x] `get_all_items` end-to-end across multiple feeds
  - [x] `get_all_items` respects per-feed cap

---

## Design Decisions (Recorded)

- **`get_all_items` limit**: cap applied per feed (`per_feed_limit = limit // len(feeds)`) before fetching. LLM is expected to sort and filter the results.
- **`get_feed_items` ownership**: server verifies the authenticated user is subscribed to the requested URL via `storage.get_feed()` before fetching.
- **`bozo` feeds**: log a warning and return an empty list for that feed rather than failing the whole request.
- **`lastFetched` / `etag`**: stored as empty strings in DynamoDB, not acted on (reserved for future caching layer).

---

## Open Questions

- ~~How does FastMCP 3.1.1 expose user identity from the Cognito JWT in tool context?~~ → `get_access_token()` from `fastmcp.server.dependencies`; the `sub` claim is in `token.claims["sub"]`.
- ~~Does FastMCP 3.1.1 support adding plain HTTP routes (e.g. `/healthz`) alongside MCP tools?~~ → Yes, via `@mcp.custom_route("/healthz", methods=["GET"])` decorator.
