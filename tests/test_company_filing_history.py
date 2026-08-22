"""Tests for the company_filing_history primitive.

Covers: CompanyFiling/CompanyFilingHistoryResult model defaults (including
the subcategory str-or-list union confirmed live on Carillion PLC),
_fetch_company_filing_history's per-page (not auto-fetch-all) pagination
shape, the category= pass-through to CH's real upstream filter, the
large-unfiltered-history advisory note, the filing_history_status
"not-available" validation-error branch, and the empty-vs-nonexistent
disambiguation via a minimal GET /company/{number} existence probe
(confirmed live: filing-history alone returns HTTP 200 + total_count=0
for a company number that does not exist at all).
"""

from __future__ import annotations

import httpx
import pytest
import pytest_asyncio
from fastmcp import Client
from fastmcp.exceptions import ToolError

import companies_house
from mcpfleet_obs import parse_error_payload
from models import CompanyFiling, CompanyFilingHistoryResult
from server import mcp


def _mock_client_factory(handler):
    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url="https://api.company-information.service.gov.uk",
            transport=httpx.MockTransport(handler),
        )

    return factory


def test_company_filing_requires_only_transaction_id_and_type():
    filing = CompanyFiling(transaction_id="ABC123", type="AA")
    assert filing.date is None
    assert filing.description_values == {}
    assert filing.resolutions == []
    assert filing.associated_filings == []
    assert filing.annotations == []
    assert filing.links == {}


def test_company_filing_subcategory_accepts_string_or_list():
    """Confirmed live on Carillion PLC: subcategory is usually a string but
    was observed as a list of strings (["compulsory", "court-order"])."""
    assert CompanyFiling(transaction_id="A", type="X", subcategory="change").subcategory == "change"
    assert CompanyFiling(
        transaction_id="A", type="X", subcategory=["compulsory", "court-order"]
    ).subcategory == ["compulsory", "court-order"]


def test_company_filing_history_result_defaults():
    result = CompanyFilingHistoryResult(
        company_number="06333469", total_count=0, start_index=0, items_per_page=100, returned=0, has_more=False
    )
    assert result.filings == []
    assert result.category is None
    assert result.note is None


def _filing_item(transaction_id, type_, category="accounts", **overrides):
    item = {
        "transaction_id": transaction_id,
        "barcode": f"X{transaction_id}",
        "type": type_,
        "date": "2020-01-01",
        "category": category,
        "subcategory": "change",
        "description": "some-description-slug",
        "description_values": {"made_up_date": "2019-12-31"},
        "pages": 4,
        "paper_filed": False,
        "links": {
            "self": f"/company/06333469/filing-history/{transaction_id}",
            "document_metadata": f"https://document-api.company-information.service.gov.uk/document/{transaction_id}",
        },
    }
    item.update(overrides)
    return item


