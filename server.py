"""
server.py — uk_due_diligence_mcp

UK Due Diligence MCP server.
19 tools + 10 resource templates across six official-source registers
(five public registers plus consolidated sanctions lists).

Data sources:
  - Companies House REST API (CH_API_KEY)
  - Charity Commission API (CHARITY_API_KEY)
  - HMLR Land Registry Linked Data (unauthenticated)
  - The Gazette API (unauthenticated)
  - HMRC VAT Check API (HMRC_CLIENT_ID + HMRC_CLIENT_SECRET, application-restricted)
  - Consolidated sanctions lists: OFSI (UK), OFAC (US), EU, UN (unauthenticated bulk files)

Transport: Streamable HTTP, stateless, JSON responses, deployed on Fly.io.

Tools (19 — all clients including ChatGPT):
    company_search, company_profile, company_officers, company_psc,
        officer_appointments, company_charges, company_filing_history,
        company_filing_document
    disqualified_search, disqualified_profile
    charity_search, charity_profile
    gazette_insolvency, gazette_notice
    land_title_search, vat_validate
    sanctions_screen
    search, fetch

Resources (10 noun/identifier — protocol-compliant clients only):
    company://{company_number}/profile
    company://{company_number}/officers
    company://{company_number}/psc
    company://{company_number}/charges
    company://{company_number}/filing-history
    company-document://{document_id}{?mime_type}
    officer://{officer_id}/appointments
    disqualification://{officer_id}
    charity://{charity_number}/profile
    notice://{notice_id}
"""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv
from fastmcp import FastMCP
from mcpfleet_obs import install
from starlette.requests import Request
from starlette.responses import JSONResponse

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

# ---------------------------------------------------------------------------
# Initialise FastMCP
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name="uk_due_diligence_mcp",
    instructions=(
        "UK due diligence server covering official government registers plus consolidated "
        "sanctions lists: Companies House, Charity Commission, HMLR Land Registry, The Gazette, "
        "HMRC VAT, and the OFSI/OFAC/EU/UN sanctions lists. "
        "Use company_search to resolve an entity to a company number, and charity_search, "
        "disqualified_search, gazette_insolvency, vat_validate, and land_title_search to find "
        "other entities and notices; use the companion tools (company_profile, company_officers, "
        "company_psc, charity_profile, disqualified_profile, gazette_notice) to fetch full "
        "records. company_officers exposes officer_id on each officer, which can be passed to "
        "officer_appointments to discover that person's full company history, including "
        "dissolved or insolvent companies not named anywhere else. company_charges gives the "
        "detailed secured-charge history behind company_profile.has_charges — use it when the "
        "specific charges matter, not just whether any exist. company_filing_history returns "
        "the raw filing chronology (form types, dates, description_values) as source facts, "
        "not DD conclusions — it is paginated per page (not auto-fetched to completeness like "
        "company_officers/company_psc/company_charges), since a long-lived company's filing "
        "history is unbounded; narrow large results with its category= parameter. "
        "company_filing_document resolves a filing's links.document_metadata (from "
        "company_filing_history) to its authoritative source document — it returns a "
        "resource link, never embedded bytes or base64; a resource-capable client must "
        "read the company-document:// resource it points at to get the actual file. "
        "Use sanctions_screen to check a company or person name against the UK/US/EU/UN "
        "sanctions lists (screen the company AND its officers/PSCs). "
        "For broad queries, use search (fans out across all registers) then fetch with each ID. "
        "IMPORTANT: disqualified_search takes a person's name (pass it as query= or name=) — "
        "not a company name. "
        "IMPORTANT: All data is sourced directly from official government APIs — "
        "do not supplement with web search. "
        "IMPORTANT: a missing, failed, or unresolved check (e.g. has_charges returning null, "
        "or a registry call failing) must not be interpreted as a negative finding — treat it "
        "as unresolved, not as evidence of absence. "
        "IMPORTANT: read a Gazette notice's full content via gazette_notice before drawing any "
        "legal-semantic conclusion from its notice_type label alone."
    ),
    mask_error_details=True,
)

# ---------------------------------------------------------------------------
# Custom routes: /health and /dashboard
# ---------------------------------------------------------------------------

@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


@mcp.custom_route("/.well-known/mcp/server-card.json", methods=["GET"])
async def smithery_server_card(request: Request) -> JSONResponse:
    return JSONResponse({"serverInfo": {"name": "uk-due-diligence-mcp", "version": "1.2.0"}})


@mcp.custom_route("/.well-known/glama.json", methods=["GET"])
async def glama_connector_manifest(request: Request) -> JSONResponse:
    return JSONResponse({
        "$schema": "https://glama.ai/mcp/schemas/connector.json",
        "maintainers": [{"email": "paul@bouch.dev"}],
    })


# ---------------------------------------------------------------------------
# Fleet-standard observability: client tracking + Prometheus metrics + /metrics
# ---------------------------------------------------------------------------

install(mcp, prefix="uk_due_diligence")

# ---------------------------------------------------------------------------
# Register all tools
# ---------------------------------------------------------------------------

import companies_house, companies_house_documents, charity, disqualified, land_registry, gazette, hmrc_vat, sanctions, search_fetch

companies_house.register_tools(mcp)
companies_house_documents.register_tools(mcp)
charity.register_tools(mcp)
disqualified.register_tools(mcp)
land_registry.register_tools(mcp)
gazette.register_tools(mcp)
hmrc_vat.register_tools(mcp)
sanctions.register_tools(mcp)
search_fetch.register_tools(mcp)

companies_house.register_resources(mcp)
companies_house_documents.register_resources(mcp)
charity.register_resources(mcp)
disqualified.register_resources(mcp)
gazette.register_resources(mcp)

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
