"""
Companies House API tools (4 tools).

Covers:
  - company_search      -> search by name/keyword
  - company_profile     -> full company record with filing signals
  - company_officers    -> directors with appointment count risk signal
  - company_psc         -> persons with significant control / beneficial ownership
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
            "title": "Get Full Company Profile",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def company_profile(
        company_number: Annotated[str, Field(description="Companies House company number, e.g. '12345678' or 'SC123456'", min_length=6, max_length=10)],
    ) -> CompanyProfile:
        """Retrieve the full Companies House profile for a specific company number.

        Returns corporate status, registered address, SIC codes, accounts
        and confirmation statement filing status (with overdue flags),
        active-charges flag, and incorporation date. Accounts overdue and
        active charges are early distress signals worth cross-referencing
        with gazette_insolvency.
        """
        company_number = _normalise_company_number(company_number)

        async with companies_house_client() as client:
            resp = await _request_with_retry(
                client, "GET", f"/company/{company_number}"
            )
            data = resp.json()

        accs_raw = data.get("accounts") or {}
        conf_raw = data.get("confirmation_statement") or {}

        accounts = CompanyAccountsSummary(
            overdue=bool(accs_raw.get("overdue", False)),
            last_accounts_made_up_to=(accs_raw.get("last_accounts") or {}).get("made_up_to"),
            next_due=accs_raw.get("next_due"),
        )
        confirmation = CompanyConfirmationStatementSummary(
            overdue=bool(conf_raw.get("overdue", False)),
            next_due=conf_raw.get("next_due"),
        )

        return CompanyProfile(
            company_number=str(data.get("company_number") or company_number),
            company_name=data.get("company_name"),
            company_status=data.get("company_status"),
            company_type=data.get("company_type"),
            date_of_creation=data.get("date_of_creation"),
            sic_codes=list(data.get("sic_codes") or []),
            registered_office_address=data.get("registered_office_address") or {},
            has_charges=bool(data.get("has_charges", False)),
            accounts=accounts,
            confirmation_statement=confirmation,
            raw=data,
        )

    # ------------------------------------------------------------------ #
    # 3. company_officers
    # ------------------------------------------------------------------ #
    @mcp.tool(
        name="company_officers",
        annotations={
            "title": "List Company Officers",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def company_officers(
        company_number: Annotated[str, Field(description="Companies House company number", min_length=6, max_length=10)],
        include_resigned: Annotated[bool, Field(description="If true, include resigned officers alongside active ones")] = False,
        limit: Annotated[int, Field(description="Max officers to fetch from Companies House (upstream items_per_page). Default 100.", ge=1, le=100)] = 100,
    ) -> CompanyOfficersResult:
        """List directors and officers for a Companies House company number.

        Returns names, roles, appointment dates, nationality, and total
        appointment count. Directors with a high appointment count
        (>=10 other companies) are flagged via
        `high_appointment_count_flag` — a common trait in nominee director
        fraud and phoenix company structures.
        """
        company_number = _normalise_company_number(company_number)
        # NB: do NOT use register_view=true — it requires a companion
        # register_type param and only returns data for the minority of
        # companies whose statutory register is held at Companies House.
        # Filter resigned officers client-side on `resigned_on` instead.
        qs: dict[str, Any] = {"items_per_page": limit}

        async with companies_house_client() as client:
            resp = await _request_with_retry(
                client, "GET",
                f"/company/{company_number}/officers",
                params=qs,
            )
            data = resp.json()

        raw_items = data.get("items", []) or []
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
                appointment_count=int(raw.get("appointment_count", 0) or 0),
                address=raw.get("address") or {},
                links=raw.get("links") or {},
            )
            for raw in raw_items
        ]

        high_count_flags = sum(
            1
            for o in officers
            if o.appointment_count >= HIGH_APPOINTMENT_COUNT and not o.resigned_on
        )

        return CompanyOfficersResult(
            company_number=company_number,
            include_resigned=include_resigned,
            total=len(officers),
            high_appointment_count_flag=high_count_flags,
            officers=officers,
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
        company_number: Annotated[str, Field(description="Companies House company number", min_length=6, max_length=10)],
        max_nature_chars: Annotated[int, Field(description="Per-entry cap on each 'nature of control' descriptor. Upstream entries are sometimes long legal text. Default 300.", ge=50, le=5000)] = 300,
    ) -> CompanyPSCResult:
        """Retrieve Persons with Significant Control (PSC) for a company.

        PSC data reveals beneficial ownership — individuals or corporate
        entities holding >25% shares, voting rights, or appointment power.
        Corporate PSC entries with overseas registration addresses are a
        key flag in beneficial ownership investigations and surface as
        `overseas_corporate_psc_flag` on the response.
        """
        company_number = _normalise_company_number(company_number)

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
            natures = _truncate_natures(
                list(raw.get("natures_of_control") or []),
                max_nature_chars,
            )
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

        return CompanyPSCResult(
            company_number=company_number,
            total=total,
            overseas_corporate_psc_flag=overseas_flag,
            psc=psc_entries,
        )
