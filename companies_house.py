"""
Companies House API tools.

Covers:
  - company_search   (tool)     -> search by name/keyword
  - company_profile  (resource) -> full company record with filing signals
  - company_officers (resource) -> directors with appointment count risk signal
  - company_psc      (resource) -> persons with significant control / beneficial ownership
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field
from fastmcp import FastMCP

from http_client import _request_with_retry, companies_house_client
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
)

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


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Shared fetch helpers (used by both tools and resources)
# ---------------------------------------------------------------------------

async def _fetch_company_profile(company_number: str) -> CompanyProfile:
    async with companies_house_client() as client:
        resp = await _request_with_retry(client, "GET", f"/company/{company_number}")
        data = resp.json()

        has_charges = False
        try:
            charges_resp = await _request_with_retry(
                client, "GET", f"/company/{company_number}/charges",
                params={"items_per_page": 1},
            )
            charges_data = charges_resp.json()
            charges_items = charges_data.get("items") or []
            has_charges = any(
                item.get("status") == "outstanding" for item in charges_items
            ) or (
                charges_data.get("total_count", 0) > 0
                and not charges_items
            )
        except Exception:
            pass

    accs_raw = data.get("accounts") or {}
    conf_raw = data.get("confirmation_statement") or {}

    return CompanyProfile(
        company_number=str(data.get("company_number") or company_number),
        company_name=data.get("company_name"),
        company_status=data.get("company_status"),
        company_type=data.get("company_type"),
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


async def _fetch_company_officers(company_number: str) -> CompanyOfficersResult:
    async with companies_house_client() as client:
        resp = await _request_with_retry(
            client, "GET",
            f"/company/{company_number}/officers",
            params={"items_per_page": 100},
        )
        data = resp.json()

    raw_items = [o for o in (data.get("items", []) or []) if not o.get("resigned_on")]
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
    return CompanyOfficersResult(
        company_number=company_number,
        include_resigned=False,
        total=len(officers),
        high_appointment_count_flag=None,
        officers=officers,
    )


async def _fetch_company_psc(company_number: str) -> CompanyPSCResult:
    async with companies_house_client() as client:
        resp = await _request_with_retry(
            client, "GET",
            f"/company/{company_number}/persons-with-significant-control",
        )
        data = resp.json()

    raw_items = data.get("items", []) or []
    total = int(data.get("total_results", len(raw_items)) or 0)

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
            natures_of_control=natures,
            identification=raw.get("identification") or {},
            address=raw.get("address") or {},
        )
        psc_entries.append(entry)

        if entry.kind in (
            "corporate-entity-person-with-significant-control",
            "legal-person-person-with-significant-control",
        ):
            place = (entry.identification.get("place_registered") or "").upper()
            if place not in UK_JURISDICTIONS:
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
                address=raw.get("registered_office_address") or {},
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
        items_per_page: Annotated[int | None, Field(description="Ignored — pagination is handled internally. Only accepted to avoid call failures.")] = None,
        start_index: Annotated[int | None, Field(description="Ignored — all officers are returned in one call.")] = None,
    ) -> CompanyOfficersResult:
        """Fetch active officers for a Companies House company number.

        Returns directors, secretaries, and other active officers with
        appointment dates, nationality, and country of residence.
        Resigned officers are excluded. Pagination is handled internally —
        do NOT pass items_per_page or start_index; this tool takes only
        company_number.
        """
        return await _fetch_company_officers(_normalise_company_number(company_number))

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
            "Active officers for a Companies House company number. "
            "Flags directors with >=10 other appointments (nominee/phoenix risk signal)."
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
