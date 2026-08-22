"""Tests for the officer_appointments primitive and officer_id extraction.

Covers: OfficerAppointment/OfficerAppointmentsResult model defaults,
officer_id extraction from company_officers, _fetch_officer_appointments
pagination (including CH's 50-item-per-page cap on this endpoint), and
the registered tool's error-taxonomy behavior.
"""

from __future__ import annotations

import httpx
import pytest
import pytest_asyncio
from fastmcp import Client
from fastmcp.exceptions import ToolError

import companies_house
from mcpfleet_obs import parse_error_payload
from models import CompanyOfficer, OfficerAppointment, OfficerAppointmentsResult
from server import mcp


def _mock_client_factory(handler):
    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url="https://api.company-information.service.gov.uk",
            transport=httpx.MockTransport(handler),
        )

    return factory


def test_officer_appointment_defaults():
    appt = OfficerAppointment(company_number="09118548", company_name="MEL PRECISION LIMITED")
    assert appt.company_status is None
    assert appt.address == {}
    assert appt.links == {}


def test_officer_appointments_result_defaults_date_of_birth_empty():
    result = OfficerAppointmentsResult(officer_id="ABC123", total=0)
    assert result.date_of_birth == {}
    assert result.appointments == []
    assert result.active_count is None


def test_company_officer_officer_id_defaults_none():
    officer = CompanyOfficer(name="TEST, Person")
    assert officer.officer_id is None


def test_officer_id_from_links_extracts_id():
    links = {
        "self": "/company/07463956/appointments/amw2SbXpRPG6TKWZMHv8maI7KT8",
        "officer": {"appointments": "/officers/TJnYbMBABbRubVheZow9VHJzXB8/appointments"},
    }
    assert companies_house._officer_id_from_links(links) == "TJnYbMBABbRubVheZow9VHJzXB8"


def test_officer_id_from_links_returns_none_when_missing():
    assert companies_house._officer_id_from_links({}) is None
    assert companies_house._officer_id_from_links({"self": "/company/X/appointments/Y"}) is None
    assert companies_house._officer_id_from_links({"officer": {}}) is None


