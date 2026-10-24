"""Regression test: an unknown company number must never read as a clean result.

Companies House answers the officers, PSC and charges collections with 200 and an
empty list for a company number that does not exist. Before this fix those looked
like "no officers / no PSCs / no charges" and an agent could write a clean bill of
health for a typo. Each collection now confirms the company on the empty path.
"""

from __future__ import annotations

import httpx
import pytest
from fastmcp.exceptions import ToolError
from mcpfleet_obs import parse_error_payload

import companies_house


UNKNOWN = "00375585"  # the typo'd number from the 2026-10-24 staging smoke test


def _factory(handler):
    def make() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url="https://api.company-information.service.gov.uk",
            transport=httpx.MockTransport(handler),
        )

    return make


def _unknown_company_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith(f"/company/{UNKNOWN}"):
        return httpx.Response(404, json={"error": "not found"})
    # Collections for an unknown company come back empty, with 200.
    return httpx.Response(200, json={"items": [], "total_results": 0, "total_count": 0})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fetch",
    [
        lambda n: companies_house._fetch_company_officers(n),
        lambda n: companies_house._fetch_company_psc(n),
        lambda n: companies_house._fetch_company_charges(n),
    ],
    ids=["officers", "psc", "charges"],
)
async def test_unknown_company_is_not_found_not_a_clean_empty(monkeypatch, fetch):
    monkeypatch.setattr(companies_house, "companies_house_client", _factory(_unknown_company_handler))

    with pytest.raises(ToolError) as exc_info:
        await fetch(UNKNOWN)

    payload = parse_error_payload(str(exc_info.value))
    assert payload is not None
    assert payload.error_category == "not_found"


@pytest.mark.asyncio
async def test_psc_note_only_for_plc(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/company/00445790"):
            return httpx.Response(200, json={"company_number": "00445790", "type": "ltd"})
        return httpx.Response(200, json={"items": [], "total_results": 0})

    monkeypatch.setattr(companies_house, "companies_house_client", _factory(handler))

    result = await companies_house._fetch_company_psc("00445790")

    assert result.total == 0
    assert result.note is None


@pytest.mark.asyncio
async def test_psc_note_kept_for_listed_plc(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/company/00445790"):
            return httpx.Response(200, json={"company_number": "00445790", "type": "plc"})
        return httpx.Response(200, json={"items": [], "total_results": 0})

    monkeypatch.setattr(companies_house, "companies_house_client", _factory(handler))

    result = await companies_house._fetch_company_psc("00445790")

    assert result.total == 0
    assert result.note is not None and "widely-held" in result.note


@pytest.mark.asyncio
async def test_happy_path_makes_no_extra_company_call(monkeypatch):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(
            200,
            json={"items": [{"name": "EXAMPLE DIRECTOR", "officer_role": "director"}],
                  "total_results": 1, "total_count": 0},
        )

    monkeypatch.setattr(companies_house, "companies_house_client", _factory(handler))

    result = await companies_house._fetch_company_officers("00445790")

    assert result.total == 1
    assert not any(p.endswith("/company/00445790") for p in seen)
