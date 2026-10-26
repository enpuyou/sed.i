---
layout: layouts/chapter.njk
title: Auth and security
part: 4
order: 3
summary: How sed.i proves who you are, keeps one user's data from another, and which known security gaps are still open in the current code.
verified: 2026-10-26
permalink: /platform/auth-and-security/
---

## In one minute

A user logs in with an email and password. The password is stored only as a bcrypt hash. Login returns a signed access token (a JWT, valid 24 hours) and a random refresh token (valid 90 days) that the database stores only as a SHA-256 hash. The web app receives both in httpOnly cookies, so page scripts cannot read them, plus a readable CSRF cookie that the page must echo back in a header on every mutating request. The browser extension and the MCP integration skip cookies and send the access token in an `Authorization: Bearer` header.

Each time the access token is renewed, the refresh token is replaced and the old one is revoked. Presenting a revoked refresh token more than 30 seconds later is treated as theft and revokes every refresh token of that user. Separation between users is enforced by a `user_id` filter written into every query, not by the database. Four routes are rate limited per client IP through Redis. Several known gaps are still open in the current code; they are listed with their fixes below.

## The problem

**How does the system know who is calling, stop one user from reading another's library, and limit the damage when a token leaks?**

The obvious simple design is one long-lived token stored in the browser's `localStorage` and sent with every request. It is easy, but any script running on the page can read it (a single cross-site-scripting bug hands over the account permanently), and it cannot be revoked without changing the signing key for everyone. Cookies marked httpOnly fix the first problem, but browsers attach cookies automatically to requests from other sites, which creates a second one: cross-site request forgery (CSRF), where a hostile page makes the browser send a state-changing request using your cookie. So the design needs short-lived access tokens, revocable refresh tokens, a defence against CSRF, and a clear rule for the clients that do not use cookies.

## Mental model

Think of a hotel. The room key card (access token) opens your door for a day and is checked by a reader without calling the front desk. The front desk holds a registration slip (refresh token) in a locked drawer; showing it gets you a new key card and the desk replaces the slip. If someone shows a slip that was already replaced, the desk assumes it was copied and cancels every slip for that guest. The key card in your pocket is httpOnly: staff on the floor (page scripts) cannot take it. The CSRF token is a second code you must say out loud with each request, so a stranger who merely pushes you toward the door cannot act in your name.

| Term | Meaning here |
|---|---|
| bcrypt | A deliberately slow password hash with a salt, via `passlib`. Only the hash is stored. |
| JWT | A signed token holding claims. Here `sub` is the user's email and `exp` the expiry; signed with HS256 and `SECRET_KEY`. |
| Refresh token | A random string exchanged for a new access token; stored as a SHA-256 hash, so a database leak does not leak usable tokens. |
| httpOnly cookie | A cookie the browser sends but page JavaScript cannot read. |
| SameSite=Lax | The browser withholds the cookie on cross-site POSTs, which blocks most CSRF by itself. |
| CSRF double submit | A readable cookie and a request header must match; a foreign page can send the cookie but cannot read it to fill in the header. |
| CORS | Browser rules about which web origins may call an API from page scripts. |
| Tenant isolation | Rows of one user are never returned to another. Here: a `user_id` filter in each query. |

{% fig "Who can authenticate, and how" %}
```text
web app (Next.js)       extension / MCP client
  httpOnly cookies        Authorization: Bearer <JWT>
  + X-CSRF-Token          (no cookies, no CSRF header)
        \                       /
         v                     v
   CSRF middleware: only checks requests that carry
   the access cookie and no Authorization header
                |
                v
   get_current_user: Bearer first, then cookie
   -> decode JWT -> look up user by email -> is_active
                |
                v
   route code filters every query by user.id
```
{% endfig %}

## Walkthrough: log in, make a mutating request, rotate, reuse

This was captured with the FastAPI `TestClient` against a scratch Postgres database (all migrations applied) and a local Redis, with `DEBUG=false` so cookies are set as in production, and an HTTPS base URL so the client keeps `Secure` cookies. No email or LLM call is made (the queued tasks only sit in a Redis list that was deleted afterwards). Token values are not shown, only their lengths. The user and its rows were deleted afterwards.

