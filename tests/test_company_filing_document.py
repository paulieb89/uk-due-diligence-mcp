"""Tests for the company_filing_document primitive and the
company-document:// resource.

Covers: strict document_metadata_url validation (host/scheme/userinfo/port/
query/fragment/path — rejected BEFORE any HTTP request, confirmed via a
request-count assertion), single- vs multi-representation MIME resolution,
metadata 404 -> not_found, the credential-safe redirect sequence (CH auth
attached only to document-api requests, never to the S3 redirect target,
never auto-followed by the authenticated client), content-type/length
provenance verification, and that the tool's response carries a
ResourceLink — never embedded bytes or base64.

Live-probed shape (see companies_house_documents.py module docstring and
PR description): GET /document/{id} -> JSON metadata with a `resources`
dict keyed by MIME type; GET /document/{id}/content with a matching
Accept header -> 302 to a pre-signed *.amazonaws.com URL (confirmed
X-Amz-Expires=60); the redirect target requires no further CH auth.
"""

from __future__ import annotations

import httpx
import pytest
import pytest_asyncio
from fastmcp import Client
from fastmcp.exceptions import ToolError
from mcp.shared.exceptions import McpError

import companies_house_documents as docs
from mcpfleet_obs import parse_error_payload
from server import mcp

VALID_METADATA_URL = (
    "https://document-api.company-information.service.gov.uk/document/EV-72TL_abc-123"
)

_ORIGINAL_ASYNC_CLIENT = httpx.AsyncClient


def _mock_ch_client_factory(handler):
    """Mocks companies_house_client() — the authenticated CH-side client.

    Always constructs via the captured _ORIGINAL_ASYNC_CLIENT, never the
    (possibly monkeypatched-for-the-anon-leg) module-level httpx.AsyncClient
    name — both the CH and anon clients are built via that same shared
    attribute, so patching it for one leg would silently redirect the other.
    """

    def factory() -> httpx.AsyncClient:
        return _ORIGINAL_ASYNC_CLIENT(
            base_url="https://document-api.company-information.service.gov.uk",
            auth=("fake-ch-api-key", ""),
            transport=httpx.MockTransport(handler),
        )

    return factory


def _mock_anon_client_factory(monkeypatch, handler):
    """Mocks the fresh, unauthenticated httpx.AsyncClient used for the S3 leg."""

    def fake_ctor(*args, **kwargs):
        return _ORIGINAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(docs.httpx, "AsyncClient", fake_ctor)


def _metadata_response(resources=None, **overrides):
    body = {
        "company_number": "06333469",
        "barcode": "XB5393J7",
        "category": "mortgages",
        "pages": 1,
        "filename": "06333469_mr04_2022-05-30",
        "created_at": "2022-05-30T15:16:32.414475929Z",
        "resources": resources if resources is not None else {"application/pdf": {"content_length": 8}},
    }
    body.update(overrides)
    return httpx.Response(200, json=body)


def _signed_url(mime="application/pdf"):
    return f"https://s3.eu-west-2.amazonaws.com/bucket/docs/x/{mime.replace('/', '-')}?X-Amz-Expires=60"


# ---------------------------------------------------------------------------
# document_metadata_url validation — must reject BEFORE any HTTP request
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://document-api.company-information.service.gov.uk/document/abc",  # wrong scheme
        "https://evil.example.com/document/abc",  # wrong host
        "https://user:pass@document-api.company-information.service.gov.uk/document/abc",  # userinfo
        "https://document-api.company-information.service.gov.uk:8443/document/abc",  # port
        "https://document-api.company-information.service.gov.uk/document/abc?x=1",  # query
        "https://document-api.company-information.service.gov.uk/document/abc#frag",  # fragment
        "https://document-api.company-information.service.gov.uk/document/",  # empty id
        "https://document-api.company-information.service.gov.uk/document/../../etc/passwd",  # traversal
        "https://document-api.company-information.service.gov.uk/document/abc/extra",  # extra path segment
        "https://document-api.company-information.service.gov.uk/other/abc",  # wrong path prefix
    ],
)
def test_rejects_malformed_or_non_ch_metadata_url_before_any_request(bad_url):
    with pytest.raises(ToolError) as exc_info:
        docs._parse_document_id_from_metadata_url(bad_url, attempted="test")

    payload = parse_error_payload(str(exc_info.value))
    assert payload is not None
    assert payload.error_category == "validation"
    assert payload.is_retryable is False


def test_accepts_valid_metadata_url_and_extracts_id():
    document_id = docs._parse_document_id_from_metadata_url(VALID_METADATA_URL, attempted="test")
    assert document_id == "EV-72TL_abc-123"


