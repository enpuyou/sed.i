"""
HTTP transport for the sed.i MCP server (Phase 2).

Mounts the FastMCP server as a Streamable HTTP ASGI app at /mcp.
Auth: Bearer token (sed.i JWT) validated per-request.

The FastMCP server defined in app.mcp.server is reused — all tools are
already registered there. Here we wrap it with JWT auth middleware so
each incoming request is authenticated before tools execute.
"""

from __future__ import annotations

import logging
import os
import contextvars

from jose import JWTError, jwt
from mcp.server.fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


from app.core.config import settings
from app.mcp.db import get_db
from app.mcp.tools.lists import (
    list_lists as _list_lists,
    get_list_content as _get_list_content,
)
from app.mcp.tools.content import (
    get_content_item as _get_content_item,
    search_content as _search_content,
    find_similar as _find_similar,
)
from app.mcp.tools.highlights import get_highlights as _get_highlights
from app.mcp.tools.drafts import get_draft as _get_draft
from app.mcp.tools.stats import get_reading_stats as _get_reading_stats
from app.mcp.tools.summarize import summarize_list as _summarize_list
from app.mcp.tools.write import (
    update_draft as _update_draft,
    add_content as _add_content,
    create_list as _create_list,
    add_to_list as _add_to_list,
)

logger = logging.getLogger("sedi.mcp.http")


def _resolve_user_from_bearer(authorization: str, db):
    """Extract and validate Bearer JWT, return User."""
    from app.models.user import User

    if not authorization.startswith("Bearer "):
        raise ValueError("Missing Bearer token")
    token = authorization[len("Bearer ") :]
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        email: str | None = payload.get("sub")
        if not email:
            raise ValueError("Token missing sub claim")
    except JWTError as exc:
        raise ValueError(f"Invalid token: {exc}") from exc

    user = db.query(User).filter(User.email == email, User.is_active.is_(True)).first()
    if not user:
        raise ValueError("User not found")
    return user


# ---------------------------------------------------------------------------
# Build a dedicated FastMCP instance for HTTP (same tools, HTTP transport)
# ---------------------------------------------------------------------------

http_mcp = FastMCP(
    "sedi",
    instructions=(
        "sed.i is a personal reading and writing assistant. "
        "Use these tools to explore the user's library, reading lists, highlights, and drafts. "
        "Always operate on the authenticated user's data only."
    ),
)


def _get_user_from_request(request: Request):
    """Extract user from request state (set by auth middleware)."""
    user = getattr(request.state, "mcp_user", None)
    if user is None:
        raise PermissionError("Unauthenticated request")
    return user


# ---------------------------------------------------------------------------
# Register all tools on http_mcp
# Each tool reads the user from request.state via a thread-local workaround.
# FastMCP HTTP passes the request context via contextvars in newer versions;
# for compatibility we resolve auth in middleware and store on a simple
# request-scoped store keyed by thread id.
# ---------------------------------------------------------------------------

_request_user_var: contextvars.ContextVar = contextvars.ContextVar("mcp_user")


def _current_user():
    user = _request_user_var.get(None)
    if user is None:
        raise PermissionError("No authenticated user in context")
    return user


@http_mcp.tool()
def list_lists() -> list[dict]:
    """List all reading lists owned by the user with article counts."""
    with get_db() as db:
        return _list_lists(user=_current_user(), db=db)


@http_mcp.tool()
def get_list_content(
    list_id: str, include_full_text: bool = False, limit: int = 50
) -> list[dict]:
    """Get articles in a reading list."""
    with get_db() as db:
        return _get_list_content(
            list_id=list_id,
            user=_current_user(),
            db=db,
            include_full_text=include_full_text,
            limit=limit,
        )


@http_mcp.tool()
def get_content_item(item_id: str, include_full_text: bool = False) -> dict:
    """Get a single article by ID."""
    with get_db() as db:
        return _get_content_item(
            item_id=item_id,
            user=_current_user(),
            db=db,
            include_full_text=include_full_text,
        )


@http_mcp.tool()
def search_content(query: str, limit: int = 10) -> list[dict]:
    """Semantic search across the user's entire library."""
    with get_db() as db:
        return _search_content(query=query, user=_current_user(), db=db, limit=limit)


@http_mcp.tool()
def find_similar(item_id: str, limit: int = 5) -> list[dict]:
    """Find articles similar to a given article."""
    with get_db() as db:
        return _find_similar(item_id=item_id, user=_current_user(), db=db, limit=limit)


@http_mcp.tool()
def get_highlights(
    item_id: str | None = None, list_id: str | None = None
) -> list[dict]:
    """Get highlights."""
    with get_db() as db:
        return _get_highlights(
            item_id=item_id, list_id=list_id, user=_current_user(), db=db
        )


@http_mcp.tool()
def get_draft(list_id: str) -> dict | None:
    """Get the writing draft for a reading list."""
    with get_db() as db:
        return _get_draft(list_id=list_id, user=_current_user(), db=db)


@http_mcp.tool()
def get_reading_stats() -> dict:
    """Get reading statistics for the user."""
    with get_db() as db:
        return _get_reading_stats(user=_current_user(), db=db)


@http_mcp.tool()
def summarize_list(list_id: str, style: str = "overview", max_items: int = 20) -> dict:
    """Summarize a reading list using AI."""
    with get_db() as db:
        return _summarize_list(
            list_id=list_id,
            user=_current_user(),
            db=db,
            style=style,
            max_items=max_items,
        )


@http_mcp.tool()
def update_draft(list_id: str, content: str, title: str | None = None) -> dict:
    """Create or update the writing draft for a reading list."""
    with get_db() as db:
        return _update_draft(
            list_id=list_id, content=content, title=title, user=_current_user(), db=db
        )


@http_mcp.tool()
def add_content(url: str) -> dict:
    """Save a URL to the user's library and queue extraction."""
    with get_db() as db:
        return _add_content(url=url, user=_current_user(), db=db)


@http_mcp.tool()
def create_list(name: str, description: str | None = None) -> dict:
    """Create a new reading list."""
    with get_db() as db:
        return _create_list(
            name=name, description=description, user=_current_user(), db=db
        )


@http_mcp.tool()
def add_to_list(list_id: str, item_id: str) -> dict:
    """Add an existing content item to a reading list."""
    with get_db() as db:
        return _add_to_list(
            list_id=list_id, item_id=item_id, user=_current_user(), db=db
        )


# ---------------------------------------------------------------------------
# Auth middleware — validates Bearer JWT and sets _request_user thread-local
# ---------------------------------------------------------------------------


class MCPAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        auth = request.headers.get("Authorization", "")
        if not auth:
            return JSONResponse(
                {"error": "Missing Authorization header"}, status_code=401
            )
        try:
            with get_db() as db:
                user = _resolve_user_from_bearer(auth, db)
            token = _request_user_var.set(user)
            try:
                response = await call_next(request)
                return response
            finally:
                _request_user_var.reset(token)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=401)


def build_mcp_asgi_app():
    """
    Return the FastMCP Streamable HTTP ASGI app wrapped with auth + CORS middleware.
    Mount this at /mcp in main.py.

    CORS is added here because the main app's CORSMiddleware does not cover
    mounted sub-applications — each ASGI sub-app needs its own CORS layer.
    """
    allowed_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")

    asgi = http_mcp.streamable_http_app()
    asgi.add_middleware(MCPAuthMiddleware)
    asgi.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_origin_regex=r"(chrome|moz)-extension://.*|safari-web-extension://.*",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    return asgi