### Step 1: log in and read the Set-Cookie headers

`POST /auth/login` with the form fields `username` (the email) and `password`:

```text
login 200 body keys ['access_token', 'refresh_token', 'token_type']
  Set-Cookie: sedi_access_token=<139 chars>; HttpOnly; Max-Age=86400; Path=/; SameSite=lax; Secure
  Set-Cookie: sedi_refresh_token=<64 chars>; HttpOnly; Max-Age=7776000; Path=/auth; SameSite=lax; Secure
  Set-Cookie: sedi_csrf_token=<43 chars>; Max-Age=86400; Path=/; SameSite=lax; Secure
```

Read it attribute by attribute. The access cookie lasts 86,400 s (24 h) and goes to every path. The refresh cookie lasts 7,776,000 s (90 days) and is limited to `Path=/auth`, so it travels only to the refresh and logout endpoints. The CSRF cookie has no `HttpOnly`, so page scripts can read it. All three are `Secure` (HTTPS only) and `SameSite=lax`. The same tokens also come back in the JSON body, which is what extensions and MCP clients use.

In production `COOKIE_DOMAIN` is set to the parent domain so the page on one subdomain can read the cookie set by the API on another; a run with a test domain set added `Domain=.example.test` to the CSRF cookie only (captured).

### Step 2: a mutating request

`POST /content` (save a URL), authenticated by the cookies:

```text
--- cookie auth, no CSRF header
  403 {'detail': 'CSRF token missing or invalid.'}
--- with X-CSRF-Token equal to the CSRF cookie
  201 id present: True
--- with a wrong token
  403 {'detail': 'CSRF token missing or invalid.'}
--- Bearer auth, no cookies, no CSRF header
  201
--- public GET needs no CSRF: 404
```

The cookie alone is not enough for a POST: the header must equal the cookie. A client using a Bearer header needs no CSRF token, because a foreign page cannot make a browser add an `Authorization` header. A GET is never checked.

### Step 3: rotation, a retry, and a replay

The web client refreshes (cookie plus CSRF header). Then the old refresh token is presented again from a client without cookies, as an extension would. The `refresh_tokens` counts are (rows, not revoked), read from the database:

```text
refresh        200  new refresh token differs      rows 3, active 2
old token again (inside 30 s)         401 'Refresh token already used'   rows 3, active 2
new token refreshed                   200                                rows 4, active 2
old token again (revoked_at moved back 60 s)
                                      401 'Refresh token already used'   rows 4, active 0
newest token                          401 'Refresh token already used'
stored value: 64 hex characters (a SHA-256 digest)
```

Inside the 30-second window the replay is rejected but nothing else changes, because a retry after a dropped response looks the same as a replay. After the window (simulated by moving `revoked_at` back 60 seconds, since waiting was unnecessary to show the branch), the same request revoked every active token of that user, including the token of a second, unrelated login, and the newest token stopped working. The error message is the same in both cases; only the database differs.

## How it is built here

| File | Role |
|---|---|
| `content-queue-backend/app/core/security.py` | bcrypt context, `create_access_token`, `create_refresh_token`, `hash_token` |
| `content-queue-backend/app/api/auth.py` | register, login, refresh, logout, email verification, password reset, account delete |
| `content-queue-backend/app/models/refresh_token.py` | hashed refresh tokens with `revoked_at` |
| `content-queue-backend/app/core/auth_cookies.py` | cookie names and attributes, `verify_csrf` |
| `content-queue-backend/app/middleware/csrf.py` | middleware calling `verify_csrf` |
| `content-queue-backend/app/core/deps.py` | `get_current_user`: Bearer first, then cookie |
| `content-queue-backend/app/middleware/rate_limit.py` | Redis sliding window per route |
| `cloudflare-worker/worker.js` | CORS handling in front of the API |
| `content-queue-backend/app/api/endpoints/public.py` | unauthenticated profile routes |
| `frontend/lib/bionicReading.ts` | `sanitizeArticleHtml` (DOMPurify) |
| `content-queue-backend/app/mcp/tools/query.py` | the text-to-SQL guard |

### Passwords and access tokens

Passwords are hashed with `CryptContext(schemes=["bcrypt"])`. The access token is a JWT whose only identity claim is the email:

