# Companies House Charge Records

Status: approved, ready for implementation planning
Author: Claude (with paulieb89)
Date: 2026-08-22
Linear: BOU-40

## Problem

`company_profile.has_charges` is a lossy `bool | None` — it can only say
whether *any* charge is outstanding. It cannot say which charge, who holds
it, when it was created, whether it's since been satisfied, or what it
actually secures. For due diligence that distinction is the whole point:
"this company has a charge" and "this company gave Lloyds Bank a floating
charge over all assets with a negative pledge in 2017, still outstanding"
are different findings.

## Goal

One new primitive: `company_charges(company_number)` → the complete,
lossless charge history for a company, paginated correctly, preserving
upstream structure rather than flattening it away.

**Acceptance case**, confirmed live against TAS Engineering (06333469)
during design (five charges: two outstanding, three fully-satisfied):

> Starting from 06333469, return charge `063334690005` with enough raw
> detail to identify what it secures, who is entitled, and whether it
> remains outstanding — and separately, return at least one **satisfied**
> charge with equivalent completeness, so the model is proven correct for
> both states, not just live security.

Verified answer the finished tool must reproduce for charge 5:

> A floating charge covering all company assets plus a fixed charge, with
> a negative pledge, in favor of Lloyds Bank Commercial Finance Limited.
> Created 2017-06-01, delivered 2017-06-06, status **outstanding**. No
> free-text particulars filed — security scope is captured entirely
> through the boolean flags.

And for charge 4 (fully-satisfied), the tool must additionally surface:
satisfied 2022-05-30, secured a specific property ("Unit 4 millers lane,
derby street, burton upon trent, staffordshire DE14 2NS"), and the
`charge-satisfaction` filing that closed it out.

## Non-goals (explicitly out of scope for this PR)

- **No filing history tool, no accounts/document ingestion.** Both stay
  separate issues so each primitive can be dogfooded independently — this
  PR is charge records only, even though charge records embed filing
  references (`transactions[].links.filing`).
- **No flattening of upstream structure.** `particulars`, `secured_details`,
  `persons_entitled`, `transactions`, and `classification` are preserved as
  their own nested shapes, not collapsed into a handful of top-level
  scalar fields. Aggressive flattening is exactly what produced the
  `has_charges` boolean's information loss in the first place.
- **`has_charges` is not removed.** It stays on `company_profile` as a
  cheap summary field, but must be derived consistently from the same
  full charge data this tool returns — not a separate, potentially
  divergent check. It stops being the primary DD surface; `company_charges`
  is.
- **No interpretation of what a charge means for creditworthiness.**
  Same discipline as `officer_appointments`: pass upstream facts through
  (status, dates, flags, text), no derived risk score.

## Design

### Live-probed field shapes (ground truth, not assumed)

Probed directly against TAS's `/company/06333469/charges` before writing
this spec, across three charges in different states (charge 5: outstanding,
no `secured_details`; charge 4: fully-satisfied, has `satisfied_on` and a
`charge-satisfaction` transaction; charge 1: fully-satisfied, pre-2006
debenture, has `secured_details` and no `particulars` boolean flags at
all). Full raw JSON for all three is in the design conversation; the
load-bearing facts extracted from it:

**Top-level response**: `etag`, `total_count`, `unfiltered_count`,
`satisfied_count`, `part_satisfied_count`, `items`. No `items_per_page` or
`start_index` echoed back in the response (unlike the officer-appointments
endpoint) — **the field is `total_count` here, not `total_results`** as it
is on the officers/appointments endpoints. Getting this field name wrong
silently breaks pagination-completeness.

**Per-charge item — three gotchas confirmed live, not assumed from docs:**

1. `charge_code` (the human-readable ID the litmus test uses, e.g.
   `"063334690005"`) is **absent** on TAS's charge #1 (a pre-2006-Companies-Act
   debenture) — only `charge_number` (a plain int, always present) is
   guaranteed. The model must treat `charge_code` as optional and use
   `charge_number` as the reliable identifier.
2. `particulars` shape varies by charge type. Charge 4 has both a
   `type`/`description` pair *and* all four boolean flags
   (`contains_floating_charge`, `contains_fixed_charge`,
   `floating_charge_covers_all`, `contains_negative_pledge`). Charge 5 has
   only the four boolean flags, no `type`/`description`. Charge 1 (the old
   debenture) has only `type`/`description`, **no boolean flags at all**.
   Every field in `particulars` must be optional.
3. `secured_details` (a free-text "amount secured" description, distinct
   from `particulars`) appears **only** on charge #1 — the old debenture.
   None of charges 2–5 (2014–2017, MR01-style filings) have it. Optional,
   not assume-present — this was flagged in BOU-40 before this spec and is
   now confirmed against the actual data, not just suspected.

**No separate "satisfactions/releases" field.** What BOU-40's scope calls
"satisfactions/releases" is represented two ways in the raw data, both of
which the model preserves rather than synthesizing a new concept:
`satisfied_on` (top-level date, present only once satisfied) and an entry
in `transactions[]` with `filing_type: "charge-satisfaction"` alongside the
`filing_type: "create-charge-*"` entry that created the charge. Each
transaction carries its own `delivered_on` and a `links.filing` pointing at
the filing-history entry — this is charge-record depth, not filing-history
breadth; no separate call is made to resolve it further in this PR.

**Pagination**: confirmed live that this endpoint *does* honor requested
`items_per_page`/`start_index` (tested with `items_per_page=2` across
TAS's 5 charges — returned exactly 2, 2, 1, ordered newest-`charge_number`-first,
`total_count` consistent across all pages). No silent cap was observed at
TAS's scale, but per the lesson from `officer_appointments`, the loop
does not rely on that — same discipline as agreed there: **paginate
strictly to `start_index >= total_count`**, never on "this page came back
shorter than requested."

