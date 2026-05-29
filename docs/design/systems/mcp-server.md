---
type: design
status: active
last_updated: 2026-05-28
consumer: both
---

# sed.i MCP Server — Technical Design

## What is MCP?

Model Context Protocol (MCP) is an open standard that lets LLM-powered applications (like Claude Desktop, Cursor, or any agent built on the Anthropic API) call structured tools exposed by a server. Think of it as a **typed RPC layer between an AI agent and your application**.

Without MCP, an LLM has no way to interact with your data — it can only respond to text. With MCP, you define a set of named tools (functions with typed parameters and return values), and the LLM can decide to call them as part of answering a user's question.

```
User: "Summarize everything I've saved about machine learning"
 │
 ▼
LLM (Claude)
 │  decides to call: list_lists(), then search_content("machine learning"), then summarize_list(...)
 ▼
MCP Server (sed.i)
 │  executes each tool against the real database
 ▼
LLM formats the results into a natural language response
```

sed.i exposes 13 tools across two deployment modes: **local stdio** (for personal use with Claude Desktop) and **hosted HTTP** (for any MCP-compatible client, authenticated via OAuth 2.1 + PKCE).

---

## Transport Modes

MCP tools are the same in both modes — the difference is only in *how the connection is made* and *how the user is authenticated*.

### Mode 1: stdio (local)

The MCP server runs as a **subprocess** on your machine. Claude Desktop launches it directly and communicates over stdin/stdout using JSON-RPC 2.0.

```
Claude Desktop
  │  spawns subprocess: poetry run python -m app.mcp.server
  │  writes JSON-RPC messages to → stdin
  │  reads JSON-RPC responses from ← stdout
  ▼
app/mcp/server.py
  │  all logging goes to stderr (stdout is reserved for JSON-RPC)
  │  reads SEDI_TOKEN env var for auth
  ▼
PostgreSQL
```