@pytest.mark.asyncio
async def test_fetch_company_officers_populates_officer_id(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/company/06333469/officers"
        return httpx.Response(
            200,
            json={
                "total_results": 1,
                "items": [
                    {
                        "name": "DAVIES, Gareth Leonard",
                        "officer_role": "director",
                        "appointed_on": "2007-08-08",
                        "links": {
                            "self": "/company/06333469/appointments/IUYz12p2QJtdYRmxlr6chgN1pwU",
                            "officer": {"appointments": "/officers/EAoJ81mtThuKM5KmSuO5U1RNLHs/appointments"},
                        },
                    }
                ],
            },
        )

    monkeypatch.setattr(companies_house, "companies_house_client", _mock_client_factory(handler))
    result = await companies_house._fetch_company_officers("06333469")

    assert result.officers[0].officer_id == "EAoJ81mtThuKM5KmSuO5U1RNLHs"


def _appointment_item(company_number, company_name, company_status, resigned_on=None):
    return {
        "appointed_to": {
            "company_number": company_number,
            "company_name": company_name,
            "company_status": company_status,
        },
        "officer_role": "director",
        "appointed_on": "2014-07-07",
        "resigned_on": resigned_on,
        "nationality": "British",
        "country_of_residence": "England",
        "address": {"locality": "Uttoxeter"},
        "links": {"company": f"/company/{company_number}"},
    }


@pytest.mark.asyncio
async def test_officer_appointments_maps_tas_gareth_mel_fixture(monkeypatch):
    """Mirrors the real acceptance case: one officer, three companies,
    spanning active/liquidation/dissolved — MEL's resigned_on stays null
    even though it's in liquidation, matching live CH behavior."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/officers/EAoJ81mtThuKM5KmSuO5U1RNLHs/appointments"
        return httpx.Response(
            200,
            json={
                "name": "Gareth Leonard DAVIES",
                "date_of_birth": {"month": 9, "year": 1964},
                "active_count": 2,
                "resigned_count": 0,
                "inactive_count": 1,
                "items_per_page": 50,
                "total_results": 3,
                "items": [
                    _appointment_item("12609854", "FXF DESIGNS LTD", "dissolved"),
                    _appointment_item("09118548", "MEL PRECISION LIMITED", "liquidation"),
                    _appointment_item("06333469", "TAS ENGINEERING LTD", "active"),
                ],
            },
        )

    monkeypatch.setattr(companies_house, "companies_house_client", _mock_client_factory(handler))
    result = await companies_house._fetch_officer_appointments("EAoJ81mtThuKM5KmSuO5U1RNLHs")

    assert result.total == 3
    assert result.active_count == 2
    assert result.resigned_count == 0
    assert result.inactive_count == 1
    by_number = {a.company_number: a for a in result.appointments}
    assert by_number["09118548"].company_status == "liquidation"
    assert by_number["09118548"].resigned_on is None
    assert by_number["06333469"].company_status == "active"
    assert by_number["12609854"].company_status == "dissolved"


@pytest.mark.asyncio
async def test_officer_appointments_date_of_birth_defaults_empty_when_absent(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "name": "CORPORATE OFFICER LTD",
                "total_results": 0,
                "items_per_page": 50,
                "items": [],
            },
        )

    monkeypatch.setattr(companies_house, "companies_house_client", _mock_client_factory(handler))
    result = await companies_house._fetch_officer_appointments("SOME-CORPORATE-OFFICER-ID")

    assert result.date_of_birth == {}
    assert result.appointments == []


@pytest.mark.asyncio
async def test_officer_appointments_paginates_past_ch_50_item_cap(monkeypatch):
    """CH clamps items_per_page to 50 on this endpoint regardless of what's
    requested. total_results=75 across two pages must yield exactly 75
    appointments — the pagination-completeness acceptance criterion."""

    page_one = [_appointment_item(f"{10000000 + i}", f"COMPANY {i}", "active") for i in range(50)]
    page_two = [_appointment_item(f"{10000050 + i}", f"COMPANY {i}", "active") for i in range(25)]
    requested_start_indexes = []

    def handler(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params.get("start_index", "0"))
        requested_start_indexes.append(start)
        # CH always reports items_per_page=50 in the response, even if a
        # larger value was requested.
        if start == 0:
            return httpx.Response(200, json={"total_results": 75, "items_per_page": 50, "items": page_one})
        return httpx.Response(200, json={"total_results": 75, "items_per_page": 50, "items": page_two})

    monkeypatch.setattr(companies_house, "companies_house_client", _mock_client_factory(handler))
    result = await companies_house._fetch_officer_appointments("BIG-PORTFOLIO-OFFICER")

    assert len(result.appointments) == 75
    assert result.total == 75
    assert requested_start_indexes == [0, 50]


@pytest_asyncio.fixture
async def mcp_client():
    async with Client(mcp) as c:
        yield c


@pytest.mark.asyncio
async def test_officer_appointments_tool_returns_expected_shape(mcp_client, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "name": "Gareth Leonard DAVIES",
                "date_of_birth": {"month": 9, "year": 1964},
                "active_count": 2,
                "resigned_count": 0,
                "inactive_count": 1,
                "items_per_page": 50,
                "total_results": 1,
                "items": [_appointment_item("09118548", "MEL PRECISION LIMITED", "liquidation")],
            },
        )

    monkeypatch.setattr(companies_house, "companies_house_client", _mock_client_factory(handler))
    result = await mcp_client.call_tool("officer_appointments", {"officer_id": "EAoJ81mtThuKM5KmSuO5U1RNLHs"})

    data = result.structured_content
    assert data["officer_id"] == "EAoJ81mtThuKM5KmSuO5U1RNLHs"
    assert data["appointments"][0]["company_status"] == "liquidation"


@pytest.mark.asyncio
async def test_officer_appointments_404_is_not_found_error(mcp_client, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"errors": [{"error": "officer-appointments-not-found"}]})

    monkeypatch.setattr(companies_house, "companies_house_client", _mock_client_factory(handler))

    with pytest.raises(ToolError) as exc_info:
        await mcp_client.call_tool("officer_appointments", {"officer_id": "does-not-exist"})

    payload = parse_error_payload(str(exc_info.value))
    assert payload is not None, f"error message did not parse as a FleetErrorPayload: {exc_info.value}"
    assert payload.error_category == "not_found"
    assert payload.is_retryable is False