### Models (`models.py`)

```python
class ChargeParticulars(BaseModel):
    type: str | None = None
    description: str | None = None
    contains_floating_charge: bool | None = None
    contains_fixed_charge: bool | None = None
    floating_charge_covers_all: bool | None = None
    contains_negative_pledge: bool | None = None

class ChargeSecuredDetails(BaseModel):
    type: str | None = None
    description: str | None = None

class ChargeClassification(BaseModel):
    type: str | None = None
    description: str | None = None

class ChargePersonEntitled(BaseModel):
    name: str | None = None

class ChargeTransaction(BaseModel):
    filing_type: str | None = None
    delivered_on: str | None = None
    links: dict[str, Any] = Field(default_factory=dict)

class CompanyCharge(BaseModel):
    charge_number: int
    charge_code: str | None = None
    status: str | None = None            # "outstanding" | "part-satisfied" | "fully-satisfied" (values as upstream returns them, not an enum we invent)
    classification: ChargeClassification | None = None
    created_on: str | None = None
    delivered_on: str | None = None
    satisfied_on: str | None = None
    particulars: ChargeParticulars | None = None
    secured_details: ChargeSecuredDetails | None = None
    persons_entitled: list[ChargePersonEntitled] = Field(default_factory=list)
    transactions: list[ChargeTransaction] = Field(default_factory=list)
    links: dict[str, Any] = Field(default_factory=dict)

class CompanyChargesResult(BaseModel):
    company_number: str
    total_count: int
    satisfied_count: int | None = None
    part_satisfied_count: int | None = None
    charges: list[CompanyCharge] = Field(default_factory=list)
```

Nested sub-models (`ChargeParticulars`, `ChargeSecuredDetails`, etc.)
rather than raw `dict[str, Any]` passthrough — unlike `address`/`links` on
existing models, these are the exact fields the acceptance case needs
structured access to (a caller must be able to check
`particulars.floating_charge_covers_all is True`, not grep a blob). `links`
stays a raw dict, matching every other model in this file, since nothing
here needs to branch on its contents.

### Fetch helper and tool/resource registration (`companies_house.py`)

Mirrors the `officer_appointments` shape exactly:

- `_fetch_company_charges(company_number: str) -> CompanyChargesResult`,
  paginated with a `start_index >= total_count` loop, alongside
  `_fetch_company_profile`/`_fetch_company_officers`/`_fetch_company_psc`
  in the same file.
- `company_charges(company_number)` tool, registered the same way as the
  other four tools in this file.
- `company://{company_number}/charges` resource, calling the same shared
  `_fetch_company_charges` helper — no duplicated mapping logic between
  tool and resource, same discipline confirmed for `officer_appointments`.

### `has_charges` stays consistent, not duplicated logic

`_fetch_company_profile`'s existing charges-pagination loop (shipped in
PR #4) becomes a thin call into the same `_fetch_company_charges` helper,
deriving `has_charges` from `any(c.status == "outstanding" for c in
result.charges)` rather than running its own separate paginated fetch
against the same endpoint. One source of truth for "does this company have
charges," not two implementations that could drift.

### Error handling

Reuses the existing `_request_with_retry` → fleet error taxonomy as-is,
same as every other endpoint in this file. No special-casing.

### Testing

- Unit tests (new `tests/test_company_charges.py`, following the
  `_mock_client_factory` pattern established in
  `tests/test_registry_mapping.py`/`tests/test_officer_appointments.py`):
  - A fixture shaped like the real TAS charge set — outstanding charge 5
    (no `secured_details`, boolean-flags-only `particulars`), satisfied
    charge 4 (has `satisfied_on` + `charge-satisfaction` transaction +
    free-text `particulars.description`), and satisfied charge 1 (has
    `secured_details`, no `particulars` boolean flags) — as the
    acceptance-mirroring regression test, covering both litmus-test charges
    (outstanding AND at least one satisfied, per explicit instruction).
  - Pagination-completeness: `len(charges) == total_count` across multiple
    pages, with an assertion on the exact `start_index` sequence requested
    (mirrors the `officer_appointments` pagination test).
  - `secured_details`/`particulars` fields absent upstream do not raise —
    result field is `None`, not a validation error.
  - `has_charges` on `company_profile` derived from the same charge data
    `company_charges` returns (no drift between the two).
- Live dogfooding against real TAS data before merge: reproduce the exact
  litmus-test answer for charge `063334690005`, and separately confirm a
  satisfied charge (4 or 1) returns complete detail — both from the
  finished MCP tool, not an ad-hoc script.

### Acceptance criteria

- `company_charges("06333469")` returns exactly 5 charges, `total_count == 5`.
- Charge `063334690005` (charge_number 5): `status == "outstanding"`,
  `particulars.contains_floating_charge is True`,
  `particulars.floating_charge_covers_all is True`,
  `particulars.contains_negative_pledge is True`,
  `persons_entitled == [{"name": "Lloyds Bank Commercail Finance Limited"}]`
  (upstream's spelling, preserved verbatim — not corrected), `secured_details is None`.
- At least one satisfied charge (4 or 1) returns `satisfied_on` populated
  and a `transactions` entry with `filing_type == "charge-satisfaction"`.
- Pagination completeness holds under a multi-page mock.
- `company_profile.has_charges` for TAS remains `True` (unchanged
  behavior) but is now derived from `company_charges`'s own data, not a
  separate paginated fetch.
