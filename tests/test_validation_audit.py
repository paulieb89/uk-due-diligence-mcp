"""Regression tests for the 2026-11-17 validation audit (findings D1-D4).

Each test pins one way the server used to return a confident, wrong answer:

- D1: company_search sent status/type filters that /search/companies ignores,
  so a search for active companies returned liquidated ones.
- D2: a profile with no accounts data reported accounts as not overdue.
- D3: a company number with a stray space was reported as not found, and a
  malformed one was indistinguishable from an absent one.
- D4: an invalid sanctions entity_type was silently dropped, screening both.
"""

from __future__ import annotations

import httpx
import pytest
import pytest_asyncio
from fastmcp import Client
from fastmcp.exceptions import ToolError
from mcpfleet_obs import parse_error_payload

import companies_house
from server import mcp

CH_BASE = "https://api.company-information.service.gov.uk"


@pytest_asyncio.fixture
async def mcp_client():
    async with Client(mcp) as c:
        yield c


def _use_ch(monkeypatch, handler) -> None:
    def make() -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=CH_BASE, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(companies_house, "companies_house_client", make)


def _item(n: int, status: str, ctype: str = "ltd") -> dict:
    return {
        "company_number": f"{n:08d}",
        "title": f"COMPANY {n}",
        "company_status": status,
        "company_type": ctype,
    }


class _Upstream:
    """A /search/companies stand-in that, like the real one, ignores status/type."""

    def __init__(self, items: list[dict]):
        self.items = items
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/search/companies"
        self.requests.append(request)
        start = int(request.url.params.get("start_index", 0))
        size = int(request.url.params.get("items_per_page", 20))
        return httpx.Response(200, json={
            "total_results": len(self.items),
            "items": self.items[start:start + size],
        })


# ---------------------------------------------------------------------------
# D1 — search filters are applied by this server, and reported
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_d1_status_filter_returns_only_matching_companies(monkeypatch, mcp_client):
    upstream = _Upstream([_item(i, "liquidation" if i % 3 else "active") for i in range(30)])
    _use_ch(monkeypatch, upstream)

    result = await mcp_client.call_tool(
        "company_search", {"query": "carillion", "company_status": "active", "items_per_page": 5},
    )
    data = result.structured_content

    assert [i["company_status"] for i in data["items"]] == ["active"] * 5
    assert data["filters_applied"] == {"company_status": "active"}
    assert data["upstream_total"] == 30
    assert data["filtered_returned"] == 5
    assert data["is_partial"] is False
    # The page ended on the 5th match (upstream index 12); the next page resumes after it.
    assert data["next_start_index"] == 13
    assert data["has_more"] is True


@pytest.mark.asyncio
async def test_d1_filter_pages_upstream_until_enough_matches(monkeypatch, mcp_client):
    # Matches are sparse: one active company every 60, so 3 matches span 3 upstream pages.
    upstream = _Upstream([_item(i, "active" if i % 60 == 59 else "dissolved") for i in range(400)])
    _use_ch(monkeypatch, upstream)

    result = await mcp_client.call_tool(
        "company_search", {"query": "xx", "company_status": "active", "items_per_page": 3},
    )
    data = result.structured_content

    assert [i["company_number"] for i in data["items"]] == ["00000059", "00000119", "00000179"]
    assert len(upstream.requests) == 2
    assert data["is_partial"] is False
    assert data["next_start_index"] == 180


@pytest.mark.asyncio
async def test_d1_cap_hit_marks_result_partial(monkeypatch, mcp_client):
    upstream = _Upstream([_item(i, "dissolved") for i in range(2000)])
    _use_ch(monkeypatch, upstream)

    result = await mcp_client.call_tool(
        "company_search", {"query": "xx", "company_status": "active", "items_per_page": 10},
    )
    data = result.structured_content

    assert data["items"] == []
    assert data["is_partial"] is True
    assert data["partial_reason"]
    assert data["upstream_scanned"] == companies_house.FILTER_SCAN_CAP
    assert data["next_start_index"] == companies_house.FILTER_SCAN_CAP
    assert data["has_more"] is True


@pytest.mark.asyncio
async def test_d1_exhausted_upstream_is_complete_not_partial(monkeypatch, mcp_client):
    upstream = _Upstream([_item(i, "active" if i == 3 else "dissolved") for i in range(8)])
    _use_ch(monkeypatch, upstream)

    result = await mcp_client.call_tool(
        "company_search", {"query": "xx", "company_status": "active", "items_per_page": 10},
    )
    data = result.structured_content

    assert [i["company_number"] for i in data["items"]] == ["00000003"]
    assert data["is_partial"] is False
    assert data["has_more"] is False
    assert data["next_start_index"] is None


@pytest.mark.asyncio
async def test_d1_type_filter_and_status_filter_combine(monkeypatch, mcp_client):
    upstream = _Upstream([
        _item(1, "active", "ltd"),
        _item(2, "active", "registered-overseas-entity"),
        _item(3, "removed", "registered-overseas-entity"),
    ])
    _use_ch(monkeypatch, upstream)

    result = await mcp_client.call_tool("company_search", {
        "query": "xx", "company_status": "active", "company_type": "registered-overseas-entity",
    })
    data = result.structured_content

    assert [i["company_number"] for i in data["items"]] == ["00000002"]
    assert data["filters_applied"] == {
        "company_status": "active", "company_type": "registered-overseas-entity",
    }