def test_malformed_port_is_structured_validation_not_a_raw_exception():
    """urlsplit() itself never raises for a bad port — its .port property
    is lazily validated and raises ValueError on access (confirmed: an
    out-of-range port like :99999999 raises 'Port out of range 0-65535').
    That must surface as our Fleet validation error, not an unhandled
    Python exception."""
    bad_url = "https://document-api.company-information.service.gov.uk:99999999/document/abc"

    with pytest.raises(ToolError) as exc_info:
        docs._parse_document_id_from_metadata_url(bad_url, attempted="test")

    payload = parse_error_payload(str(exc_info.value))
    assert payload is not None, f"error message did not parse as a FleetErrorPayload: {exc_info.value}"
    assert payload.error_category == "validation"
    assert payload.is_retryable is False


# ---------------------------------------------------------------------------
# document_id validation — must apply consistently whether it arrives via a
# validated document_metadata_url or directly through a resource read
# ---------------------------------------------------------------------------


def test_validate_document_id_accepts_url_safe_charset():
    docs._validate_document_id("EV-72TL_abc-123", attempted="test")  # does not raise


@pytest.mark.parametrize("bad_id", ["", "../../etc/passwd", "abc/def", "abc?x=1", "abc#frag", "abc def"])
def test_validate_document_id_rejects_bad_charset(bad_id):
    with pytest.raises(ToolError) as exc_info:
        docs._validate_document_id(bad_id, attempted="test")
    payload = parse_error_payload(str(exc_info.value))
    assert payload.error_category == "validation"
    assert payload.is_retryable is False


@pytest.mark.asyncio
async def test_direct_resource_read_rejects_malformed_document_id_before_any_request(monkeypatch):
    """A company-document:// read bypasses company_filing_document's URL
    validation entirely — document_id needs its own independent check
    before it's used to build a request URL, confirmed here via a
    request-count assertion (same discipline as the tool's URL check)."""

    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, json={})

    monkeypatch.setattr(docs, "companies_house_client", _mock_ch_client_factory(handler))

    async with Client(mcp) as c:
        with pytest.raises(McpError) as exc_info:
            await c.read_resource("company-document://abc%2F..%2F..%2Fetc")

    payload = parse_error_payload(str(exc_info.value))
    assert payload.error_category == "validation"
    assert request_count == 0


@pytest.mark.asyncio
async def test_non_ch_host_rejected_before_any_authenticated_request(monkeypatch):
    """The credential-safety invariant: a hostile document_metadata_url must
    never reach the point where CH_API_KEY could be attached to a request."""

    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, json={})

    monkeypatch.setattr(docs, "companies_house_client", _mock_ch_client_factory(handler))

    with pytest.raises(ToolError) as exc_info:
        await docs._build_filing_document_result("https://evil.example.com/document/abc")

    payload = parse_error_payload(str(exc_info.value))
    assert payload.error_category == "validation"
    assert request_count == 0


# ---------------------------------------------------------------------------
# MIME resolution
# ---------------------------------------------------------------------------


def test_resolve_mime_type_auto_selects_single_representation():
    resolved = docs._resolve_mime_type({"application/pdf": {"content_length": 5}}, None, attempted="t")
    assert resolved == "application/pdf"


def test_resolve_mime_type_requires_explicit_choice_when_ambiguous():
    with pytest.raises(ToolError) as exc_info:
        docs._resolve_mime_type(
            {"application/pdf": {"content_length": 5}, "image/tiff": {"content_length": 9}}, None, attempted="t"
        )
    payload = parse_error_payload(str(exc_info.value))
    assert payload.error_category == "validation"
    assert "application/pdf" in payload.description
    assert "image/tiff" in payload.description


def test_resolve_mime_type_honors_valid_explicit_choice():
    resolved = docs._resolve_mime_type(
        {"application/pdf": {"content_length": 5}, "image/tiff": {"content_length": 9}},
        "image/tiff",
        attempted="t",
    )
    assert resolved == "image/tiff"


def test_resolve_mime_type_rejects_unavailable_explicit_choice():
    with pytest.raises(ToolError) as exc_info:
        docs._resolve_mime_type({"application/pdf": {"content_length": 5}}, "image/tiff", attempted="t")
    payload = parse_error_payload(str(exc_info.value))
    assert payload.error_category == "validation"
    assert payload.is_retryable is False


