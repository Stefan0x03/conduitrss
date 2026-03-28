# Conduit

A self-hosted RSS reader MCP service deployed on AWS ECS Fargate, exposing MCP tools over HTTP authenticated via AWS Cognito OAuth proxied through FastMCP. Callable from Claude on iPhone, iPad, and personal computers.

---

## Project Structure

```
conduit/
├── src/
│   └── conduit/
│       ├── server.py          # FastMCP app, OAuth wiring, tool registration
│       ├── feeds.py           # Live HTTP fetch + parse via feedparser
│       └── storage.py         # DynamoDB CRUD for feed subscriptions
├── infra/
│   ├── cognito.yaml           # Cognito user pool + app client
│   ├── ecs.yaml               # ECS Fargate cluster, task def, service, IAM roles
│   ├── networking.yaml        # ALB, listener, target group, ACM cert
│   ├── storage.yaml           # DynamoDB table
│   └── params/
│       ├── dev.json
│       └── prod.json
├── tests/
│   ├── unit/
│   └── integration/
├── seeds/
│   └── feeds.opml             # InoReader OPML export — replace before local dev
├── docker-compose.yml
├── Dockerfile
├── Makefile
├── pyproject.toml
├── .pre-commit-config.yaml
└── CLAUDE.md
```

---

## Tech Stack

| Concern | Choice |
|---|---|
| Language | Python 3.12 |
| MCP framework | FastMCP (latest stable — resolve and pin at project init) |
| RSS/Atom parsing | feedparser |
| AWS SDK | boto3 |
| Linting + formatting | ruff |
| Type checking | mypy (strict) |
| Testing | pytest |
| Secret scanning | gitleaks (via pre-commit) |

---

## Architecture

### Overview

- ECS Fargate runs the Conduit container behind an ALB
- ALB terminates TLS using an ACM certificate for `YOUR_DOMAIN`
- FastMCP proxies OAuth flows to AWS Cognito
- DynamoDB stores per-user feed subscriptions
- Feeds are fetched live on each MCP tool call — no caching or polling

### Authentication

- AWS Cognito User Pool is the OAuth provider
- FastMCP's OAuth adapter proxies the auth flow so Claude sees Conduit as the OAuth server
- User identity is derived from the Cognito JWT `sub` claim
- `AUTH_DISABLED=true` bypasses Cognito entirely for local development, using a hardcoded local user ID

### DynamoDB Table Design

Table name: `conduit-feeds`

| Key | Value | Notes |
|---|---|---|
| PK | `user#{userId}` | Cognito `sub` claim |
| SK | `feed#{feedUrl}` | Full feed URL |
| `label` | string | Optional display name |
| `addedAt` | ISO 8601 string | When the feed was added |
| `lastFetched` | ISO 8601 string | Reserved for future caching |
| `etag` | string | Reserved for future HTTP conditional requests |

`lastFetched` and `etag` are reserved for a future caching layer and should be stored but not acted on yet.

### CloudFormation Stacks

Four stacks, deployed in this order:

1. `storage.yaml` — DynamoDB table
2. `cognito.yaml` — Cognito user pool and app client
3. `networking.yaml` — ALB, listener, target group, ACM cert
4. `ecs.yaml` — ECS Fargate cluster, task definition, service, IAM roles

All environment-specific values live in `params/dev.json` and `params/prod.json`. Use `YOUR_DOMAIN` as the domain placeholder throughout — replace before deploying.

---

## MCP Tools

Implement the following tools in `server.py`. All tools require an authenticated user unless `AUTH_DISABLED=true`.

```python
add_feed(url: str, label: str | None = None) -> dict
# Adds a feed subscription for the authenticated user.
# Validates that url is a reachable RSS/Atom feed before storing.

remove_feed(url: str) -> dict
# Removes a feed subscription for the authenticated user.

list_feeds() -> list[dict]
# Returns all feed subscriptions for the authenticated user.
# Each item includes url, label, addedAt.

get_feed_items(url: str, limit: int = 50) -> list[dict]
# Fetches live items from a single feed.
# Returns title, link, published, summary for each item.

get_all_items(limit: int = 200) -> list[dict]
# Fetches live items across all subscribed feeds.
# Returns title, link, published, feed_url for each item (no summary).
# Use get_feed_items to retrieve full content including summary for specific feeds.
# Default limit is generous to support LLM-side time filtering.
```

**Design note:** Time-based filtering (e.g. "articles from the last 24 hours") is intentionally left to the LLM consumer. Tools return raw items with `published` timestamps and let Claude filter. Keep `limit` defaults generous enough to cover typical time windows.

---

## Module Responsibilities

### `server.py`
- FastMCP app instantiation
- OAuth wiring and Cognito adapter configuration
- `AUTH_DISABLED` env var handling
- Tool registration (imports from `feeds.py` and `storage.py`)
- No business logic

### `feeds.py`
- Live HTTP fetching via feedparser
- Feed validation (confirm URL is a valid RSS/Atom feed)
- Item normalization (consistent dict shape regardless of RSS vs Atom)
- No AWS dependencies

