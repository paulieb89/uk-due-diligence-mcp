# Officer Appointments & Related-Company Discovery

Status: approved, ready for implementation planning
Author: Claude (with paulieb89)
Date: 2026-08-22

## Problem

`company_officers` returns an officer's role at *one* company. It cannot answer
the acquisition-research question that actually matters: "who else has this
person been a director of, and what's the current status of those
companies?" Today that requires already knowing the other company numbers —
exactly the information due diligence is trying to surface.

Two fields already anticipate this gap and sit unpopulated in the current
model: `CompanyOfficer.appointment_count` and
`CompanyOfficersResult.high_appointment_count_flag`, both documented as
requiring "a separate per-officer call" that doesn't exist yet.

## Goal

Add the minimum primitive that lets an agent discover a person's full
company history from a single starting company number, without the server
building a graph-traversal abstraction it doesn't need yet.

**Acceptance case**: starting only with TAS Engineering (06333469), the MCP
must be able to discover Gareth Davies' connection to MEL Precision
(09118548) — including MEL's liquidation status — without MEL being supplied
anywhere in the prompt.

Verified achievable via a two-call chain, confirmed against live data during
design:

```
company_officers(06333469)
  -> officer with officer_id "EAoJ81mtThuKM5KmSuO5U1RNLHs" (Gareth Leonard Davies)

officer_appointments("EAoJ81mtThuKM5KmSuO5U1RNLHs")
  -> TAS ENGINEERING LTD   (06333469, active)
  -> MEL PRECISION LIMITED (09118548, liquidation)
  -> FXF DESIGNS LTD       (12609854, dissolved)   <- discovered, not previously known
```

Note from the live probe: MEL's appointment record has `resigned_on: null`
even though the company is in liquidation — Companies House does not
auto-resign directors on insolvency. This is why `company_status` on each
appointment matters more than `resigned_on`: `resigned_on` alone would show
Gareth as still an active director of a company in liquidation, which is
misleading without the status alongside it.

## Non-goals (explicitly out of scope for this PR)

- **No `related_companies` aggregation tool.** The two-call chain above is
  the shipped capability. A convenience tool that does officers -> appointments
  -> dedupe server-side is not being built now; it may be worth adding later
  if real usage shows the two-hop chain is a friction point, but that's a
  separate, smaller follow-up if it's ever needed at all.
- **No auto-populated `appointment_count` / `high_appointment_count_flag`.**
  Wiring these into `company_officers` would mean one upstream call per
  officer on every `company_officers` call (N+1), turning a cheap lookup
  into an expensive one nobody asked for. They stay `None`. A caller that
  wants counts for a specific officer calls `officer_appointments` directly.
- **No changes to full charge records, filing history, or pagination
  completeness elsewhere.** Those are separate follow-up specs.
- **No `include_resigned` toggle on `officer_appointments`.** Unlike
  `company_officers` (where "who currently runs this company" is the
  default question), the entire value of this tool is historical discovery.
  It always returns the full history.

## Design

### Data flow

1. `company_officers` gains a new field on each `CompanyOfficer`:
   `officer_id: str | None`, extracted from the officer's existing
   `links.officer.appointments` URL (e.g.
   `/officers/EAoJ81mtThuKM5KmSuO5U1RNLHs/appointments` -> officer_id is the
   path segment between `/officers/` and `/appointments`). This is a new
   extraction helper, distinct from `disqualified.py`'s `_extract_officer_id`
   (which reads a different link shape — the `self` link's tail).
2. New tool `officer_appointments(officer_id)` calls Companies House's
   `GET /officers/{officer_id}/appointments`, paginated the same way
   `company_officers`/charges pagination already works in this file
   (`start_index`/page-size loop until exhausted), and returns every
   appointment — current and historic — with no filtering.

### Models (`models.py`)

```python
class OfficerAppointment(BaseModel):
    company_number: str | None
    company_name: str | None
    company_status: str | None   # e.g. "active", "liquidation", "dissolved"
    officer_role: str | None
    appointed_on: str | None
    resigned_on: str | None
    nationality: str | None
    country_of_residence: str | None
    address: dict[str, Any]
    links: dict[str, Any]        # raw pass-through; includes links.company = "/company/{number}"


class OfficerAppointmentsResult(BaseModel):
    officer_id: str
    name: str | None
    date_of_birth: dict[str, Any]
    total: int
    active_count: int | None
    resigned_count: int | None
    inactive_count: int | None
    appointments: list[OfficerAppointment]
```

`active_count`, `resigned_count`, `inactive_count` are passed through as
distinct fields exactly as Companies House returns them — not collapsed.

`CompanyOfficer` (existing model) gains:

```python
officer_id: str | None = Field(
    None,
    description="Companies House officer ID, extracted from links.officer.appointments. Use with officer_appointments to discover this person's full appointment history across companies.",
)
```

### Tool & resource registration (`companies_house.py`)

Added alongside `company_officers` in the same file — same domain, same API,
and `officer_id` is sourced directly from `_fetch_company_officers`'s output,
so co-locating avoids a cross-file dependency for no benefit.

- `@mcp.tool(name="officer_appointments")` — takes `officer_id: str`.
- `@mcp.resource("officer://{officer_id}/appointments")` — mirrors the
  existing tool+resource dual registration used by every other CH endpoint
  in this file (and by `disqualified_profile`).

### Error handling

Reuses the existing `_request_with_retry` -> structured fleet error taxonomy
as-is. A bad `officer_id` produces the same `not_found` error shape as a bad
`company_number` does elsewhere in this file. No special-casing needed —
unlike `disqualified_profile`, which tries two upstream endpoints
(natural/corporate) before giving up, `officer_appointments` has exactly one
upstream endpoint.

### Testing

- Unit tests (new `tests/test_officer_appointments.py`, following the
  existing `_mock_client_factory` pattern from `tests/test_registry_mapping.py`):
  - Pagination across multiple pages of appointments.
  - A fixture shaped like the real TAS/Gareth/MEL case — one officer with
    appointments spanning active/liquidation/dissolved statuses — as the
    acceptance-mirroring regression test.
  - `officer_id` extraction from the `links.officer.appointments` shape.
- Live dogfooding against the real `officer_id` for Gareth Leonard Davies
  before merge, reproducing the TAS -> MEL discovery chain end-to-end
  against live Companies House data (already spiked once during design;
  will be re-run cleanly against the finished implementation).
