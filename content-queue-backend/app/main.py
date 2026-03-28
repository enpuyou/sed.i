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


@asynccontextmanager
async def lifespan(application: FastAPI):
    # Startup: initialise PostHog (no-ops when key is absent)
    if settings.POSTHOG_API_KEY:
        posthog.project_api_key = settings.POSTHOG_API_KEY
        posthog.host = settings.POSTHOG_HOST
    else:
        posthog.disabled = True
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

# MCP HTTP transport — Streamable HTTP for claude.ai web access
from app.mcp.http_server import build_mcp_asgi_app  # noqa: E402

_mcp_asgi = build_mcp_asgi_app()
app.mount("/mcp", _mcp_asgi)
app.mount("/mcp/", _mcp_asgi)

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
