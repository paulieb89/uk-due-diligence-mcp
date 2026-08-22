"""Offline regression tests for source-to-model mapping correctness."""

from __future__ import annotations

import httpx
import pytest
import pytest_asyncio
from fastmcp import Client
from fastmcp.exceptions import ToolError

import companies_house
from mcpfleet_obs import parse_error_payload
from server import mcp


def _mock_client_factory(handler):
    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url="https://api.company-information.service.gov.uk",
            transport=httpx.MockTransport(handler),
        )

    return factory


@pytest_asyncio.fixture
async def mcp_client():
    async with Client(mcp) as c:
        yield c


@pytest.mark.asyncio
async def test_server_docstring_tool_and_resource_counts_are_current(mcp_client):
    """Guards against server.py's module docstring drifting from the
    actual registered tool/resource count again, as it did after
    officer_appointments/company_charges shipped without updating it."""

    tools = await mcp_client.list_tools()
    resources = await mcp_client.list_resource_templates()
    assert len(tools) == 17
    assert len(resources) == 8
    tool_names = {t.name for t in tools}
    resource_names = {t.name for t in resources}
    assert {"officer_appointments", "company_charges", "sanctions_screen"} <= tool_names
    assert "company_charges" in resource_names


@pytest.mark.asyncio
async def test_company_officers_resource_description_has_no_stale_flag_claim(mcp_client):
    """The officers resource must not claim the unimplemented
    high-appointment-count flag — appointment_count and
    high_appointment_count_flag are hardcoded None."""

    templates = await mcp_client.list_resource_templates()
    officers_template = next(t for t in templates if t.name == "company_officers")
    description = (officers_template.description or "").lower()
    assert "high-appointment" not in description
    assert "flag" not in description


def _officer_item(name, resigned_on=None):
    return {
        "name": name,
        "officer_role": "director",
        "appointed_on": "2020-01-01",
        "resigned_on": resigned_on,
        "links": {"officer": {"appointments": f"/officers/{name}/appointments"}},
    }


@pytest.mark.asyncio
async def test_officers_does_not_stop_early_on_a_short_non_final_page(monkeypatch):
    """Regression for the removed `len(page_items) < page_size` heuristic:
    a page shorter than page_size that is NOT the last page (more data
    remains per total_results) must not be treated as end-of-data. Two
    short (60-item) pages followed by a final 30-item page, total=150,
    must yield all 150 officers via start_index 0, 60, 120 — the old
    heuristic would have silently stopped after the first short page,
    returning only 60."""

    page_one = [_officer_item(f"OFFICER {i}") for i in range(60)]
    page_two = [_officer_item(f"OFFICER {60 + i}") for i in range(60)]
    page_three = [_officer_item(f"OFFICER {120 + i}") for i in range(30)]
    requested_start_indexes = []

    def handler(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params.get("start_index", "0"))
        requested_start_indexes.append(start)
        if start == 0:
            return httpx.Response(200, json={"total_results": 150, "items": page_one})
        if start == 60:
            return httpx.Response(200, json={"total_results": 150, "items": page_two})
        return httpx.Response(200, json={"total_results": 150, "items": page_three})

    monkeypatch.setattr(companies_house, "companies_house_client", _mock_client_factory(handler))
    result = await companies_house._fetch_company_officers("06333469")

    assert result.total == 150
    assert requested_start_indexes == [0, 60, 120]


@pytest.mark.asyncio
async def test_officers_raises_on_empty_page_before_total_reached(monkeypatch):
    """An unexpected empty page before total_results is reached must
    raise, not silently return a truncated collection."""

    def handler(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params.get("start_index", "0"))
        if start == 0:
            return httpx.Response(200, json={"total_results": 5, "items": [_officer_item("A"), _officer_item("B")]})
        return httpx.Response(200, json={"total_results": 5, "items": []})

    monkeypatch.setattr(companies_house, "companies_house_client", _mock_client_factory(handler))

    with pytest.raises(ToolError) as exc_info:
        await companies_house._fetch_company_officers("06333469")

    payload = parse_error_payload(str(exc_info.value))
    assert payload is not None, f"error message did not parse as a FleetErrorPayload: {exc_info.value}"
    assert payload.error_category == "transient"
    assert payload.is_retryable is True


