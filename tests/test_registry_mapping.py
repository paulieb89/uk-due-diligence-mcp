"""Offline regression tests for source-to-model mapping correctness."""

from __future__ import annotations

import httpx
import pytest

import companies_house


def _mock_client_factory(handler):
    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url="https://api.company-information.service.gov.uk",
            transport=httpx.MockTransport(handler),
        )

    return factory


@pytest.mark.asyncio
async def test_company_profile_maps_type_and_scans_all_charge_pages(monkeypatch):
    """Profile must read CH `type` and not infer charge state from page one only."""

    satisfied = [{"charge_number": i, "status": "fully-satisfied"} for i in range(100)]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/company/07463956":
            return httpx.Response(
                200,
                json={
                    "company_number": "07463956",
                    "company_name": "ABSOLUTE CNC LTD",
                    "company_status": "active",
                    "type": "ltd",
                    "accounts": {},
                    "confirmation_statement": {},
                },
            )
        if request.url.path == "/company/07463956/charges":
            start = int(request.url.params.get("start_index", "0"))
            if start == 0:
                return httpx.Response(200, json={"total_count": 101, "items": satisfied})
            return httpx.Response(
                200,
                json={"total_count": 101, "items": [{"charge_number": 100, "status": "outstanding"}]},
            )
        raise AssertionError(f"unexpected request: {request.url}")

    monkeypatch.setattr(companies_house, "companies_house_client", _mock_client_factory(handler))
    result = await companies_house._fetch_company_profile("07463956")

    assert result.company_type == "ltd"
    assert result.has_charges is True


@pytest.mark.asyncio
async def test_charges_endpoint_unavailable_yields_unknown_not_false(monkeypatch):
    """A charges endpoint outage must surface as has_charges=None, never a silent False."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/company/06333469":
            return httpx.Response(
                200,
                json={
                    "company_number": "06333469",
                    "company_name": "TAS ENGINEERING LTD",
                    "company_status": "active",
                    "type": "ltd",
                    "accounts": {},
                    "confirmation_statement": {},
                },
            )
        if request.url.path == "/company/06333469/charges":
            return httpx.Response(503, json={"errors": [{"error": "service unavailable"}]})
        raise AssertionError(f"unexpected request: {request.url}")

    monkeypatch.setattr(companies_house, "companies_house_client", _mock_client_factory(handler))
    result = await companies_house._fetch_company_profile("06333469")

    assert result.has_charges is None
    assert result.company_type == "ltd"


@pytest.mark.asyncio
async def test_officers_can_include_resigned_history(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/company/07463956/officers"
        return httpx.Response(
            200,
            json={
                "total_results": 2,
                "items": [
                    {
                        "name": "BAKER, Carl Douglas",
                        "officer_role": "director",
                        "appointed_on": "2026-05-27",
                    },
                    {
                        "name": "BECK, Paul Brian",
                        "officer_role": "director",
                        "appointed_on": "2010-12-08",
                        "resigned_on": "2026-05-27",
                        "date_of_birth": {"month": 9, "year": 1970},
                    },
                ],
            },
        )

    monkeypatch.setattr(companies_house, "companies_house_client", _mock_client_factory(handler))

    current = await companies_house._fetch_company_officers("07463956")
    history = await companies_house._fetch_company_officers("07463956", include_resigned=True)

    assert current.include_resigned is False
    assert [o.name for o in current.officers] == ["BAKER, Carl Douglas"]
    assert history.include_resigned is True
    assert [o.name for o in history.officers] == ["BAKER, Carl Douglas", "BECK, Paul Brian"]
    assert history.officers[1].date_of_birth == {"month": 9, "year": 1970}


@pytest.mark.asyncio
async def test_psc_preserves_dob_and_does_not_flag_uk_corporate_psc(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/company/07463956/persons-with-significant-control"
        return httpx.Response(
            200,
            json={
                "total_results": 2,
                "items": [
                    {
                        "kind": "individual-person-with-significant-control",
                        "name": "Mr Paul Brian Beck",
                        "ceased_on": "2026-05-27",
                        "date_of_birth": {"month": 9, "year": 1970},
                        "natures_of_control": ["ownership-of-shares-25-to-50-percent"],
                    },
                    {
                        "kind": "corporate-entity-person-with-significant-control",
                        "name": "Brown & Holmes (Tamworth) Holdings Limited",
                        "identification": {
                            "place_registered": "Companies House",
                            "country_registered": "England",
                            "legal_authority": "England And Wales",
                            "registration_number": "15993300",
                        },
                        "natures_of_control": ["ownership-of-shares-75-to-100-percent"],
                    },
                ],
            },
        )

    monkeypatch.setattr(companies_house, "companies_house_client", _mock_client_factory(handler))
    result = await companies_house._fetch_company_psc("07463956")

    assert result.psc[0].date_of_birth == {"month": 9, "year": 1970}
    assert result.overseas_corporate_psc_flag == 0