<!--src content-queue-backend/app/core/security.py-->
```python
to_encode = data.copy()
if expires_delta:
    expire = datetime.utcnow() + expires_delta
else:
    expire = datetime.utcnow() + timedelta(
        minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
to_encode.update({"exp": expire})
return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
```

The default lifetime is `ACCESS_TOKEN_EXPIRE_MINUTES = 1440` (24 hours) in `core/config.py`. The module docstring of `api/auth.py` says "default 7 days", which is out of date. Validation of a token (`core/deps.py`) decodes it, looks the user up by email and, on routes using `get_current_active_user`, rejects inactive users. An access token cannot be revoked before it expires; the 24-hour lifetime is the exposure window.

### Refresh rotation with reuse detection

The refresh token is 48 random bytes, URL-safe encoded (64 characters); only its SHA-256 is stored. The key step in `/auth/refresh` is an atomic update that revokes a token only if it is not yet revoked:

<!--src content-queue-backend/app/api/auth.py-->
```python
rotated = db.execute(
    RefreshToken.__table__.update()
    .where(
        RefreshToken.id == record.id,
        RefreshToken.revoked_at.is_(None),
    )
    .values(revoked_at=datetime.now(timezone.utc))
)
db.commit()

if rotated.rowcount == 0:
```

If two requests present the same token at once, the database serializes the updates and exactly one matches a row. The other gets zero rows and falls into the grace-window branch: revoked within 30 seconds counts as the client's own retry, later counts as theft and revokes all of the user's active tokens. The window is a constant (`_REFRESH_RETRY_GRACE_WINDOW`). The consequence is that a thief who replays a stolen token within 30 seconds of the real client's refresh is not detected.

### CSRF

<!--src content-queue-backend/app/core/auth_cookies.py-->
```python
if request.method not in _CSRF_PROTECTED_METHODS:
    return True
if request.url.path in _CSRF_EXEMPT_PATHS:
    return True
if request.headers.get("authorization"):
    return True
if ACCESS_TOKEN_COOKIE not in request.cookies:
    return True
```

Four exemptions: reads (GET, HEAD, OPTIONS), `/auth/login` (no cookie yet to forge), any request that carries an `Authorization` header, and any request without the access cookie. After these, the header and cookie are compared with `secrets.compare_digest`. The last exemption has a gap: `/auth/refresh` and `/auth/logout` accept the refresh cookie on its own, so a request carrying only that cookie (the access cookie expired or absent) skips the CSRF check. What stops a forged cross-site request in that case is `SameSite=Lax` alone, which blocks the cookie on cross-site POSTs in current browsers. The fix is to require the CSRF header whenever either auth cookie is present.

### CORS and why a Cloudflare Worker exists

FastAPI adds `CORSMiddleware` with origins from the `ALLOWED_ORIGINS` environment variable plus a regex that accepts any `chrome-extension://`, `moz-extension://` or `safari-web-extension://` origin, with credentials allowed. The regex does not check the extension id, so any installed extension is an allowed origin; this is safe only because those clients authenticate with a Bearer token that a foreign extension does not have, and cookies are `SameSite=Lax`.

`cloudflare-worker/worker.js` sits in front of the API. Its header comment gives the reason: Railway's edge rejects requests whose `Origin` header it does not like ("Disallowed CORS origin"). So the Worker answers preflight requests itself from a fixed allow-list (the app's origins, `claude.ai`, `claude.com`, `localhost:3000` and the extension regex), removes `Origin` and `Host` before forwarding to Railway, and then writes the CORS headers onto the response. A consequence is that the FastAPI CORS middleware never sees the browser's origin in production, so the allow-list in the Worker is the one that matters, and two lists must be kept in step. Another is that the API sees the Worker's or Railway's address as the client, which matters for rate limiting (below).

### Email verification and password reset

Both use one-time rows in `verification_tokens`: a 32-byte URL-safe random string stored as plain text (not hashed), a `token_type`, an expiry (24 hours for verification, 1 hour for reset) and an `is_used` flag. Requesting a reset marks earlier unused reset tokens as used. The forgot-password response is identical whether or not the address exists. Verification does not gate anything: the backend sets `is_verified` but never reads it, so unverified users can log in and use the API. Resetting a password does not revoke existing refresh tokens (read from `reset_password`: it changes the hash and marks the token used, nothing else).

