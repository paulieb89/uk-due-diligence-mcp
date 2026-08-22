# Officer Appointments & Related-Company Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an `officer_appointments(officer_id)` tool that returns a person's full Companies House appointment history (current + historic), and expose `officer_id` on `company_officers` results so an agent can chain the two calls to discover companies not named anywhere in the prompt.

**Architecture:** Two new Pydantic models (`OfficerAppointment`, `OfficerAppointmentsResult`) plus one new field (`CompanyOfficer.officer_id`) in `models.py`. One new paginated fetch helper (`_fetch_officer_appointments`) and one new extraction helper (`_officer_id_from_links`) in `companies_house.py`, alongside the existing `company_officers`/`company_psc` code it's adjacent to. One new tool + one new resource registration, following the exact dual-registration pattern every other endpoint in that file already uses.

**Tech Stack:** Python 3.12, FastMCP v3, Pydantic v2, httpx (`httpx.MockTransport` for offline tests), pytest + pytest-asyncio, `uv`.

**Spec:** `docs/superpowers/specs/2026-08-22-officer-appointments-design.md`

## Global Constraints

- No `related_companies` aggregation tool — only the atomic `officer_appointments` primitive ships in this plan.
- No auto-populated `appointment_count` / `high_appointment_count_flag` — they stay `None`; `company_officers` makes no extra upstream calls.
- No `include_resigned` toggle on `officer_appointments` — it always returns full history (current + historic), no filtering.
- No interpretation of `company_status`/counts into a derived health/risk field — pass upstream facts through verbatim.
- `date_of_birth` must default to `{}` when absent upstream, never raise.
- Pagination must be exact: `len(appointments)` must equal upstream `total_results` when all pages succeed. The loop terminates on `start_index >= total_results` (or an empty page), never on "this page came back shorter than requested" — a short page is not reliable evidence of completion. **CH clamps `items_per_page` to 50 on the `/officers/{id}/appointments` endpoint regardless of the requested value** (confirmed live: requesting 100 still returns `items_per_page: 50` in the response) — this is why a real officer with >50 appointments needs multiple requests, but it is not itself the termination signal; `total_results` is.
- `inactive_count` (and any other upstream count whose exact categorization logic hasn't been independently verified against per-appointment data) is documented as an unverified upstream value, not asserted to mean anything beyond what Companies House itself reports.
- Error handling reuses the existing `_request_with_retry` → fleet error taxonomy as-is; no special-casing.

---

## Task 1: Models

**Files:**
- Modify: `models.py` (add `OfficerAppointment`, `OfficerAppointmentsResult` after `CompanyOfficersResult`; add `officer_id` field to `CompanyOfficer`)
- Test: `tests/test_officer_appointments.py` (new file)

**Interfaces:**
- Produces: `OfficerAppointment(company_number, company_name, company_status, officer_role, appointed_on, resigned_on, nationality, country_of_residence, address: dict, links: dict)`, `OfficerAppointmentsResult(officer_id: str, name, date_of_birth: dict, total: int, active_count, resigned_count, inactive_count, appointments: list[OfficerAppointment])`, `CompanyOfficer.officer_id: str | None`. Later tasks construct these types by these exact names.

- [ ] **Step 1: Write the failing test**

Create `tests/test_officer_appointments.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_officer_appointments.py -v`
Expected: FAIL — `ImportError: cannot import name 'OfficerAppointment' from 'models'` (and `officer_id` not a valid field on `CompanyOfficer`).

- [ ] **Step 3: Write minimal implementation**

In `models.py`, find the end of `CompanyOfficersResult` (the class ends just before `class CompanyPSCEntry(BaseModel):`). Insert these two new classes between them:

```python
class OfficerAppointment(BaseModel):
    """A single appointment from an officer's full appointment history."""

    model_config = BASE_CFG

    company_number: str | None = Field(
        None, description="Companies House company number for this appointment."
    )
    company_name: str | None = Field(
        None, description="Company name as recorded at CH for this appointment."
    )
    company_status: str | None = Field(
        None,
        description=(
            "Company status at the time of lookup (e.g. 'active', 'liquidation', "
            "'dissolved'), as returned upstream. Companies House does not "
            "auto-resign directors on insolvency, so resigned_on may be null "
            "even when company_status shows the company is no longer trading — "
            "check company_status, not just resigned_on, to assess a relationship."
        ),
    )
    officer_role: str | None = Field(
        None, description="Officer role at this company (e.g. 'director', 'secretary')."
    )
    appointed_on: str | None = Field(
        None, description="Date of appointment (ISO YYYY-MM-DD)."
    )
    resigned_on: str | None = Field(
        None, description="Date of resignation, or null if not resigned."
    )
    nationality: str | None = Field(None, description="Declared nationality.")
    country_of_residence: str | None = Field(
        None, description="Declared country of residence."
    )
    address: dict[str, Any] = Field(
        default_factory=dict, description="Officer correspondence address for this appointment."
    )
    links: dict[str, Any] = Field(
        default_factory=dict,
        description="Upstream relational links, e.g. links.company = '/company/{number}'.",
    )


class OfficerAppointmentsResult(BaseModel):
    """A person's full appointment history across companies, by officer ID."""

    model_config = BASE_CFG

    officer_id: str = Field(..., description="Companies House officer ID.")
    name: str | None = Field(None, description="Officer name as recorded at CH.")
    date_of_birth: dict[str, Any] = Field(
        default_factory=dict,
        description="Partial date of birth (month/year), or empty if not disclosed upstream.",
    )
    total: int = Field(..., description="Total appointments returned.")
    active_count: int | None = Field(
        None, description="Upstream count of active appointments, or null if not provided."
    )
    resigned_count: int | None = Field(
        None, description="Upstream count of resigned appointments, or null if not provided."
    )
    inactive_count: int | None = Field(
        None,
        description=(
            "Upstream count of appointments Companies House categorizes as "
            "'inactive', passed through as-is. Exact categorization semantics "
            "have not been independently verified against per-appointment "
            "data — treat as an unverified upstream fact, not a derived signal, "
            "or null if not provided."
        ),
    )
    appointments: list[OfficerAppointment] = Field(
        default_factory=list, description="Every appointment, current and historic."
    )
```

In the same file, inside `class CompanyOfficer(BaseModel):`, find:

```python
    date_of_birth: dict[str, Any] = Field(
        default_factory=dict,
        description="Partial date of birth (month/year) as returned by CH.",
    )
    appointment_count: int | None = Field(
```

Replace with:

```python
    date_of_birth: dict[str, Any] = Field(
        default_factory=dict,
        description="Partial date of birth (month/year) as returned by CH.",
    )
    officer_id: str | None = Field(
        None,
        description=(
            "Companies House officer ID, extracted from links.officer.appointments. "
            "Pass to officer_appointments to discover this person's full appointment "
            "history across companies, including dissolved or insolvent ones."
        ),
    )
    appointment_count: int | None = Field(
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_officer_appointments.py -v`
Expected: 3 passed (the other imports in the test file, e.g. `companies_house`, `Client`, `mcp`, are unused by these three tests but must still import cleanly — confirm no `ImportError` from the file-level imports).

- [ ] **Step 5: Commit**

```bash
git add models.py tests/test_officer_appointments.py
git commit -m "feat: add OfficerAppointment models and CompanyOfficer.officer_id field"
```

---

## Task 2: officer_id extraction on company_officers

**Files:**
- Modify: `companies_house.py` (add `_officer_id_from_links` helper; wire into `_fetch_company_officers`)
- Test: `tests/test_officer_appointments.py`

**Interfaces:**
- Consumes: `CompanyOfficer.officer_id` field from Task 1.
- Produces: `_officer_id_from_links(links: dict[str, Any]) -> str | None` — module-level helper in `companies_house.py`. `_fetch_company_officers` now populates `officer_id` on every returned `CompanyOfficer`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_officer_appointments.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_officer_appointments.py -v`
Expected: FAIL — `AttributeError: module 'companies_house' has no attribute '_officer_id_from_links'`.

- [ ] **Step 3: Write minimal implementation**

In `companies_house.py`, add the helper after `_truncate_natures` (before the `# Tool registration` section comment):

```python
def _officer_id_from_links(links: dict[str, Any]) -> str | None:
    """Extract the officer ID from an officer's links.officer.appointments URL.

    Distinct from disqualified.py's `_extract_officer_id`, which reads a
    different link shape (the `self` link's tail). Here the officer ID is
    the path segment between `/officers/` and `/appointments`, e.g.
    "/officers/EAoJ81mtThuKM5KmSuO5U1RNLHs/appointments" ->
    "EAoJ81mtThuKM5KmSuO5U1RNLHs".
    """
    officer_links = (links or {}).get("officer")
    appointments_link = officer_links.get("appointments") if isinstance(officer_links, dict) else None
    if not appointments_link:
        return None
    parts = [p for p in str(appointments_link).split("/") if p]
    if len(parts) >= 2 and parts[0] == "officers":
        return parts[1]
    return None
```

In `_fetch_company_officers`, find:

```python
    officers = [
        CompanyOfficer(
            name=raw.get("name"),
            officer_role=raw.get("officer_role"),
            appointed_on=raw.get("appointed_on"),
            resigned_on=raw.get("resigned_on"),
            nationality=raw.get("nationality"),
            country_of_residence=raw.get("country_of_residence"),
            occupation=raw.get("occupation"),
            date_of_birth=raw.get("date_of_birth") or {},
            appointment_count=None,
            address=raw.get("address") or {},
            links=raw.get("links") or {},
        )
        for raw in raw_items
    ]
```

Replace with:

```python
    officers = [
        CompanyOfficer(
            name=raw.get("name"),
            officer_role=raw.get("officer_role"),
            appointed_on=raw.get("appointed_on"),
            resigned_on=raw.get("resigned_on"),
            nationality=raw.get("nationality"),
            country_of_residence=raw.get("country_of_residence"),
            occupation=raw.get("occupation"),
            date_of_birth=raw.get("date_of_birth") or {},
            officer_id=_officer_id_from_links(raw.get("links") or {}),
            appointment_count=None,
            address=raw.get("address") or {},
            links=raw.get("links") or {},
        )
        for raw in raw_items
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_officer_appointments.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add companies_house.py tests/test_officer_appointments.py
git commit -m "feat: extract officer_id from company_officers links"
```

---

## Task 3: officer_appointments fetch helper with pagination

**Files:**
- Modify: `companies_house.py` (add `_fetch_officer_appointments`)
- Test: `tests/test_officer_appointments.py`

**Interfaces:**
- Consumes: `OfficerAppointment`, `OfficerAppointmentsResult` from Task 1; `_request_with_retry`, `companies_house_client` from `http_client`.
- Produces: `async def _fetch_officer_appointments(officer_id: str) -> OfficerAppointmentsResult` — the shared helper Task 4's tool and resource both call.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_officer_appointments.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_officer_appointments.py -v`
Expected: FAIL — `AttributeError: module 'companies_house' has no attribute '_fetch_officer_appointments'`.

- [ ] **Step 3: Write minimal implementation**

In `companies_house.py`, update the models import to include the two new types:

```python
from models import (
    CompanyAccountsSummary,
    CompanyConfirmationStatementSummary,
    CompanyOfficer,
    CompanyOfficersResult,
    CompanyProfile,
    CompanyPSCEntry,
    CompanyPSCResult,
    CompanySearchItem,
    CompanySearchResult,
    OfficerAppointment,
    OfficerAppointmentsResult,
)
```

Add the fetch helper after `_fetch_company_psc` (before the `# Tool registration` section that follows it):

```python
async def _fetch_officer_appointments(officer_id: str) -> OfficerAppointmentsResult:
    oid = officer_id.strip()
    raw_items: list[dict[str, Any]] = []
    top: dict[str, Any] = {}
    async with companies_house_client() as client:
        start_index = 0
        # CH clamps items_per_page to 50 on this endpoint regardless of the
        # requested value (confirmed live: requesting 100 still returns
        # items_per_page=50 in the response) — unlike /officers, which
        # allows up to 100. That cap is why a large officer needs multiple
        # requests, but it is NOT the termination signal: a page coming
        # back shorter than requested is not reliable evidence of
        # completion. The loop paginates strictly to total_results.
        requested_page_size = 50
        while True:
            resp = await _request_with_retry(
                client,
                "GET",
                f"/officers/{oid}/appointments",
                params={"items_per_page": requested_page_size, "start_index": start_index},
            )
            data = resp.json()
            if not top:
                top = data
            page_items = data.get("items", []) or []
            raw_items.extend(page_items)
            total_results = int(data.get("total_results", len(raw_items)) or 0)
            start_index += len(page_items)
            if not page_items or start_index >= total_results:
                break

    appointments = [
        OfficerAppointment(
            company_number=(raw.get("appointed_to") or {}).get("company_number"),
            company_name=(raw.get("appointed_to") or {}).get("company_name"),
            company_status=(raw.get("appointed_to") or {}).get("company_status"),
            officer_role=raw.get("officer_role"),
            appointed_on=raw.get("appointed_on"),
            resigned_on=raw.get("resigned_on"),
            nationality=raw.get("nationality"),
            country_of_residence=raw.get("country_of_residence"),
            address=raw.get("address") or {},
            links=raw.get("links") or {},
        )
        for raw in raw_items
    ]

    return OfficerAppointmentsResult(
        officer_id=oid,
        name=top.get("name"),
        date_of_birth=top.get("date_of_birth") or {},
        total=int(top.get("total_results", len(appointments)) or 0),
        active_count=top.get("active_count"),
        resigned_count=top.get("resigned_count"),
        inactive_count=top.get("inactive_count"),
        appointments=appointments,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_officer_appointments.py -v`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add companies_house.py tests/test_officer_appointments.py
git commit -m "feat: add _fetch_officer_appointments with CH 50-item-page pagination"
```

---

## Task 4: Tool + resource registration

**Files:**
- Modify: `companies_house.py` (`register_tools`, `register_resources`)
- Test: `tests/test_officer_appointments.py`

**Interfaces:**
- Consumes: `_fetch_officer_appointments` from Task 3.
- Produces: registered MCP tool `officer_appointments(officer_id)` and resource `officer://{officer_id}/appointments`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_officer_appointments.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_officer_appointments.py -v`
Expected: FAIL — `ToolError: Unknown tool: officer_appointments` (tool not registered yet).

- [ ] **Step 3: Write minimal implementation**

In `companies_house.py`'s `register_tools`, add after the `company_psc` tool (section `4.`), before the closing of `register_tools`:

```python
    # ------------------------------------------------------------------ #
    # 5. officer_appointments
    # ------------------------------------------------------------------ #
    @mcp.tool(
        name="officer_appointments",
        annotations={
            "title": "Get Officer Appointment History",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def officer_appointments(
        officer_id: Annotated[str, Field(description="Companies House officer ID. Returned as officer_id on entries from company_officers.", min_length=1, max_length=100)],
    ) -> OfficerAppointmentsResult:
        """Fetch a person's full company appointment history by officer ID.

        Returns every appointment — current and historic — with each
        company's number, name, status, role, and appointment/resignation
        dates. Use company_officers first to find an officer_id, then this
        tool to discover other companies that person has been a director
        or secretary of, including dissolved or insolvent ones not
        mentioned anywhere else. Always returns full history; there is no
        current-only filter, since historical discovery is the point.
        """
        return await _fetch_officer_appointments(officer_id)
```

In `register_resources`, add after the `company_psc_resource`:

```python
    @mcp.resource(
        "officer://{officer_id}/appointments",
        name="officer_appointments",
        description=(
            "Full appointment history for a Companies House officer ID: every "
            "company they've been a director/secretary of, current and historic, "
            "with company status, role, and dates."
        ),
        mime_type="application/json",
    )
    async def officer_appointments_resource(officer_id: str) -> str:
        result = await _fetch_officer_appointments(officer_id)
        return result.model_dump_json()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_officer_appointments.py -v`
Expected: 11 passed.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: all tests pass (16 pre-existing + 11 new = 27).

- [ ] **Step 6: Commit**

```bash
git add companies_house.py tests/test_officer_appointments.py
git commit -m "feat: register officer_appointments tool and resource"
```

---

## Task 5: Live dogfooding

**Files:** none (verification only)

**Interfaces:**
- Consumes: the finished `officer_appointments` tool and `officer_id`-bearing `company_officers` from Tasks 1–4, against real Companies House data via the local `.env` (`CH_API_KEY` already present per prior session).

- [ ] **Step 1: Reproduce the TAS -> Gareth -> MEL/FXF chain end to end**

Run:

```bash
uv run python -c "
import asyncio
from dotenv import load_dotenv
load_dotenv()
import companies_house as ch

async def main():
    officers = await ch._fetch_company_officers('06333469')
    gareth = next(o for o in officers.officers if 'DAVIES' in (o.name or '') and 'Gareth' in (o.name or ''))
    print('officer_id:', gareth.officer_id)
    assert gareth.officer_id, 'officer_id was not populated on the live company_officers result'

    appts = await ch._fetch_officer_appointments(gareth.officer_id)
    print('total:', appts.total, 'active:', appts.active_count, 'resigned:', appts.resigned_count, 'inactive:', appts.inactive_count)
    for a in appts.appointments:
        print(' -', a.company_number, a.company_name, a.company_status, 'resigned_on=', a.resigned_on)

    by_number = {a.company_number: a for a in appts.appointments}
    assert '09118548' in by_number, 'MEL Precision not discovered via officer_appointments'
    assert by_number['09118548'].company_status == 'liquidation'
    print('ACCEPTANCE CASE PASSED: TAS -> Gareth Davies -> MEL Precision discovered, status=liquidation')

asyncio.run(main())
"
```

Expected output includes `ACCEPTANCE CASE PASSED` and lists TAS Engineering (active), MEL Precision (liquidation), and FXF Designs Ltd (dissolved) among the appointments — matching the spec's documented live probe.

- [ ] **Step 2: Confirm pagination-completeness against a real officer with many appointments (optional but recommended)**

If you know a real officer_id with >50 appointments (a serial/nominee director), re-run the same pattern against it and confirm `len(appointments) == total`. If none is readily known, this is covered adequately by the mocked test in Task 3 and may be skipped — note in the PR description that this specific case wasn't live-verified.

- [ ] **Step 3: No commit** — this task is verification only; proceed to Task 6.

---

## Task 6: Open the PR

**Files:** none (git/GitHub operations only)

- [ ] **Step 1: Run the full test suite one last time**

Run: `uv run pytest -q`
Expected: all tests pass.

- [ ] **Step 2: Push the branch**

```bash
git push -u origin feat/officer-appointments
```

- [ ] **Step 3: Open the PR**

```bash
gh pr create --title "feat: officer appointment history and related-company discovery" --body "$(cat <<'EOF'
## Summary

Adds the officer_appointments primitive from docs/superpowers/specs/2026-08-22-officer-appointments-design.md:

- company_officers now exposes officer_id on each officer (extracted from links.officer.appointments)
- new officer_appointments(officer_id) tool + officer://{officer_id}/appointments resource: full appointment history, current + historic, no filtering — historical discovery is the point
- company_status is preserved per appointment as a raw upstream fact (no derived risk/health interpretation); resigned_on can be null even for a company in liquidation, since CH doesn't auto-resign directors on insolvency
- pagination correctly handles CH's undocumented 50-item-per-page cap on this endpoint (confirmed live — requesting 100 is silently clamped to 50)

Explicitly out of scope (per spec): no related_companies aggregation tool, no auto-populated appointment_count/high_appointment_count_flag on company_officers, no include_resigned toggle on the new tool.

## Verification

- Unit tests: `uv run pytest -q`
- Live dogfooding: starting only from TAS Engineering (06333469), chaining company_officers -> officer_appointments discovers MEL Precision (09118548, liquidation) and FXF Designs Ltd (12609854, dissolved) — neither supplied in the input — reproducing the spec's acceptance case against real Companies House data.

## Test plan

- [x] uv run pytest -q
- [x] Live chain: TAS -> Gareth Davies -> MEL Precision/FXF Designs discovered

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 4: Report the PR URL back to the user.**
