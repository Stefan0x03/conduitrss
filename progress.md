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
- [ ] `pre-commit install` — activate gitleaks hook in local git repo
- [ ] `[tool.pytest.ini_options]` added to `pyproject.toml` — separate unit/integration via markers

---

## Stage 1 — `storage.py` + unit tests

- [ ] Define `FeedRecord` TypedDict: `pk`, `sk`, `url`, `label`, `addedAt`, `lastFetched`, `etag`
- [ ] `DYNAMODB_TABLE` sourced from environment variable, fail fast if missing
- [ ] All key construction (`user#{userId}`, `feed#{feedUrl}`) contained in this module
- [ ] Implement `add_feed(user_id, url, label)` — PutItem with full key schema; `lastFetched` and `etag` stored as empty strings (reserved)
- [ ] Implement `remove_feed(user_id, url)` — DeleteItem
- [ ] Implement `list_feeds(user_id)` — Query by PK, return list of `FeedRecord`
- [ ] Implement `get_feed(user_id, url)` — GetItem, return `FeedRecord | None` (used by server to verify subscription ownership)
- [ ] Passes `mypy --strict` and `ruff`
- [ ] `tests/unit/test_storage.py`
  - [ ] Mock boto3 client
  - [ ] Test key construction for `add_feed`
  - [ ] Test `add_feed` — correct PutItem shape including reserved fields
  - [ ] Test `remove_feed` — correct DeleteItem call
  - [ ] Test `list_feeds` — correct Query and return shape
  - [ ] Test `get_feed` — returns `FeedRecord` when found, `None` when not found

---

## Stage 2 — `feeds.py` + unit tests

- [ ] Define `FeedItem` TypedDict: `title`, `link`, `published`, `summary`
- [ ] Define `AggregatedFeedItem` TypedDict: `title`, `link`, `published`, `summary`, `feed_url`
- [ ] Implement `validate_feed(url)` — confirm URL is reachable and parses as RSS/Atom; raise on failure
- [ ] Implement `fetch_items(url, limit)` — live fetch single feed, return `list[FeedItem]`; run `feedparser.parse()` in thread pool executor
- [ ] Implement `fetch_all_items(urls, per_feed_limit)` — concurrent fetch via `asyncio.gather`, return `list[AggregatedFeedItem]`
- [ ] Normalize field differences between RSS 2.0 and Atom (`published` vs `updated`, `summary` vs `content`)
- [ ] Handle `bozo` flag and malformed feeds gracefully (log warning, return empty list for that feed)
- [ ] No AWS dependencies in this module
- [ ] Passes `mypy --strict` and `ruff`
- [ ] `tests/unit/test_feeds.py`
  - [ ] Mock `feedparser.parse`
  - [ ] Test `FeedItem` normalization for RSS 2.0 feed
  - [ ] Test `FeedItem` normalization for Atom feed
  - [ ] Test `validate_feed` rejects non-feed URLs
  - [ ] Test `bozo` feed returns empty list without raising
  - [ ] Test `fetch_all_items` calls `fetch_items` concurrently and attaches `feed_url`

---

## Stage 3 — `server.py` + unit tests

- [ ] Inspect `fastmcp==3.1.1` source to confirm OAuth proxy API before writing
- [ ] Instantiate `FastMCP` app
- [ ] Wire Cognito OAuth adapter (authorization endpoint, token endpoint, JWKS)
- [ ] Extract authenticated user `sub` claim from FastMCP `Context`
- [ ] Implement `AUTH_DISABLED=true` branch — skip OAuth, inject `local-dev-user`
- [ ] Health check endpoint (`/healthz`) for ALB
- [ ] OPML seed loader — parse `seeds/feeds.opml` at startup when `AUTH_DISABLED=true`, pre-populate DynamoDB for `local-dev-user`
- [ ] `if __name__ == "__main__"` entry point so `python -m conduit.server` works
- [ ] Register `add_feed` tool — calls `feeds.validate_feed(url)` first, then `storage.add_feed()`
- [ ] Register `remove_feed` tool — calls `storage.remove_feed()`
- [ ] Register `list_feeds` tool — calls `storage.list_feeds()`
- [ ] Register `get_feed_items` tool — calls `storage.get_feed()` to verify subscription, then `feeds.fetch_items()`
- [ ] Register `get_all_items` tool — calls `storage.list_feeds()`, computes `per_feed_limit = limit // len(feeds)`, calls `feeds.fetch_all_items(urls, per_feed_limit)`
- [ ] No business logic in this module
- [ ] Passes `mypy --strict` and `ruff`
- [ ] `tests/unit/test_server.py`
  - [ ] Test `AUTH_DISABLED` identity injection
  - [ ] Test `add_feed` calls `validate_feed` before `storage.add_feed`
  - [ ] Test `get_feed_items` raises when user is not subscribed
  - [ ] Test `get_all_items` computes correct `per_feed_limit`

---

## Stage 4 — Integration Tests

- [ ] `tests/integration/conftest.py` — fixtures: DynamoDB Local client, pre-created table, seeded `local-dev-user`
- [ ] `tests/integration/test_server.py` against DynamoDB Local with `AUTH_DISABLED=true`
  - [ ] `add_feed` end-to-end (validates feed, stores subscription)
  - [ ] `add_feed` duplicate is idempotent
  - [ ] `remove_feed` end-to-end
  - [ ] `remove_feed` on non-existent feed is handled gracefully
  - [ ] `list_feeds` end-to-end
  - [ ] `get_feed_items` rejects unsubscribed URL
  - [ ] `get_feed_items` end-to-end for subscribed feed
  - [ ] `get_all_items` end-to-end across multiple feeds
  - [ ] `get_all_items` respects per-feed cap

---

## Design Decisions (Recorded)

- **`get_all_items` limit**: cap applied per feed (`per_feed_limit = limit // len(feeds)`) before fetching. LLM is expected to sort and filter the results.
- **`get_feed_items` ownership**: server verifies the authenticated user is subscribed to the requested URL via `storage.get_feed()` before fetching.
- **`bozo` feeds**: log a warning and return an empty list for that feed rather than failing the whole request.
- **`lastFetched` / `etag`**: stored as empty strings in DynamoDB, not acted on (reserved for future caching layer).

---

## Open Questions

- How does FastMCP 3.1.1 expose user identity from the Cognito JWT in tool context? (Inspect source at Stage 3 start)
- Does FastMCP 3.1.1 support adding plain HTTP routes (e.g. `/healthz`) alongside MCP tools?
