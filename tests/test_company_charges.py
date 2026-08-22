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