### Rate limiting

`middleware/rate_limit.py` implements a sliding window per route in a Redis sorted set (see [Background work](/platform/background-work/)). It covers exactly four routes:

<!--src content-queue-backend/app/middleware/rate_limit.py-->
```python
_LIMITED_ROUTES: dict[tuple[str, str], tuple[int, int]] = {
    ("POST", "/content"): (10, 50),
    ("POST", "/auth/login"): (10, 30),
    ("POST", "/auth/register"): (5, 15),
    ("GET", "/search/semantic"): (30, 300),
}
```

The pairs are (requests per minute, requests per hour). Forgot-password and reset-password are not limited, so an address can be sent mail repeatedly. The identifier is built from `request.state.user` if it exists, otherwise from `request.client.host`. Nothing in `app/` ever sets `request.state.user` (it runs before authentication), so in practice every limit is per client IP. Behind the Worker and Railway's proxy that address may be a proxy address shared by many users; whether the deployed server reads the forwarded address was not verified. If Redis is unreachable the limiter allows the request (fail open). Its two Redis steps (check, then record) are separate pipelines, so a burst of simultaneous requests can exceed the limit by about the number of requests in flight.

### Tenant isolation

Every route filters by the caller's id: `ContentItem.user_id == current_user.id`, `Highlight.user_id == ...`, `List.owner_id == ...`, and the raw SQL in search uses `WHERE user_id = :uid`. There is no Postgres row-level security, so the filter is a convention each query must follow; a single query that forgets it would return other users' rows, and the database would not object. Two shared stores are worth knowing about: `tag_embeddings` has no owner (one row per label for everyone), and cached query embeddings in Redis are keyed by the query text only (they carry no user data).

### Public profiles

`api/endpoints/public.py` has four unauthenticated routes. They check the profile flag, the queue or crates flag, and the item's own `is_public` flag, and filter `deleted_at`. A missing queue and a private queue return the same 404. One problem: `GET /public/u/{username}` uses `UserResponse` as its response model, which includes `email`, `id`, `is_active` and `is_verified`, so a public profile shows the owner's email address to anyone. The fix is a dedicated public schema with only username, full name and the visibility flags. Separately, a profile that exists but is private answers 403 while a missing one answers 404, so the route confirms which usernames exist.

### Sanitizing article HTML

Article HTML comes from arbitrary third-party pages and is stored as extracted. The backend does not sanitize it (no sanitizer in `app/`). The browser does, at render time: `sanitizeArticleHtml` runs `DOMPurify.sanitize` on the final HTML string, after highlight spans and heading anchors are injected, and the extension's reader overlay sanitizes with a vendored copy of the same library and refuses to render if it is not loaded. The consequence is that every new place that renders `full_text` must remember to call the function; the draft editor renders block content with `dangerouslySetInnerHTML` for image blocks, which holds the user's own content (not verified to be sanitized).

### Text-to-SQL

The MCP `query_library` tool lets a model write SQL. The guard parses it with sqlglot, allows one `SELECT` over seven listed tables, forbids data-changing nodes anywhere in the tree, requires a direct `user_id = :user_id` predicate for each user-scoped table, and runs with a 500 ms timeout and a 50-row cap. It does not require `deleted_at IS NULL`, so trashed items can be returned. See [MCP and text-to-SQL](/llm/mcp-and-text-to-sql/).

### Secrets and configuration

`core/config.py` reads settings from the environment (and a `.env` file). Two defaults matter:

<!--src content-queue-backend/app/core/config.py-->
```python
SECRET_KEY: str
...
DEBUG: bool = True
```

`DEBUG` defaults to `True`. When it is true the app mounts the `test_pdf` router and sets cookies without the `Secure` flag (`_COOKIE_SECURE = not settings.DEBUG`), so a production service that loses its `DEBUG` variable silently downgrades. `SECRET_KEY` is required but has no length or strength check. Settings classes carry no validator that would reject a short key.

### Status of known production-readiness gaps

