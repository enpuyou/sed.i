---
layout: layouts/chapter.njk
title: MCP and text-to-SQL
part: 3
order: 5
summary: How an AI client such as Claude reaches the library through the Model Context Protocol, how it logs in with OAuth and PKCE, and how a model-written SQL query is checked before it runs.
verified: 2026-10-26
permalink: /llm/mcp-and-text-to-sql/
---

## In one minute

The Model Context Protocol (MCP) is an open protocol that lets an AI client, such as Claude Desktop or claude.ai, ask a server what tools it offers and call them. sed.i runs an MCP server with 16 tools: read tools (search, highlights, lists, stats), write tools (save a URL, edit a draft), a deep-research starter and a tool called `query_library` that answers questions by writing SQL. The server speaks MCP over two transports: standard input and output for a local client, and streamable HTTP for remote ones. Remote clients log in with OAuth 2.1 using the authorization-code flow with PKCE, and receive the same signed token (a JWT) the website uses. The server takes the user from that token, never from a tool argument, so every tool acts only on that user's rows.

`query_library` is the risky tool. A model writes a SQL query from the user's question. The server parses the query, accepts only one SELECT statement, accepts only 7 tables, requires an equality test against the current user's id on every table that holds user data, runs it with a 500 ms timeout, and passes the user id as a bound parameter. The checks have a real gap, described below, and the tool also returns rows the user has moved to the trash.

## The problem

The question this chapter answers: **how can an outside AI client read and change one user's library without being able to touch anyone else's, and how can a model be allowed to write SQL at all?**

Two simpler designs exist, and each falls short.

1. **One-off integrations per client.** A custom plugin for each AI product duplicates work and each has its own login. MCP replaces that with one description of tools that any MCP client can discover.
2. **Fixed queries only.** "How many unread articles do I have longer than 10 minutes?" has no tool of its own. A fixed set of tools covers common questions and nothing else. A model that writes SQL covers the long tail.

The price of the second is that a model's output is untrusted input. A saved article could contain text that steers the model into writing a different query ("prompt injection"). Prompt instructions alone cannot stop that, so the server checks the SQL itself, in code, before running it. [ADR-0006](/talking/decision-log/) records that decision (`docs/decisions/0006-text-to-sql-security.md`).

## Mental model

MCP is a menu protocol. The client connects and asks "what do you offer?" The server lists tools with names, descriptions and argument schemas. The client's model decides which to call and the client sends the call. Messages are JSON-RPC 2.0: small JSON objects with a method name, an id and parameters.

OAuth with PKCE is a coat-check ticket with a secret handshake. The client wants a token. It sends the user to sed.i's login page. After the user signs in, sed.i hands the client a short-lived one-time code. The client swaps the code for a token. PKCE (Proof Key for Code Exchange) makes sure the client that asks for the token is the client that started the login: it invents a random secret (the verifier), sends only a hash of it at the start (the challenge), and reveals the verifier at the end. Someone who steals the code from the redirect cannot use it without the verifier.

| Term | Meaning here |
|---|---|
| MCP | An open protocol for an AI client to list and call a server's tools and read its resources. |
| Tool | A named function the model can call, with typed arguments. |
| Resource | Read-only content the server exposes by URI, such as `skills://sedi`. |
| Transport | How messages travel. stdio: lines on standard input and output. Streamable HTTP: HTTP requests to one URL. |
| OAuth 2.1 authorization-code flow | Log in at the server, receive a code, swap it for tokens. |
| PKCE (S256) | Challenge = base64url(SHA-256(verifier)). Proves the same client started and finished the flow. |
| JWT | A signed token. The access token here is the same JWT the web app issues, with the user's email as `sub`. |
| Allow-list | A fixed list of what is permitted; anything else is rejected. |

{% fig "Two transports, one set of tools" %}
```text
local client (Claude Desktop)           remote client (claude.ai, mcp-remote)
   |  JSON-RPC lines over stdio            |  HTTPS + Bearer token
   v                                       v
python -m app.mcp.server              /mcp-transport  (FastAPI mount)
   token from env SEDI_TOKEN            MCPAuthMiddleware: JWT -> user
   |                                       |
   +-------------- 16 tools ---------------+
                       |
              each tool takes (user, db) and filters by user.id
```
{% endfig %}

## Walkthrough: a client logs in, lists tools, and asks a question