# ---------------------------------------------------------------------------
# Metadata fetch / not_found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metadata_404_is_not_found_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "Invalid document ID", "type": "ch:service"})

    monkeypatch.setattr(docs, "companies_house_client", _mock_ch_client_factory(handler))

    with pytest.raises(ToolError) as exc_info:
        await docs._build_filing_document_result(VALID_METADATA_URL)

    payload = parse_error_payload(str(exc_info.value))
    assert payload.error_category == "not_found"
    assert payload.is_retryable is False


# ---------------------------------------------------------------------------
# Tool result shape: ResourceLink, never embedded bytes/base64
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_returns_resource_link_not_embedded_bytes(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return _metadata_response()

    monkeypatch.setattr(docs, "companies_house_client", _mock_ch_client_factory(handler))
    result = await docs._build_filing_document_result(VALID_METADATA_URL)

    resource_links = [c for c in result.content if c.type == "resource_link"]
    assert len(resource_links) == 1
    link = resource_links[0]
    assert link.uri.scheme == "company-document"
    assert link.mimeType == "application/pdf"
    assert link.size == 8

    # No content block anywhere carries raw bytes or a base64 blob.
    for block in result.content:
        assert block.type in ("text", "resource_link")
        assert not hasattr(block, "blob")

    assert result.structured_content["mime_type"] == "application/pdf"
    assert result.structured_content["resource_uri"] == str(link.uri)
    assert result.structured_content["document_id"] == "EV-72TL_abc-123"


@pytest.mark.asyncio
async def test_resource_uri_always_carries_resolved_mime_type_even_when_unambiguous(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return _metadata_response()

    monkeypatch.setattr(docs, "companies_house_client", _mock_ch_client_factory(handler))
    result = await docs._build_filing_document_result(VALID_METADATA_URL)

    assert "mime_type=application%2Fpdf" in result.structured_content["resource_uri"]


@pytest.mark.asyncio
async def test_large_document_gets_advisory_note_without_blocking(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return _metadata_response(resources={"application/pdf": {"content_length": 50_000_000}})

    monkeypatch.setattr(docs, "companies_house_client", _mock_ch_client_factory(handler))
    result = await docs._build_filing_document_result(VALID_METADATA_URL)

    assert result.structured_content["note"] is not None
    assert result.structured_content["content_length"] == 50_000_000


@pytest.mark.asyncio
async def test_small_document_gets_no_advisory_note(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return _metadata_response()

    monkeypatch.setattr(docs, "companies_house_client", _mock_ch_client_factory(handler))
    result = await docs._build_filing_document_result(VALID_METADATA_URL)

    assert result.structured_content["note"] is None


# ---------------------------------------------------------------------------
# Credential-safe content fetch: redirect handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_content_fetch_sends_ch_auth_to_document_api_only(monkeypatch):
    ch_requests = []

    def ch_handler(request: httpx.Request) -> httpx.Response:
        ch_requests.append(request)
        if request.url.path.endswith("/content"):
            assert request.headers.get("accept") == "application/pdf"
            return httpx.Response(302, headers={"location": _signed_url()})
        return httpx.Response(200)

    anon_requests = []

    def anon_handler(request: httpx.Request) -> httpx.Response:
        anon_requests.append(request)
        return httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF-fake")

    monkeypatch.setattr(docs, "companies_house_client", _mock_ch_client_factory(ch_handler))
    _mock_anon_client_factory(monkeypatch, anon_handler)

    content = await docs._fetch_document_content_bytes("abc123", "application/pdf", len(b"%PDF-fake"))

    assert content == b"%PDF-fake"
    assert all(r.headers.get("authorization") for r in ch_requests)
    assert len(anon_requests) == 1


@pytest.mark.asyncio
async def test_authenticated_client_does_not_auto_follow_cross_host_redirect(monkeypatch):
    """The authenticated client must receive the raw 302, not a followed
    200 — otherwise CH_API_KEY's transport could theoretically leak to
    the redirect target regardless of httpx's own cross-origin stripping."""

    ch_call_count = 0

    def ch_handler(request: httpx.Request) -> httpx.Response:
        nonlocal ch_call_count
        ch_call_count += 1
        # If the authenticated client ever followed the redirect itself,
        # this handler (bound only to the CH mock transport) would be
        # asked for the S3 URL too — assert it never is.
        assert request.url.host == "document-api.company-information.service.gov.uk"
        return httpx.Response(302, headers={"location": _signed_url()})

    def anon_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"x")

    monkeypatch.setattr(docs, "companies_house_client", _mock_ch_client_factory(ch_handler))
    _mock_anon_client_factory(monkeypatch, anon_handler)

    await docs._fetch_document_content_bytes("abc123", "application/pdf", 1)
    assert ch_call_count == 1


@pytest.mark.asyncio
async def test_s3_fetch_is_unauthenticated(monkeypatch):
    def ch_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": _signed_url()})

    seen_auth_header = "sentinel-not-replaced"

    def anon_handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_auth_header
        seen_auth_header = request.headers.get("authorization")
        return httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"x")

    monkeypatch.setattr(docs, "companies_house_client", _mock_ch_client_factory(ch_handler))
    _mock_anon_client_factory(monkeypatch, anon_handler)

    await docs._fetch_document_content_bytes("abc123", "application/pdf", 1)
    assert seen_auth_header is None


@pytest.mark.asyncio
async def test_redirect_to_non_amazonaws_host_is_rejected(monkeypatch):
    def ch_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://evil.example.com/steal"})

    monkeypatch.setattr(docs, "companies_house_client", _mock_ch_client_factory(ch_handler))

    with pytest.raises(ToolError) as exc_info:
        await docs._fetch_document_content_bytes("abc123", "application/pdf", 1)

    payload = parse_error_payload(str(exc_info.value))
    assert payload.error_category == "transient"
    assert payload.is_retryable is True


@pytest.mark.asyncio
async def test_redirect_to_non_https_is_rejected(monkeypatch):
    def ch_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://s3.eu-west-2.amazonaws.com/x"})

    monkeypatch.setattr(docs, "companies_house_client", _mock_ch_client_factory(ch_handler))

    with pytest.raises(ToolError) as exc_info:
        await docs._fetch_document_content_bytes("abc123", "application/pdf", 1)

    payload = parse_error_payload(str(exc_info.value))
    assert payload.error_category == "transient"


# ---------------------------------------------------------------------------
# Signed-URL secrecy: a failing S3 fetch must never expose the pre-signed
# URL (or its X-Amz-* query values) anywhere model-visible.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_s3_error_response_does_not_expose_signed_url(monkeypatch):
    """An expired/invalid signed URL (403 from S3) is the realistic failure
    case — content-type/length mismatch handling covers this too, but the
    error must never contain the URL itself, only a generic message."""

    secret_url = "https://s3.eu-west-2.amazonaws.com/bucket/docs/x/pdf?X-Amz-Signature=SUPERSECRETVALUE&X-Amz-Credential=AKIA123"

    def ch_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": secret_url})

    def anon_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, content=b"<Error>AccessDenied</Error>")

    monkeypatch.setattr(docs, "companies_house_client", _mock_ch_client_factory(ch_handler))
    _mock_anon_client_factory(monkeypatch, anon_handler)

    with pytest.raises(ToolError) as exc_info:
        await docs._fetch_document_content_bytes("abc123", "application/pdf", 1)

    message = str(exc_info.value)
    payload = parse_error_payload(message)
    assert payload.error_category == "transient"
    assert payload.is_retryable is True
    assert "X-Amz" not in message
    assert "SUPERSECRETVALUE" not in message
    assert "AKIA123" not in message
    assert secret_url not in message
    assert "s3.eu-west-2.amazonaws.com" not in message


@pytest.mark.asyncio
async def test_s3_network_error_does_not_expose_signed_url(monkeypatch):
    """A network-level failure (not an HTTP status) reaching the signed URL
    — the exception's own str()/repr() can embed the request URL (httpx
    does this for e.g. ConnectTimeout), so the handler must never
    interpolate the caught exception into the raised error either."""

    secret_url = "https://s3.eu-west-2.amazonaws.com/bucket/docs/x/pdf?X-Amz-Signature=SUPERSECRETVALUE&X-Amz-Credential=AKIA123"

    def ch_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": secret_url})

    def anon_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    monkeypatch.setattr(docs, "companies_house_client", _mock_ch_client_factory(ch_handler))
    _mock_anon_client_factory(monkeypatch, anon_handler)

    with pytest.raises(ToolError) as exc_info:
        await docs._fetch_document_content_bytes("abc123", "application/pdf", 1)

    message = str(exc_info.value)
    payload = parse_error_payload(message)
    assert payload.error_category == "transient"
    assert payload.is_retryable is True
    assert "X-Amz" not in message
    assert "SUPERSECRETVALUE" not in message
    assert "AKIA123" not in message
    assert secret_url not in message
    assert "s3.eu-west-2.amazonaws.com" not in message


# ---------------------------------------------------------------------------
# Provenance verification: content-type / content-length mismatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_content_type_mismatch_is_transient_error_not_served(monkeypatch):
    def ch_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": _signed_url()})

    def anon_handler(request: httpx.Request) -> httpx.Response:
        # Expired/wrong signed URL returning an XML error body instead of the PDF.
        return httpx.Response(200, headers={"content-type": "application/xml"}, content=b"<Error/>")

    monkeypatch.setattr(docs, "companies_house_client", _mock_ch_client_factory(ch_handler))
    _mock_anon_client_factory(monkeypatch, anon_handler)

    with pytest.raises(ToolError) as exc_info:
        await docs._fetch_document_content_bytes("abc123", "application/pdf", 1)

    payload = parse_error_payload(str(exc_info.value))
    assert payload.error_category == "transient"
    assert payload.is_retryable is True


@pytest.mark.asyncio
async def test_content_type_with_parameters_normalizes_before_comparison(monkeypatch):
    def ch_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": _signed_url()})

    def anon_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "application/pdf; charset=binary"}, content=b"%PDF-x"
        )

    monkeypatch.setattr(docs, "companies_house_client", _mock_ch_client_factory(ch_handler))
    _mock_anon_client_factory(monkeypatch, anon_handler)

    content = await docs._fetch_document_content_bytes("abc123", "application/pdf", len(b"%PDF-x"))
    assert content == b"%PDF-x"


