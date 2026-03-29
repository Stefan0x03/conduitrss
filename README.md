# Conduit

A self-hosted RSS reader exposed as an [MCP](https://modelcontextprotocol.io/) service, deployed on AWS ECS Fargate. Authenticated via AWS Cognito OAuth and callable directly from Claude on iPhone, iPad, and desktop.

## How it works

Conduit runs as a FastMCP HTTP server behind an ALB. Claude authenticates via OAuth (proxied through FastMCP to AWS Cognito), then calls MCP tools to manage RSS subscriptions and fetch feed content. Feed items are fetched live on each tool call — there is no background polling or caching layer.

```
Claude → OAuth (Cognito) → ALB → ECS Fargate (Conduit) → DynamoDB
                                                         → RSS feeds (live HTTP)
```

## MCP Tools

| Tool | Description |
|---|---|
| `add_feed(url, label?)` | Subscribe to an RSS/Atom feed. Validates the URL before storing. |
| `remove_feed(url)` | Unsubscribe from a feed. |
| `list_feeds()` | List all subscribed feeds with URL, label, and addedAt. |
| `get_feed_items(url, limit=50)` | Fetch live items from one feed. Returns title, link, published, summary. |
| `get_all_items(limit=200)` | Fetch headlines across all subscribed feeds (no summaries). Use for time-based filtering on the LLM side. |
| `get_article_content(url)` | Fetch and extract full plain-text content of an article. Returns title, author, published, content (≤100k chars). |

## Project Structure

```
conduit/
├── src/conduit/
│   ├── server.py      # FastMCP app, OAuth wiring, tool registration
│   ├── feeds.py       # HTTP fetch + parse (feedparser), article extraction (trafilatura)
│   └── storage.py     # DynamoDB CRUD for feed subscriptions
├── infra/
│   ├── vpc.yaml
│   ├── storage.yaml   # DynamoDB table
│   ├── cognito.yaml   # Cognito user pool + app client
│   ├── networking.yaml # ALB, ACM cert, Route 53
│   ├── ecs.yaml       # ECS Fargate cluster, task def, IAM roles
│   └── params/
│       ├── dev.json
│       └── prod.json
├── seeds/feeds.opml   # Replace with your OPML export for local dev
├── tests/
├── docker-compose.yml
├── Dockerfile
└── Makefile
```

## Local Development

**Prerequisites:** Docker, Docker Compose, AWS CLI (for production deploys)

```bash
# Replace seeds/feeds.opml with your own OPML export first (optional)
make local
```

This starts:
- Conduit on `http://localhost:8000` with `AUTH_DISABLED=true`
- DynamoDB Local on port 8001
- An init container that creates the `conduit-feeds` table

On startup, Conduit parses `seeds/feeds.opml` and pre-populates the feed list for the local user.

Connect Claude Desktop or any MCP client to `http://localhost:8000/mcp`.

## Deployment

### Dev

Five CloudFormation stacks, deployed in order:

```bash
make deploy-all ENV=dev
```

Or individually:

```bash
make deploy-vpc ENV=dev
make deploy-storage ENV=dev
make deploy-cognito ENV=dev
make deploy-cognito-secret ENV=dev   # stores client secret in SSM Parameter Store
make deploy-networking ENV=dev
make deploy-image                    # builds and pushes Docker image to ECR
make deploy-ecs ENV=dev
```

### Prod

Same sequence, passing `ENV=prod`:

```bash
make deploy-all ENV=prod
```

Before deploying prod for the first time:

1. Set `Domain` in `infra/params/prod.json` to your production domain (e.g. `conduit.example.com`).
2. Ensure a Route 53 public hosted zone exists for the root domain in the same AWS account — `make deploy-networking` looks it up automatically.
3. After `deploy-cognito`, run `make deploy-cognito-secret ENV=prod` to store the Cognito client secret in SSM Parameter Store at `/conduit/prod/cognito-client-secret`. The ECS task reads it from there at runtime.
4. Create a Cognito user for each person who should have access: `aws cognito-idp admin-create-user --user-pool-id <id> --username <email>`.

Environment parameters live in `infra/params/dev.json` and `infra/params/prod.json`. The `Domain` parameter must match a Route 53 hosted zone in the same account.

To tear down:

```bash
make destroy-dev    # or destroy-prod, destroy-all
```

## Authorization Model

Every MCP tool call is scoped to the authenticated user. Conduit extracts the `sub` claim from the Cognito JWT and uses it as a partition key (`user#<sub>`) in DynamoDB. This means:

- A user can only list, add, or remove their own feed subscriptions — there is no cross-user visibility.
- `get_feed_items` enforces subscription ownership: it rejects requests for feeds the calling user has not subscribed to.
- `get_article_content` does not check subscriptions — any authenticated user can fetch article content from any URL, since the URL comes from feed items they already have access to.
- In local dev (`AUTH_DISABLED=true`), all operations use the hardcoded identity `local-dev-user`. This mode must never be enabled in production.

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `AUTH_DISABLED` | No | Set to `true` to bypass Cognito. **Never set in production.** |
| `DYNAMODB_TABLE` | Yes | DynamoDB table name (`conduit-feeds`) |
| `AWS_REGION` | Yes | AWS region |
| `COGNITO_USER_POOL_ID` | Prod | Cognito User Pool ID |
| `COGNITO_APP_CLIENT_ID` | Prod | Cognito App Client ID |
| `COGNITO_CLIENT_SECRET` | Prod | Cognito App Client secret |
| `DOMAIN` | Prod | Service domain (e.g. `conduit.example.com`) |

## Development Commands

```bash
make lint       # ruff check src/
make format     # ruff format src/
make typecheck  # mypy src/
make test       # pytest
```

## Tech Stack

- **Python 3.12**, FastMCP, feedparser, trafilatura, boto3
- **AWS:** ECS Fargate, ALB, ACM, Cognito, DynamoDB, Route 53, SSM, ECR
- **Infra:** CloudFormation, Docker (multi-stage, non-root)
- **Quality:** ruff, mypy (strict), pytest, gitleaks (pre-commit)