The guide's author ran the real OAuth router (mounted in a throwaway FastAPI app with a test client) against a scratch Postgres database and Redis logical database 15, using a dummy user and `https://api.example.invalid` as the base URL. The stdio server was started as a subprocess and spoken to with raw JSON-RPC. The SQL checks were run on real strings. Model output is not involved in the login steps, so everything in steps 1 to 6 is captured. In step 7 the model's SQL is hand-written and labelled.

### Step 1: discovery

A remote client first fetches two documents. The protected-resource document says where the resource and its authorization server are; the authorization-server document lists the endpoints and what is supported (captured):

```json
{"issuer": "https://api.example.invalid",
 "authorization_endpoint": "https://api.example.invalid/mcp-transport/authorize",
 "token_endpoint": "https://api.example.invalid/mcp-transport/token",
 "registration_endpoint": "https://api.example.invalid/mcp-transport/register",
 "response_types_supported": ["code"],
 "grant_types_supported": ["authorization_code", "refresh_token"],
 "code_challenge_methods_supported": ["S256"],
 "token_endpoint_auth_methods_supported": ["none"]}
```

`"none"` means the client has no secret: it is a public client and PKCE is its only proof. Some clients also call the registration endpoint first. It returns a `client_id` for any request with status 201 and checks nothing.

When a request reaches `/mcp-transport` without a token, `MCPAuthMiddleware` answers 401 with a `WWW-Authenticate` header pointing at the protected-resource document, which is how a client learns where to start.

### Step 2: the PKCE pair

The client makes a verifier (43 to 128 random URL-safe characters) and computes the challenge. The worked example uses the verifier from RFC 7636 appendix B so the result can be checked against the standard. Captured:

```text
verifier  : dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk
sha256 hex: 13d31e961a1ad8ec2f16b10c4c982e0876a878ad6df144566ee1894acb70f9c3
challenge : E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM
matches the value printed in RFC 7636: True
```

The challenge is the 32 bytes of the SHA-256 digest written in URL-safe base64 with the trailing `=` removed. A hash is one-way: from the challenge in the browser's address bar nobody can recover the verifier.

### Step 3: authorize

The client opens `/mcp-transport/authorize?client_id=...&redirect_uri=...&response_type=code&code_challenge=...&code_challenge_method=S256&state=...`. The server rejects a `response_type` other than `code` and a method other than `S256`, then checks the client against an allow-list (see "What is weak or missing": with the setting empty, it accepts everything). It shows an email and password form. On submit, `authorize_post` verifies the password and stores the code:

<!--src content-queue-backend/app/mcp/oauth.py#334-347-->
```python
code = secrets.token_urlsafe(32)
r = _get_redis()
r.setex(
    f"{CODE_PREFIX}{code}",
    AUTH_CODE_TTL,
    json.dumps(
        {
            "email": user.email,
            "code_challenge": code_challenge,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
        }
    ),
)
```

Captured: the response was a 302 to the redirect URI with `?code=<43 characters>&state=xyz`. The Redis key was `mcp:code:<code>` with a TTL of 300 seconds, and the stored JSON had the keys `client_id`, `code_challenge`, `email` and `redirect_uri`. The `state` value is echoed back so the client can detect a forged redirect.

### Step 4: a wrong verifier is refused

The client posts to `/mcp-transport/token` with the code, `redirect_uri`, `client_id` and a `code_verifier`. The server recomputes the challenge and compares:

<!--src content-queue-backend/app/mcp/oauth.py#476-481-->
```python
digest = hashlib.sha256(code_verifier.encode()).digest()
computed_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
if computed_challenge != data["code_challenge"]:
    return _token_error(
        "invalid_grant", "code_verifier does not match code_challenge"
    )
```

Captured with a wrong verifier: HTTP 400, `{'error': 'invalid_grant', 'error_description': 'code_verifier does not match code_challenge'}`, and the code was still in Redis afterwards (a failed attempt does not burn it).

### Step 5: the right verifier

Captured: the response has the keys `access_token`, `expires_in`, `refresh_token` and `token_type`; `expires_in` was 604,800 seconds (7 days, the default `ACCESS_TOKEN_EXPIRE_MINUTES`). The access token's claims were `sub` (an email address) and `exp` only. It is the same kind of token the web app uses, so an MCP client holds the user's full web session power for 7 days. After success the code was deleted and a replay returned `{'error': 'invalid_grant', 'error_description': 'Invalid or expired authorization code'}`.

### Step 6: refresh