Claude Desktop config (`~/.../Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "sedi": {
      "command": "poetry",
      "args": ["run", "python", "-m", "app.mcp.server"],
      "cwd": "/absolute/path/to/content-queue-backend",
      "env": { "SEDI_TOKEN": "<your-sedi-jwt>" }
    }
  }
}
```

**Why stdout/stderr split?** The MCP protocol multiplexes all communication over stdout as newline-delimited JSON. If any log line contaminates stdout, it breaks the JSON parser on the client side. All Python `logging` calls route to stderr so they never interfere.

**Auth:** The `SEDI_TOKEN` is a standard sed.i JWT, copied from `localStorage['token']` in the browser. The server decodes it on every tool call (`app/mcp/auth.py` in stdio mode, `app/mcp/http_server.py` in hosted HTTP mode), verifies the signature against `SECRET_KEY`, and resolves the active user. No separate token type — same JWT the REST API uses.

### Mode 2: Streamable HTTP (hosted)

The MCP server is mounted as an ASGI app inside the main FastAPI server at `/mcp`. Any MCP-compatible client can connect to it over HTTPS — no local subprocess needed.

```
MCP Client (remote)
  │  HTTPS POST /mcp  (Streamable HTTP transport)
  │  Authorization: Bearer <sed.i-jwt>
  ▼
MCPAuthMiddleware (starlette)
  │  decodes Bearer token, resolves user, stores in contextvar
  ▼
http_mcp FastMCP instance  (app/mcp/http_server.py)
  │  dispatches to same tool implementations
  ▼
PostgreSQL
```

Auth for HTTP uses a Starlette middleware class:

```python
class MCPAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        authorization = request.headers.get("Authorization", "")
        with get_db() as db:
            user = _resolve_user_from_bearer(authorization, db)
        token = _request_user_var.set(user)   # stored in a contextvar
        try:
            return await call_next(request)
        finally:
            _request_user_var.reset(token)
```

The tool handlers then call `_request_user_var.get()` (via `_current_user()`) to retrieve the authenticated user. **Contextvars** (Python's `contextvars.ContextVar`) are the right tool here because each request runs in its own async context — unlike a global variable, a contextvar is isolated per coroutine execution tree.

---

## OAuth 2.1 + PKCE (getting the Bearer token)

To connect a remote MCP client, the user first needs to obtain a sed.i JWT. This is handled by an OAuth 2.1 flow defined in `app/mcp/oauth.py`.

### Why OAuth instead of "just paste a token"?

The stdio mode requires manually copying a JWT from your browser. This works for personal local use but doesn't scale to hosted clients (e.g., Claude.ai's remote MCP integrations) where the user shouldn't have to touch tokens at all. OAuth gives the client a standard way to request a token on behalf of the user.

### Why PKCE?

PKCE (Proof Key for Code Exchange) is required for any OAuth 2.1 client that can't securely store a client secret — which includes browser-based apps and native apps. Here's the attack it prevents:

```
Without PKCE:
  Attacker intercepts auth code in redirect_uri → exchanges code for token → full access

With PKCE:
  Client generates random code_verifier (never sent over network)
  Client sends code_challenge = SHA-256(code_verifier) in the initial request
  Auth code is useless without the original code_verifier
  Only the legitimate client can exchange the code
```

### The full flow

```
1. Client fetches /.well-known/oauth-authorization-server
   → discovers all endpoint URLs

2. Client generates:
   code_verifier  = random 32-byte hex string (kept secret, never sent)
   code_challenge = base64url(sha256(code_verifier))

3. Client redirects browser to:
   /mcp-transport/authorize
     ?client_id=...
     &redirect_uri=...
     &code_challenge=<hash>
     &code_challenge_method=S256
     &state=<csrf-token>

4. User sees sed.i login form, enters credentials

5. Server:
   a. Verifies email + password against DB (bcrypt)
   b. Creates a sed.i JWT (access token)
   c. Stores in Redis: mcp:code:<random_code> → {jwt, code_challenge, client_id, redirect_uri}
      TTL: 5 minutes
   d. Redirects browser to redirect_uri?code=<random_code>&state=<csrf-token>

6. Client POSTs to /mcp-transport/token:
   {
     grant_type: "authorization_code",
     code: <random_code>,
     code_verifier: <original_secret>
   }

7. Server:
   a. Looks up code in Redis, deletes it (single-use)
   b. Verifies: sha256(code_verifier) == stored code_challenge
   c. Returns {access_token: <sed.i-jwt>, token_type: "bearer", ...}

8. Client uses JWT as Bearer token on all /mcp requests
```

**Key design decision:** The access token *is* the sed.i JWT — not a separate OAuth-specific token. This means no extra token validation logic, no separate token store, and the MCP server reuses the same auth middleware as the REST API.

### Token storage in Redis

| Key pattern | Contents | TTL |
|---|---|---|
| `mcp:code:<random>` | `{jwt, code_challenge, client_id, redirect_uri}` | 5 min |
| `mcp:refresh:<sha256-hash>` | `{user_email}` | configurable (`MCP_REFRESH_TOKEN_EXPIRE_DAYS`) |

Auth codes are stored as random opaque strings. Refresh tokens are stored as SHA-256 hashes of the actual token value — so a Redis breach doesn't expose usable tokens.

---

## Tool Implementation Pattern

Every tool follows the same three-line pattern:

```python
@mcp.tool()
def get_content_item(item_id: str, include_full_text: bool = False) -> dict:
    """Single article by ID. ..."""
    with get_db() as db:
        user = get_user_from_env(db)          # auth
        return _get_content_item(...)         # delegate to tool module
```

1. `get_db()` — context manager that opens a SQLAlchemy session and closes it on exit
2. `get_user_from_env(db)` (stdio) or `_request_user_var.get()` via `_current_user()` (HTTP) — resolves the authenticated user
3. Delegate to a function in `app/mcp/tools/` — the actual business logic, importable and testable independently

The tool functions in `app/mcp/tools/` accept `(user, db)` as explicit parameters. This makes them independently testable without going through the MCP transport at all — tests can call `_get_content_item(item_id, user, db)` directly.

```
app/mcp/
├── server.py          # stdio entrypoint — registers tools, runs stdio transport
├── http_server.py     # HTTP entrypoint — JWT middleware, registers same tools
├── oauth.py           # OAuth 2.1 + PKCE endpoints
├── auth.py            # JWT → User resolution (stdio mode)
├── db.py              # get_db() context manager
└── tools/
    ├── content.py     # search_content, get_content_item, find_similar
    ├── lists.py       # list_lists, get_list_content
    ├── highlights.py  # get_highlights
    ├── drafts.py      # get_draft
    ├── stats.py       # get_reading_stats
    ├── summarize.py   # summarize_list (calls OpenAI, caches result)
    └── write.py       # add_content, update_draft, create_list, add_to_list
```

---

## Tools Reference

### Read tools

| Tool | Parameters | Returns |
|---|---|---|
| `list_lists()` | — | All reading lists with item counts |
| `get_list_content(list_id, include_full_text, limit)` | list UUID, bool, int ≤ 200 | Articles in the list. `full_text` capped at ~32k chars |
| `get_content_item(item_id, include_full_text)` | item UUID, bool | Single article metadata or full content |
| `search_content(query, limit)` | natural language string, int ≤ 50 | Hybrid search results (keyword + semantic + RRF fusion) |
| `find_similar(item_id, limit)` | item UUID, int | Articles similar by embedding cosine distance |
| `get_highlights(item_id?, list_id?)` | optional UUIDs | Highlights scoped to article, list, or full library (max 100) |
| `get_draft(list_id)` | list UUID | Writing draft as markdown, or null |
| `get_reading_stats()` | — | `{total_items, read_count, unread_count, archived_count}` |
| `summarize_list(list_id, style, max_items)` | list UUID, style string, int | AI summary with 4 styles: `overview`, `themes`, `gaps`, `timeline` |

### Write tools

| Tool | Parameters | Returns |
|---|---|---|
| `add_content(url)` | URL string | `{item_id, status}` — status is `'queued'` or `'exists'` |
| `update_draft(list_id, content, title?)` | list UUID, markdown string, optional title | Saved draft |
| `create_list(name, description?)` | strings | New list object |
| `add_to_list(list_id, item_id)` | two UUIDs | `{status}` — `'added'` or `'already_in_list'` |

### Design decisions

**Why cap `full_text` at 32k chars?**
An LLM calling `get_list_content` with `include_full_text=True` on a 50-article list could send ~500k tokens into the model context — exceeding most LLMs' context window and generating a large API bill. The cap lets an agent get enough content for most tasks while protecting against accidental overflow. Agents that need the full text of a specific article use `get_content_item` instead.

**Why is `summarize_list` a tool and not just a prompt?**
The agent would need to: call `get_list_content`, format all articles into a prompt, call OpenAI, and return the result. Wrapping this as a tool makes it one call from the agent's perspective, and adds server-side caching keyed on `(user_id, list_id, content_hash, style)` — repeated calls with the same list state are free.

**Why are write tools limited to 4?**
The read tools cover the full breadth of the data model. Write tools are deliberately minimal — enough for an agent to save articles it discovers during a session and collaborate on drafts, without giving it the ability to destructively modify or delete a user's library.

---

## Security Model

Every tool call is authenticated. There is no unauthenticated MCP surface.

| Threat | Mitigation |
|---|---|
| Unauthenticated access | JWT verified on every tool call before any DB query |
| Cross-user data access | All DB queries filter by `user_id == current_user.id` |
| Auth code interception | PKCE S256 — code is useless without the code verifier |
| Reflected XSS in OAuth form | All URL parameters HTML-escaped before rendering |
| CSRF in OAuth flow | `state` parameter carries CSRF token, verified on redirect |
| Redis breach exposing refresh tokens | Stored as SHA-256 hashes, not raw token values |
| Token leakage via stdout | All logging routed to stderr in stdio mode |
| Context window flooding | `full_text` capped, `get_highlights` capped at 100 |

---

## Example: what an agent session looks like

```
User: "What are the main themes in my 'AI Research' list?"

Agent calls:
  1. list_lists()
     → finds list_id for "AI Research"

  2. summarize_list(list_id="...", style="themes")
     → server fetches list articles, builds prompt, calls OpenAI,
       caches result, returns summary

Agent responds:
  "Your AI Research list of 14 articles covers three main themes:
   transformer scaling laws, agent tool use, and evaluation benchmarks.
   The scaling laws cluster is the most represented (6 articles)..."
```

Or a write session:

```
User: "Save this paper I'm reading and add it to my 'Papers' list"
      [pastes URL]

Agent calls:
  1. add_content(url="https://arxiv.org/abs/...")
     → {item_id: "uuid-...", status: "queued"}

  2. list_lists()  → finds "Papers" list_id

  3. add_to_list(list_id="...", item_id="uuid-...")
     → {status: "added"}

Agent responds:
  "Saved. The paper is queued for extraction and has been added to
   your Papers list."
```
