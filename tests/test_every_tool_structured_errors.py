"""Every registered tool reports upstream failure as a structured fleet error.

With mask_error_details=True, an exception that is not a structured ToolError
reaches the client as a bare "Error calling tool" and is counted as
error_category=unknown whatever the real cause (#37). This test forces two
upstream failures under every tool, through the real call path:

(a) every upstream request times out   -> error_category "transient"
(b) every upstream answers 200 + HTML   -> a structured payload, not "unknown"

The upstream layer is patched at its narrowest points: httpx.AsyncClient.send
(Companies House, Charity Commission, HMLR, sanctions downloads) and
http_client._urllib_get (the Gazette, which goes through urllib).

A third case covers arguments the input schema rejects (#41): they must come
back as a structured "validation" error, not pydantic's plain text.

A newly registered tool fails test_every_tool_has_cases until it is given
arguments below (valid and schema-rejected), so coverage cannot silently lapse.
"""

from __future__ import annotations

import asyncio
import types
from collections import deque

import httpx
import pytest
import pytest_asyncio
from fastmcp import Client
from fastmcp.exceptions import ToolError
from mcpfleet_obs import parse_error_payload

import http_client
import sanctions
from server import mcp

DOC_URL = "https://document-api.company-information.service.gov.uk/document/abc123"

# Minimal valid arguments per tool. Several entries per tool where the tool
# routes to different upstream paths (fetch dispatches by ID prefix).
CASES: dict[str, list[dict]] = {
    "company_search": [{"query": "tesco"}, {"query": "tesco", "company_status": "active"}],
    "company_profile": [{"company_number": "00445790"}],
    "company_officers": [{"company_number": "00445790"}],
    "company_psc": [{"company_number": "00445790"}],
    "officer_appointments": [{"officer_id": "abc123"}],
    "company_charges": [{"company_number": "00445790"}],
    "company_filing_history": [{"company_number": "00445790"}],
    "company_filing_document": [{"document_metadata_url": DOC_URL}],
    "charity_search": [{"query": "oxfam"}],
    "charity_profile": [{"charity_number": "202918"}],
    "disqualified_search": [{"query": "john smith"}],
    "disqualified_profile": [{"officer_id": "abc123"}],
    "land_title_search": [{"address_or_postcode": "NG1 1AB"}],
    "gazette_notice": [{"notice_id": "4512345"}],
    "gazette_insolvency": [{"name": "carillion"}],
    "sanctions_screen": [{"name": "Rosneft"}],
    "search": [{"query": "carillion"}],
    "fetch": [
        {"id": "company:00445790"},
        {"id": "charity:202918"},
        {"id": "disqualification:abc123"},
        {"id": "notice:4512345"},
    ],
}

# Arguments each tool's input schema rejects (#41). The first two entries are
# the prod repros from the issue; every tool has at least one.
INVALID_CASES: dict[str, list[dict]] = {
    "company_search": [{"query": "x"}],
    "company_profile": [{"company_number": "00445790000000"}],
    "company_officers": [{"company_number": "00445790", "include_resigned": "maybe"}],
    "company_psc": [{"company_number": ""}],
    "officer_appointments": [{"officer_id": ""}],
    "company_charges": [{"company_number": ["00445790"]}],
    "company_filing_history": [{"company_number": "00445790", "items_per_page": "lots"}],
    "company_filing_document": [{}],
    "charity_search": [{"query": "oxfam", "limit": "ten"}],
    "charity_profile": [{"charity_number": ""}],
    "disqualified_search": [{"query": ["john smith"]}],
    "disqualified_profile": [{"officer_id": ""}],
    "land_title_search": [{"address_or_postcode": "NG1"}],
    "gazette_notice": [{"notice_id": ""}],
    "gazette_insolvency": [{"name": "x"}],
    "sanctions_screen": [{"name": "Rosneft", "entity_type": "company"}],
    "search": [{"query": "x"}],
    "fetch": [{"id": "c"}],
}

# Tools that make no upstream call, so there is nothing to force. Empty today:
# every DD tool reads a register.
NO_UPSTREAM: dict[str, str] = {}

HTML = b"<!DOCTYPE html><html><body>Service temporarily unavailable</body></html>"


