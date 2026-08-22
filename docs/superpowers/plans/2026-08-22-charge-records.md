# Companies House Charge Records Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `company_charges(company_number)` tool returning the complete, structured Companies House charge history for a company (every charge, current and historic, with full status/dates/security detail), and refactor `company_profile.has_charges` to derive from the same source instead of running a separate, semantically-narrower paginated check.

**Architecture:** Six new Pydantic models (`ChargeParticulars`, `ChargeSecuredDetails`, `ChargeClassification`, `ChargePersonEntitled`, `ChargeTransaction`, `CompanyCharge`) plus one result wrapper (`CompanyChargesResult`) in `models.py`. One new paginated fetch helper (`_fetch_company_charges`) in `companies_house.py`, following the exact shape of `_fetch_officer_appointments`. One new tool + one new resource, dual-registered like every other endpoint in that file. `_fetch_company_profile`'s existing charges-check is refactored to call the new helper instead of duplicating the pagination loop, while preserving its existing fault-tolerant failure handling exactly.

**Tech Stack:** Python 3.12, FastMCP v3, Pydantic v2, httpx (`httpx.MockTransport` for offline tests), pytest + pytest-asyncio, `uv`.

**Spec:** `docs/superpowers/specs/2026-08-22-charge-records-design.md`

## Global Constraints

