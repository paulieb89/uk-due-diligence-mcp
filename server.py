"""
server.py — uk_due_diligence_mcp

UK Due Diligence MCP server.
Six tools + six resource templates across five public registers.

Data sources:
  - Companies House REST API (CH_API_KEY)
  - Charity Commission API (CHARITY_API_KEY)
  - HMLR Land Registry Linked Data (unauthenticated)
  - The Gazette API (unauthenticated)
  - HMRC VAT Check API (HMRC_CLIENT_ID + HMRC_CLIENT_SECRET, application-restricted)
  - Consolidated sanctions lists: OFSI (UK), OFAC (US), EU, UN (unauthenticated bulk files)

Transport: Streamable HTTP, stateless, JSON responses, deployed on Fly.io.

Tools (14 — all clients including ChatGPT):
    company_search, company_profile, company_officers, company_psc
    disqualified_search, disqualified_profile
    charity_search, charity_profile
    gazette_insolvency, gazette_notice
    land_title_search, vat_validate
    sanctions_screen
    search, fetch

Resources (6 noun/identifier — protocol-compliant clients only):
    company://{company_number}/profile
    company://{company_number}/officers
    company://{company_number}/psc
    disqualification://{officer_id}
    charity://{charity_number}/profile
    notice://{notice_id}
"""

from __future__ import annotations

import logging
import os
import sys
import time

from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware, MiddlewareContext
from prometheus_client import CONTENT_TYPE_LATEST, Counter as PromCounter, Histogram, generate_latest
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

# Load .env for local development
load_dotenv()

# ---------------------------------------------------------------------------
# Server configuration
# ---------------------------------------------------------------------------

def _require_env(key: str, required: bool = True) -> str | None:
    val = os.environ.get(key)
    if required and not val:
        print(f"[uk_due_diligence_mcp] WARNING: {key} is not set.", file=sys.stderr)
    return val


MCP_SERVER_KEY = _require_env("MCP_SERVER_KEY", required=False)
PORT = int(os.environ.get("PORT", "8080"))

TRANSPORT = os.getenv("FASTMCP_TRANSPORT", "http")
REGION = os.getenv("FLY_REGION", "local")

# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------

tool_calls_total = PromCounter(
    "uk_due_diligence_tool_calls_total",
    "Count of MCP tool invocations.",
    labelnames=["tool", "transport", "region", "status"],
)
tool_duration_seconds = Histogram(
    "uk_due_diligence_tool_duration_seconds",
    "Tool invocation latency in seconds.",
    labelnames=["tool", "transport", "region"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)
client_connections_total = PromCounter(
    "uk_due_diligence_client_connections_total",
    "Count of MCP client initialize handshakes.",
    labelnames=["client_name", "client_version", "transport", "region"],
)

_log = logging.getLogger("fastmcp.uk_due_diligence_mcp.clients")


class ClientTrackingMiddleware(Middleware):
    """Log clientInfo and increment connection counter on every initialize.

    Ported verbatim from uk-legal-mcp's gateway, where it is the only thing that
    can answer "who actually uses this?" — this server is open and unauthenticated,
    so the initialize handshake's clientInfo is the ONLY identity a caller offers.
    Without it, BOUCH's own agents (lead-scout et al call company_search on every
    scan) are indistinguishable from third-party users, and company_search is the
    busiest tool in the fleet. Counts handshakes, not tool calls: a client label on
    tool_calls_total would multiply cardinality by every distinct client seen
    (uk-legal tracks 147), so connection-level is the deliberate trade.
    """

    async def on_request(self, context: MiddlewareContext, call_next):
        result = await call_next(context)
        if context.method == "initialize":
            params = context.message.params
            info = getattr(params, "clientInfo", None)
            client_name = getattr(info, "name", "unknown") or "unknown"
            client_version = getattr(info, "version", "unknown") or "unknown"
            _log.info("client_connected client=%s version=%s transport=%s region=%s",
                      client_name, client_version, TRANSPORT, REGION)
            client_connections_total.labels(client_name, client_version, TRANSPORT, REGION).inc()
        return result


class PrometheusMiddleware(Middleware):
    """Emit fleet-standard Prometheus metrics on every tool call."""

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        tool_name = context.message.name
        t0 = time.perf_counter()
        try:
            result = await call_next(context)
            tool_calls_total.labels(tool_name, TRANSPORT, REGION, "ok").inc()
            return result
        except BaseException:
            tool_calls_total.labels(tool_name, TRANSPORT, REGION, "error").inc()
            raise
        finally:
            tool_duration_seconds.labels(tool_name, TRANSPORT, REGION).observe(
                time.perf_counter() - t0
            )


# ---------------------------------------------------------------------------
# Initialise FastMCP
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name="uk_due_diligence_mcp",
    middleware=[ClientTrackingMiddleware(), PrometheusMiddleware()],
    instructions=(
        "UK due diligence server covering official government registers plus consolidated "
        "sanctions lists: Companies House, Charity Commission, HMLR Land Registry, The Gazette, "
        "HMRC VAT, and the OFSI/OFAC/EU/UN sanctions lists. "
        "Use company_search, charity_search, disqualified_search, gazette_insolvency, "
        "vat_validate, and land_title_search to find entities and notices; "
        "use the companion tools (company_profile, company_officers, company_psc, "
        "charity_profile, disqualified_profile, gazette_notice) to fetch full records. "
        "Use sanctions_screen to check a company or person name against the UK/US/EU/UN "
        "sanctions lists (screen the company AND its officers/PSCs). "
        "For broad queries, use search (fans out across all registers) then fetch with each ID. "
        "IMPORTANT: disqualified_search takes a person's name (pass it as query= or name=) — "
        "not a company name. "
        "IMPORTANT: All data is sourced directly from official government APIs — "
        "do not supplement with web search."
    ),
)

