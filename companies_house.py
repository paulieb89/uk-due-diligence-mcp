"""
Companies House API tools.

Tools + companion resources (tools and resources share the same
company_number-keyed fetch helpers below):
  - company_search          (tool)            -> search by name/keyword
  - company_profile         (tool + resource) -> full company record, incl. has_charges
  - company_officers        (tool + resource) -> current/historic officers (appointment_count is
                                                  always null here — see officer_appointments)
  - company_psc             (tool + resource) -> persons with significant control / beneficial ownership
  - officer_appointments    (tool + resource) -> one officer's full cross-company appointment history
  - company_charges         (tool + resource) -> full secured-charge (mortgage) history
  - company_filing_history  (tool + resource) -> filing chronology, one page at a time (see
                                                  _fetch_company_filing_history for why this one
                                                  doesn't auto-paginate to completeness)
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

import httpx
from fastmcp.exceptions import ToolError
from pydantic import Field
from fastmcp import FastMCP

from http_client import _request_with_retry, companies_house_client
from mcpfleet_obs import raise_tool_error
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
    CompanyFiling,
    CompanyFilingHistoryResult,
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

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Risk thresholds
# ---------------------------------------------------------------------------
HIGH_APPOINTMENT_COUNT = 10  # Directors with >= this many active appointments flagged

# Jurisdictions within the UK that are NOT considered overseas for PSC
# beneficial-ownership analysis.
UK_JURISDICTIONS = {
    "",
    "ENGLAND AND WALES",
    "SCOTLAND",
    "NORTHERN IRELAND",
    "WALES",
    "ENGLAND",
    "UNITED KINGDOM",
}

# Charge statuses that represent a live, not-fully-discharged security
# interest. Explicit set rather than "!= fully-satisfied", so an
# unrecognized future status from upstream isn't silently miscategorized
# as still-live.
LIVE_CHARGE_STATUSES = {"outstanding", "part-satisfied"}
FULLY_SATISFIED_STATUS = "fully-satisfied"


def _normalise_company_number(v: str) -> str:
    return v.zfill(8) if v.isdigit() else v.upper()


def _truncate_natures(natures: list[str], max_chars: int) -> list[str]:
    """Cap each nature-of-control entry to `max_chars`."""
    out: list[str] = []
    for n in natures:
        if not isinstance(n, str):
            continue
        if len(n) > max_chars:
            out.append(n[:max_chars] + " …[truncated]")
        else:
            out.append(n)
    return out


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


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Shared fetch helpers (used by both tools and resources)
# ---------------------------------------------------------------------------

def _summarize_has_charges(charges: list[CompanyCharge]) -> bool | None:
    """Tri-state has_charges summary from a complete charge collection.

    True: at least one charge is confirmed live (outstanding/part-satisfied).
    False: every charge is confirmed fully-satisfied (or there are none —
    vacuously true for an empty list, so this branch correctly covers both
    "no charges at all" and "all resolved").
    None: at least one charge has an unrecognized or missing status and
    none are confirmed live — refuse to guess False when the data doesn't
    support it, same discipline as the endpoint-failure case below.
    """
    if any(c.status in LIVE_CHARGE_STATUSES for c in charges):
        return True
    if all(c.status == FULLY_SATISFIED_STATUS for c in charges):
        return False
    return None


async def _fetch_company_profile(company_number: str) -> CompanyProfile:
    async with companies_house_client() as client:
        resp = await _request_with_retry(client, "GET", f"/company/{company_number}")
        data = resp.json()

    has_charges: bool | None = None
    try:
        charges_result = await _fetch_company_charges(company_number)
        has_charges = _summarize_has_charges(charges_result.charges)
    except (ToolError, httpx.HTTPError):
        logger.warning(
            "charges check failed for %s — has_charges is unknown", company_number
        )

    accs_raw = data.get("accounts") or {}
    conf_raw = data.get("confirmation_statement") or {}

    return CompanyProfile(
        company_number=str(data.get("company_number") or company_number),
        company_name=data.get("company_name"),
        company_status=data.get("company_status"),
        company_type=data.get("type"),
        date_of_creation=data.get("date_of_creation"),
        sic_codes=list(data.get("sic_codes") or []),
        registered_office_address=data.get("registered_office_address") or {},
        has_charges=has_charges,
        accounts=CompanyAccountsSummary(
            overdue=bool(accs_raw.get("overdue", False)),
            last_accounts_made_up_to=(accs_raw.get("last_accounts") or {}).get("made_up_to"),
            next_due=accs_raw.get("next_due"),
        ),
        confirmation_statement=CompanyConfirmationStatementSummary(
            overdue=bool(conf_raw.get("overdue", False)),
            next_due=conf_raw.get("next_due"),
        ),
    )


async def _fetch_company_officers(
    company_number: str, *, include_resigned: bool = False
) -> CompanyOfficersResult:
    raw_items: list[dict[str, Any]] = []
    top: dict[str, Any] = {}
    attempted = f"GET /company/{company_number}/officers"
    async with companies_house_client() as client:
        start_index = 0
        page_size = 100
        while True:
            resp = await _request_with_retry(
                client,
                "GET",
                f"/company/{company_number}/officers",
                params={"items_per_page": page_size, "start_index": start_index},
            )
            data = resp.json()
            if not top:
                top = data
            page_items = data.get("items", []) or []
            total_results = int(data.get("total_results", len(raw_items) + len(page_items)) or 0)
            if not page_items and start_index < total_results:
                # Upstream said there should be more, but this page came
                # back empty. Not "must be the end" — an incomplete
                # collection returned silently is worse than no result.
                raise_tool_error(
                    "transient",
                    is_retryable=True,
                    attempted=attempted,
                    description=(
                        f"Officers pagination returned an empty page at "
                        f"start_index={start_index} before reaching "
                        f"total_results={total_results} — incomplete collection, "
                        f"not a valid result."
                    ),
                )
            raw_items.extend(page_items)
            start_index += len(page_items)
            if start_index >= total_results:
                break

    result_total = int(top.get("total_results", len(raw_items)) or 0)
    if len(raw_items) != result_total:
        # Post-loop invariant, not just trust in the loop's own logic.
        raise_tool_error(
            "transient",
            is_retryable=True,
            attempted=attempted,
            description=(
                f"Officers collection incomplete or inconsistent: received "
                f"{len(raw_items)} officers but upstream reported "
                f"total_results={result_total}."
            ),
        )

    if not include_resigned:
        raw_items = [o for o in raw_items if not o.get("resigned_on")]
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
    return CompanyOfficersResult(
        company_number=company_number,
        include_resigned=include_resigned,
        total=len(officers),
        high_appointment_count_flag=None,
        officers=officers,
    )


async def _fetch_company_psc(company_number: str) -> CompanyPSCResult:
    raw_items: list[dict[str, Any]] = []
    top: dict[str, Any] = {}
    attempted = f"GET /company/{company_number}/persons-with-significant-control"
    async with companies_house_client() as client:
        start_index = 0
        page_size = 100
        while True:
            resp = await _request_with_retry(
                client,
                "GET",
                f"/company/{company_number}/persons-with-significant-control",
                params={"items_per_page": page_size, "start_index": start_index},
            )
            data = resp.json()
            if not top:
                top = data
            page_items = data.get("items", []) or []
            total_results = int(data.get("total_results", len(raw_items) + len(page_items)) or 0)
            if not page_items and start_index < total_results:
                raise_tool_error(
                    "transient",
                    is_retryable=True,
                    attempted=attempted,
                    description=(
                        f"PSC pagination returned an empty page at "
                        f"start_index={start_index} before reaching "
                        f"total_results={total_results} — incomplete collection, "
                        f"not a valid result."
                    ),
                )
            raw_items.extend(page_items)
            start_index += len(page_items)
            if start_index >= total_results:
                break

    total = int(top.get("total_results", len(raw_items)) or 0)
    if len(raw_items) != total:
        raise_tool_error(
            "transient",
            is_retryable=True,
            attempted=attempted,
            description=(
                f"PSC collection incomplete or inconsistent: received "
                f"{len(raw_items)} PSC entries but upstream reported "
                f"total_results={total}."
            ),
        )

    psc_entries: list[CompanyPSCEntry] = []
    overseas_flag = 0
    for raw in raw_items:
        natures = _truncate_natures(list(raw.get("natures_of_control") or []), 300)
        entry = CompanyPSCEntry(
            kind=raw.get("kind"),
            name=raw.get("name"),
            notified_on=raw.get("notified_on"),
            ceased_on=raw.get("ceased_on"),
            nationality=raw.get("nationality"),
            country_of_residence=raw.get("country_of_residence"),
            date_of_birth=raw.get("date_of_birth") or {},
            natures_of_control=natures,
            identification=raw.get("identification") or {},
            address=raw.get("address") or {},
        )
        psc_entries.append(entry)

        if entry.kind in (
            "corporate-entity-person-with-significant-control",
            "legal-person-person-with-significant-control",
        ):
            jurisdiction = (
                entry.identification.get("country_registered")
                or entry.identification.get("legal_authority")
                or ""
            )
            normalised = " ".join(str(jurisdiction).upper().replace("&", "AND").split())
            if normalised and normalised not in UK_JURISDICTIONS:
                overseas_flag += 1

    note = None
    if total == 0:
        note = (
            "No registrable PSC. Typical for widely-held listed PLCs where "
            "no single person or entity holds 25%+ of shares or voting rights."
        )
    return CompanyPSCResult(
        company_number=company_number,
        total=total,
        overseas_corporate_psc_flag=overseas_flag,
        psc=psc_entries,
        note=note,
    )


async def _fetch_officer_appointments(officer_id: str) -> OfficerAppointmentsResult:
    oid = officer_id.strip()
    raw_items: list[dict[str, Any]] = []
    top: dict[str, Any] = {}
    attempted = f"GET /officers/{oid}/appointments"
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
            total_results = int(data.get("total_results", len(raw_items) + len(page_items)) or 0)
            if not page_items and start_index < total_results:
                # Upstream said there should be more, but this page came
                # back empty. Not "must be the end" — an incomplete
                # collection returned silently is worse than no result.
                raise_tool_error(
                    "transient",
                    is_retryable=True,
                    attempted=attempted,
                    description=(
                        f"Officer appointments pagination returned an empty page at "
                        f"start_index={start_index} before reaching "
                        f"total_results={total_results} — incomplete collection, "
                        f"not a valid result."
                    ),
                )
            raw_items.extend(page_items)
            start_index += len(page_items)
            if start_index >= total_results:
                break

    result_total = int(top.get("total_results", len(raw_items)) or 0)
    if len(raw_items) != result_total:
        # Post-loop invariant, not just trust in the loop's own logic.
        raise_tool_error(
            "transient",
            is_retryable=True,
            attempted=attempted,
            description=(
                f"Officer appointments collection incomplete or inconsistent: "
                f"received {len(raw_items)} appointments but upstream reported "
                f"total_results={result_total}."
            ),
        )

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
    attempted = f"GET /company/{company_number}/charges"
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
            total_count = int(data.get("total_count", len(raw_items) + len(page_items)) or 0)
            if not page_items and start_index < total_count:
                # Upstream said there should be more, but this page came
                # back empty. Not "must be the end" — an incomplete
                # collection returned silently is worse than no result.
                raise_tool_error(
                    "transient",
                    is_retryable=True,
                    attempted=attempted,
                    description=(
                        f"Charges pagination returned an empty page at "
                        f"start_index={start_index} before reaching "
                        f"total_count={total_count} — incomplete collection, "
                        f"not a valid result."
                    ),
                )
            raw_items.extend(page_items)
            start_index += len(page_items)
            if start_index >= total_count:
                break

    charges = [_map_charge(raw) for raw in raw_items]
    result_total_count = int(top.get("total_count", len(charges)) or 0)

    if len(charges) != result_total_count:
        # Post-loop invariant, not just trust in the loop's own logic —
        # catches anything the mid-loop guard above didn't (e.g. a
        # duplicate/extra item on the last page inflating the count past
        # what upstream originally reported).
        raise_tool_error(
            "transient",
            is_retryable=True,
            attempted=attempted,
            description=(
                f"Charges collection incomplete or inconsistent: received "
                f"{len(charges)} charges but upstream reported "
                f"total_count={result_total_count}."
            ),
        )

    return CompanyChargesResult(
        company_number=company_number,
        total_count=result_total_count,
        unfiltered_count=top.get("unfiltered_count"),
        satisfied_count=top.get("satisfied_count"),
        part_satisfied_count=top.get("part_satisfied_count"),
        charges=charges,
    )


# Large-unfiltered-history advisory threshold. Not a cap — total_count and
# has_more are always reported truthfully regardless of size; this only
# decides whether to nudge the caller toward category= narrowing. Chosen
# well above a normal company's lifetime filing count (TAS 54, Carillion
# in liquidation 323) and well below a long-lived PLC's (Tesco: 8,367).
LARGE_FILING_HISTORY_THRESHOLD = 500


def _map_filing(raw: dict[str, Any]) -> CompanyFiling:
    return CompanyFiling(
        transaction_id=raw.get("transaction_id"),
        type=raw.get("type"),
        date=raw.get("date"),
        action_date=raw.get("action_date"),
        category=raw.get("category"),
        subcategory=raw.get("subcategory"),
        description=raw.get("description"),
        description_values=raw.get("description_values") or {},
        pages=raw.get("pages"),
        paper_filed=raw.get("paper_filed"),
        barcode=raw.get("barcode"),
        resolutions=list(raw.get("resolutions") or []),
        associated_filings=list(raw.get("associated_filings") or []),
        annotations=list(raw.get("annotations") or []),
        links=raw.get("links") or {},
    )


async def _fetch_company_filing_history(
    company_number: str,
    *,
    category: str | None = None,
    items_per_page: int = 100,
    start_index: int = 0,
) -> CompanyFilingHistoryResult:
    """Fetch one page of a company's filing-history.

    Unlike officers/PSC/charges, this does NOT auto-paginate to
    completeness internally — a long-lived company's filing history is
    unbounded in practice (Tesco PLC: 8,367 filings, ~4MB raw), so
    pagination is exposed to the caller via start_index/has_more instead
    of hidden behind a fetch-everything loop. category is passed straight
    to CH's own server-side filter (confirmed live, comma-separated for
    multiple categories) — narrowing there reduces total_count upstream,
    not just what's displayed.

    This endpoint never 404s, even for a company number that doesn't
    exist — a bogus number returns HTTP 200 with total_count=0, identical
    to a real company with zero filings. Disambiguated below with a
    minimal existence probe only when total_count is 0 for this query
    (not per-page: paging past the end of a non-empty history is not
    the same ambiguity and must not trigger this).

    A page that comes back with zero items while total_count promises
    more remain at this start_index (a stalled page) also raises —
    distinct from both total_count == 0 (genuine zero-history) and
    start_index >= total_count (caller paged past the end, legitimately
    empty, not an error).
    """
    attempted = f"GET /company/{company_number}/filing-history"
    params: dict[str, Any] = {"items_per_page": items_per_page, "start_index": start_index}
    if category:
        params["category"] = category

    async with companies_house_client() as client:
        resp = await _request_with_retry(client, "GET", attempted.split(" ", 1)[1], params=params)
        data = resp.json()

        status = data.get("filing_history_status")
        if status and str(status).startswith("filing-history-not-available"):
            raise_tool_error(
                "validation",
                is_retryable=False,
                attempted=attempted,
                description=(
                    f"Companies House rejected company_number {company_number!r} as "
                    f"malformed (filing_history_status={status!r}) — not a valid "
                    f"company number format."
                ),
            )

        total_count = int(data.get("total_count", 0) or 0)
        raw_items = data.get("items", []) or []

        if total_count == 0:
            # filing-history alone can't distinguish "exists, zero filings"
            # from "doesn't exist" — both are this same 200/empty shape.
            # A plain GET /company/{number} (not _fetch_company_profile,
            # which also fetches charges) resolves it: 404 there raises
            # not_found via raise_http_tool_error; 200 confirms a genuine
            # empty result.
            await _request_with_retry(client, "GET", f"/company/{company_number}")
        elif not raw_items and start_index < total_count:
            # Stalled page: total_count promises more filings exist at
            # this start_index, but the page came back with zero items.
            # Distinct from total_count == 0 (genuine zero-history,
            # handled above) and from start_index >= total_count (caller
            # legitimately paged past the end — falls through below with
            # returned=0, has_more=False, no error). Zero progress despite
            # promised remaining items is not a valid result.
            raise_tool_error(
                "transient",
                is_retryable=True,
                attempted=attempted,
                description=(
                    f"Filing-history page at start_index={start_index} came back "
                    f"empty but total_count={total_count} indicates more filings "
                    f"exist — stalled page, not a valid result."
                ),
            )

    filings = [_map_filing(raw) for raw in raw_items]
    returned = len(filings)
    has_more = (start_index + returned) < total_count

    note = None
    if category is None and total_count > LARGE_FILING_HISTORY_THRESHOLD:
        note = (
            f"{total_count} filings — consider narrowing with category= "
            f"(e.g. 'mortgage', 'officers', 'insolvency', 'accounts') to reduce response size."
        )

    return CompanyFilingHistoryResult(
        company_number=company_number,
        category=category,
        filing_history_status=status,
        total_count=total_count,
        start_index=start_index,
        items_per_page=int(data.get("items_per_page", items_per_page) or items_per_page),
        returned=returned,
        has_more=has_more,
        note=note,
        filings=filings,
    )


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

def register_tools(mcp: FastMCP) -> None:

    # ------------------------------------------------------------------ #
    # 1. company_search
    # ------------------------------------------------------------------ #
    @mcp.tool(
        name="company_search",
        annotations={
            "title": "Search Companies House",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def company_search(
        query: Annotated[str, Field(description="Company name or keyword to search for", min_length=2, max_length=200)],
        company_status: Annotated[str | None, Field(description="Filter by company status (e.g. 'active', 'dissolved'). Omit to search all.")] = None,
        company_type: Annotated[str | None, Field(description="Filter by company type (e.g. 'ltd', 'llp'). Omit to search all.")] = None,
        items_per_page: Annotated[int, Field(description="Number of results to return (max 100). Default 20.", ge=1, le=100)] = 20,
        start_index: Annotated[int, Field(description="Pagination offset. Default 0.", ge=0, le=10000)] = 0,
    ) -> CompanySearchResult:
        """Search the Companies House register by company name or keyword.

        Returns a paginated list of matching companies with name, number,
        status, SIC codes, incorporation date, and registered address.
        Use company_profile for the full record once you have the company
        number. Re-call with start_index=start_index+items_per_page to
        fetch the next page.
        """
        qs: dict[str, Any] = {
            "q": query,
            "items_per_page": items_per_page,
            "start_index": start_index,
        }
        if company_status:
            qs["status"] = company_status
        if company_type:
            qs["type"] = company_type

        async with companies_house_client() as client:
            resp = await _request_with_retry(client, "GET", "/search/companies", params=qs)
            data = resp.json()

        raw_items = data.get("items", []) or []
        total = int(data.get("total_results", 0) or 0)

        items = [
            CompanySearchItem(
                company_number=raw.get("company_number"),
                title=raw.get("title"),
                company_status=raw.get("company_status"),
                company_type=raw.get("company_type"),
                date_of_creation=raw.get("date_of_creation"),
                sic_codes=list(raw.get("sic_codes") or []),
                address=raw.get("address") or raw.get("registered_office_address") or {},
                description=raw.get("description"),
            )
            for raw in raw_items
        ]

        has_more = (start_index + len(items)) < total

        return CompanySearchResult(
            query=query,
            total_results=total,
            start_index=start_index,
            items_per_page=items_per_page,
            returned=len(items),
            has_more=has_more,
            items=items,
        )

    # ------------------------------------------------------------------ #
    # 2. company_profile
    # ------------------------------------------------------------------ #
    @mcp.tool(
        name="company_profile",
        annotations={
            "title": "Get Company Profile",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def company_profile(
        company_number: Annotated[str, Field(description="Companies House company number (8 digits, e.g. '03782379'). Returned by company_search.", min_length=1, max_length=10)],
    ) -> CompanyProfile:
        """Fetch the full Companies House profile for a company number.

        Returns status, registered address, SIC codes, filing compliance
        (overdue accounts and confirmation statement flags), and whether
        the company has outstanding charges. Use company_search first to
        find the company number.
        """
        return await _fetch_company_profile(_normalise_company_number(company_number))

    # ------------------------------------------------------------------ #
    # 3. company_officers
    # ------------------------------------------------------------------ #
    @mcp.tool(
        name="company_officers",
        annotations={
            "title": "Get Company Officers",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def company_officers(
        company_number: Annotated[str, Field(description="Companies House company number (8 digits, e.g. '03782379'). Returned by company_search.", min_length=1, max_length=10)],
        include_resigned: Annotated[bool, Field(description="Include resigned/historic officers. Default false for backwards-compatible current-officer queries.")] = False,
        items_per_page: Annotated[int | None, Field(description="Ignored — pagination is handled internally. Only accepted to avoid call failures.")] = None,
        start_index: Annotated[int | None, Field(description="Ignored — pagination is handled internally. Only accepted to avoid call failures.")] = None,
    ) -> CompanyOfficersResult:
        """Fetch officers for a Companies House company number.

        Returns directors, secretaries, and other officers with appointment
        dates, nationality, and country of residence. Resigned officers are
        excluded by default; set include_resigned=true for historical DD.
        Pagination is handled internally.
        """
        return await _fetch_company_officers(
            _normalise_company_number(company_number), include_resigned=include_resigned
        )

    # ------------------------------------------------------------------ #
    # 4. company_psc
    # ------------------------------------------------------------------ #
    @mcp.tool(
        name="company_psc",
        annotations={
            "title": "Get Persons with Significant Control",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def company_psc(
        company_number: Annotated[str, Field(description="Companies House company number (8 digits, e.g. '03782379'). Returned by company_search.", min_length=1, max_length=10)],
    ) -> CompanyPSCResult:
        """Fetch Persons with Significant Control (beneficial ownership) for a company.

        Returns PSC entries with natures of control, nationality, and
        country of residence. Flags overseas corporate PSC entries as a
        beneficial ownership risk signal. Returns an explanatory note for
        widely-held PLCs with no registrable PSC.
        """
        return await _fetch_company_psc(_normalise_company_number(company_number))

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
        True/False/unknown summary derived from this same data; use this
        tool when the specific charges matter, not just whether any exist.
        """
        return await _fetch_company_charges(_normalise_company_number(company_number))

    # ------------------------------------------------------------------ #
    # 7. company_filing_history
    # ------------------------------------------------------------------ #
    @mcp.tool(
        name="company_filing_history",
        annotations={
            "title": "Get Company Filing History",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def company_filing_history(
        company_number: Annotated[str, Field(description="Companies House company number (8 digits, e.g. '03782379'). Returned by company_search.", min_length=1, max_length=10)],
        category: Annotated[str | None, Field(description="Filter by CH filing category — comma-separated for multiple, e.g. 'mortgage' or 'mortgage,officers'. Omit for all categories. Common values: accounts, confirmation-statement, officers, address, capital, mortgage, persons-with-significant-control, incorporation, insolvency, resolution, annual-return, change-of-name, change-of-constitution, gazette, miscellaneous.", max_length=200)] = None,
        items_per_page: Annotated[int, Field(description="Results per page (Companies House caps at 100 regardless of a higher value). Default 100.", ge=1, le=100)] = 100,
        start_index: Annotated[int, Field(description="Pagination offset. Default 0. Re-call with start_index=start_index+returned while has_more is true.", ge=0)] = 0,
    ) -> CompanyFilingHistoryResult:
        """Fetch one page of a company's Companies House filing chronology.

        Returns the raw source facts for each filing — transaction ID,
        form type/category, dates, and the description_values CH uses to
        render its own text — as delivered upstream, not interpreted into
        DD conclusions. links.document_metadata on each filing is the
        identifier a future document-retrieval tool would need; no
        document content is fetched here.

        Unlike company_officers/company_psc/company_charges, this does
        NOT auto-fetch every page — a long-lived company's filing history
        is unbounded in practice (a decades-old PLC can carry thousands
        of filings). total_count/returned/has_more are always reported
        truthfully for whatever page and category filter was requested;
        nothing is silently truncated. Narrow with category= for a
        specific slice (e.g. category='mortgage' for charge-related
        filings, category='insolvency' for administration/liquidation
        filings) — a note is included when an unfiltered history is large.

        A company_number that doesn't resolve to any company returns a
        structured not_found error, distinct from a genuine zero-filing
        result — Companies House's filing-history endpoint alone cannot
        tell these apart, so existence is confirmed separately when the
        result would otherwise be empty.
        """
        return await _fetch_company_filing_history(
            _normalise_company_number(company_number),
            category=category,
            items_per_page=items_per_page,
            start_index=start_index,
        )


# ---------------------------------------------------------------------------
# Resource registration
# ---------------------------------------------------------------------------

def register_resources(mcp: FastMCP) -> None:

    @mcp.resource(
        "company://{company_number}/profile",
        name="company_profile",
        description=(
            "Full Companies House profile for a company number: status, address, "
            "SIC codes, filing compliance (overdue flags), and active-charges flag."
        ),
        mime_type="application/json",
    )
    async def company_profile_resource(company_number: str) -> str:
        result = await _fetch_company_profile(_normalise_company_number(company_number))
        return result.model_dump_json()

    @mcp.resource(
        "company://{company_number}/officers",
        name="company_officers",
        description=(
            "Active officers for a Companies House company number. Each officer "
            "carries an officer_id — pass it to officer_appointments to discover "
            "that person's full company history."
        ),
        mime_type="application/json",
    )
    async def company_officers_resource(company_number: str) -> str:
        result = await _fetch_company_officers(_normalise_company_number(company_number))
        return result.model_dump_json()

    @mcp.resource(
        "company://{company_number}/psc",
        name="company_psc",
        description=(
            "Persons with Significant Control (beneficial ownership) for a company. "
            "Flags overseas corporate PSC entries as a beneficial ownership risk signal."
        ),
        mime_type="application/json",
    )
    async def company_psc_resource(company_number: str) -> str:
        result = await _fetch_company_psc(_normalise_company_number(company_number))
        return result.model_dump_json()

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

    @mcp.resource(
        "company://{company_number}/filing-history",
        name="company_filing_history",
        description=(
            "First page (up to 100) of a Companies House company's filing "
            "chronology, unfiltered. For pagination past the first page or "
            "a category filter, use the company_filing_history tool."
        ),
        mime_type="application/json",
    )
    async def company_filing_history_resource(company_number: str) -> str:
        result = await _fetch_company_filing_history(_normalise_company_number(company_number))
        return result.model_dump_json()