@pytest.mark.asyncio
async def test_fetch_filing_history_maps_tas_litmus_filing(monkeypatch):
    """MR04 mortgage-satisfaction filing, the litmus case: charge_number in
    description_values, document_metadata present, no barcode (older
    NEWINC-style filings sometimes omit it) — confirmed live shape."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/company/06333469/filing-history"
        return httpx.Response(
            200,
            json={
                "filing_history_status": "filing-history-available",
                "total_count": 1,
                "items_per_page": 100,
                "start_index": 0,
                "items": [
                    _filing_item(
                        "MzM0MDk3MzA2OGFkaXF6a2N4",
                        "MR04",
                        category="mortgage",
                        subcategory="satisfy",
                        description="mortgage-satisfy-charge-full",
                        description_values={"charge_number": "063334690004"},
                        barcode=None,
                    )
                ],
            },
        )

    monkeypatch.setattr(companies_house, "companies_house_client", _mock_client_factory(handler))
    result = await companies_house._fetch_company_filing_history("06333469")

    assert result.total_count == 1
    assert result.returned == 1
    assert result.has_more is False
    filing = result.filings[0]
    assert filing.transaction_id == "MzM0MDk3MzA2OGFkaXF6a2N4"
    assert filing.category == "mortgage"
    assert filing.subcategory == "satisfy"
    assert filing.description_values == {"charge_number": "063334690004"}
    assert filing.links["document_metadata"].startswith("https://document-api")


@pytest.mark.asyncio
async def test_fetch_filing_history_preserves_list_subcategory_and_nested_blobs(monkeypatch):
    """Carillion-shape filing: subcategory is a list, resolutions/
    associated_filings/annotations are nested filing-like dicts passed
    through raw (they carry no transaction_id/links of their own)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "filing_history_status": "filing-history-available",
                "total_count": 1,
                "items_per_page": 100,
                "start_index": 0,
                "items": [
                    _filing_item(
                        "X1",
                        "600",
                        category="insolvency",
                        subcategory=["compulsory", "court-order"],
                        resolutions=[{"category": "capital", "type": "RES13", "description": "resolution"}],
                        associated_filings=[{"category": "capital", "type": "SH01"}],
                        annotations=[{"annotation": "Clarification note", "type": "ANNOTATION"}],
                    )
                ],
            },
        )

    monkeypatch.setattr(companies_house, "companies_house_client", _mock_client_factory(handler))
    result = await companies_house._fetch_company_filing_history("03782379")

    filing = result.filings[0]
    assert filing.subcategory == ["compulsory", "court-order"]
    assert filing.resolutions == [{"category": "capital", "type": "RES13", "description": "resolution"}]
    assert filing.associated_filings == [{"category": "capital", "type": "SH01"}]
    assert filing.annotations == [{"annotation": "Clarification note", "type": "ANNOTATION"}]


@pytest.mark.asyncio
async def test_fetch_filing_history_missing_document_metadata_link_is_none_field_ok(monkeypatch):
    """Confirmed live: some pre-2008 RESOLUTIONS filings have links.self
    but no links.document_metadata — must not raise, must pass through
    whatever links are present."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "total_count": 1,
                "items": [
                    {
                        "transaction_id": "OLD1",
                        "type": "RESOLUTIONS",
                        "category": "resolution",
                        "links": {"self": "/company/09118548/filing-history/OLD1"},
                    }
                ],
            },
        )

    monkeypatch.setattr(companies_house, "companies_house_client", _mock_client_factory(handler))
    result = await companies_house._fetch_company_filing_history("09118548")

    assert "document_metadata" not in result.filings[0].links
    assert result.filings[0].links["self"] == "/company/09118548/filing-history/OLD1"


@pytest.mark.asyncio
async def test_fetch_filing_history_is_a_single_page_not_auto_fetched(monkeypatch):
    """Unlike officers/PSC/charges, this must NOT loop past one page —
    total_count=323 with only 100 items on this page must be reported as
    returned=100, has_more=True, with exactly one upstream request."""

    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(
            200,
            json={
                "total_count": 323,
                "items_per_page": 100,
                "start_index": 0,
                "items": [_filing_item(f"T{i}", "AA") for i in range(100)],
            },
        )

    monkeypatch.setattr(companies_house, "companies_house_client", _mock_client_factory(handler))
    result = await companies_house._fetch_company_filing_history("03782379")

    assert request_count == 1
    assert result.total_count == 323
    assert result.returned == 100
    assert result.has_more is True


@pytest.mark.asyncio
async def test_fetch_filing_history_has_more_false_on_last_page(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params.get("start_index", "0"))
        assert start == 300
        return httpx.Response(
            200,
            json={"total_count": 323, "items": [_filing_item(f"T{i}", "AA") for i in range(23)]},
        )

    monkeypatch.setattr(companies_house, "companies_house_client", _mock_client_factory(handler))
    result = await companies_house._fetch_company_filing_history("03782379", start_index=300)

    assert result.returned == 23
    assert result.has_more is False


@pytest.mark.asyncio
async def test_fetch_filing_history_passes_category_filter_to_upstream(monkeypatch):
    seen_params = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_params["category"] = request.url.params.get("category")
        return httpx.Response(200, json={"total_count": 20, "items": [_filing_item("M1", "MR01", category="mortgage")]})

    monkeypatch.setattr(companies_house, "companies_house_client", _mock_client_factory(handler))
    result = await companies_house._fetch_company_filing_history("00445790", category="mortgage,officers")

    assert seen_params["category"] == "mortgage,officers"
    assert result.category == "mortgage,officers"


@pytest.mark.asyncio
async def test_fetch_filing_history_large_unfiltered_result_gets_advisory_note(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"total_count": 8367, "items": [_filing_item(f"T{i}", "SH03") for i in range(100)]})

    monkeypatch.setattr(companies_house, "companies_house_client", _mock_client_factory(handler))
    result = await companies_house._fetch_company_filing_history("00445790")

    assert result.note is not None
    assert "category=" in result.note


@pytest.mark.asyncio
async def test_fetch_filing_history_no_advisory_note_when_category_filter_applied(monkeypatch):
    """A caller who already narrowed with category= doesn't need the nudge,
    even if the filtered total is still large."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"total_count": 8367, "items": [_filing_item(f"T{i}", "SH03") for i in range(100)]})

    monkeypatch.setattr(companies_house, "companies_house_client", _mock_client_factory(handler))
    result = await companies_house._fetch_company_filing_history("00445790", category="capital")

    assert result.note is None


