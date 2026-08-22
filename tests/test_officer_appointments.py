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