### `storage.py`
- DynamoDB CRUD operations for feed subscriptions
- All table key construction (`user#{userId}`, `feed#{feedUrl}`)
- No feed fetching or parsing logic

---

## Local Development

### Running Locally

```bash
make local
```

This starts the Conduit container via docker-compose with:
- `AUTH_DISABLED=true` — Cognito bypass, hardcoded local user ID
- OPML seed file mounted at `seeds/feeds.opml`
- Port 8000 exposed

### OPML Seeding

At startup in local dev, parse `seeds/feeds.opml` and pre-populate the feed list for the local user. The file at `seeds/feeds.opml` is a placeholder skeleton — replace it with your InoReader OPML export before running locally.

### Auth Bypass

When `AUTH_DISABLED=true`:
- Skip all Cognito token validation
- Use a hardcoded user ID (e.g. `local-dev-user`) for all DynamoDB operations
- This env var must never be set in production

---

## Dockerfile

Use a multi-stage build:

1. **Builder stage** — Python 3.12, installs all dependencies via pip
2. **Runtime stage** — Slim Python 3.12 image, copies only installed packages and source
3. Run as a non-root user
4. No secrets or credentials baked into the image — all config via environment variables

---

## Makefile Targets

| Target | Description |
|---|---|
| `make local` | Start docker-compose for local dev |
| `make deploy-storage` | Deploy `infra/storage.yaml` |
| `make deploy-cognito` | Deploy `infra/cognito.yaml` |
| `make deploy-networking` | Deploy `infra/networking.yaml` |
| `make deploy-ecs` | Deploy `infra/ecs.yaml` |
| `make deploy-all` | Deploy all stacks in order |
| `make lint` | Run `ruff check src/` |
| `make format` | Run `ruff format src/` |
| `make typecheck` | Run `mypy src/` |
| `make test` | Run `pytest` |

---

## Code Quality

### Ruff

- Used for both linting and formatting (replaces black, isort, flake8)
- Target: Python 3.12
- Run per-file after every Claude Code file write via postToolUse hook

### Mypy

- Strict mode enabled (`--strict`)
- `ignore_missing_imports = true` for third-party libraries (feedparser, boto3 stubs are imperfect)
- Run on the full project after every Claude Code file write via postToolUse hook
- All source code must pass mypy before committing

### Type Annotations

- Required on all functions and methods
- No `Any` unless explicitly justified with a comment

---

## Hooks

### pre-commit (gitleaks)

Configured in `.pre-commit-config.yaml`. Scans every commit for secrets before they are recorded.

Install after scaffolding:

```bash
pip install pre-commit
pre-commit install
```

### postToolUse (Claude Code)

After every file write, Claude Code runs:

1. `ruff check --fix <file>` — lint the written file
2. `ruff format <file>` — format the written file
3. `mypy src/` — type-check the full project

Configure in `.claude/settings.json`:

```json
{
  "hooks": {
    "postToolUse": [
      {
        "matcher": "Write|Edit|MultiEdit",
        "hooks": [
          {
            "type": "command",
            "command": "ruff check --fix $CLAUDE_TOOL_RESULT_FILE && ruff format $CLAUDE_TOOL_RESULT_FILE && mypy src/"
          }
        ]
      }
    ]
  }
}
```

---

## pyproject.toml Structure

```toml
[project]
name = "conduit"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastmcp",       # pin to latest stable at init
    "feedparser",
    "boto3",
]

[project.optional-dependencies]
dev = [
    "pytest",
    "ruff",
    "mypy",
    "pre-commit",
    "boto3-stubs[dynamodb]",
]

[tool.ruff]
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP"]

[tool.mypy]
strict = true
ignore_missing_imports = true
```

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `AUTH_DISABLED` | No | Set to `true` to bypass Cognito in local dev |
| `DYNAMODB_TABLE` | Yes | DynamoDB table name (`conduit-feeds`) |
| `AWS_REGION` | Yes | AWS region |
| `COGNITO_USER_POOL_ID` | Prod only | Cognito User Pool ID |
| `COGNITO_APP_CLIENT_ID` | Prod only | Cognito App Client ID |
| `DOMAIN` | Prod only | Service domain (`YOUR_DOMAIN`) |

---

## Future Considerations

The following are out of scope for the initial implementation but the codebase should not foreclose them:

- **Caching** — `lastFetched` and `etag` columns are already in the table schema. A future enhancement would store ETags from feed HTTP responses and use `If-None-Match` on subsequent fetches to avoid re-downloading unchanged feeds.
- **Polling** — An EventBridge scheduled rule triggering a Lambda or ECS task could pre-fetch and cache feed items, making tool calls instantaneous.
- **Multi-account deployment** — Currently single AWS account. Params structure supports graduating to separate dev/prod accounts.
- **Additional MCP tools** — `mark_read`, `search_items`, `get_feed_stats` are natural extensions once the core is stable.

---

## Scaffold Instructions

When starting a new session, read this file first, then await instructions. Do not generate application code unless explicitly asked. Scaffold, infrastructure, and configuration files should be generated before any Python source code.
