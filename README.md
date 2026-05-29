# sed.i

**A personal reading and writing workspace with hybrid search and an AI interface to your library.**

[read-sedi.com](https://read-sedi.com) · [Changelog](docs/changelog/) · v0.4.0

---

Save URLs from anywhere, read them distraction-free, highlight and connect ideas across articles, and search your library with a query classifier that routes to keyword, semantic, or filtered search — all in under a millisecond, no LLM call. Claude can query your library directly via an MCP server with full OAuth 2.1.

## Features

- **Ingestion** — paste a URL or send it from the browser extension; metadata, full text, and embeddings are extracted in the background
- **Reader** — clean article view with scroll progress, table of contents, and reading time tracking
- **Hybrid search** — query classifier routes to tsvector keyword search, pgvector semantic search, SQL filter, or RRF-fused hybrid based on query shape; no LLM in the hot path
- **Highlights** — select text to highlight with color and notes; cross-article connections discovered via embedding similarity
- **Lists and writing workspace** — group articles into named lists, draft notes alongside them in a split-pane writing environment
- **Browser extension** — save and read articles from Chrome and Safari; includes an ephemeral reader overlay before saving
- **MCP server** — connect Claude Desktop or claude.ai to your library via OAuth 2.1 + PKCE; search, summarize, and query content without leaving your AI assistant
- **Multi-provider AI** — OpenAI or AWS Bedrock, switchable via environment variable with no code change

## Stack

| Layer | Technology |
|-------|-----------|
| Frontend | Next.js 14 (App Router), TypeScript, Tailwind CSS v4 |
| Backend | FastAPI, SQLAlchemy, Alembic, Python 3.11 |
| Database | PostgreSQL 16 + pgvector |
| Queue | Celery + Redis; Prefect for durable pipeline orchestration (opt-in) |
| Search | tsvector (keyword) + pgvector cosine similarity (semantic) + RRF fusion; sub-millisecond query classifier, no LLM in the hot path |
| AI — LLM | OpenAI GPT-4o-mini / GPT-4o or AWS Bedrock Nova / Claude — switchable per task via `LLM_PROVIDER` |
| AI — Embeddings | OpenAI `text-embedding-3-small` (1536-dim) or AWS Bedrock Titan Embed — switchable via `EMBED_PROVIDER` |
| AI — IaC | Pulumi (AWS: IAM, S3, Bedrock access policies) |
| Storage | S3 for PDF assets; presigned URL delivery |
| Observability | Braintrust (LLM tracing), Sentry (errors), OpenTelemetry → Grafana Cloud (distributed traces across FastAPI + SQLAlchemy + Celery) |
| Evals | pytest-based eval harness; classification accuracy and search quality gates run in CI |
| Extension | Chrome Manifest V3 + Safari Web Extension |
| MCP | FastMCP, Streamable HTTP, OAuth 2.1 + PKCE; `query_library` tool runs natural language → SQL with AST-level security validation |
| Infra | Railway (API + Celery worker), Vercel (frontend), Cloudflare Workers (CORS proxy + WAF) |

## Quick start

**Prerequisites:** Docker, Python 3.11, pyenv, Node.js 20+, pnpm

```bash
# 1. Start Postgres + Redis
docker compose up -d

# 2. Configure environment
cp content-queue-backend/.env.example content-queue-backend/.env
# Fill in SECRET_KEY and OPENAI_API_KEY at minimum

# 3. Install git hooks
make install-hooks

# 4. Start everything
make dev
```

Open [http://localhost:3000](http://localhost:3000), register an account, and paste a URL to get started.

All environment variables are documented in `content-queue-backend/.env.example`.

## MCP integration

Connect Claude Desktop or claude.ai to your sed.i library:

```json
{
  "mcpServers": {
    "sedi": {
      "url": "https://api.read-sedi.com/mcp-transport/mcp"
    }
  }
}
```

Claude will prompt for authentication on first connection. Once authorized, it can search your library, summarize lists, retrieve highlights, and find similar content.

Self-hosting the MCP server: see [`docs/design/systems/mcp-wiki.md`](docs/design/systems/mcp-wiki.md) for the full OAuth flow, transport architecture, and Cloudflare Worker setup.

## Development

```bash
make test          # backend (pytest) + frontend (jest)
make lint          # ruff + tsc + eslint
make migrate       # apply pending Alembic migrations
make test-backend  # backend only
```

Pattern and convention references:
- Backend — [`docs/instructions/backend-patterns.md`](docs/instructions/backend-patterns.md)
- Frontend — [`docs/instructions/frontend-patterns.md`](docs/instructions/frontend-patterns.md)
- Testing — [`docs/instructions/testing-standards.md`](docs/instructions/testing-standards.md)

## Deployment

Backend and Celery worker on Railway, frontend on Vercel. See [`docs/instructions/deploy-to-prod.md`](docs/instructions/deploy-to-prod.md) for the full procedure including environment variable setup, migration strategy, and Railway service configuration.

## License

MIT