@pytest.mark.asyncio
async def test_fetch_filing_history_no_advisory_note_below_threshold(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"total_count": 54, "items": [_filing_item(f"T{i}", "AA") for i in range(54)]})

    monkeypatch.setattr(companies_house, "companies_house_client", _mock_client_factory(handler))
    result = await companies_house._fetch_company_filing_history("06333469")

    assert result.note is None


@pytest.mark.asyncio
async def test_fetch_filing_history_invalid_format_status_is_validation_error(monkeypatch):
    """Confirmed live: a malformed company number returns HTTP 200 with
    filing_history_status='filing-history-not-available-invalid-format',
    total_count=0 — must surface as a validation error, and must NOT
    trigger the existence-check probe (upstream already told us why)."""

    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(
            200,
            json={
                "filing_history_status": "filing-history-not-available-invalid-format",
                "total_count": 0,
                "items": [],
            },
        )

    monkeypatch.setattr(companies_house, "companies_house_client", _mock_client_factory(handler))

    with pytest.raises(ToolError) as exc_info:
        await companies_house._fetch_company_filing_history("NOTREAL1")

    payload = parse_error_payload(str(exc_info.value))
    assert payload is not None, f"error message did not parse as a FleetErrorPayload: {exc_info.value}"
    assert payload.error_category == "validation"
    assert payload.is_retryable is False
    assert request_count == 1


@pytest.mark.asyncio
async def test_fetch_filing_history_empty_result_with_existing_company_is_genuine_success(monkeypatch):
    """total_count=0 + the existence probe returns 200 -> a real,
    successful, empty filing-history result, not an error."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/company/06333469/filing-history":
            return httpx.Response(
                200, json={"filing_history_status": "filing-history-available", "total_count": 0, "items": []}
            )
        assert request.url.path == "/company/06333469"
        return httpx.Response(200, json={"company_number": "06333469", "company_name": "TAS ENGINEERING LTD"})

    monkeypatch.setattr(companies_house, "companies_house_client", _mock_client_factory(handler))
    result = await companies_house._fetch_company_filing_history("06333469")

    assert result.total_count == 0
    assert result.filings == []


@pytest.mark.asyncio
async def test_fetch_filing_history_empty_result_for_nonexistent_company_is_not_found(monkeypatch):
    """The critical disambiguation: filing-history itself returns 200 +
    empty for a company number that doesn't exist at all (confirmed live
    against company_number='00000001') — the existence probe's 404 must
    surface as a structured not_found error instead of a false-empty success."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/company/00000001/filing-history":
            return httpx.Response(
                200, json={"filing_history_status": "filing-history-available", "total_count": 0, "items": []}
            )
        assert request.url.path == "/company/00000001"
        return httpx.Response(404, json={"errors": [{"error": "company-profile-not-found"}]})

    monkeypatch.setattr(companies_house, "companies_house_client", _mock_client_factory(handler))

    with pytest.raises(ToolError) as exc_info:
        await companies_house._fetch_company_filing_history("00000001")

    payload = parse_error_payload(str(exc_info.value))
    assert payload is not None, f"error message did not parse as a FleetErrorPayload: {exc_info.value}"
    assert payload.error_category == "not_found"
    assert payload.is_retryable is False