The refresh token is stored in Redis under a hash of itself, so a copy of the Redis data does not contain usable tokens (captured: the key was `mcp:refresh:` plus 64 hex characters; the raw token was not a key; TTL 30 days by default). Refreshing deletes the old token and issues a new pair in one atomic step:

<!--src content-queue-backend/app/mcp/oauth.py#430-433-->
```python
raw = r.getdel(_refresh_token_redis_key(refresh_token))
if not raw:
    return _token_error("invalid_grant", "Invalid or expired refresh token")
data = json.loads(raw)
```

Captured: the new refresh token differed from the old one, the old key was gone, and using the old one again returned `invalid_grant`.

### Step 7: the transport, the tools and the user

On stdio, the client starts `python -m app.mcp.server` and exchanges lines. Captured exchange (shortened; the real replies are longer):

```text
>> {"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18", ...}}
<< {"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2025-06-18","capabilities":{... "resources":{...},"tools":{...}},"serverInfo":{"name":"sedi","version":"1.26.0"},"instructions":"sed.i is a personal reading and writing assistant. ..."}}
>> {"jsonrpc":"2.0","id":2,"method":"tools/list"}
<< {"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"list_lists", ...
>> {"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"get_reading_stats","arguments":{}}}
<< {"jsonrpc":"2.0","id":3,"result":{"content":[{"type":"text","text":"Error executing tool get_reading_stats: SEDI_TOKEN not set"}],"isError":true}}
```

Without `SEDI_TOKEN` in its environment, the stdio server refuses to act: the token is read on every tool call. On HTTP, the middleware decodes the Bearer token, loads the active user and stores it in a context variable for that request:

<!--src content-queue-backend/app/mcp/http_server.py#315-323-->
```python
try:
    with get_db() as db:
        user = _resolve_user_from_bearer(auth, db)
    token = _request_user_var.set(user)
    try:
        response = await call_next(request)
        return response
    finally:
        _request_user_var.reset(token)
```

A tool body never receives a user id from the model. For example `list_lists()` has no arguments, and `_current_user()` supplies the identity. This is the tenant isolation: the identity comes from the verified token, and each tool filters its queries by `user.id`.

### Step 8: ask a question, and the SQL guard

The model calls `query_library` with a question. The pipeline in `query_library`:

1. A model writes SQL from a schema prompt (the 7 tables, the columns it may use, and rules). Temperature 0, at most 512 tokens.
2. `_validate_sql` parses it with sqlglot (Postgres dialect), requires exactly one statement, requires the root to be a SELECT, scans the whole tree for INSERT, UPDATE, DELETE, DROP, CREATE, ALTER, TRUNCATE and GRANT nodes, and checks every referenced table against the allow-list.
3. `_enforce_user_isolation` requires `:user_id` in the text, then requires, for every user-scoped table in the statement, an equality between that table's user column and `:user_id`.
4. The SQL runs after `SET LOCAL statement_timeout = '500ms'`, with `user_id` as a bound parameter, and at most 50 rows are fetched.
5. A model summarizes up to 20 rows; more than 20 are returned as a plain table.

The two checks, in the part that matters:

<!--src content-queue-backend/app/mcp/tools/query.py#288-310-->
```python
for eq in stmt.find_all(exp.EQ):
    for col_side, val_side in ((eq.this, eq.expression), (eq.expression, eq.this)):
        if (
            isinstance(val_side, exp.Placeholder)
            and val_side.name == "user_id"
            and isinstance(col_side, exp.Column)
        ):
            col_name = col_side.name.lower()
            col_table = (col_side.table or "").lower()
            # Candidate aliases whose table has a matching user-scoping column.
            candidates = [
                alias
                for alias, tbl in alias_map.items()
                if _USER_SCOPED_TABLES.get(tbl) == col_name
            ]
            if col_table:
                # Qualified: isolates exactly the named alias.
                if col_table in candidates:
                    filtered_aliases.add(col_table)
            elif len(candidates) == 1:
                # Unqualified but unambiguous — only one joined table could own
                # this column, so Postgres (and this checker) can resolve it.
                filtered_aliases.add(candidates[0])
```

The 7 tables are `content_items`, `highlights`, `lists`, `content_list_membership`, `drafts`, `content_chunks` and `reading_clusters`. Six carry a user column (`user_id`, or `owner_id` for `lists`). The bridge table `content_list_membership` has none and is exempt, so a query must join it to a filtered table. The `users`, `refresh_tokens` and `tag_embeddings` tables are not listed.