@pytest.mark.asyncio
async def test_content_length_mismatch_is_transient_error_not_served(monkeypatch):
    def ch_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": _signed_url()})

    def anon_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"only-4-bytes")

    monkeypatch.setattr(docs, "companies_house_client", _mock_ch_client_factory(ch_handler))
    _mock_anon_client_factory(monkeypatch, anon_handler)

    with pytest.raises(ToolError) as exc_info:
        await docs._fetch_document_content_bytes("abc123", "application/pdf", 999_999)

    payload = parse_error_payload(str(exc_info.value))
    assert payload.error_category == "transient"
    assert payload.is_retryable is True


# ---------------------------------------------------------------------------
# Resource read
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def mcp_client():
    async with Client(mcp) as c:
        yield c


@pytest.mark.asyncio
async def test_direct_resource_read_without_mime_type_works_when_unambiguous(monkeypatch, mcp_client):
    def ch_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/content"):
            return httpx.Response(302, headers={"location": _signed_url()})
        return _metadata_response()

    def anon_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"12345678")

    monkeypatch.setattr(docs, "companies_house_client", _mock_ch_client_factory(ch_handler))
    _mock_anon_client_factory(monkeypatch, anon_handler)

    result = await mcp_client.read_resource("company-document://abc123")
    assert result[0].blob is not None or result[0].mimeType == "application/pdf"


