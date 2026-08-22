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
