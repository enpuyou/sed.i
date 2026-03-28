"""
OAuth 2.1 + PKCE endpoints for the hosted MCP server (Phase 2).

Flow:
  1. MCP client fetches /.well-known/oauth-authorization-server to discover endpoints.
  2. Client redirects user browser to /mcp/authorize with PKCE challenge.
  3. User logs in with their sed.i credentials (email + password).
  4. Server stores a short-lived auth code in Redis and redirects to client's redirect_uri.
  5. Client POSTs to /mcp/token to exchange code for a sed.i JWT access token.
  6. Client uses JWT as Bearer token on /mcp/ (Streamable HTTP transport).

The access token IS the sed.i JWT — no separate token store needed.
Auth codes are stored in Redis with a 5-minute TTL.
"""

from __future__ import annotations

import html
import hashlib
import base64
import secrets
import json
import os
from typing import Annotated
from datetime import timedelta
from urllib.parse import urlencode

import redis
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.security import verify_password, create_access_token
from app.models.user import User

router = APIRouter(tags=["mcp-oauth"])

# ---------------------------------------------------------------------------
# Redis client (auth codes expire in 5 minutes)
# ---------------------------------------------------------------------------

_redis: redis.Redis | None = None


def _get_redis() -> redis.Redis:
    global _redis
    if _redis is None:
        _redis = redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis


AUTH_CODE_TTL = 300  # 5 minutes
CODE_PREFIX = "mcp:code:"


def _normalize_redirects(client_id: str, redirects: object) -> set[str]:
    if not isinstance(redirects, list) or not redirects:
        raise RuntimeError(
            f"Invalid MCP_OAUTH_CLIENTS_JSON: client '{client_id}' must map to non-empty list"
        )

    allowed_redirects: set[str] = set()
    for uri in redirects:
        if not isinstance(uri, str) or not uri.strip():
            raise RuntimeError(
                f"Invalid MCP_OAUTH_CLIENTS_JSON: redirect for '{client_id}' must be non-empty string"
            )
        allowed_redirects.add(uri.strip())
    return allowed_redirects


def _validate_clients_payload(data: object) -> dict[str, set[str]]:
    if not isinstance(data, dict):
        raise RuntimeError("Invalid MCP_OAUTH_CLIENTS_JSON: expected object")

    clients: dict[str, set[str]] = {}
    for client_id, redirects in data.items():
        if not isinstance(client_id, str) or not client_id.strip():
            raise RuntimeError(
                "Invalid MCP_OAUTH_CLIENTS_JSON: client_id must be non-empty string"
            )
        client_key = client_id.strip()
        clients[client_key] = _normalize_redirects(client_key, redirects)
    return clients


def _load_registered_clients() -> dict[str, set[str]]:
    """
    Load OAuth client allowlist from MCP_OAUTH_CLIENTS_JSON env var.

    Expected format:
      {
        "client_id_1": ["https://client.example/callback"],
        "client_id_2": ["http://localhost:5173/callback"]
      }
    """
    raw = os.getenv("MCP_OAUTH_CLIENTS_JSON", "").strip()
    if not raw:
        return {}

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Invalid MCP_OAUTH_CLIENTS_JSON: must be valid JSON"
        ) from exc
    return _validate_clients_payload(data)


_REGISTERED_CLIENTS = _load_registered_clients()


def _validate_client_and_redirect(client_id: str, redirect_uri: str) -> str | None:
    if client_id not in _REGISTERED_CLIENTS:
        return "Unknown OAuth client_id"

    allowed = _REGISTERED_CLIENTS[client_id]
    if redirect_uri not in allowed:
        return "redirect_uri is not allowed for this client_id"

    return None


def _escape(value: str) -> str:
    return html.escape(value, quote=True)


# ---------------------------------------------------------------------------
# Discovery document
# ---------------------------------------------------------------------------