Now ten-plus hand-written SQL strings, each run through `_validate_sql` and then `_enforce_user_isolation`. These are not model outputs; they were written for this guide to cover safe and attack-shaped queries. Result captured from the real functions:

| # | Query shape | Result | Reason given by the code |
|---|---|---|---|
| 1 | One table, `user_id = :user_id` | accept | |
| 2 | Join of `content_items` and `highlights`, both qualified with the alias | accept | |
| 3 | `lists` joined through the bridge to `content_items`, `owner_id` and `user_id` both filtered | accept | |
| 4 | No user filter at all | reject | "Generated SQL must filter by :user_id" |
| 5 | Join, only the first table filtered | reject | "Table 'highlights' (alias 'h') is user-scoped but has no direct WHERE user_id = :user_id predicate" |
| 6 | Join, one unqualified `user_id = :user_id` | reject | Same message, for `content_items`: the bare column could belong to either table, so it is credited to neither (the 2026-09-25 fix) |
| 7 | The `:user_id` filter combined with an alternative condition | **accept** | No reason: the check found an equality and stopped. This is the gap described below |
| 8 | Filtered query with a subquery on `users` | reject | "references disallowed table(s): users" |
| 9 | Subquery on `highlights`, predicates unqualified | reject | The bare `user_id` is ambiguous between the two tables (conservative: Postgres would accept it) |
| 9b | Same, with aliases `ci` and `h` both filtered | accept | |
| 10 | `UPDATE content_items SET ...` | reject | "Only SELECT statements are allowed (got Update)" |
| 11 | Two statements separated by `;` | reject | "Exactly one SQL statement is required" |
| 12 | `WITH d AS (DELETE ... RETURNING *) SELECT * FROM d` | reject | "Query contains forbidden Delete node" |
| 13 | `SELECT email FROM users ...` | reject | "references disallowed table(s): users" |

The table has two lessons. The checks do their stated job on missing filters, wrong tables, writes and multiple statements. And row 7 shows what the isolation check really tests: that an equality against `:user_id` exists somewhere in the parsed statement, not that every returned row must satisfy it. Chapter text below describes the fix.

### Step 9: does it hide trashed articles?

[The production-readiness audit](/reliability/gaps-and-roadmap/) (item P1-6) asked whether `query_library` returns soft-deleted rows. It does, and the check was run: on the scratch database, one of five articles was given a `deleted_at` timestamp (a trashed article), then three hand-written queries were run through the real `query_library` function (the model call replaced by the SQL string). Captured:

```text
trashed: A history of the espresso machine
--- list all titles: row_count=5      (the trashed title is in the list)
--- count items:     {'n': 5}          (trashed item counted)
--- chunks per item: row_count=5       (its chunk is counted)
```

The cause is visible in the code. `deleted_at` is not in `_TABLE_SCHEMA`, so the model is never told the column exists, and the validator does not require a predicate on it. The other MCP tools filter it (`get_content_item`, `get_highlights`, `get_list_content`, `get_reading_stats` and the search used by `synthesize_topic` all include `deleted_at IS NULL`). `add_to_list` does not, which was noticed but not tested. For `synthesize_topic`: it reads through `hybrid_search`, which filters deleted items (inferred from reading `app/mcp/tools/synthesis.py`; not run).

## How it is built here

| File | Role |
|---|---|
| `content-queue-backend/app/mcp/server.py` | stdio server: registers the 16 tools and the `skills://sedi` resource; user from `SEDI_TOKEN` |
| `content-queue-backend/app/mcp/http_server.py` | HTTP server and `MCPAuthMiddleware`; same 16 tools; no resources |
| `content-queue-backend/app/main.py` | mounts the HTTP app at `/mcp-transport` and includes the OAuth router |
| `content-queue-backend/app/mcp/oauth.py` | discovery documents, registration, authorize, token, refresh |
| `content-queue-backend/app/mcp/auth.py` | stdio token to user |
| `content-queue-backend/app/mcp/skills.py` | three instruction blocks for a calling agent |
| `content-queue-backend/app/mcp/tools/query.py` | `query_library`, `_validate_sql`, `_enforce_user_isolation` |
| `content-queue-backend/tests/mcp/test_tool_parity.py` | fails if the two servers register different tools |

The tool inventory (captured from `list_tools` on both servers, which return the same 16 names):