@pytest.mark.asyncio
async def test_officers_raises_on_final_count_mismatch(monkeypatch):
    """Post-loop invariant: len(officers) must equal total_results before
    include_resigned filtering. Upstream returning more items across
    pages than the first page originally reported (e.g. a duplicate on
    the last page) must raise, not silently overcount."""

    def handler(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params.get("start_index", "0"))
        if start == 0:
            return httpx.Response(200, json={"total_results": 3, "items": [_officer_item("A"), _officer_item("B")]})
        return httpx.Response(200, json={"total_results": 3, "items": [_officer_item("C"), _officer_item("D")]})

    monkeypatch.setattr(companies_house, "companies_house_client", _mock_client_factory(handler))

    with pytest.raises(ToolError) as exc_info:
        await companies_house._fetch_company_officers("06333469")

    payload = parse_error_payload(str(exc_info.value))
    assert payload is not None, f"error message did not parse as a FleetErrorPayload: {exc_info.value}"
    assert payload.error_category == "transient"
    assert payload.is_retryable is True


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


def _psc_item(name, kind="individual-person-with-significant-control"):
    return {
        "kind": kind,
        "name": name,
        "natures_of_control": ["ownership-of-shares-25-to-50-percent"],
    }


@pytest.mark.asyncio
async def test_psc_paginates_past_a_single_page(monkeypatch):
    """Regression: _fetch_company_psc previously made exactly one
    unpaginated request. total_results=45 across two pages must yield
    all 45 PSC entries, requested via start_index 0 then 25."""

    page_one = [_psc_item(f"PSC {i}") for i in range(25)]
    page_two = [_psc_item(f"PSC {25 + i}") for i in range(20)]
    requested_start_indexes = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/company/07463956/persons-with-significant-control"
        start = int(request.url.params.get("start_index", "0"))
        requested_start_indexes.append(start)
        if start == 0:
            return httpx.Response(200, json={"total_results": 45, "items": page_one})
        return httpx.Response(200, json={"total_results": 45, "items": page_two})

    monkeypatch.setattr(companies_house, "companies_house_client", _mock_client_factory(handler))
    result = await companies_house._fetch_company_psc("07463956")

    assert result.total == 45
    assert len(result.psc) == 45
    assert requested_start_indexes == [0, 25]


@pytest.mark.asyncio
async def test_psc_raises_on_empty_page_before_total_reached(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params.get("start_index", "0"))
        if start == 0:
            return httpx.Response(200, json={"total_results": 5, "items": [_psc_item("A"), _psc_item("B")]})
        return httpx.Response(200, json={"total_results": 5, "items": []})

    monkeypatch.setattr(companies_house, "companies_house_client", _mock_client_factory(handler))

    with pytest.raises(ToolError) as exc_info:
        await companies_house._fetch_company_psc("07463956")

    payload = parse_error_payload(str(exc_info.value))
    assert payload is not None, f"error message did not parse as a FleetErrorPayload: {exc_info.value}"
    assert payload.error_category == "transient"
    assert payload.is_retryable is True


@pytest.mark.asyncio
async def test_psc_raises_on_final_count_mismatch(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params.get("start_index", "0"))
        if start == 0:
            return httpx.Response(200, json={"total_results": 3, "items": [_psc_item("A"), _psc_item("B")]})
        return httpx.Response(200, json={"total_results": 3, "items": [_psc_item("C"), _psc_item("D")]})

    monkeypatch.setattr(companies_house, "companies_house_client", _mock_client_factory(handler))

    with pytest.raises(ToolError) as exc_info:
        await companies_house._fetch_company_psc("07463956")

    payload = parse_error_payload(str(exc_info.value))
    assert payload is not None, f"error message did not parse as a FleetErrorPayload: {exc_info.value}"
    assert payload.error_category == "transient"
    assert payload.is_retryable is True
