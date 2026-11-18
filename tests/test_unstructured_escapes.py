"""Regression tests for #37: residual unstructured error escapes.

Each case is an upstream failure that used to escape as a bare exception — a
masked "Error calling tool" to the client and error_category=unknown in metrics
— instead of a structured fleet error carrying the real category.
"""

from __future__ import annotations

import http.client
import ssl
import urllib.request

import httpx
import pytest
from fastmcp.exceptions import ToolError
from mcpfleet_obs import parse_error_payload

import companies_house
import companies_house_documents
import http_client

CH_BASE = "https://api.company-information.service.gov.uk"


def _category(exc_info) -> str | None:
    payload = parse_error_payload(str(exc_info.value))
    return payload.error_category if payload else None


def _ch(monkeypatch, module, handler) -> None:
    def make() -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=CH_BASE, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(module, "companies_house_client", make)


# --- _json_or_error ----------------------------------------------------------

def test_json_or_error_parses_json():
    resp = httpx.Response(200, json={"a": 1}, request=httpx.Request("GET", "https://x/y"))
    assert http_client._json_or_error(resp) == {"a": 1}


def test_json_or_error_turns_a_non_json_body_into_a_transient_error():
    resp = httpx.Response(
        200, content=b"<html>maintenance</html>", request=httpx.Request("GET", "https://x/y"),
    )
    with pytest.raises(ToolError) as exc_info:
        http_client._json_or_error(resp)

    payload = parse_error_payload(str(exc_info.value))
    assert payload is not None
    assert payload.error_category == "transient"
    assert payload.is_retryable is True
    assert payload.attempted == "GET https://x/y"


@pytest.mark.asyncio
async def test_html_200_from_companies_house_is_structured(monkeypatch):
    _ch(monkeypatch, companies_house, lambda r: httpx.Response(200, content=b"<html></html>"))

    with pytest.raises(ToolError) as exc_info:
        await companies_house._confirm_company_exists("00000001")
    assert _category(exc_info) == "transient"


# --- has_charges degrades to unknown on a parse failure ----------------------

@pytest.mark.asyncio
async def test_unparseable_charges_leave_has_charges_unknown(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/charges"):
            return httpx.Response(200, content=b"<html>oops</html>")
        return httpx.Response(200, json={"company_number": "00000001", "company_name": "X"})

    _ch(monkeypatch, companies_house, handler)

    profile = await companies_house._fetch_company_profile("00000001")
    assert profile.company_name == "X"
    assert profile.has_charges is None


# --- document content: transport errors on the raw redirect request ----------

@pytest.mark.asyncio
async def test_document_redirect_request_transport_error_is_structured(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    _ch(monkeypatch, companies_house_documents, handler)

    with pytest.raises(ToolError) as exc_info:
        await companies_house_documents._fetch_document_content_bytes("abc123", "application/pdf", None)
    assert _category(exc_info) == "transient"


# --- _urllib_get normalises every stdlib transport failure -------------------

@pytest.mark.parametrize("exc", [
    http.client.RemoteDisconnected("closed"),
    http.client.IncompleteRead(b"partial"),
    ssl.SSLError("bad handshake"),
    ConnectionResetError("reset"),
], ids=["remote-disconnected", "incomplete-read", "ssl", "connection-reset"])
def test_urllib_get_raises_httpx_transport_errors(monkeypatch, exc):
    def boom(*args, **kwargs):
        raise exc

    monkeypatch.setattr(urllib.request, "urlopen", boom)

    with pytest.raises(httpx.TransportError):
        http_client._urllib_get("https://www.thegazette.co.uk/x", None)
