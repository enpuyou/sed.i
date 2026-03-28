"""
Auth for the local stdio MCP server (Phase 1).

Resolves a sed.i JWT (set via SEDI_TOKEN env var) to a User object.
The token is the same JWT the frontend uses — copy it from browser devtools
localStorage → 'token', then set it in Claude Desktop's config env section.

Phase 2 (hosted HTTP) will replace this with OAuth 2.1.
"""

import os
import sys
from jose import JWTError, jwt
from sqlalchemy.orm import Session
from app.core.config import settings
from app.models.user import User


class MCPAuthError(Exception):
    """Raised when the MCP token is missing or invalid."""


def get_user_from_token(token: str, db: Session) -> User:
    """
    Decode a sed.i JWT and return the corresponding active User.

    Raises MCPAuthError if the token is invalid, expired, or the user
    is not found / inactive.
    """
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        email: str | None = payload.get("sub")
        if not email:
            raise MCPAuthError("Token missing 'sub' claim")
    except JWTError as exc:
        raise MCPAuthError(f"Invalid token: {exc}") from exc

    user = (
        db.query(User)
        .filter(User.email == email, User.is_active == True)  # noqa: E712
        .first()
    )
    if not user:
        raise MCPAuthError(f"No active user found for email '{email}'")

    return user


def get_user_from_env(db: Session) -> User:
    """
    Resolve the user from the SEDI_TOKEN environment variable.

    This is the Phase 1 auth path for local stdio usage.
    Claude Desktop injects the token via the env block in claude_desktop_config.json.
    """
    token = os.environ.get("SEDI_TOKEN", "").strip()
    if not token:
        print(
            "ERROR: SEDI_TOKEN environment variable is not set.\n"
            "Set it in Claude Desktop's MCP server config under 'env':\n"
            '  "env": { "SEDI_TOKEN": "<your-sedi-jwt>" }',
            file=sys.stderr,
        )
        raise MCPAuthError("SEDI_TOKEN not set")
    return get_user_from_token(token, db)
