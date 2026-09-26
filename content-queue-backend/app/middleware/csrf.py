"""
CSRF middleware for cookie-authenticated requests.

Only matters once auth can happen via cookie (app/core/auth_cookies.py) —
a request authenticated via Authorization: Bearer (extension, MCP clients)
has no CSRF risk, since a cross-site page can't forge that header the way it
can a cookie. See app/core/auth_cookies.py::verify_csrf for the actual check
(double-submit: X-CSRF-Token header must match the sedi_csrf_token cookie).
"""

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.auth_cookies import verify_csrf


class CSRFMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not verify_csrf(request):
            return JSONResponse(
                status_code=403,
                content={"detail": "CSRF token missing or invalid."},
            )
        return await call_next(request)