| Tool | What it does | Writes? |
|---|---|---|
| `list_lists` | The user's reading lists with counts | no |
| `get_list_content` | Articles in a list | no |
| `get_content_item` | One article | no |
| `search_content` | Hybrid search over the library | no |
| `find_similar` | Articles like a given one | no |
| `get_highlights` | Highlights for an item, a list, or the latest 100 | no |
| `get_draft` | The draft attached to a list | no |
| `get_reading_stats` | Counts of total, read, unread, archived | no |
| `summarize_list` | Model summary of a list; cached in process memory | no (model call, spend) |
| `query_library` | Text-to-SQL | no (model calls, spend) |
| `synthesize_topic` | `quick`: one-pass synthesis. `deep`: starts a research run (chapter 3.4) | deep: creates a run |
| `update_draft` | Replace a list's draft | yes |
| `add_content` | Save a URL and queue extraction | yes |
| `create_list` | Create a list | yes |
| `add_to_list` | Put an item in a list | yes |
| `assist_draft` | Model drafts a paragraph and appends it to the draft | yes |

Skills are three named instruction blocks (`weekly-digest`, `connect-new-save`, `draft-from-highlights`) that tell a calling agent which tools to call in what order. The stdio server exposes them as the resource `skills://sedi`. The HTTP server registers no resources (captured: stdio lists `['skills://sedi']`, HTTP lists `[]`), so remote clients do not receive them.

## Design choices

| Choice | Alternatives | Why this one |
|---|---|---|
| MCP | A custom REST plug-in per AI product | One interface for every MCP client, with discovery built in. |
| Two transports sharing tools | stdio only; HTTP only | stdio suits a developer's desktop client. HTTP is the only option for hosted clients such as claude.ai. The parity test exists because `synthesize_topic` and `assist_draft` once shipped on stdio only. |
| OAuth 2.1 with PKCE and no client secret | API keys; a pasted JWT | Clients are public (they cannot keep a secret); PKCE protects the code. The user logs in on sed.i's own page. |
| Access token is the web JWT | A separate MCP token with scopes | No new token store. But it has no scopes and the same 7-day lifetime as a web session. |
| Refresh tokens hashed in Redis, read with `GETDEL` | Plain keys; read then delete | A Redis leak yields no usable tokens, and two concurrent refreshes cannot both succeed. |
| User from the token, never from tool arguments | A `user_id` argument checked against the token | The model cannot ask for another user's data. |
| Text-to-SQL with an AST check ([ADR-0006](/talking/decision-log/)) | Prompt-only rules; a sandbox database; a read-only role | Parsing catches structure a regex cannot (a write inside a CTE). A separate sandbox or row-level security would be stronger and cost more infrastructure. |
| A fixed schema prompt of safe columns | Introspect the live schema | Columns such as embeddings are never offered to the model. |
| 500 ms timeout and 50 rows | None | Bounds a runaway query and the size of the prompt that summarizes it. |

## How it fails

| Failure | What causes it | What happens | What was done |
|---|---|---|---|
| Unqualified predicate credited to two tables | `WHERE user_id = :user_id` in a join of two tables that both have `user_id` | One table's rows were unfiltered, and the check passed | Fixed 2026-09-25: an unqualified predicate counts only when exactly one candidate table matches |
| Tools unreachable over HTTP | `synthesize_topic` and `assist_draft` registered only on stdio | Remote clients could not call them | Fixed; `test_tool_parity.py` guards it |
| Refresh token read then deleted in two steps | Two concurrent refreshes both read the old token | Two token pairs from one token | Fixed with the atomic `GETDEL`; the error path restores the old token |
| Trashed items returned by `query_library` | `deleted_at` unknown to the model and unchecked | Deleted articles appear in answers | Open (this chapter confirmed it) |
| The generated SQL is rejected | The model omitted the filter, or used a table not allowed | `ValueError` with the reason; no query runs | By design |

## What is weak or missing

Ranked by how likely each is to matter. Per the safety rule for this site, each item states the problem and the fix.

