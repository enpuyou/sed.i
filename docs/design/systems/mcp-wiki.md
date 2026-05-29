---
type: design
status: active
last_updated: 2026-05-28
consumer: both
---

# sed.i MCP Server — Technical Wiki

> **Audience:** Developer building or maintaining the sed.i MCP integration. Covers how MCP works conceptually, every architectural decision we made, the full OAuth 2.1 + PKCE flow, the Cloudflare/Railway production topology, all deployed tools, and practical troubleshooting. Written to be useful as interview prep as well as operational reference.

---

## Table of Contents

1. [What is MCP and why sed.i uses it](#1-what-is-mcp-and-why-sedi-uses-it)
2. [How MCP works end-to-end](#2-how-mcp-works-end-to-end)
3. [Transport: Streamable HTTP](#3-transport-streamable-http)
4. [OAuth 2.1 + PKCE flow](#4-oauth-21--pkce-flow)
5. [Production topology](#5-production-topology)
6. [Cloudflare Worker](#6-cloudflare-worker)
7. [Architectural decisions and why](#7-architectural-decisions-and-why)
8. [All deployed tools](#8-all-deployed-tools)
9. [Connection setup](#9-connection-setup)
10. [Deploying and updating the Cloudflare Worker](#10-deploying-and-updating-the-cloudflare-worker)
11. [mcp-remote patch for Claude Desktop](#11-mcp-remote-patch-for-claude-desktop)
12. [Troubleshooting](#12-troubleshooting)

---

## 1. What is MCP and why sed.i uses it

**Model Context Protocol (MCP)** is an open standard from Anthropic that lets LLMs connect to external tools and data sources through a uniform interface. The core insight: instead of writing bespoke API integrations for every AI client, you build one MCP server and every MCP-capable client — Claude Desktop, claude.ai, Cursor, Cline, whatever ships next year — can use it without any additional work on our end.

### Why MCP instead of exposing the existing REST API directly

sed.i already has a REST API. The difference is not technical capability — it is discovery and orchestration:

| | REST API | MCP server |
| --- | --- | --- |
| Who calls it | sed.i frontend, with hand-written code | Any MCP client, LLM decides |
| Discovery | Manual: read the docs | Automatic: LLM asks `tools/list` |
| Chaining | You write the orchestration logic | LLM decides how to chain tool calls |
| Per-client integration | Yes, every new client needs work | None |
| Auth | Frontend JWT | OAuth 2.1 per user, per client |

The practical result: a user opens Claude and says "Summarize my AI Research list and tell me what my draft is missing." Claude calls `list_lists`, then `summarize_list` with `style: "gaps"`, then synthesizes an answer — all without us writing any Claude-specific code.

---

## 2. How MCP works end-to-end

MCP is built on **JSON-RPC 2.0**: every message is a JSON object with a `method`, `params`, `id`, and either a `result` or `error`. The protocol layer is simple; the value is the tool discovery and invocation convention on top of it.

### Connection lifecycle

```text
1. Client → Server:  initialize  { protocolVersion, clientInfo, capabilities }
2. Server → Client:  initialized { serverInfo, capabilities }
3. Client → Server:  tools/list  {}
4. Server → Client:  { tools: [{ name, description, inputSchema }, ...] }
5. Client → Server:  tools/call  { name: "get_list_content", arguments: { list_id: "..." } }
6. Server → Client:  { content: [{ type: "text", text: "..." }] }
```

The LLM never sees raw JSON. The MCP client layer parses tool results and inserts them into the conversation as context. From the LLM's perspective, it called a function and got back text.

### What a raw tools/call looks like

Request:

```json
{
  "jsonrpc": "2.0",
  "id": 4,
  "method": "tools/call",
  "params": {
    "name": "summarize_list",
    "arguments": { "list_id": "abc-123", "style": "gaps" }
  }
}
```

Response:

```json
{
  "jsonrpc": "2.0",
  "id": 4,
  "result": {
    "content": [{ "type": "text", "text": "**Gaps:** Your draft doesn't address..." }]
  }
}
```

---

## 3. Transport: Streamable HTTP

We use **Streamable HTTP** (not stdio, not SSE-only). This is the MCP 2025-11-25 transport: clients send `POST` requests to a single endpoint, responses may stream via SSE for long-running tools, and the connection is stateless at the HTTP level.

```text
Claude Desktop / claude.ai
        │
        │  POST https://api.read-sedi.com/mcp-transport/mcp
        │  Authorization: Bearer <jwt>
        │  Content-Type: application/json
        ▼
  Cloudflare Worker (CORS proxy)
        │
        ▼
  Railway: FastAPI + FastMCP at /mcp-transport
```

The MCP endpoint is `/mcp-transport/mcp`. See Section 7 for why it is `/mcp-transport` and not `/mcp`.

---

## 4. OAuth 2.1 + PKCE flow

MCP's remote auth spec requires OAuth 2.1 with PKCE. We implemented a full authorization server inside FastAPI. The flow from first connect to first tool call:

### Discovery

The MCP client first fetches the auth server metadata:

```http
GET /.well-known/oauth-authorization-server
```

Response tells the client where to authorize, where to exchange tokens, and what grant types are supported.

```http
GET /.well-known/oauth-protected-resource
```

Response tells the client which auth server protects this resource.

### Authorization (PKCE)

The client generates a random `code_verifier`, hashes it to produce `code_challenge`, then opens the browser:

```http
GET /mcp-transport/authorize
    ?client_id=mcp-client
    &response_type=code
    &redirect_uri=<client-callback>
    &code_challenge=<sha256-base64url(code_verifier)>
    &code_challenge_method=S256
    &state=<random>
```

The user is presented with a sed.i login/consent page. On approval, the server generates an auth code and redirects:

```http
302 → <redirect_uri>?code=<auth_code>&state=<state>
```

Auth codes are stored in Redis with a 5-minute TTL.

### Token exchange

The client exchanges the code for a token:

```http
POST /mcp-transport/token
Content-Type: application/x-www-form-urlencoded

grant_type=authorization_code
&code=<auth_code>
&redirect_uri=<same-as-before>
&client_id=mcp-client
&code_verifier=<original-verifier>
```

The server verifies `sha256(code_verifier) == code_challenge` (PKCE check), then issues a response:

```json
{
  "access_token": "<sed.i JWT>",
  "token_type": "bearer",
  "expires_in": 86400,
  "scope": ""
}
```

The access token is a standard sed.i JWT — no separate token store. `scope` is an empty string (see Section 7 for why).

### Authenticated tool calls

Every subsequent MCP request carries the JWT:

```http
POST /mcp-transport/mcp
Authorization: Bearer <jwt>
```

The existing `_resolve_user_from_bearer` middleware resolves the JWT to a `User` object. All tools filter data by that user — there is no path to another user's data.

---

## 5. Production topology

```text
User's Claude client
        │
        ▼
api.read-sedi.com  (Cloudflare-proxied DNS)
        │
        ▼
Cloudflare Worker  (cloudflare-worker/worker.js)
  - CORS headers injected
  - redirect: "manual" (passes 302s through)
  - Body buffered via arrayBuffer() before forwarding
  - WAF rule skips bot protection for Claude-User UA
        │
        ▼
content-queue-fast-api-production.up.railway.app  (Railway)
  - FastAPI app
  - FastMCP mounted at /mcp-transport via _MCPProxy
  - Auth endpoints at /mcp-transport/authorize, /mcp-transport/token
  - Discovery at /.well-known/oauth-authorization-server
  - Redis for auth code storage
  - PostgreSQL + pgvector for content and embeddings
  - OpenAI API for summarize_list and semantic search
```

There is no separate MCP process. FastMCP is mounted directly inside the FastAPI ASGI app via `app.mount("/mcp-transport", ...)`.

---

## 6. Cloudflare Worker

The Worker (`cloudflare-worker/worker.js`) sits between the public hostname and Railway. Its jobs:

1. **CORS**: Inject `Access-Control-Allow-Origin`, `Access-Control-Allow-Headers`, etc. so browser-based clients (claude.ai web) can make cross-origin requests.
2. **Preflight handling**: Return 200 to `OPTIONS` requests immediately.
3. **Body buffering**: Read the request body via `request.arrayBuffer()` before forwarding. This prevents streaming body errors if Railway issues any redirect.
4. **Redirect passthrough**: Fetch with `redirect: "manual"` so 302 responses from the authorize endpoint are forwarded to the browser untouched instead of being followed server-side.
5. **Header forwarding**: Passes `Authorization`, `Content-Type`, and all relevant headers to Railway.

The Worker does not do auth — that is Railway's job. The Worker is purely infrastructure plumbing.

---

## 7. Architectural decisions and why

Every decision here was made to solve a real production problem. Understanding the "why" matters for debugging and for explaining the system clearly.

### Mount path is `/mcp-transport`, not `/mcp`

Railway uses Fastly as its edge network. We discovered a routing bug: `POST` requests to exactly `/mcp` where the `Authorization` header contains URL-safe base64 characters (`_` and `-`, which JWT tokens use extensively) were being routed to the wrong upstream and returned **421 Misdirected Request**.

Renaming the mount to `/mcp-transport` side-steps this entirely. The path `/mcp-transport` does not trigger the same routing logic. We verified this by trying several path names; `/mcp-transport` was the first that worked consistently.

The lesson: when a CDN/edge layer returns 421, suspect header-based routing rules before suspecting your application code.

### FastMCP DNS rebinding protection is disabled

FastMCP validates the `Host` header on incoming requests by default, to prevent DNS rebinding attacks (where an attacker tricks a browser into sending requests to `localhost:PORT` via a malicious DNS record). The validation fails in production because:

- Cloudflare strips the original `Host` header
- Railway's internal hostname (`content-queue-fast-api-production.up.railway.app`) doesn't match `api.read-sedi.com`
- FastMCP rejects the request

We set `enable_dns_rebinding_protection=False` in FastMCP's config. The security tradeoff is acceptable because:

- We require a valid JWT on every tool call
- Cloudflare sits in front and enforces HTTPS
- The Railway URL is not publicly advertised

We rely on Cloudflare + JWT auth as the security perimeter instead of Host header validation.

### Cloudflare Worker uses `redirect: "manual"`

The OAuth authorize endpoint returns a `302` redirect to the client's callback URL. If the Cloudflare Worker followed this redirect server-side, it would fetch the client's callback URL from Railway's perspective — which is wrong and would fail. The user's browser would never receive the authorization code.

`redirect: "manual"` tells the Worker's `fetch()` to pass 302 responses through as-is. The browser receives the redirect and follows it to the client's callback, completing the OAuth handshake correctly.

### Cloudflare Worker buffers the request body

Streaming request bodies cannot be retransmitted. If Railway issues any redirect (even a 307) on a streamed request, the Worker would fail to forward the body on retry.

We call `request.arrayBuffer()` to fully buffer the body before forwarding. For MCP's JSON payloads (which are small), the memory cost is negligible and the reliability gain is real.

### Cloudflare WAF rule for Claude-User agent

Cloudflare's "Manage AI bots" feature — specifically **Super Bot Fight Mode** — blocks user agents containing `Claude-User` and `Anthropic-AI`. After a user completes OAuth and claude.ai starts making tool calls, every request was silently dropped with no error visible to the user.

We created a custom WAF rule:

```text
Expression: (http.user_agent contains "Claude-User") or (http.user_agent contains "Anthropic-AI")
Action:     Skip → All Super Bot Fight Mode Rules
```

Without this rule, claude.ai web integration does not work at all. The failure mode is particularly opaque because Cloudflare returns a Cloudflare error page (not a 403 JSON response), which the MCP client may interpret as a connection failure rather than an auth or policy issue.

### mcp-remote HTTP/2 coalescing issue

`mcp-remote` (the npm package that gives Claude Desktop HTTP MCP support) uses `undici` as its HTTP client, which defaults to HTTP/2. HTTP/2 connection coalescing means: if you already have an open HTTP/2 connection to an IP that Railway also resolves to, `undici` reuses that connection for `api.read-sedi.com` requests. Railway's edge then returns **421** because the virtual host doesn't match.

The fix is patching `undici` to force HTTP/1.1. See Section 11 for the exact patch.

### `_MCPProxy` class in main.py

FastMCP's `StreamableHTTPSessionManager` is not designed to be constructed more than once in the same process. During development, hot reloads or test restarts would fail because the manager couldn't be re-initialized.

We wrapped FastMCP in a `_MCPProxy` class that defers ASGI app construction until first request. This means each startup (including Uvicorn hot reload) gets a fresh ASGI app without import-time side effects.

### Scope is an empty string

The MCP token response spec requires a `scope` field. sed.i's auth model is user-identity-only — the JWT `sub` is the user's email, and all authorization decisions are "is this the owner of this resource?" rather than fine-grained capability scopes.

We return `"scope": ""` to satisfy the protocol without implementing a scope system we don't need. If we later add third-party MCP clients that need scope restrictions (e.g., read-only access for a shared client), we can add scopes at that point.

### Access token is the sed.i JWT

We don't maintain a separate OAuth token store. After PKCE verification, the authorization server issues a standard sed.i JWT. This means the entire existing auth stack — `_resolve_user_from_bearer`, JWT decode, user lookup — works unchanged for MCP tool calls. There is no "MCP token" concept; it's the same token you'd get from logging into the web app.

---

## 8. All deployed tools

All tools are user-scoped: every query filters by `owner_id = current_user.id`. Cross-user access is not possible at the data layer.

### `list_lists()`

Returns all reading lists for the authenticated user with article counts.

```text
Arguments: none
Returns:   [{ id, name, description, item_count, created_at }]
```

### `get_list_content(list_id, include_full_text?, limit?)`

Returns articles in a list.

```text
Arguments:
  list_id:           required UUID
  include_full_text: optional bool, default false — full article HTML, truncated at 8k tokens
  limit:             optional int, default 50, max 200
Returns: [{ id, title, url, description, summary, tags, is_read, reading_time_minutes, word_count }]
```

### `get_content_item(item_id, include_full_text?)`

Returns a single article by ID.

```text
Arguments:
  item_id:           required UUID
  include_full_text: optional bool, default false
Returns: { id, title, url, author, summary, tags, is_read, read_position, ... }
```

### `search_content(query, limit?)`

Semantic search across the user's entire library using OpenAI embeddings and pgvector cosine distance.

```text
Arguments:
  query: required — natural language query
  limit: optional int, default 10, max 50
Returns: [{ item: {...}, similarity_score: 0.83 }]
```

### `find_similar(item_id, limit?)`

Finds articles semantically similar to a given article.

```text
Arguments:
  item_id: required UUID
  limit:   optional int, default 5
Returns: [{ item: {...}, similarity_score: 0.79 }]
```

### `get_highlights(item_id?, list_id?)`

Returns highlights. Modes:

- `item_id` set → highlights from one article
- `list_id` set → highlights from all articles in a list
- Neither → all user highlights (capped at 100)

```text
Arguments:
  item_id: optional UUID
  list_id: optional UUID
Returns: [{ id, text, note, color, article_title, article_id }]
```

### `get_draft(list_id)`

Returns the writing draft for a list, or null if none exists.

```text
Arguments:
  list_id: required UUID
Returns: { title, content (markdown), word_count, updated_at } | null
```

### `get_reading_stats()`

Returns aggregate reading statistics for the authenticated user.

```text
Arguments: none
Returns: { total_items, read_count, unread_count, archived_count, avg_reading_time_minutes }
```

### `summarize_list(list_id, style?, max_items?)`

AI-generated summary of a reading list using OpenAI.

```text
Arguments:
  list_id:   required UUID
  style:     optional — "overview" | "themes" | "gaps" | "timeline", default "overview"
  max_items: optional int, default 20, max 50
Returns: { summary: "...", style, item_count, cached: bool }
```

Styles:

- `overview` — bullet summary of each article's key points
- `themes` — cluster by topic, summarize per cluster
- `gaps` — compare list content against the list's draft, flag uncovered topics
- `timeline` — chronological narrative of the articles

### `update_draft(list_id, content, title?)`

Write or replace the draft for a reading list.

```text
Arguments:
  list_id: required UUID
  content: required string (markdown)
  title:   optional string
Returns: { id, list_id, title, content, word_count, updated_at }
```

### `add_content(url)`

Save a URL to the user's library. Triggers the same extraction pipeline as adding via the web app.

```text
Arguments:
  url: required string
Returns: { id, title, url, status }
```

### `create_list(name, description?)`

Create a new reading list.

```text
Arguments:
  name:        required string
  description: optional string
Returns: { id, name, description, created_at }
```

### `add_to_list(list_id, item_id)`

Add an existing content item to a list.

```text
Arguments:
  list_id: required UUID
  item_id: required UUID
Returns: { success: true }
```

---

## 9. Connection setup

### Claude Desktop

Claude Desktop requires two things — the config and a manual patch. Do both.

#### Step 1 — Apply the mcp-remote HTTP/2 patch (required, see Section 11 for details)

Without this, Claude Desktop returns 421 errors and never connects. The patch forces HTTP/1.1 in the underlying `undici` client:

```text
~/.nvm/versions/node/v20.19.6/lib/node_modules/mcp-remote/node_modules/undici/lib/dispatcher/client.js
```

Find `allowH2 = true` near the top and change it to `allowH2 = false`. Save the file.

#### Step 2 — Edit the Claude Desktop config

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "sedi": {
      "type": "http",
      "url": "https://api.read-sedi.com/mcp-transport/mcp",
      "auth": { "type": "oauth" }
    }
  }
}
```

#### Step 3 — Restart Claude Desktop

It will open a browser window for OAuth authorization on first launch. After authorizing, the "sedi" tools become available in Claude.

> **Note:** The patch in Step 1 is reset if `mcp-remote` is updated or the Node.js version changes. If Claude Desktop starts returning 421s again after an update, re-apply it.

### claude.ai web

Settings → Integrations → Add custom integration → enter:

```text
https://api.read-sedi.com/mcp-transport/mcp
```

Click Connect. claude.ai will open an OAuth window. After authorizing, the integration appears in the list and is available in conversations.

### Starter prompts

These prompts work well once connected:

- "List all my reading lists and tell me which has the most unread articles"
- "Summarize my '[list name]' list — what are the key themes?"
- "Search my sed.i library for articles about [topic]"
- "What have I been highlighting about [topic]?"
- "Check my draft for the '[list name]' list — what topics from my readings am I missing?"
- "Find articles in my library similar to this one: [title]"
- "What are my reading stats?"
- "Save [url] to my library and add it to my [list name] list"

---

## 10. Deploying and updating the Cloudflare Worker

The Worker is in `cloudflare-worker/worker.js`. The Wrangler config is in `cloudflare-worker/wrangler.toml`.

### Deploy

```bash
cd cloudflare-worker && npx wrangler deploy
```

Wrangler uses the Cloudflare account and zone configured in `wrangler.toml`. You need to be authenticated (`npx wrangler login`) before deploying.

### Route configuration

The Worker must be routed to `api.read-sedi.com/*` in the Cloudflare Dashboard:

Workers & Pages → the Worker → Settings → Triggers → Custom Domains / Routes

The route `api.read-sedi.com/*` should point to the Worker. Cloudflare's DNS for `api.read-sedi.com` should proxy through Cloudflare (orange cloud, not grey).

### When to redeploy

Redeploy after any change to `worker.js`. The Worker is stateless, so deploys are instant with no downtime. The most common reason to update the Worker is adjusting CORS headers (adding new allowed origins) or tweaking the upstream Railway URL.

### Upstream URL

If Railway ever changes the deployment URL, update the target in `worker.js` and redeploy. The Railway URL is currently:

```text
https://content-queue-fast-api-production.up.railway.app
```

---

## 11. mcp-remote patch for Claude Desktop

Claude Desktop uses `mcp-remote` (npm global) to support HTTP MCP servers. `mcp-remote` uses `undici` as its HTTP client. The problem: `undici` defaults to HTTP/2, which causes connection coalescing — multiple hostnames that resolve to the same IP share a single HTTP/2 connection. Railway routes by `Host` header, so coalesced requests get **421 Misdirected Request**.

### The patch

Find the `undici` `client.js` inside `mcp-remote`'s own `node_modules`:

```text
~/.nvm/versions/node/v20.19.6/lib/node_modules/mcp-remote/node_modules/undici/lib/dispatcher/client.js
```

In that file, locate the constants at the top and change:

```js
// Find this:
allowH2 = true

// Change to:
allowH2 = false
```

This forces `undici` to use HTTP/1.1, which does not coalesce connections. The 421 errors stop.

### Important caveat

This is a manual patch to a file inside `node_modules`. It gets reset if:

- `mcp-remote` is updated (`npm update -g mcp-remote`)
- `node_modules` is deleted and reinstalled
- The Node.js version changes (different path)

After any `mcp-remote` update, re-apply the patch. There is no automatic mechanism to keep it applied. This is a known limitation and the correct long-term fix is for `mcp-remote` to expose an option to disable HTTP/2, or for Railway/Fastly to fix their 421 behavior.

---

## 12. Troubleshooting

### 421 Misdirected Request

**Symptom:** MCP calls from Claude Desktop return 421.

**Cause A — mcp-remote HTTP/2 coalescing:** The `undici` patch in Section 11 has been reset. Re-apply it.

**Cause B — mount path:** If the mount path was changed back to `/mcp`, Railway/Fastly routing will 421 JWT-authenticated requests. Ensure the path is `/mcp-transport`.

### claude.ai tool calls silently fail after OAuth

**Symptom:** OAuth completes, the integration shows as connected, but tool calls return no results or a generic error.

**Cause:** Cloudflare Super Bot Fight Mode is blocking `Claude-User` / `Anthropic-AI` user agents. Check the WAF rule exists and is active:

Cloudflare Dashboard → Security → WAF → Custom Rules → look for the rule matching `Claude-User` with action "Skip - All Super Bot Fight Mode Rules".

If the rule is missing or disabled, re-create it as described in Section 7.

### OAuth authorize redirect not reaching the client

**Symptom:** Browser opens for OAuth but the authorization code never arrives at the client.

**Cause:** The Cloudflare Worker is following redirects instead of passing them through. Verify the Worker uses `redirect: "manual"` in its `fetch()` call.

### "Host header mismatch" or "DNS rebinding" error in Railway logs

**Symptom:** FastMCP logs a DNS rebinding protection error.

**Cause:** `enable_dns_rebinding_protection=True` got re-enabled. The FastMCP setup must have `enable_dns_rebinding_protection=False` because Cloudflare strips the `Host` header before forwarding to Railway.

### OAuth token response missing `scope`

**Symptom:** MCP client rejects the token response.

**Cause:** Some MCP clients strictly require the `scope` field in the token response. Ensure `/mcp-transport/token` returns `"scope": ""` even though we don't enforce scopes.

### `StreamableHTTPSessionManager` errors on restart

**Symptom:** FastAPI restarts (hot reload, Railway redeploy) fail with an error about the session manager being already initialized.

**Cause:** The `_MCPProxy` class was removed or bypassed. The proxy defers ASGI construction to first request, allowing clean restarts. Ensure FastMCP is mounted through `_MCPProxy`, not directly.

### Redis auth code lookup fails

**Symptom:** `POST /mcp-transport/token` returns "invalid code" immediately.

**Cause A:** Redis is down or the Railway Redis connection string changed. Check the `REDIS_URL` env var.

**Cause B:** The auth code TTL (5 minutes) expired. This is expected if the user took too long to complete the browser OAuth step.

### Tool returns data for wrong user

This should not happen — every query is scoped by `owner_id = current_user.id`. If it does, the JWT `sub` is being resolved incorrectly. Check `_resolve_user_from_bearer` and confirm the user lookup uses `User.email == payload["sub"]` (or `User.id`, depending on what `sub` contains).

---

## References

- [MCP specification (2025-11-25)](https://modelcontextprotocol.io/specification/2025-11-25/)
- [Python MCP SDK](https://py.sdk.modelcontextprotocol.io/)
- [FastMCP docs](https://modelcontextprotocol.io/docs/develop/build-server)
- [Cloudflare Workers docs](https://developers.cloudflare.com/workers/)
- [Wrangler CLI](https://developers.cloudflare.com/workers/wrangler/)
- [undici HTTP/2 docs](https://undici.nodejs.org/#/?id=http2)