@pytest.mark.asyncio
async def test_fetch_filing_history_existence_probe_propagates_transient_failure(monkeypatch):
    """If the existence probe itself fails for a transient reason (503),
    that failure must propagate as-is, not be swallowed into a false
    empty-success."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/company/06333469/filing-history":
            return httpx.Response(200, json={"total_count": 0, "items": []})
        return httpx.Response(503, json={"errors": [{"error": "service unavailable"}]})

    monkeypatch.setattr(companies_house, "companies_house_client", _mock_client_factory(handler))

    with pytest.raises(ToolError) as exc_info:
        await companies_house._fetch_company_filing_history("06333469")

    payload = parse_error_payload(str(exc_info.value))
    assert payload is not None, f"error message did not parse as a FleetErrorPayload: {exc_info.value}"
    assert payload.error_category == "transient"
    assert payload.is_retryable is True


@pytest.mark.asyncio
async def test_fetch_filing_history_nonempty_result_never_calls_existence_probe(monkeypatch):
    """No wasted request in the common case: total_count > 0 must resolve
    in exactly one upstream call."""

    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, json={"total_count": 1, "items": [_filing_item("A1", "AA")]})

    monkeypatch.setattr(companies_house, "companies_house_client", _mock_client_factory(handler))
    await companies_house._fetch_company_filing_history("06333469")

    assert request_count == 1


@pytest_asyncio.fixture
async def mcp_client():
    async with Client(mcp) as c:
        yield c


@pytest.mark.asyncio
async def test_company_filing_history_tool_returns_expected_shape(mcp_client, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "filing_history_status": "filing-history-available",
                "total_count": 1,
                "items_per_page": 100,
                "start_index": 0,
                "items": [_filing_item("MEL1", "PSC04", category="persons-with-significant-control")],
            },
        )

    monkeypatch.setattr(companies_house, "companies_house_client", _mock_client_factory(handler))
    result = await mcp_client.call_tool("company_filing_history", {"company_number": "09118548"})

    data = result.structured_content
    assert data["total_count"] == 1
    assert data["filings"][0]["category"] == "persons-with-significant-control"
    assert data["has_more"] is False


@pytest.mark.asyncio
async def test_company_filing_history_tool_accepts_category_and_pagination_params(mcp_client, monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["category"] = request.url.params.get("category")
        seen["start_index"] = request.url.params.get("start_index")
        return httpx.Response(200, json={"total_count": 5, "items": [_filing_item("A1", "AA")]})

    monkeypatch.setattr(companies_house, "companies_house_client", _mock_client_factory(handler))
    await mcp_client.call_tool(
        "company_filing_history",
        {"company_number": "00445790", "category": "insolvency", "start_index": 4},
    )

    assert seen["category"] == "insolvency"
    assert seen["start_index"] == "4"


@pytest.mark.asyncio
async def test_company_filing_history_nonexistent_company_is_not_found_error(mcp_client, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/company/00000001/filing-history":
            return httpx.Response(200, json={"total_count": 0, "items": []})
        return httpx.Response(404, json={"errors": [{"error": "company-profile-not-found"}]})

    monkeypatch.setattr(companies_house, "companies_house_client", _mock_client_factory(handler))

    with pytest.raises(ToolError) as exc_info:
        await mcp_client.call_tool("company_filing_history", {"company_number": "00000001"})

    payload = parse_error_payload(str(exc_info.value))
    assert payload is not None, f"error message did not parse as a FleetErrorPayload: {exc_info.value}"
    assert payload.error_category == "not_found"
    assert payload.is_retryable is False