- One primitive: `company_charges(company_number)`. No filing history tool, no accounts/document ingestion in this PR — separate issues.
- Preserve upstream structure — `particulars`, `secured_details`, `persons_entitled`, `transactions`, `classification` stay their own nested shapes, not flattened into scalars. Every field on `ChargeParticulars`/`ChargeSecuredDetails` is optional — field presence varies by charge age/type (confirmed live: pre-2006 debentures have no boolean flags; MR01-style charges often have no free text).
- `charge_code` is optional; `charge_number` (always present) is the reliable identifier.
- No separate "satisfactions/releases" model — satisfaction is `satisfied_on` (top-level date) plus a `transactions[]` entry with `filing_type == "charge-satisfaction"`, exactly as upstream represents it.
- Pagination paginates strictly to `start_index >= total_count` — **the field is `total_count` here, not `total_results`** (different from the officer-appointments endpoints) — never on "this page came back shorter than requested."
- `has_charges` truth table (must hold exactly, including the failure case which is a PR #4 regression protection):

  | Charges retrieved | `has_charges` |
  |---|---|
  | All fully-satisfied | `False` |
  | At least one outstanding | `True` |
  | At least one part-satisfied (none outstanding) | `True` |
  | Empty (no charges at all) | `False` |
  | Charges endpoint call fails | `None` |

- `_fetch_company_charges` propagates upstream failures normally (raises), matching every other fetch helper in the file. `_fetch_company_profile` is the one place that catches that failure and degrades to `has_charges=None` while still returning the rest of the profile — this existing PR #4 behavior must survive the refactor unchanged.
- No derived risk/health interpretation of what a charge means — pass upstream facts through.
- Not literally "lossless" — a typed field projection, not a raw-JSON passthrough. `unfiltered_count` is retained (a real upstream fact, free to keep) but no `raw` blob is added.
- Error handling reuses the existing `_request_with_retry` → fleet error taxonomy as-is for `company_charges` itself; no special-casing.

---

## Task 1: Models

**Files:**
- Modify: `models.py` (add `ChargeParticulars`, `ChargeSecuredDetails`, `ChargeClassification`, `ChargePersonEntitled`, `ChargeTransaction`, `CompanyCharge`, `CompanyChargesResult` after `CompanyPSCResult`, before the `# Disqualified directors` section)
- Test: `tests/test_company_charges.py` (new file)

**Interfaces:**
- Produces: `ChargeParticulars(type, description, contains_floating_charge, contains_fixed_charge, floating_charge_covers_all, contains_negative_pledge)`, `ChargeSecuredDetails(type, description)`, `ChargeClassification(type, description)`, `ChargePersonEntitled(name)`, `ChargeTransaction(filing_type, delivered_on, links: dict)`, `CompanyCharge(charge_number: int, charge_code, status, classification, created_on, delivered_on, satisfied_on, particulars, secured_details, persons_entitled: list[ChargePersonEntitled], transactions: list[ChargeTransaction], links: dict)`, `CompanyChargesResult(company_number: str, total_count: int, unfiltered_count, satisfied_count, part_satisfied_count, charges: list[CompanyCharge])`. Later tasks construct these types by these exact names.

- [ ] **Step 1: Write the failing test**

Create `tests/test_company_charges.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_company_charges.py -v`
Expected: FAIL — `ImportError: cannot import name 'ChargeParticulars' from 'models'`.

- [ ] **Step 3: Write minimal implementation**

In `models.py`, find the end of `CompanyPSCResult` (just before
`# =============================================================================`
`# Disqualified directors` section). Insert these new classes between
`CompanyPSCResult` and that section header:

```python
class ChargeParticulars(BaseModel):
    """What a charge covers — free text and/or boolean flags.

    Field presence varies by charge age/type: pre-2006 debentures often
    carry only type/description with no boolean flags; MR01-style charges
    (2013+) often carry only the boolean flags with no free text. Every
    field is optional — do not assume any subset is always present.
    """

    model_config = BASE_CFG

    type: str | None = Field(
        None, description="Particulars entry type as returned by CH (e.g. 'brief-description', 'short-particulars')."
    )
    description: str | None = Field(
        None, description="Free-text description of what the charge covers, where filed."
    )
    contains_floating_charge: bool | None = Field(
        None, description="True if the charge includes a floating charge."
    )
    contains_fixed_charge: bool | None = Field(
        None, description="True if the charge includes a fixed charge."
    )
    floating_charge_covers_all: bool | None = Field(
        None, description="True if the floating charge covers the whole undertaking/all assets."
    )
    contains_negative_pledge: bool | None = Field(
        None, description="True if the charge includes a negative pledge (restricting further charges)."
    )


class ChargeSecuredDetails(BaseModel):
    """Free-text 'amount secured' description.

    Seen only on older/pre-2006 charge filings — absent on newer
    MR01-style charges, which express scope entirely through
    ChargeParticulars' boolean flags instead. Optional, not assume-present.
    """

    model_config = BASE_CFG

    type: str | None = Field(
        None, description="Secured-details entry type as returned by CH (e.g. 'amount-secured')."
    )
    description: str | None = Field(
        None, description="Free-text description of the amount/obligation secured."
    )


class ChargeClassification(BaseModel):
    """Charge type classification (e.g. 'A registered charge', 'Debenture')."""

    model_config = BASE_CFG

    type: str | None = Field(
        None, description="Classification type as returned by CH (e.g. 'charge-description')."
    )
    description: str | None = Field(
        None, description="Human-readable charge type (e.g. 'A registered charge', 'Debenture')."
    )


class ChargePersonEntitled(BaseModel):
    """A secured party named on a charge."""

    model_config = BASE_CFG

    name: str | None = Field(
        None, description="Name of the person or entity entitled under this charge, as filed."
    )


class ChargeTransaction(BaseModel):
    """A filing event against a charge (creation, satisfaction, etc.)."""

    model_config = BASE_CFG

    filing_type: str | None = Field(
        None, description="Filing type (e.g. 'create-charge-with-deed', 'charge-satisfaction')."
    )
    delivered_on: str | None = Field(
        None, description="Date this filing was delivered (ISO YYYY-MM-DD)."
    )
    links: dict[str, Any] = Field(
        default_factory=dict,
        description="Upstream relational links, e.g. links.filing = filing-history path.",
    )


class CompanyCharge(BaseModel):
    """A single Companies House charge (secured debt registration).

    Satisfaction is represented via satisfied_on and a transactions[]
    entry with filing_type == 'charge-satisfaction' — there is no
    separate 'release' concept upstream, so none is invented here.
    """

    model_config = BASE_CFG

    charge_number: int = Field(
        ..., description="Sequential charge number for this company. Always present, unlike charge_code."
    )
    charge_code: str | None = Field(
        None,
        description=(
            "Human-readable charge ID (e.g. '063334690005'). Absent on some "
            "older charges (e.g. pre-2006 debentures) — use charge_number "
            "as the reliable identifier."
        ),
    )
    status: str | None = Field(
        None,
        description="Charge status as returned upstream (e.g. 'outstanding', 'part-satisfied', 'fully-satisfied').",
    )
    classification: ChargeClassification | None = Field(
        None, description="Charge type classification."
    )
    created_on: str | None = Field(
        None, description="Date the charge was created (ISO YYYY-MM-DD)."
    )
    delivered_on: str | None = Field(
        None, description="Date the charge was delivered/registered (ISO YYYY-MM-DD)."
    )
    satisfied_on: str | None = Field(
        None, description="Date the charge was satisfied, or null if still live."
    )
    particulars: ChargeParticulars | None = Field(
        None, description="What the charge covers — free text and/or boolean flags, depending on charge age/type."
    )
    secured_details: ChargeSecuredDetails | None = Field(
        None, description="Free-text amount-secured description. Present only on some older charges."
    )
    persons_entitled: list[ChargePersonEntitled] = Field(
        default_factory=list, description="Secured parties named on this charge."
    )
    transactions: list[ChargeTransaction] = Field(
        default_factory=list,
        description=(
            "Filing events against this charge — creation, satisfaction, etc. "
            "Satisfaction is represented here (filing_type='charge-satisfaction') "
            "and via satisfied_on, not a separate 'release' concept."
        ),
    )
    links: dict[str, Any] = Field(
        default_factory=dict, description="Upstream relational links, e.g. links.self = charge record path."
    )


class CompanyChargesResult(BaseModel):
    """Complete charge history for a company."""

    model_config = BASE_CFG

    company_number: str = Field(..., description="Companies House company number.")
    total_count: int = Field(..., description="Total charges returned.")
    unfiltered_count: int | None = Field(
        None, description="Upstream unfiltered charge count, or null if not provided."
    )
    satisfied_count: int | None = Field(
        None, description="Upstream count of satisfied charges, or null if not provided."
    )
    part_satisfied_count: int | None = Field(
        None, description="Upstream count of part-satisfied charges, or null if not provided."
    )
    charges: list[CompanyCharge] = Field(
        default_factory=list, description="Every charge, current and historic."
    )


# =============================================================================
# Disqualified directors
# =============================================================================
```

Note: the trailing `# Disqualified directors` header block is already in
the file — this step only inserts the new classes immediately above it,
it does not duplicate the header.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_company_charges.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add models.py tests/test_company_charges.py
git commit -m "feat: add charge record models"
```

---

## Task 2: `_fetch_company_charges` fetch helper with pagination

**Files:**
- Modify: `companies_house.py` (add `_fetch_company_charges`)
- Test: `tests/test_company_charges.py`

**Interfaces:**
- Consumes: `CompanyCharge`, `CompanyChargesResult`, and the five sub-models from Task 1; `_request_with_retry`, `companies_house_client` from `http_client`.
- Produces: `async def _fetch_company_charges(company_number: str) -> CompanyChargesResult` — the shared helper Task 3's tool/resource and Task 4's `has_charges` refactor both call.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_company_charges.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_company_charges.py -v`
Expected: FAIL — `AttributeError: module 'companies_house' has no attribute '_fetch_company_charges'`.

- [ ] **Step 3: Write minimal implementation**

In `companies_house.py`, update the models import to include the charge types:

```python
from models import (
    ChargeClassification,
    ChargeParticulars,
    ChargePersonEntitled,
    ChargeSecuredDetails,
    ChargeTransaction,
    CompanyAccountsSummary,
    CompanyCharge,
    CompanyChargesResult,
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

Add a module-level constant near `HIGH_APPOINTMENT_COUNT`/`UK_JURISDICTIONS`
(top of the file, with the other risk-threshold constants):

```python
# Charge statuses that represent a live, not-fully-discharged security
# interest. Explicit set rather than "!= fully-satisfied", so an
# unrecognized future status from upstream isn't silently miscategorized
# as still-live.
LIVE_CHARGE_STATUSES = {"outstanding", "part-satisfied"}
```

Add the fetch helper after `_fetch_officer_appointments` (before the
`# Tool registration` section that follows it):

```python
def _map_charge(raw: dict[str, Any]) -> CompanyCharge:
    classification_raw = raw.get("classification")
    particulars_raw = raw.get("particulars")
    secured_details_raw = raw.get("secured_details")
    return CompanyCharge(
        charge_number=raw.get("charge_number"),
        charge_code=raw.get("charge_code"),
        status=raw.get("status"),
        classification=ChargeClassification(**classification_raw) if classification_raw else None,
        created_on=raw.get("created_on"),
        delivered_on=raw.get("delivered_on"),
        satisfied_on=raw.get("satisfied_on"),
        particulars=ChargeParticulars(**particulars_raw) if particulars_raw else None,
        secured_details=ChargeSecuredDetails(**secured_details_raw) if secured_details_raw else None,
        persons_entitled=[
            ChargePersonEntitled(name=p.get("name")) for p in (raw.get("persons_entitled") or [])
        ],
        transactions=[
            ChargeTransaction(
                filing_type=t.get("filing_type"),
                delivered_on=t.get("delivered_on"),
                links=t.get("links") or {},
            )
            for t in (raw.get("transactions") or [])
        ],
        links=raw.get("links") or {},
    )


async def _fetch_company_charges(company_number: str) -> CompanyChargesResult:
    raw_items: list[dict[str, Any]] = []
    top: dict[str, Any] = {}
    async with companies_house_client() as client:
        start_index = 0
        page_size = 100
        while True:
            resp = await _request_with_retry(
                client,
                "GET",
                f"/company/{company_number}/charges",
                params={"items_per_page": page_size, "start_index": start_index},
            )
            data = resp.json()
            if not top:
                top = data
            page_items = data.get("items", []) or []
            raw_items.extend(page_items)
            total_count = int(data.get("total_count", len(raw_items)) or 0)
            start_index += len(page_items)
            if not page_items or start_index >= total_count:
                break

    charges = [_map_charge(raw) for raw in raw_items]

    return CompanyChargesResult(
        company_number=company_number,
        total_count=int(top.get("total_count", len(charges)) or 0),
        unfiltered_count=top.get("unfiltered_count"),
        satisfied_count=top.get("satisfied_count"),
        part_satisfied_count=top.get("part_satisfied_count"),
        charges=charges,
    )
```

`_map_charge` is a module-level function (not inlined in a comprehension)
so the raw-item-to-model mapping has one implementation, readable and
testable on its own, rather than being buried inside the pagination loop.
Task 4's `has_charges` refactor doesn't call it directly — it calls
`_fetch_company_charges`, which uses `_map_charge` internally — but
factoring it out keeps that internal logic legible.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_company_charges.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add companies_house.py tests/test_company_charges.py
git commit -m "feat: add _fetch_company_charges with total_count-driven pagination"
```

---

## Task 3: Tool + resource registration

**Files:**
- Modify: `companies_house.py` (`register_tools`, `register_resources`)
- Test: `tests/test_company_charges.py`

**Interfaces:**
- Consumes: `_fetch_company_charges` from Task 2.
- Produces: registered MCP tool `company_charges(company_number)` and resource `company://{company_number}/charges`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_company_charges.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_company_charges.py -v`
Expected: FAIL — `ToolError: Unknown tool: company_charges` (tool not registered yet).

- [ ] **Step 3: Write minimal implementation**

In `companies_house.py`'s `register_tools`, add after the `officer_appointments`
tool (section `5.`), before the closing of `register_tools`:

```python
    # ------------------------------------------------------------------ #
    # 6. company_charges
    # ------------------------------------------------------------------ #
    @mcp.tool(
        name="company_charges",
        annotations={
            "title": "Get Company Charges",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def company_charges(
        company_number: Annotated[str, Field(description="Companies House company number (8 digits, e.g. '03782379'). Returned by company_search.", min_length=1, max_length=10)],
    ) -> CompanyChargesResult:
        """Fetch the complete Companies House charge history for a company.

        Returns every registered charge (secured debt) — current and
        historic — with status, dates, secured parties, and what each
        charge covers (fixed/floating/negative-pledge flags and any
        free-text particulars). Satisfaction is represented as
        satisfied_on plus a charge-satisfaction filing entry, not a
        separate 'release' record. company_profile.has_charges is a
        cheap True/False/unknown summary derived from this same data;
        use this tool when the specific charges matter, not just whether
        any exist.
        """
        return await _fetch_company_charges(_normalise_company_number(company_number))
```

In `register_resources`, add after `officer_appointments_resource`:

```python
    @mcp.resource(
        "company://{company_number}/charges",
        name="company_charges",
        description=(
            "Complete Companies House charge history for a company number: "
            "every registered charge, current and historic, with status, "
            "dates, secured parties, and what each charge covers."
        ),
        mime_type="application/json",
    )
    async def company_charges_resource(company_number: str) -> str:
        result = await _fetch_company_charges(_normalise_company_number(company_number))
        return result.model_dump_json()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_company_charges.py -v`
Expected: 8 passed.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: all tests pass (27 pre-existing + 8 new = 35).

- [ ] **Step 6: Commit**

```bash
git add companies_house.py tests/test_company_charges.py
git commit -m "feat: register company_charges tool and resource"
```

---

## Task 4: `has_charges` refactor

**Files:**
- Modify: `companies_house.py` (`_fetch_company_profile`)
- Test: `tests/test_company_charges.py`

**Interfaces:**
- Consumes: `_fetch_company_charges`, `LIVE_CHARGE_STATUSES` from Tasks 2–3.
- Produces: `_fetch_company_profile`'s `has_charges` now derives from `_fetch_company_charges`, covering the full truth table, with the existing endpoint-failure → `None` behavior preserved exactly.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_company_charges.py`. These tests exercise
`_fetch_company_profile` directly (already imported via `companies_house`);
add the profile-shaping helper and the five truth-table cases:

```python
def _profile_response(company_number="06333469"):
    return {
        "company_number": company_number,
        "company_name": "TAS ENGINEERING LTD",
        "company_status": "active",
        "type": "ltd",
        "accounts": {},
        "confirmation_statement": {},
    }


@pytest.mark.asyncio
async def test_has_charges_true_when_outstanding(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/company/06333469":
            return httpx.Response(200, json=_profile_response())
        return httpx.Response(200, json={"total_count": 1, "items": [_charge_item(5, "X", "outstanding")]})

    monkeypatch.setattr(companies_house, "companies_house_client", _mock_client_factory(handler))
    result = await companies_house._fetch_company_profile("06333469")
    assert result.has_charges is True


@pytest.mark.asyncio
async def test_has_charges_true_when_only_part_satisfied(monkeypatch):
    """Regression: a part-satisfied-only charge history must be True, not
    False — the semantics bug caught in spec review."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/company/06333469":
            return httpx.Response(200, json=_profile_response())
        return httpx.Response(200, json={"total_count": 1, "items": [_charge_item(5, "X", "part-satisfied")]})

    monkeypatch.setattr(companies_house, "companies_house_client", _mock_client_factory(handler))
    result = await companies_house._fetch_company_profile("06333469")
    assert result.has_charges is True


@pytest.mark.asyncio
async def test_has_charges_false_when_all_fully_satisfied(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/company/06333469":
            return httpx.Response(200, json=_profile_response())
        return httpx.Response(200, json={"total_count": 1, "items": [_charge_item(5, "X", "fully-satisfied")]})

    monkeypatch.setattr(companies_house, "companies_house_client", _mock_client_factory(handler))
    result = await companies_house._fetch_company_profile("06333469")
    assert result.has_charges is False


@pytest.mark.asyncio
async def test_has_charges_false_when_no_charges(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/company/06333469":
            return httpx.Response(200, json=_profile_response())
        return httpx.Response(200, json={"total_count": 0, "items": []})

    monkeypatch.setattr(companies_house, "companies_house_client", _mock_client_factory(handler))
    result = await companies_house._fetch_company_profile("06333469")
    assert result.has_charges is False


@pytest.mark.asyncio
async def test_has_charges_none_when_charges_endpoint_fails(monkeypatch):
    """PR #4 regression: a charges-endpoint outage must not silently
    become has_charges=False, and must not break the rest of the profile."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/company/06333469":
            return httpx.Response(200, json=_profile_response())
        return httpx.Response(503, json={"errors": [{"error": "service unavailable"}]})

    monkeypatch.setattr(companies_house, "companies_house_client", _mock_client_factory(handler))
    result = await companies_house._fetch_company_profile("06333469")

    assert result.has_charges is None
    assert result.company_type == "ltd"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_company_charges.py -v`
Expected: FAIL on `test_has_charges_true_when_only_part_satisfied` (current
code only checks `status == "outstanding"`, so a part-satisfied-only
history incorrectly produces `False`). The other four should already pass
against the pre-refactor code, since it happens to get those cases right
today — that's fine, they lock in behavior this refactor must not break.

- [ ] **Step 3: Write minimal implementation**

In `companies_house.py`, replace the entire charges-check block inside
`_fetch_company_profile`. Find:

```python
async def _fetch_company_profile(company_number: str) -> CompanyProfile:
    async with companies_house_client() as client:
        resp = await _request_with_retry(client, "GET", f"/company/{company_number}")
        data = resp.json()

        has_charges: bool | None = None
        try:
            start_index = 0
            page_size = 100
            while True:
                charges_resp = await _request_with_retry(
                    client,
                    "GET",
                    f"/company/{company_number}/charges",
                    params={"items_per_page": page_size, "start_index": start_index},
                )
                charges_data = charges_resp.json()
                charges_items = charges_data.get("items") or []
                if any(item.get("status") == "outstanding" for item in charges_items):
                    has_charges = True
                    break

                total_count = int(charges_data.get("total_count", len(charges_items)) or 0)
                start_index += len(charges_items)
                if not charges_items or start_index >= total_count or len(charges_items) < page_size:
                    has_charges = False
                    break
        except (ToolError, httpx.HTTPError):
            logger.warning(
                "charges check failed for %s — has_charges is unknown", company_number
            )
```

Replace with:

```python
async def _fetch_company_profile(company_number: str) -> CompanyProfile:
    async with companies_house_client() as client:
        resp = await _request_with_retry(client, "GET", f"/company/{company_number}")
        data = resp.json()

    has_charges: bool | None = None
    try:
        charges_result = await _fetch_company_charges(company_number)
        has_charges = any(c.status in LIVE_CHARGE_STATUSES for c in charges_result.charges)
    except (ToolError, httpx.HTTPError):
        logger.warning(
            "charges check failed for %s — has_charges is unknown", company_number
        )
```

Note the `async with companies_house_client() as client:` block now only
wraps the single `/company/{company_number}` call — `_fetch_company_charges`
opens its own client internally (matching every other `_fetch_*` helper in
this file). This is a deliberate, acknowledged tradeoff from the spec: one
extra HTTP client per profile fetch, in exchange for a single
implementation of charge pagination/mapping instead of two. Not optimized
further in this PR.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_company_charges.py -v`
Expected: 13 passed.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: all tests pass (27 pre-existing + 13 new = 40).

- [ ] **Step 6: Commit**

```bash
git add companies_house.py tests/test_company_charges.py
git commit -m "fix: has_charges counts part-satisfied as live, derives from _fetch_company_charges"
```

---

## Task 5: Live dogfooding

**Files:** none (verification only)

**Interfaces:**
- Consumes: the finished `company_charges` tool and refactored `has_charges` from Tasks 1–4, against real Companies House data via the local `.env` (`CH_API_KEY` already present).

- [ ] **Step 1: Reproduce the litmus test — charge 063334690005**

Run:

```bash
uv run python -c "
import asyncio
from dotenv import load_dotenv
load_dotenv()
import companies_house as ch

async def main():
    result = await ch._fetch_company_charges('06333469')
    print('total_count:', result.total_count, 'unfiltered_count:', result.unfiltered_count)
    for c in result.charges:
        print(' -', c.charge_code or f'(charge_number {c.charge_number}, no charge_code)', c.status, 'satisfied_on=', c.satisfied_on)

    litmus = next(c for c in result.charges if c.charge_code == '063334690005')
    assert litmus.status == 'outstanding'
    assert litmus.particulars.contains_floating_charge is True
    assert litmus.particulars.contains_fixed_charge is True
    assert litmus.particulars.floating_charge_covers_all is True
    assert litmus.particulars.contains_negative_pledge is True
    assert litmus.secured_details is None
    assert litmus.persons_entitled[0].name == 'Lloyds Bank Commercail Finance Limited'
    print('LITMUS TEST PASSED: 063334690005 — floating charge over all assets + fixed charge + negative pledge, Lloyds Bank Commercial Finance Limited, outstanding')

    satisfied = [c for c in result.charges if c.status == 'fully-satisfied' and c.satisfied_on]
    assert satisfied, 'no satisfied charge found — the second litmus case (proving correctness beyond live security) failed'
    s = satisfied[0]
    print('SATISFIED CHARGE:', s.charge_code or s.charge_number, s.satisfied_on, s.secured_details.description if s.secured_details else s.particulars.description if s.particulars else None)

asyncio.run(main())
"
```

Expected: prints `LITMUS TEST PASSED` and a satisfied charge with its
`satisfied_on` date and either a `secured_details` or `particulars`
description populated.

- [ ] **Step 2: Confirm the has_charges truth table against real TAS data**

Run:

```bash
uv run python -c "
import asyncio
from dotenv import load_dotenv
load_dotenv()
import companies_house as ch

async def main():
    profile = await ch._fetch_company_profile('06333469')
    print('has_charges:', profile.has_charges)
    assert profile.has_charges is True, 'TAS has an outstanding charge (063334690005) — has_charges must be True'

asyncio.run(main())
"
```

Expected: `has_charges: True`.

- [ ] **Step 3: No commit** — this task is verification only; proceed to Task 6.

---

## Task 6: Open the PR

**Files:** none (git/GitHub operations only)

- [ ] **Step 1: Run the full test suite one last time**

Run: `uv run pytest -q`
Expected: all tests pass.

- [ ] **Step 2: Push the branch**

```bash
git push -u origin feat/charge-records
```

- [ ] **Step 3: Open the PR**

```bash
gh pr create --title "feat: Companies House charge records (BOU-40)" --body "$(cat <<'EOF'
## Summary

Adds the company_charges primitive from docs/superpowers/specs/2026-08-22-charge-records-design.md (BOU-40):

- new company_charges(company_number) tool + company://{company_number}/charges resource: complete charge history, current and historic, preserving upstream structure (particulars, secured_details, persons_entitled, transactions, classification) rather than flattening it
- every field on particulars/secured_details is optional — confirmed live that field presence varies by charge age/type (pre-2006 debentures have no boolean flags; MR01-style charges often have no free text)
- charge_code is optional (absent on some older charges); charge_number is the reliable identifier
- pagination paginates strictly to total_count (not total_results — different field name than the officer-appointments endpoints), never on page-length-shorter-than-requested
- has_charges semantics fix: part-satisfied charges now count as True, not just outstanding, via an explicit LIVE_CHARGE_STATUSES set rather than a complement check. Endpoint-failure -> None (the PR #4 regression) is preserved exactly.
- has_charges is now derived from the same _fetch_company_charges data company_charges returns, not a separate paginated check — one source of truth

Explicitly out of scope (per spec/BOU-40): no filing history tool, no accounts/document ingestion, no derived risk interpretation of charge data.

## Verification

- Unit tests: `uv run pytest -q` — 40 passed (27 pre-existing + 13 new), including the full has_charges truth table (outstanding/part-satisfied/all-satisfied/empty/endpoint-failure) and a pagination-completeness test asserting exact start_index sequence.
- Live dogfooding against TAS Engineering (06333469): reproduces the litmus test exactly — charge 063334690005 is outstanding, floating charge over all assets, fixed charge, negative pledge, Lloyds Bank Commercial Finance Limited, no secured_details. A second, satisfied charge is also confirmed complete (satisfied_on + secured_details/particulars populated), proving the model isn't only correct for live security. has_charges confirmed True against real data.

## Test plan

- [x] uv run pytest -q — 40 passed
- [x] Live litmus test: charge 063334690005 fully reproduced from the finished MCP tool
- [x] Live satisfied-charge check
- [x] Live has_charges confirmation

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 4: Report the PR URL back to the user.**
