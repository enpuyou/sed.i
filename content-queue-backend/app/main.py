import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import posthog

from app.core.config import settings
from app.api import auth, content, lists, search, analytics, highlights, vinyl, drafts
from app.api.endpoints import public
from app.middleware.rate_limit import RateLimitMiddleware
from app.mcp.oauth import router as mcp_oauth_router
from app.mcp.http_server import build_mcp_asgi_app, http_mcp  # noqa: E402


class _MCPProxy:
    """
    Thin ASGI proxy that always delegates to the current MCP ASGI app.
    This allows the lifespan to rebuild the ASGI app (and its session manager)
    on each startup without needing to swap the mounted app reference.
    """

    def __init__(self):
        self._app = None

    def rebuild(self):
        self._app = build_mcp_asgi_app()

    async def __call__(self, scope, receive, send):
        await self._app(scope, receive, send)


_mcp_proxy = _MCPProxy()


@asynccontextmanager
async def lifespan(application: FastAPI):
    # Startup: initialise PostHog (no-ops when key is absent)
    if settings.POSTHOG_API_KEY:
        posthog.project_api_key = settings.POSTHOG_API_KEY
        posthog.host = settings.POSTHOG_HOST
    else:
        posthog.disabled = True
    # Rebuild the MCP ASGI app (and its session manager) on every startup so
    # repeated test runs each get a fresh StreamableHTTPSessionManager instance.
    http_mcp._session_manager = None  # force streamable_http_app() to create a new one
    _mcp_proxy.rebuild()  # calls streamable_http_app() → new session manager
    async with http_mcp.session_manager.run():
        yield
    # Shutdown: flush any buffered events
    try:
        posthog.shutdown()
    except Exception as exc:
        logging.getLogger(__name__).warning("PostHog shutdown flush failed: %s", exc)


app = FastAPI(
    title="Content Queue API",
    description="Personal content aggregation and reading queue",
    version="0.1.0",
    debug=settings.DEBUG,
    lifespan=lifespan,
)

# CORS
allowed_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    # Allow browser extension origins (Chrome, Firefox, Safari)
    allow_origin_regex=r"(chrome|moz)-extension://.*|safari-web-extension://.*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate Limiting Middleware
app.add_middleware(RateLimitMiddleware)


# Rewrite bare /mcp → /mcp/ so the MCP ASGI sub-app receives the request
# directly without Starlette issuing a 307 redirect that strips the
# Authorization header.
@app.middleware("http")
async def _mcp_trailing_slash(request, call_next):
    if request.url.path == "/mcp":
        request.scope["path"] = "/mcp/mcp"
        request.scope["raw_path"] = b"/mcp/mcp"
    return await call_next(request)


# Include routers
app.include_router(auth.router)
app.include_router(content.router)
app.include_router(highlights.router)
app.include_router(lists.router)
app.include_router(search.router)
app.include_router(analytics.router)
app.include_router(vinyl.router)
app.include_router(drafts.router)
app.include_router(public.router)
app.include_router(mcp_oauth_router)

from starlette.routing import Mount  # noqa: E402

app.router.routes.append(Mount("/mcp", app=_mcp_proxy))

# Dev-only test routes (serves local PDFs from gitignored pdf/ directory)
# Only mounted when DEBUG=true — never active in production
if settings.DEBUG:
    from app.api import test_pdf  # noqa: PLC0415

    app.include_router(test_pdf.router)


@app.get("/")
def root():
    return {"message": "Content Queue API", "version": "0.1.0", "docs": "/docs"}


@app.get("/health")
def health_check():
    return {"status": "healthy"}