@pytest.mark.asyncio
async def test_direct_resource_read_without_mime_type_errors_when_ambiguous(monkeypatch, mcp_client):
    """resources/read errors surface client-side as McpError (a JSON-RPC
    error), not ToolError (which is specific to tools/call results) —
    confirmed by the actual FastMCP Client behavior, not assumed."""

    def ch_handler(request: httpx.Request) -> httpx.Response:
        return _metadata_response(resources={"application/pdf": {"content_length": 5}, "image/tiff": {"content_length": 9}})

    monkeypatch.setattr(docs, "companies_house_client", _mock_ch_client_factory(ch_handler))

    with pytest.raises(McpError) as exc_info:
        await mcp_client.read_resource("company-document://abc123")

    payload = parse_error_payload(str(exc_info.value))
    assert payload is not None, f"error message did not parse as a FleetErrorPayload: {exc_info.value}"
    assert payload.error_category == "validation"


@pytest.mark.asyncio
async def test_tool_via_mcp_client_returns_resource_link_content(monkeypatch, mcp_client):
    def handler(request: httpx.Request) -> httpx.Response:
        return _metadata_response()

    monkeypatch.setattr(docs, "companies_house_client", _mock_ch_client_factory(handler))
    result = await mcp_client.call_tool("company_filing_document", {"document_metadata_url": VALID_METADATA_URL})

    assert result.structured_content["mime_type"] == "application/pdf"
    types = {getattr(b, "type", None) for b in result.content}
    assert "resource_link" in types


@pytest.mark.asyncio
async def test_resource_template_does_not_declare_a_fixed_pdf_mime_type(mcp_client):
    """The declared template MIME is advisory listing metadata only — the
    actual served type is always resolved dynamically per-document (see
    ResourceContent(..., mime_type=selected_mime) in the resource body).
    It must not bake in an application/pdf-only assumption, since
    `resources` is upstream's own open dict."""

    templates = await mcp_client.list_resource_templates()
    doc_template = next(t for t in templates if t.name == "company_filing_document_content")
    assert doc_template.mimeType != "application/pdf"