@router.get("/.well-known/oauth-authorization-server")
def oauth_discovery(request: Request) -> JSONResponse:
    """
    RFC 8414 OAuth 2.0 Authorization Server Metadata.
    MCP clients fetch this to discover authorize/token endpoints.
    """
    # Prefer explicit API_BASE_URL (set in Railway) over request.base_url,
    # which resolves to the internal Railway host behind the reverse proxy.
    base = (settings.API_BASE_URL or str(request.base_url)).rstrip("/")
    return JSONResponse(
        {
            "issuer": base,
            "authorization_endpoint": f"{base}/mcp/authorize",
            "token_endpoint": f"{base}/mcp/token",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
        }
    )


# ---------------------------------------------------------------------------
# /mcp/authorize  — show login form / handle POST
# ---------------------------------------------------------------------------

_LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>sed.i — Authorize</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: system-ui, sans-serif; background: #0f0f0f; color: #e5e5e5;
         display: flex; align-items: center; justify-content: center; min-height: 100vh; }}
  .card {{ background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 12px;
           padding: 2rem; width: 100%; max-width: 380px; }}
  h1 {{ font-size: 1.25rem; font-weight: 600; margin-bottom: 0.25rem; }}
  .sub {{ color: #888; font-size: 0.85rem; margin-bottom: 1.5rem; }}
  label {{ display: block; font-size: 0.8rem; color: #aaa; margin-bottom: 0.3rem; }}
  input {{ display: block; width: 100%; padding: 0.6rem 0.75rem; background: #111;
           border: 1px solid #333; border-radius: 6px; color: #e5e5e5;
           font-size: 0.9rem; margin-bottom: 1rem; }}
  input:focus {{ outline: none; border-color: #555; }}
  button {{ width: 100%; padding: 0.65rem; background: #e5e5e5; color: #111;
            border: none; border-radius: 6px; font-size: 0.9rem; font-weight: 600;
            cursor: pointer; }}
  button:hover {{ background: #fff; }}
  .error {{ color: #f87171; font-size: 0.85rem; margin-bottom: 1rem; }}
</style>
</head>
<body>
<div class="card">
  <h1>sed.i</h1>
  <p class="sub">Sign in to connect your library</p>
  {error_block}
  <form method="post">
    <input type="hidden" name="client_id" value="{client_id}">
    <input type="hidden" name="redirect_uri" value="{redirect_uri}">
    <input type="hidden" name="state" value="{state}">
    <input type="hidden" name="code_challenge" value="{code_challenge}">
    <input type="hidden" name="code_challenge_method" value="{code_challenge_method}">
    <label for="email">Email</label>
    <input id="email" type="email" name="email" required autofocus>
    <label for="password">Password</label>
    <input id="password" type="password" name="password" required>
    <button type="submit">Authorize</button>
  </form>
</div>
</body>
</html>"""


@router.get(
    "/mcp/authorize",
    response_class=HTMLResponse,
    responses={400: {"description": "Invalid OAuth authorization request"}},
)
def authorize_get(
    client_id: Annotated[str, Query(...)],
    redirect_uri: Annotated[str, Query(...)],
    response_type: Annotated[str, Query(...)],
    code_challenge: Annotated[str, Query(...)],
    code_challenge_method: Annotated[str, Query()] = "S256",
    state: Annotated[str, Query()] = "",
):
    """Show the login form."""
    validation_error = _validate_client_and_redirect(client_id, redirect_uri)
    if validation_error:
        raise HTTPException(400, validation_error)
    if response_type != "code":
        raise HTTPException(400, "Only response_type=code is supported")
    if code_challenge_method != "S256":
        raise HTTPException(400, "Only code_challenge_method=S256 is supported")

    html = _LOGIN_HTML.format(
        client_id=_escape(client_id),
        redirect_uri=_escape(redirect_uri),
        state=_escape(state),
        code_challenge=_escape(code_challenge),
        code_challenge_method=_escape(code_challenge_method),
        error_block="",
    )
    return HTMLResponse(html)


@router.post(
    "/mcp/authorize",
    response_class=HTMLResponse,
    responses={400: {"description": "Invalid OAuth authorization request"}},
)
def authorize_post(
    client_id: Annotated[str, Form(...)],
    redirect_uri: Annotated[str, Form(...)],
    code_challenge: Annotated[str, Form(...)],
    email: Annotated[str, Form(...)],
    password: Annotated[str, Form(...)],
    db: Annotated[Session, Depends(get_db)],
    state: Annotated[str, Form()] = "",
    code_challenge_method: Annotated[str, Form()] = "S256",
):
    """Validate credentials, store auth code in Redis, redirect to client."""
    validation_error = _validate_client_and_redirect(client_id, redirect_uri)
    if validation_error:
        raise HTTPException(400, validation_error)
    if code_challenge_method != "S256":
        raise HTTPException(400, "Only code_challenge_method=S256 is supported")

    def _show_error(msg: str) -> HTMLResponse:
        html = _LOGIN_HTML.format(
            client_id=_escape(client_id),
            redirect_uri=_escape(redirect_uri),
            state=_escape(state),
            code_challenge=_escape(code_challenge),
            code_challenge_method=_escape(code_challenge_method),
            error_block=f'<p class="error">{_escape(msg)}</p>',
        )
        return HTMLResponse(html, status_code=401)

    user = db.query(User).filter(User.email == email, User.is_active.is_(True)).first()
    if not user or not verify_password(password, user.hashed_password):
        return _show_error("Invalid email or password.")

    # Generate auth code and store challenge + user email in Redis
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

    params = {"code": code}
    if state:
        params["state"] = state
    return RedirectResponse(f"{redirect_uri}?{urlencode(params)}", status_code=302)


# ---------------------------------------------------------------------------
# /mcp/token  — exchange code for access token
# ---------------------------------------------------------------------------


@router.post(
    "/mcp/token",
    responses={400: {"description": "Invalid OAuth token exchange request"}},
)
def token(
    grant_type: Annotated[str, Form(...)],
    code: Annotated[str, Form(...)],
    redirect_uri: Annotated[str, Form(...)],
    client_id: Annotated[str, Form(...)],
    code_verifier: Annotated[str, Form(...)],
    db: Annotated[Session, Depends(get_db)],
):
    """Exchange an auth code + PKCE verifier for a sed.i JWT access token."""
    validation_error = _validate_client_and_redirect(client_id, redirect_uri)
    if validation_error:
        raise HTTPException(400, validation_error)
    if grant_type != "authorization_code":
        raise HTTPException(400, "Unsupported grant_type")

    r = _get_redis()
    raw = r.get(f"{CODE_PREFIX}{code}")
    if not raw:
        raise HTTPException(400, "Invalid or expired authorization code")

    data = json.loads(raw)

    # Validate redirect_uri matches
    if data["redirect_uri"] != redirect_uri:
        raise HTTPException(400, "redirect_uri mismatch")

    # Validate client_id matches
    if data.get("client_id") != client_id:
        raise HTTPException(400, "client_id mismatch")

    # Validate PKCE S256
    digest = hashlib.sha256(code_verifier.encode()).digest()
    computed_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    if computed_challenge != data["code_challenge"]:
        raise HTTPException(400, "code_verifier does not match code_challenge")

    # Code is single-use — delete it
    r.delete(f"{CODE_PREFIX}{code}")

    user = (
        db.query(User)
        .filter(User.email == data["email"], User.is_active.is_(True))
        .first()
    )
    if not user:
        raise HTTPException(400, "User not found")

    access_token = create_access_token(
        data={"sub": user.email},
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )

    return JSONResponse(
        {
            "access_token": access_token,
            "token_type": "bearer",
            "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        }
    )