1. **The isolation check proves a predicate exists, not that it restricts the rows.** It looks for any equality with `:user_id` anywhere in the tree. A condition that is combined with an alternative branch can still satisfy it (row 7 above), so the check cannot guarantee that every returned row belongs to the user. The fix is structural: require the predicate to be a top-level AND term of the WHERE clause of the same query block, or remove the dependence on the model altogether by querying through views that already filter by the current user, or by Postgres row-level security with the user id set per transaction. A read-only database role for this tool is a cheap additional layer.
2. **No function allow-list.** Table references are checked, but a statement that calls database functions and names no table is not restricted by the table rules, and the `:user_id` text test can be met by any text in the statement. The same fixes apply, plus a role that has no access to administrative functions.
3. **Trashed articles are returned.** Fix: add `deleted_at` to the schema prompt and require `deleted_at IS NULL` on `content_items` in the validator, or query through a view that excludes trashed rows. Highlights and chunks of a trashed article need the same treatment. `add_to_list` should also refuse a trashed item.
4. **The client allow-list is optional.** When `MCP_OAUTH_CLIENTS_JSON` is empty, `_validate_client_and_redirect` accepts any `client_id` and any `redirect_uri`, and the registration endpoint registers anything without recording it. An attacker who can lure a user to a crafted authorize link could receive the code at a redirect URI of their choosing (PKCE limits what the code alone can do). The captured run had an empty allow-list. Whether production sets the variable was not checked. The fix is to register clients and compare redirect URIs exactly.
5. **The authorization code is read and then deleted as separate steps.** The refresh path uses `GETDEL`; the code path does not, so two simultaneous exchanges with the right verifier could both succeed. The verifier requirement makes this hard to exploit. The fix is one line.
6. **Tokens have no scope.** A client that needs only read access receives the full web JWT with write power, valid for 7 days. A scoped token or a shorter lifetime would limit that.
7. **Skills are stdio-only and out of date.** The resource is missing on HTTP, and the skill text names tools and arguments that do not exist (`explore_concept`; `get_highlights(content_item_ids=...)`; `update_draft(list_id, appended_content)` where the argument is `content`).
8. **No rate limit specific to `query_library` and no per-call spend record**, apart from the gateway's daily ceiling (chapter 3.1).

{% say %}
MCP is an open protocol that lets an AI client, like Claude, discover and call a server's tools. sed.i exposes sixteen tools over two transports: stdio for a local client, and streamable HTTP behind OAuth for hosted ones. The login is the authorization-code flow with PKCE: the client invents a secret, sends only its hash, gets a one-time code that lives five minutes in Redis, and swaps it plus the secret for the same JWT the website uses, along with a hashed refresh token. The server takes the user from the token, so no tool lets the model name a user. The one risky tool is text-to-SQL: a model writes the query, and the server parses it, allows only one SELECT on seven tables, demands a user-id equality on every user table, and runs it with a 500-millisecond timeout. The weak spots are that the check only proves an equality exists somewhere, so a query that also says "or true" passes, that trashed articles come back, and that the client allow-list is optional.
{% endsay %}

## Check yourself

{% qa "What does PKCE protect against, and what is stored where?" %}
Someone who intercepts the one-time code from the redirect. The client sends the challenge (a hash) at the start and the verifier at the end. Redis keeps the code, keyed `mcp:code:<code>`, with the challenge, redirect URI, client id and email, for 5 minutes. Without the verifier the code cannot be exchanged.
{% endqa %}

{% qa "Why can a tool not be tricked into returning another user's article by passing a different id?" %}
No tool takes a user id. The user comes from the verified token (HTTP) or from the `SEDI_TOKEN` environment variable (stdio), and each tool filters by that user's id. An item id of someone else's article simply finds no row for this user.
{% endqa %}

{% qa "Query 5 filters `content_items` but not `highlights`. Why is it rejected, and why does that rule exist?" %}
The check requires a direct `user_id = :user_id` predicate for every user-scoped table in the statement. A filter on one table does not restrict another table in a join, so rows from other users' highlights would otherwise come back.
{% endqa %}

{% qa "Row 7 is accepted. What does that show about what the isolation check proves, and how would you fix it?" %}
The enforcer walks the parsed tree and accepts when it finds an equality of a user column with `:user_id`; it does not check how that equality is combined with other conditions. Require the equality to be a top-level AND term in the WHERE clause, or push isolation below the SQL text: views or row-level security.
{% endqa %}

{% qa "What did the soft-delete test show, and where in the code is the cause?" %}
A trashed article was listed, counted and joined to its chunks by `query_library`. `_TABLE_SCHEMA` does not include `deleted_at`, so the model never filters it, and `_validate_sql` and `_enforce_user_isolation` do not require it.
{% endqa %}

{% qa "Why does the refresh path use GETDEL while the code path uses GET followed by DELETE?" %}
GETDEL is atomic, so two concurrent refreshes cannot both read the token. The code path was not given the same treatment; it is safe in practice mainly because the exchange also needs the verifier. Using GETDEL there too would close the window.
{% endqa %}
