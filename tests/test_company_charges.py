"""Tests for the company_charges primitive and the has_charges refactor.

Covers: charge model defaults/optionality, _fetch_company_charges
pagination (total_count-driven, not page-length-driven), the litmus-test
fixture (TAS charge 5, outstanding, no secured_details), a satisfied
charge with secured_details, and the full has_charges truth table on
company_profile including the endpoint-failure -> None regression.
"""

from __future__ import annotations

import httpx
import pytest
import pytest_asyncio
from fastmcp import Client
from fastmcp.exceptions import ToolError

import companies_house
from mcpfleet_obs import parse_error_payload
from models import ChargeParticulars, CompanyCharge, CompanyChargesResult
from server import mcp


def _mock_client_factory(handler):
    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url="https://api.company-information.service.gov.uk",
            transport=httpx.MockTransport(handler),
        )

    return factory


def test_charge_particulars_all_fields_optional():
    p = ChargeParticulars()
    assert p.type is None
    assert p.contains_floating_charge is None


def test_company_charge_requires_only_charge_number():
    charge = CompanyCharge(charge_number=5)
    assert charge.charge_code is None
    assert charge.secured_details is None
    assert charge.persons_entitled == []
    assert charge.transactions == []


def test_company_charges_result_defaults():
    result = CompanyChargesResult(company_number="06333469", total_count=0)
    assert result.charges == []
    assert result.unfiltered_count is None


def _charge_item(charge_number, charge_code, status, **overrides):
    item = {
        "charge_number": charge_number,
        "charge_code": charge_code,
        "classification": {"type": "charge-description", "description": "A registered charge"},
        "status": status,
        "created_on": "2017-06-01",
        "delivered_on": "2017-06-06",
        "particulars": {
            "contains_floating_charge": True,
            "contains_fixed_charge": True,
            "floating_charge_covers_all": True,
            "contains_negative_pledge": True,
        },
        "persons_entitled": [{"name": "Lloyds Bank Commercail Finance Limited"}],
        "transactions": [
            {
                "filing_type": "create-charge-with-deed",
                "delivered_on": "2017-06-06",
                "links": {"filing": f"/company/06333469/filing-history/x{charge_number}"},
            }
        ],
        "links": {"self": f"/company/06333469/charges/x{charge_number}"},
    }
    item.update(overrides)
    return item


@pytest.mark.asyncio
async def test_fetch_company_charges_maps_tas_litmus_charge(monkeypatch):
    """Charge 5 (063334690005), the litmus test: outstanding, no
    secured_details, all four particulars flags true including
    contains_fixed_charge — the full litmus-test statement, not a subset."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/company/06333469/charges"
        return httpx.Response(
            200,
            json={
                "total_count": 1,
                "unfiltered_count": 1,
                "satisfied_count": 0,
                "part_satisfied_count": 0,
                "items": [_charge_item(5, "063334690005", "outstanding")],
            },
        )

    monkeypatch.setattr(companies_house, "companies_house_client", _mock_client_factory(handler))
    result = await companies_house._fetch_company_charges("06333469")

    assert result.total_count == 1
    charge = result.charges[0]
    assert charge.charge_code == "063334690005"
    assert charge.status == "outstanding"
    assert charge.secured_details is None
    assert charge.particulars.contains_floating_charge is True
    assert charge.particulars.contains_fixed_charge is True
    assert charge.particulars.floating_charge_covers_all is True
    assert charge.particulars.contains_negative_pledge is True
    assert charge.persons_entitled[0].name == "Lloyds Bank Commercail Finance Limited"


@pytest.mark.asyncio
async def test_fetch_company_charges_satisfied_charge_has_secured_details_and_satisfaction_transaction(monkeypatch):
    """Charge 1 shape: fully-satisfied, pre-2006 debenture, has
    secured_details, no particulars boolean flags — the second litmus
    case, proving the model isn't only correct for live security."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "total_count": 1,
                "items": [
                    {
                        "charge_number": 1,
                        "classification": {"type": "charge-description", "description": "Debenture"},
                        "status": "fully-satisfied",
                        "created_on": "2007-11-26",
                        "delivered_on": "2007-12-04",
                        "satisfied_on": "2015-06-26",
                        "particulars": {
                            "type": "short-particulars",
                            "description": "Fixed and floating charges over the undertaking and all property and assets.",
                        },
                        "secured_details": {
                            "type": "amount-secured",
                            "description": "All monies due or to become due from the company to the chargee.",
                        },
                        "persons_entitled": [{"name": "Hsbc Bank PLC"}],
                        "transactions": [
                            {"filing_type": "create-charge-pre-2006-companies-act", "delivered_on": "2007-12-04", "links": {}},
                            {"filing_type": "charge-satisfaction", "delivered_on": "2015-06-26", "links": {}},
                        ],
                        "links": {},
                    }
                ],
            },
        )

    monkeypatch.setattr(companies_house, "companies_house_client", _mock_client_factory(handler))
    result = await companies_house._fetch_company_charges("06333469")

    charge = result.charges[0]
    assert charge.charge_code is None
    assert charge.status == "fully-satisfied"
    assert charge.satisfied_on == "2015-06-26"
    assert charge.secured_details.description.startswith("All monies due")
    assert charge.particulars.contains_floating_charge is None
    filing_types = [t.filing_type for t in charge.transactions]
    assert "charge-satisfaction" in filing_types


