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

Transport: Streamable HTTP, stateless, JSON responses, deployed on Fly.io.

Tools (13 — all clients including ChatGPT):
    company_search, company_profile, company_officers, company_psc
    disqualified_search, disqualified_profile
    charity_search, charity_profile
    gazette_insolvency, gazette_notice
    land_title_search, vat_validate
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
    middleware=[PrometheusMiddleware()],
    instructions=(
        "UK due diligence server covering 5 official government registers: "
        "Companies House, Charity Commission, HMLR Land Registry, The Gazette, and HMRC VAT. "
        "Use company_search, charity_search, disqualified_search, gazette_insolvency, "
        "vat_validate, and land_title_search to find entities and notices; "
        "use the companion tools (company_profile, company_officers, company_psc, "
        "charity_profile, disqualified_profile, gazette_notice) to fetch full records. "
        "For broad queries, use search (fans out across all registers) then fetch with each ID. "
        "IMPORTANT: disqualified_search takes a person's name — not a company name. "
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
    return JSONResponse({"serverInfo": {"name": "uk-due-diligence-mcp", "version": "1.0.6"}})


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

import companies_house, charity, disqualified, land_registry, gazette, hmrc_vat, search_fetch
import prompts as prompts_module
from fastmcp.server.transforms import PromptsAsTools

companies_house.register_tools(mcp)
charity.register_tools(mcp)
disqualified.register_tools(mcp)
land_registry.register_tools(mcp)
gazette.register_tools(mcp)
hmrc_vat.register_tools(mcp)
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

def main() -> None:
    """Run the MCP server with streamable HTTP transport."""
    print(
        f"[uk_due_diligence_mcp] Starting on port {PORT} (streamable HTTP, stateless)",
        file=sys.stderr,
    )
    mcp.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=PORT,
        path="/mcp",
        stateless_http=True,
        json_response=True,
    )


if __name__ == "__main__":
    main()