An internal audit on 2026-10-12 (not published) listed P0 and P1 items. Each was re-checked against the current code on 2026-10-26:

| Item | State now | Evidence in code |
|---|---|---|
| SSRF in URL fetch | **Open** | `fetch_bounded` in `core/fetch.py` calls `requests.get` with redirects followed and no check of the resolved address; nothing in `app/` mentions `ipaddress` or private ranges |
| Unsafe defaults (`DEBUG`, `SECRET_KEY`) | **Open** | `DEBUG: bool = True`; no key validator |
| Backups and restore procedure | **Open** | no procedure in the repository; live state not checked |
| Duplicate-save race | Fixed | partial unique index `uq_content_items_user_url_active` ([Data model](/platform/data-model/)) |
| Registration race and email case | **Open** | `register` queries then inserts with no `IntegrityError` handler; no `lower(email)`; separate "Email already registered" and "Username already registered" messages |
| Stuck extraction jobs | **Open** | `recover_stale_pending_items` selects only `pending`; `extract_metadata` sets `processing` and nothing sweeps it; no `acks_late` |
| Account deletion leaves S3 objects | **Open** | `delete_current_user` calls `db.delete`; no storage delete anywhere in `app/` |
| `/health` checks nothing | **Open** | returns `{"status": "healthy"}` unconditionally; no readiness route |
| Trashed items via text-to-SQL | **Open** | `deleted_at` absent from `mcp/tools/query.py` |
| Fetch size limits (a P2 item) | Fixed | `FETCH_HTML_MAX_BYTES`, `FETCH_PDF_MAX_BYTES`, `FETCH_MAX_SECONDS` |

The SSRF problem in plain terms: the worker fetches whatever URL a user saves, so a user can point it at addresses that only the server's network can reach. The fix is to allow only http and https, resolve the host, reject private, loopback, link-local and reserved ranges, and repeat the check on each redirect, in the one shared `fetch_bounded` helper that every extraction call already uses.

## Design choices

| Choice | Alternatives | Why this one |
|---|---|---|
| Short access JWT plus rotating refresh token | One long-lived token; server-side sessions | Access checks need no database call for the signature; refresh tokens can be revoked and rotated. Cost: an access token cannot be revoked early. |
| Refresh tokens stored as SHA-256 | Plain text; bcrypt | They are random 288-bit values, so a fast hash is enough and lookup stays one indexed query. |
| httpOnly cookies for the web app, Bearer for other clients | `localStorage` tokens for everyone | Page scripts cannot steal the cookies. Cost: CSRF protection is needed, and two auth paths must stay consistent. |
| Double-submit CSRF plus `SameSite=Lax` | Per-session server-stored token; `SameSite=Strict` only | Stateless. Relies on the cookie being readable by the front end, hence the parent-domain cookie setting. |
| Reuse detection with a 30 s grace window | Revoke on any reuse; no detection | Tolerates a retry after a dropped response. Cost: a replay inside 30 s goes unnoticed. |
| Isolation by query filters | Row-level security; one schema per user | Simple, visible in each query. Cost: a missed filter is a leak with no second line of defence. |
| CORS in a Worker | Fix the Railway edge setting | Works around the edge rejecting Origin. Cost: a second allow-list and an extra hop. |
| Fail-open rate limiting and budgets | Fail closed | Redis is already a hard dependency of the queue; blocking users adds a second failure mode. Cost: no limit during an outage. |
| Sanitize at render time | Sanitize once on save | Raw HTML stays available for highlights and re-processing. Cost: every renderer must call the sanitizer. |

## How it fails

| Failure | What causes it | What happens | What was done |
|---|---|---|---|
| Two refreshes of one token at once | A double request | A read-then-write check let both pass | Atomic conditional UPDATE (found by concurrency testing) |
| Retry after a dropped refresh response | Flaky network | The retry looks like a replay | 30 s grace window, so it is not treated as theft |
| Stolen refresh token replayed | Token leak | All the user's sessions are revoked | Reuse detection (after 30 s) |
| Missing `DEBUG` setting | Misconfigured deploy | Debug route mounted, cookies lose `Secure` | Open |
| Redis down | Outage | Rate limits and spend caps are skipped | Deliberate fail-open |
| Public profile response | Wrong response model | Email address visible | Open |
| Password reset does not log out other sessions | Reset only changes the hash | Existing refresh tokens keep working | Open |