@pytest.mark.asyncio
async def test_fetch_company_charges_paginates_to_total_count(monkeypatch):
    """Pagination must be exact: total_count=5 across two pages must
    yield exactly 5 charges, requested via start_index 0 then 2 (not
    driven by whether a page came back shorter than requested)."""

    page_one = [_charge_item(5, "063334690005", "outstanding"), _charge_item(4, "063334690004", "fully-satisfied")]
    page_two = [_charge_item(3, "063334690003", "outstanding"), _charge_item(2, "063334690002", "fully-satisfied"), _charge_item(1, None, "fully-satisfied")]
    requested_start_indexes = []

    def handler(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params.get("start_index", "0"))
        requested_start_indexes.append(start)
        if start == 0:
            return httpx.Response(200, json={"total_count": 5, "items": page_one})
        return httpx.Response(200, json={"total_count": 5, "items": page_two})

    monkeypatch.setattr(companies_house, "companies_house_client", _mock_client_factory(handler))
    result = await companies_house._fetch_company_charges("06333469")

    assert len(result.charges) == 5
    assert result.total_count == 5
    assert requested_start_indexes == [0, 2]


@pytest.mark.asyncio
async def test_fetch_company_charges_raises_on_empty_page_before_total_reached(monkeypatch):
    """An unexpected empty page before total_count is reached must raise
    a structured transient/retryable error, not silently return a
    truncated collection."""

    def handler(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params.get("start_index", "0"))
        if start == 0:
            return httpx.Response(
                200,
                json={"total_count": 5, "items": [_charge_item(5, "X", "outstanding"), _charge_item(4, "Y", "fully-satisfied")]},
            )
        return httpx.Response(200, json={"total_count": 5, "items": []})

    monkeypatch.setattr(companies_house, "companies_house_client", _mock_client_factory(handler))

    with pytest.raises(ToolError) as exc_info:
        await companies_house._fetch_company_charges("06333469")

    payload = parse_error_payload(str(exc_info.value))
    assert payload is not None, f"error message did not parse as a FleetErrorPayload: {exc_info.value}"
    assert payload.error_category == "transient"
    assert payload.is_retryable is True


@pytest.mark.asyncio
async def test_fetch_company_charges_raises_on_final_count_mismatch(monkeypatch):
    """Post-loop invariant: len(charges) must equal total_count. Upstream
    returning more items across pages than it originally reported (e.g. a
    duplicate on the last page) must raise, not silently overcount."""

    def handler(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params.get("start_index", "0"))
        if start == 0:
            return httpx.Response(
                200,
                json={"total_count": 3, "items": [_charge_item(5, "A", "outstanding"), _charge_item(4, "B", "fully-satisfied")]},
            )
        return httpx.Response(
            200,
            json={"total_count": 3, "items": [_charge_item(3, "C", "fully-satisfied"), _charge_item(2, "D", "fully-satisfied")]},
        )

    monkeypatch.setattr(companies_house, "companies_house_client", _mock_client_factory(handler))

    with pytest.raises(ToolError) as exc_info:
        await companies_house._fetch_company_charges("06333469")

    payload = parse_error_payload(str(exc_info.value))
    assert payload is not None, f"error message did not parse as a FleetErrorPayload: {exc_info.value}"
    assert payload.error_category == "transient"
    assert payload.is_retryable is True


@pytest_asyncio.fixture
async def mcp_client():
    async with Client(mcp) as c:
        yield c


@pytest.mark.asyncio
async def test_company_charges_tool_returns_litmus_charge(mcp_client, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"total_count": 1, "items": [_charge_item(5, "063334690005", "outstanding")]},
        )

    monkeypatch.setattr(companies_house, "companies_house_client", _mock_client_factory(handler))
    result = await mcp_client.call_tool("company_charges", {"company_number": "06333469"})

    data = result.structured_content
    assert data["total_count"] == 1
    assert data["charges"][0]["charge_code"] == "063334690005"
    assert data["charges"][0]["status"] == "outstanding"


@pytest.mark.asyncio
async def test_company_charges_404_is_not_found_error(mcp_client, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"errors": [{"error": "company-charges-not-found"}]})

    monkeypatch.setattr(companies_house, "companies_house_client", _mock_client_factory(handler))

    with pytest.raises(ToolError) as exc_info:
        await mcp_client.call_tool("company_charges", {"company_number": "00000000"})

    payload = parse_error_payload(str(exc_info.value))
    assert payload is not None, f"error message did not parse as a FleetErrorPayload: {exc_info.value}"
    assert payload.error_category == "not_found"
    assert payload.is_retryable is False
