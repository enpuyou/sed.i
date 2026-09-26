"""
httpOnly cookie + double-submit CSRF token helpers for browser-based auth.

Only the Next.js web frontend uses cookies for auth — the browser extension
(Bearer token via chrome.storage, see extension/popup/popup.js) and MCP OAuth
clients (token issued directly in the OAuth response body) are unaffected;
both keep working exactly as before via the Authorization header, which
get_current_user (app/core/deps.py) still checks as a fallback.

Cookie scope: frontend (www.read-sedi.com) and backend (api.read-sedi.com)
are subdomains of the same registrable domain, so SameSite=Lax is sufficient
— no cross-site cookie fragility, no SameSite=None requirement.
"""

from __future__ import annotations

import secrets

from fastapi import Request, Response

from app.core.config import settings

ACCESS_TOKEN_COOKIE = "sedi_access_token"
REFRESH_TOKEN_COOKIE = "sedi_refresh_token"
CSRF_COOKIE = "sedi_csrf_token"
CSRF_HEADER = "X-CSRF-Token"

# Secure cookies are only sent over HTTPS — correct in production, but local
# dev (http://localhost:3000, per .env.example) and the test client both run
# over plain HTTP, so a browser/httpx correctly refuses to store or replay a
# Secure cookie there. settings.DEBUG is already this codebase's dev/prod
# signal (see app/api/test_pdf.py gating).
_COOKIE_SECURE = not settings.DEBUG

# Methods that mutate state and therefore need CSRF verification. GET/HEAD/
# OPTIONS are exempt — CSRF is only a risk for state-changing requests.
_CSRF_PROTECTED_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def set_auth_cookies(
    response: Response,
    *,
    access_token: str,
    refresh_token: str | None,
    access_max_age_s: int,
    refresh_max_age_s: int,
) -> None:
    """Set the httpOnly auth cookies and a readable CSRF cookie on a login/
    refresh response. Called in addition to returning the tokens in the JSON
    body — the JSON body is what the extension and MCP clients use; the
    cookies are what the web frontend uses."""
    response.set_cookie(
        ACCESS_TOKEN_COOKIE,
        access_token,
        max_age=access_max_age_s,
        httponly=True,
        secure=_COOKIE_SECURE,
        samesite="lax",
        path="/",
    )
    if refresh_token is not None:
        response.set_cookie(
            REFRESH_TOKEN_COOKIE,
            refresh_token,
            max_age=refresh_max_age_s,
            httponly=True,
            secure=_COOKIE_SECURE,
            samesite="lax",
            # Scoped narrowly — the refresh token only needs to be sent to
            # the refresh/logout endpoints, not every request.
            path="/auth",
        )
    # CSRF cookie is deliberately NOT httpOnly — the frontend must be able to
    # read it and echo it back in the X-CSRF-Token header (double-submit
    # pattern). It carries no secret value an attacker could use on its own;
    # its only job is proving the request originated from a page that could
    # read this cookie, i.e. same-origin JS, not a cross-site form/fetch.
    #
    # domain=settings.COOKIE_DOMAIN: without this, the cookie defaults to
    # host-only (api.read-sedi.com), which frontend JS running on
    # www.read-sedi.com cannot read via document.cookie — a different host,
    # even though it's the same registrable domain SameSite=Lax already
    # allows the *request* to cross. Set to ".read-sedi.com" in production so
    # both subdomains share it; empty in dev (host-only is correct for
    # localhost, which has no parent domain to share across ports).
    response.set_cookie(
        CSRF_COOKIE,
        secrets.token_urlsafe(32),
        max_age=access_max_age_s,
        httponly=False,
        secure=_COOKIE_SECURE,
        samesite="lax",
        path="/",
        domain=settings.COOKIE_DOMAIN or None,
    )


def clear_auth_cookies(response: Response) -> None:
    """Clear all auth cookies on logout.

    domain/path must match what set_auth_cookies used — a delete_cookie call
    with different attributes sets a *new*, separate cookie rather than
    clearing the original (cookies are keyed by name+domain+path together).
    """
    response.delete_cookie(ACCESS_TOKEN_COOKIE, path="/")
    response.delete_cookie(REFRESH_TOKEN_COOKIE, path="/auth")
    response.delete_cookie(CSRF_COOKIE, path="/", domain=settings.COOKIE_DOMAIN or None)


def get_token_from_cookie(request: Request) -> str | None:
    return request.cookies.get(ACCESS_TOKEN_COOKIE)


def get_refresh_token_from_cookie(request: Request) -> str | None:
    return request.cookies.get(REFRESH_TOKEN_COOKIE)


# /auth/login never has a cookie yet (nothing to forge) — exempt by URL.
# /auth/refresh and /auth/logout are NOT blanket-exempt: extension/MCP-style
# clients pass their refresh token in the request body and are already
# exempt via the Authorization-header/no-cookie checks below (they don't set
# the access-token cookie in the first place). But the web frontend's cookie-
# authenticated flow through these same endpoints is exactly CSRF's threat
# model — a cross-site page can trigger POST /auth/logout or /auth/refresh
# using only the ambient httpOnly cookie, no body token required by the
# endpoint's own fallback logic (see app/api/auth.py). A prior version of
# this exemption assumed a caller "must already know a secret token," which
# isn't true once the endpoint accepts a cookie fallback — so these paths get
# the same double-submit check as everything else instead of a blanket pass.
_CSRF_EXEMPT_PATHS = {"/auth/login"}


def verify_csrf(request: Request) -> bool:
    """
    Double-submit CSRF check: the X-CSRF-Token header must match the
    sedi_csrf_token cookie. Only enforced for cookie-authenticated requests —
    a request authenticated via Authorization: Bearer (extension, MCP) has no
    ambient-cookie CSRF risk in the first place, since a cross-site page
    can't forge an Authorization header the way it can a cookie.

    Returns True (pass) for:
    - Non-mutating methods (GET/HEAD/OPTIONS)
    - Requests authenticated via Authorization header, not cookie
    - Requests where no auth cookie is present at all (nothing to forge)
    - _CSRF_EXEMPT_PATHS (see comment above — /auth/login only)
    """
    if request.method not in _CSRF_PROTECTED_METHODS:
        return True
    if request.url.path in _CSRF_EXEMPT_PATHS:
        return True
    if request.headers.get("authorization"):
        return True
    if ACCESS_TOKEN_COOKIE not in request.cookies:
        return True

    cookie_value = request.cookies.get(CSRF_COOKIE)
    header_value = request.headers.get(CSRF_HEADER)
    return (
        bool(cookie_value)
        and bool(header_value)
        and secrets.compare_digest(cookie_value, header_value)
    )
