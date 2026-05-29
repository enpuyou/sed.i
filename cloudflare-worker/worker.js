/**
 * Cloudflare Worker — CORS proxy for api.read-sedi.com
 *
 * Sits in front of Railway/Fastly and handles CORS preflights itself,
 * then forwards requests to Railway with the Origin header stripped so
 * Railway's Fastly edge doesn't block them with "Disallowed CORS origin".
 *
 * Deploy to Cloudflare Workers and route api.read-sedi.com to it.
 */

// Set RAILWAY_ORIGIN as a secret in Cloudflare Workers dashboard (Settings → Variables → Secrets)
// Value: https://<your-railway-service>.up.railway.app
const RAILWAY_ORIGIN = RAILWAY_ORIGIN_SECRET ?? "";

const ALLOWED_ORIGINS = new Set([
  "http://localhost:3000",
  "https://read-sedi.com",
  "https://www.read-sedi.com",
  "https://content-queue.vercel.app",
  "https://claude.ai",
  "https://claude.com",
]);

const ALLOWED_ORIGIN_REGEX = /^(chrome|moz)-extension:\/\/.*|^safari-web-extension:\/\/.*/;

function getCorsHeaders(origin) {
  const allowed =
    ALLOWED_ORIGINS.has(origin) || ALLOWED_ORIGIN_REGEX.test(origin);

  if (!allowed) return {};

  return {
    "Access-Control-Allow-Origin": origin,
    "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
    "Access-Control-Allow-Headers":
      "Authorization, Content-Type, Mcp-Session-Id, Mcp-Protocol-Version, Accept",
    "Access-Control-Allow-Credentials": "true",
    "Access-Control-Max-Age": "600",
    "Vary": "Origin",
  };
}

export default {
  async fetch(request) {
    const origin = request.headers.get("Origin") || "";
    const corsHeaders = getCorsHeaders(origin);

    // Handle CORS preflight — respond directly, never forward to Railway
    if (request.method === "OPTIONS") {
      return new Response(null, {
        status: 204,
        headers: corsHeaders,
      });
    }

    // Build the forwarded request to Railway
    const url = new URL(request.url);
    url.hostname = new URL(RAILWAY_ORIGIN).hostname;
    url.protocol = "https:";
    url.port = "";

    // Strip Origin so Railway's Fastly edge doesn't block it.
    // Strip Host so Railway sees the correct host derived from the forwarded URL.
    // Strip Origin so Railway's Fastly edge doesn't block it.
    // Strip Host so the fetch uses the correct Railway hostname.
    const forwardHeaders = new Headers(request.headers);
    forwardHeaders.delete("Origin");
    forwardHeaders.delete("Host");

    // Buffer the body so it can be retransmitted if Railway/Fastly redirects.
    // Streaming bodies can't be replayed across redirects.
    const bodyBuffer = request.body ? await request.arrayBuffer() : null;

    const proxiedRequest = new Request(url.toString(), {
      method: request.method,
      headers: forwardHeaders,
      body: bodyBuffer,
      redirect: "manual",
    });

    let response;
    try {
      response = await fetch(proxiedRequest);
    } catch (err) {
      return new Response(JSON.stringify({ error: "upstream_error", detail: err.message }), {
        status: 502,
        headers: { "Content-Type": "application/json", ...corsHeaders },
      });
    }

    // Pass through redirects (3xx) directly to the browser — do not follow them.
    // opaqueredirect type means redirect: "manual" caught a 3xx response.
    if (response.type === "opaqueredirect" || (response.status >= 300 && response.status < 400)) {
      const redirectHeaders = new Headers(response.headers);
      for (const [key, value] of Object.entries(corsHeaders)) {
        redirectHeaders.set(key, value);
      }
      return new Response(null, {
        status: response.status || 302,
        headers: redirectHeaders,
      });
    }

    // Rebuild response with correct CORS headers
    // (Railway may return its own CORS headers for localhost — override them)
    const responseHeaders = new Headers(response.headers);
    for (const [key, value] of Object.entries(corsHeaders)) {
      responseHeaders.set(key, value);
    }

    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: responseHeaders,
    });
  },
};