## What is weak or missing

Ranked by how likely each is to matter. The fixes are one line each; none needs a redesign.

1. **Server-side fetch has no address check (SSRF).** Fix: validate scheme and resolved address, and re-validate on redirects, in `fetch_bounded`.
2. **Public profile responses carry more fields than the page needs.** Fix: a separate response schema for public routes, and the same status code for private and missing profiles.
3. **Unsafe defaults.** Fix: `DEBUG` defaults to false; reject a `SECRET_KEY` under 32 characters when not debugging.
4. **No tested backup.** Fix: enable a Railway backup policy, restore once, time it, document it.
5. **Rate limits are per IP, cover four routes, and may share one bucket behind the proxy.** Fix: read the real client address from trusted proxy headers; add forgot-password and reset-password.
6. **Password reset does not revoke refresh tokens, and email is not case-normalized.** Fix: revoke tokens on reset; store and compare lower-cased emails with a unique index on `lower(email)`; catch `IntegrityError` on register.
7. **CSRF is skipped when only the refresh cookie is present.** Fix: require the header whenever either cookie is present.
8. **No row-level security, and soft delete is not applied in `query_library`.** Fix: RLS or a filtered view.
9. **`/health` cannot tell a dead database from a healthy app.** Fix: keep it as liveness; add a readiness route that runs `SELECT 1` and a Redis ping, and point the platform at it.
10. **Verification emails gate nothing.** Fix: decide whether unverified accounts may save content.

{% say %}
Passwords are stored as bcrypt hashes. Login gives a 24-hour signed access token and a 90-day random refresh token; the database keeps only a hash of the refresh token. The web app gets both as httpOnly cookies plus a readable CSRF cookie that must be echoed in a header on any mutating request, while the extension and MCP clients just send a Bearer token. Every refresh rotates the token, and presenting an already-used one more than 30 seconds later revokes all of that user's tokens. Users are separated by a user id filter in every query, not by the database, and four routes are rate limited per IP in Redis. The open problems are server-side request forgery in the fetcher, a public profile that shows the email address, debug-on defaults, and no tested backup.
{% endsay %}

## Check yourself

{% qa "Why does a POST with only the access cookie fail with 403, and why does the same POST with a Bearer header succeed without a CSRF token?" %}
A browser attaches cookies automatically, even to requests started by another site, so the server demands a second value that only same-site script can read: the CSRF cookie echoed in a header. A Bearer header is something a foreign page cannot make the browser add, so there is no forgery risk to defend against.
{% endqa %}

{% qa "Two requests present the same refresh token at the same moment. What does the database do, and why is that safe?" %}
Both run an UPDATE that sets `revoked_at` only where it is null. The database serializes them, so one matches the row and the other matches zero rows. A read-then-write check could let both pass.
{% endqa %}

{% qa "A refresh token is replayed 10 seconds after it was used, and another 5 minutes after. What differs?" %}
The first is treated as the client's own retry: rejected, nothing else changes. The second is treated as theft: every active refresh token of that user is revoked, logging out all their devices.
{% endqa %}

{% qa "What is the gap in the CSRF exemption rules, and what covers it today?" %}
A request with neither the access cookie nor an Authorization header skips the check, yet `/auth/refresh` and `/auth/logout` accept the refresh cookie alone. Only `SameSite=Lax` stands in the way of a cross-site POST. Requiring the header whenever any auth cookie is present would close it.
{% endqa %}

{% qa "Why does the Cloudflare Worker exist, and what does it make you maintain twice?" %}
Railway's edge rejects requests carrying certain Origin headers, so the Worker handles preflight, strips Origin and re-adds CORS headers. The allowed origins are therefore listed in the Worker and in the FastAPI settings.
{% endqa %}

{% qa "There is no row-level security. What protects one user's rows from another, and what would you add?" %}
Each query filters by the caller's id, and the text-to-SQL path enforces that with a parser. Nothing in the database stops a query that forgets the filter. Row-level security policies keyed to a per-request user setting would add a second line of defence.
{% endqa %}
