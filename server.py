"""
server.py — uk_biz_intel_mcp

Unified UK Business Intelligence MCP server.
Five public registers. One cross-registry reasoning layer.

Data sources:
  - Companies House REST API (CH_API_KEY)
  - Charity Commission API (CHARITY_API_KEY)
  - HMLR Land Registry Linked Data (unauthenticated)
  - The Gazette Linked Data API (unauthenticated)
  - HMRC VAT Check API (unauthenticated)

Transport: Streamable HTTP, stateless, deployed on Fly.io.
Auth: Bearer token (MCP_SERVER_KEY env var).

Tools:
  Layer 1 — Raw registry tools (9):
    company_search, company_profile, company_officers, company_psc
    charity_search, charity_profile
    land_title_search
    gazette_insolvency
    vat_validate

  Layer 2 — Composite (1):
    entity_due_diligence
"""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv
from fastmcp import FastMCP

# Load .env for local development
load_dotenv()

# ---------------------------------------------------------------------------
# Server configuration
# ---------------------------------------------------------------------------

def _require_env(key: str, required: bool = True) -> str | None:
    val = os.environ.get(key)
    if required and not val:
        print(f"[uk_biz_intel_mcp] WARNING: {key} is not set.", file=sys.stderr)
    return val


MCP_SERVER_KEY = _require_env("MCP_SERVER_KEY", required=False)
PORT = int(os.environ.get("PORT", "8080"))

# ---------------------------------------------------------------------------
# Initialise FastMCP
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name="uk_biz_intel_mcp",
    instructions=(
        "UK Business Intelligence MCP server. "
        "Provides cross-registry due diligence across Companies House, "
        "Charity Commission, HMLR Land Registry, The Gazette, and HMRC VAT. "
        "Start with entity_due_diligence for a full risk summary, or use "
        "individual registry tools for targeted lookups."
    ),
)

# ---------------------------------------------------------------------------
# Register all tools
# ---------------------------------------------------------------------------

import companies_house, charity, land_registry, gazette, hmrc_vat, composite

companies_house.register_tools(mcp)
charity.register_tools(mcp)
land_registry.register_tools(mcp)
gazette.register_tools(mcp)
hmrc_vat.register_tools(mcp)
composite.register_tools(mcp)

# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    """Run the MCP server with streamable HTTP transport."""
    print(
        f"[uk_biz_intel_mcp] Starting on port {PORT} (streamable HTTP, stateless)",
        file=sys.stderr,
    )
    mcp.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=PORT,
        path="/mcp",
        stateless_http=True,
    )


if __name__ == "__main__":
    main()
