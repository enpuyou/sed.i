"""
Guards against MCP tool registration drift between the two servers.

app/mcp/server.py (stdio transport, local clients) and app/mcp/http_server.py
(HTTP transport, OAuth-based remote clients) each maintain their own tool
registration list — a tool added to one and not the other is silently
unreachable from whichever transport was missed (synthesize_topic and
assist_draft shipped stdio-only for a period; see
docs/changelog for the fix). This test fails loudly on that drift instead of
requiring someone to notice a tool is unreachable over HTTP.
"""

import asyncio

from app.mcp.server import mcp
from app.mcp.http_server import http_mcp


def _run(coro):
    """Run a coroutine synchronously without closing the shared default
    event loop — asyncio.run() creates and closes its own loop each call,
    which breaks other tests in this session that rely on
    asyncio.get_event_loop() finding a persistent current loop (see
    tests/test_rate_limiter.py's own `run` helper for the same pattern)."""
    return asyncio.get_event_loop().run_until_complete(coro)


def test_stdio_and_http_servers_register_the_same_tools():
    stdio_tools = {t.name for t in _run(mcp.list_tools())}
    http_tools = {t.name for t in _run(http_mcp.list_tools())}

    missing_from_http = stdio_tools - http_tools
    missing_from_stdio = http_tools - stdio_tools

    assert not missing_from_http, (
        f"Tools registered on the stdio server but not the HTTP server "
        f"(unreachable to OAuth/remote clients): {missing_from_http}"
    )
    assert not missing_from_stdio, (
        f"Tools registered on the HTTP server but not the stdio server: "
        f"{missing_from_stdio}"
    )