# ---------------------------------------------------------------------------
# Custom routes: /health and /dashboard
# ---------------------------------------------------------------------------

@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


@mcp.custom_route("/.well-known/mcp/server-card.json", methods=["GET"])
async def smithery_server_card(request: Request) -> JSONResponse:
    return JSONResponse({"serverInfo": {"name": "uk-due-diligence-mcp", "version": "1.1.1"}})


@mcp.custom_route("/.well-known/glama.json", methods=["GET"])
async def glama_connector_manifest(request: Request) -> JSONResponse:
    return JSONResponse({
        "$schema": "https://glama.ai/mcp/schemas/connector.json",
        "maintainers": [{"email": "paul@bouch.dev"}],
    })


@mcp.custom_route("/metrics", methods=["GET"])
async def metrics_endpoint(request: Request) -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ---------------------------------------------------------------------------
# Register all tools
# ---------------------------------------------------------------------------

import companies_house, charity, disqualified, land_registry, gazette, hmrc_vat, sanctions, search_fetch
import prompts as prompts_module
from fastmcp.server.transforms import PromptsAsTools

companies_house.register_tools(mcp)
charity.register_tools(mcp)
disqualified.register_tools(mcp)
land_registry.register_tools(mcp)
gazette.register_tools(mcp)
hmrc_vat.register_tools(mcp)
sanctions.register_tools(mcp)
search_fetch.register_tools(mcp)

companies_house.register_resources(mcp)
charity.register_resources(mcp)
disqualified.register_resources(mcp)
gazette.register_resources(mcp)

prompts_module.register_prompts(mcp)
mcp.add_transform(PromptsAsTools(mcp))

# ResourcesAsTools removed — causes ChatGPT to route through read_resource (double-encoded)
# instead of the named companion tools. Re-add with: mcp.add_transform(ResourcesAsTools(mcp))
# and restore the import: from fastmcp.server.transforms import ResourcesAsTools

# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

class _HttpGuard:
    """Return a held-open SSE stream for GET /mcp; 405 for DELETE /mcp.

    claude.ai probes GET /mcp to establish an SSE stream before sending MCP
    protocol messages via POST. With stateless_http=True FastMCP only registers
    POST routes, so GET returns 405 — claude.ai treats this as a connection
    failure even though POST works fine.

    Fix: intercept GET /mcp and return 200 text/event-stream held open until
    the client disconnects. FastMCP never sees the GET; stateless semantics
    are preserved. DELETE is rejected (405) — stateless servers have no sessions.
    """

    def __init__(self, app, mcp_path: bytes = b"/mcp"):
        self.app = app
        self._mcp_path = mcp_path.rstrip(b"/")

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            path = scope.get("path", "").rstrip("/").encode()
            method = scope.get("method", "").upper().encode()
            if path == self._mcp_path:
                if method == b"GET":
                    await send({"type": "http.response.start", "status": 200, "headers": [
                        (b"content-type", b"text/event-stream"),
                        (b"cache-control", b"no-cache"),
                        (b"connection", b"keep-alive"),
                    ]})
                    await send({"type": "http.response.body", "body": b"", "more_body": True})
                    while True:
                        event = await receive()
                        if event["type"] == "http.disconnect":
                            break
                    return
                if method == b"DELETE":
                    from starlette.responses import Response as StarletteResponse
                    await StarletteResponse("Method Not Allowed", status_code=405, headers={"Allow": "POST"})(scope, receive, send)
                    return
        await self.app(scope, receive, send)


class _AcceptNormalizer:
    """Stamp Accept to the MCP-spec value on /mcp only, so json_response=True never 406s.

    Anthropic sends mixed Accept headers per request type (application/json for
    initialize, text/event-stream for tools/list). Only stamp the MCP endpoint —
    leave /metrics, /health, /.well-known/* with their original Accept headers.
    """
    def __init__(self, app, mcp_path: bytes = b"/mcp"):
        self.app = app
        self._mcp_path = mcp_path.rstrip(b"/")

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and scope.get("path", "").rstrip("/").encode() == self._mcp_path:
            headers = [
                (b"accept", b"application/json, text/event-stream")
                if name.lower() == b"accept"
                else (name, value)
                for name, value in scope.get("headers", [])
            ]
            scope = {**scope, "headers": headers}
        await self.app(scope, receive, send)


def main() -> None:
    """Run the MCP server with streamable HTTP transport."""
    import uvicorn
    from fastmcp.server.http import create_streamable_http_app

    print(
        f"[uk_due_diligence_mcp] Starting on port {PORT} (streamable HTTP, stateless)",
        file=sys.stderr,
    )
    app = create_streamable_http_app(
        mcp,
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
    )
    # Warm the sanctions list index on boot (fire-and-forget; lazy-loads otherwise).
    if hasattr(app, "add_event_handler"):
        app.add_event_handler("startup", sanctions.warm_cache)
    uvicorn.run(
        _HttpGuard(_AcceptNormalizer(app)),
        host="0.0.0.0",
        port=PORT,
        forwarded_allow_ips="*",
        proxy_headers=True,
        lifespan="on",
        log_level="info",
    )


if __name__ == "__main__":
    main()