@pytest.mark.asyncio
async def test_d1_unfiltered_search_is_one_upstream_call(monkeypatch, mcp_client):
    upstream = _Upstream([_item(i, "active") for i in range(50)])
    _use_ch(monkeypatch, upstream)

    result = await mcp_client.call_tool("company_search", {"query": "xx", "items_per_page": 20})
    data = result.structured_content

    assert len(upstream.requests) == 1
    assert data["filters_applied"] == {}
    assert data["returned"] == data["filtered_returned"] == 20
    assert data["upstream_total"] == 50
    assert data["next_start_index"] == 20


@pytest.mark.asyncio
@pytest.mark.parametrize("args", [
    {"company_status": "live"},
    {"company_type": "limited"},
])
async def test_d1_unknown_filter_value_is_rejected(monkeypatch, mcp_client, args):
    upstream = _Upstream([])
    _use_ch(monkeypatch, upstream)

    with pytest.raises(ToolError):
        await mcp_client.call_tool("company_search", {"query": "xx", **args})
    assert upstream.requests == []


# ---------------------------------------------------------------------------
# D2 — missing accounts data is unknown, not "not overdue"
# ---------------------------------------------------------------------------

def _profile_handler(profile: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/charges"):
            return httpx.Response(200, json={"items": [], "total_count": 0})
        return httpx.Response(200, json=profile)

    return handler


@pytest.mark.asyncio
async def test_d2_absent_accounts_and_confirmation_blocks_are_unknown(monkeypatch):
    _use_ch(monkeypatch, _profile_handler({
        "company_number": "OE000002", "company_name": "X", "type": "registered-overseas-entity",
    }))

    profile = await companies_house._fetch_company_profile("OE000002")

    assert profile.accounts.overdue is None
    assert profile.confirmation_statement.overdue is None


@pytest.mark.asyncio
async def test_d2_block_present_but_flag_absent_is_unknown(monkeypatch):
    _use_ch(monkeypatch, _profile_handler({
        "company_number": "00000001",
        "accounts": {"next_due": "2027-01-01"},
        "confirmation_statement": {"next_due": "2027-01-01"},
    }))

    profile = await companies_house._fetch_company_profile("00000001")

    assert profile.accounts.overdue is None
    assert profile.confirmation_statement.overdue is None


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", [True, False])
async def test_d2_explicit_flags_are_preserved(monkeypatch, flag):
    _use_ch(monkeypatch, _profile_handler({
        "company_number": "00000001",
        "accounts": {"overdue": flag},
        "confirmation_statement": {"overdue": flag},
    }))

    profile = await companies_house._fetch_company_profile("00000001")

    assert profile.accounts.overdue is flag
    assert profile.confirmation_statement.overdue is flag


# ---------------------------------------------------------------------------
# D3 — malformed numbers are validation errors; padding is forgiving
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    (" 3782379", "03782379"),
    ("3782379 ", "03782379"),
    ("03782379", "03782379"),
    ("sc123456", "SC123456"),
    ("SC12345", "SC012345"),
    ("OE000002", "OE000002"),
])
def test_d3_normalisation(raw, expected):
    assert companies_house._normalise_company_number(raw) == expected


@pytest.mark.parametrize("raw", ["ABC", "123456789", "03-782379", "SC 12345", "", "   "])
def test_d3_malformed_number_is_a_validation_error(raw):
    with pytest.raises(ToolError) as exc_info:
        companies_house._normalise_company_number(raw)

    payload = parse_error_payload(str(exc_info.value))
    assert payload is not None
    assert payload.error_category == "validation"
    assert "8" in payload.description


@pytest.mark.asyncio
async def test_d3_leading_space_reaches_the_right_company(monkeypatch, mcp_client):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path.endswith("/charges"):
            return httpx.Response(200, json={"items": [], "total_count": 0})
        return httpx.Response(200, json={"company_number": "03782379", "company_name": "CARILLION PLC"})

    _use_ch(monkeypatch, handler)

    result = await mcp_client.call_tool("company_profile", {"company_number": " 3782379"})

    assert result.structured_content["company_name"] == "CARILLION PLC"
    assert seen[0] == "/company/03782379"


# ---------------------------------------------------------------------------
# D4 — sanctions entity_type is a closed set
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_d4_invalid_entity_type_is_rejected(mcp_client):
    with pytest.raises(ToolError):
        await mcp_client.call_tool("sanctions_screen", {"name": "Rosneft", "entity_type": "company"})


@pytest.mark.asyncio
async def test_d4_schema_lists_the_allowed_entity_types(mcp_client):
    tools = {t.name: t for t in await mcp_client.list_tools()}
    schema = tools["sanctions_screen"].inputSchema["properties"]["entity_type"]

    assert "person" in str(schema) and "entity" in str(schema)
    assert "enum" in str(schema)