async def _no_sleep(_seconds: float) -> None:
    return None


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setenv("CH_API_KEY", "test-key")
    monkeypatch.setenv("CHARITY_API_KEY", "test-key")
    # Skip retry backoff and Gazette pacing waits; keep real Lock/to_thread.
    monkeypatch.setattr(http_client, "asyncio", types.SimpleNamespace(
        sleep=_no_sleep, to_thread=asyncio.to_thread, Lock=asyncio.Lock,
    ))
    monkeypatch.setattr(http_client, "_gazette_sent", deque())
    monkeypatch.setattr(http_client, "_gazette_blocked_until", 0.0)
    # Cold sanctions cache, so the forced failure reaches the list downloads.
    monkeypatch.setattr(sanctions, "_INDEX", None)
    monkeypatch.setattr(sanctions, "_LIST_AS_AT", {})
    monkeypatch.setattr(sanctions, "_LIST_LOADED_MONO", {})
    monkeypatch.setattr(sanctions, "_LIST_FAILED_MONO", {})


@pytest_asyncio.fixture
async def mcp_client():
    async with Client(mcp) as c:
        yield c


def _force(monkeypatch, mode: str) -> None:
    async def send(self, request: httpx.Request, **kwargs) -> httpx.Response:
        if mode == "timeout":
            raise httpx.ConnectTimeout("forced connect timeout", request=request)
        return httpx.Response(
            200, content=HTML, headers={"content-type": "text/html"}, request=request,
        )

    def urllib_get(url, params, headers=None) -> httpx.Response:
        request = httpx.Request("GET", url)
        if mode == "timeout":
            raise httpx.ReadTimeout("forced read timeout", request=request)
        return httpx.Response(
            200, content=HTML, headers={"content-type": "text/html"}, request=request,
        )

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    monkeypatch.setattr(http_client, "_urllib_get", urllib_get)


def _params(cases: dict[str, list[dict]] = CASES):
    for tool, arg_sets in cases.items():
        for i, args in enumerate(arg_sets):
            yield pytest.param(tool, args, id=f"{tool}-{i}")


async def _error_payload(client: Client, tool: str, args: dict):
    with pytest.raises(ToolError) as exc_info:
        await client.call_tool(tool, args)
    payload = parse_error_payload(str(exc_info.value))
    assert payload is not None, (
        f"{tool}{args} failed unstructured (masked to the client, counted as "
        f"unknown): {exc_info.value}"
    )
    return payload


@pytest.mark.asyncio
async def test_every_tool_has_cases(mcp_client):
    registered = {t.name for t in await mcp_client.list_tools()}
    missing = registered - set(CASES) - set(NO_UPSTREAM)
    stale = (set(CASES) | set(NO_UPSTREAM)) - registered
    assert not missing, f"add forced-failure cases for: {sorted(missing)}"
    assert not stale, f"remove cases for tools no longer registered: {sorted(stale)}"
    missing_invalid = registered - set(INVALID_CASES)
    stale_invalid = set(INVALID_CASES) - registered
    assert not missing_invalid, f"add schema-rejected cases for: {sorted(missing_invalid)}"
    assert not stale_invalid, (
        f"remove schema-rejected cases for tools no longer registered: {sorted(stale_invalid)}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("tool, args", list(_params()))
async def test_upstream_timeout_is_transient(monkeypatch, mcp_client, tool, args):
    _force(monkeypatch, "timeout")
    payload = await _error_payload(mcp_client, tool, args)
    assert payload.error_category == "transient", payload
    assert payload.is_retryable is True


@pytest.mark.asyncio
@pytest.mark.parametrize("tool, args", list(_params()))
async def test_upstream_html_body_is_structured_not_unknown(monkeypatch, mcp_client, tool, args):
    _force(monkeypatch, "html")
    payload = await _error_payload(mcp_client, tool, args)
    assert payload.error_category != "unknown", payload


@pytest.mark.asyncio
@pytest.mark.parametrize("tool, args", list(_params(INVALID_CASES)))
async def test_schema_rejected_arguments_are_validation(monkeypatch, mcp_client, tool, args):
    # Upstream forced to time out: arguments that slipped past the schema would
    # come back "transient", not "validation", and fail here.
    _force(monkeypatch, "timeout")
    payload = await _error_payload(mcp_client, tool, args)
    assert payload.error_category == "validation", payload
    assert payload.is_retryable is False
    assert payload.attempted == tool
    assert payload.description.startswith("Arguments rejected by the tool's input schema: ")
    assert f"call[{tool}]" in payload.description
