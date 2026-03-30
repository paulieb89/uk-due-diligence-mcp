"""
tools/companies_house.py — Companies House API tools (4 tools).

Covers:
  - company_search      → search by name/keyword
  - company_profile     → full company record with filing signals
  - company_officers    → directors with appointment count risk signal
  - company_psc         → persons with significant control / beneficial ownership
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

from http_client import (
    _request_with_retry,
    companies_house_client,
    format_api_error,
)
from inputs import (
    CompanyOfficersInput,
    CompanyProfileInput,
    CompanyPSCInput,
    CompanySearchInput,
    ResponseFormat,
)

# ---------------------------------------------------------------------------
# Risk thresholds
# ---------------------------------------------------------------------------
HIGH_APPOINTMENT_COUNT = 10  # Directors with ≥ this many active appointments flagged


def _flag(condition: bool, label: str) -> str:
    return f"🚩 {label}" if condition else f"✅ {label}"


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _format_company_summary(item: dict[str, Any]) -> str:
    sic = ", ".join(item.get("sic_codes", [])) or "—"
    return (
        f"**{item.get('title', 'Unknown')}**\n"
        f"  Number: {item.get('company_number', '—')}\n"
        f"  Status: {item.get('company_status', '—')}\n"
        f"  Type: {item.get('company_type', '—')}\n"
        f"  SIC: {sic}\n"
        f"  Incorporated: {item.get('date_of_creation', '—')}\n"
        f"  Address: {_address_str(item.get('registered_office_address', {}))}\n"
    )


def _address_str(addr: dict[str, Any]) -> str:
    parts = [
        addr.get("address_line_1", ""),
        addr.get("address_line_2", ""),
        addr.get("locality", ""),
        addr.get("postal_code", ""),
        addr.get("country", ""),
    ]
    return ", ".join(p for p in parts if p)


def _format_officer(o: dict[str, Any], idx: int) -> str:
    name = o.get("name", "Unknown")
    role = o.get("officer_role", "—")
    appointed = o.get("appointed_on", "—")
    resigned = o.get("resigned_on")
    dob = o.get("date_of_birth", {})
    dob_str = f"{dob.get('month', '?')}/{dob.get('year', '?')}" if dob else "—"
    nationality = o.get("nationality", "—")
    other_apps = o.get("appointment_count", 0)

    risk = ""
    if other_apps >= HIGH_APPOINTMENT_COUNT:
        risk = f" 🚩 HIGH-APPOINTMENT-COUNT ({other_apps} total appointments)"

    status = f"resigned {resigned}" if resigned else "active"
    return (
        f"{idx}. **{name}** ({role}) — {status}\n"
        f"   Appointed: {appointed} | DOB: {dob_str} | Nationality: {nationality}\n"
        f"   Other appointments: {other_apps}{risk}\n"
    )


def _format_psc_entry(p: dict[str, Any], idx: int) -> str:
    kind = p.get("kind", "—")
    name = p.get("name", "Unknown")
    natures = ", ".join(p.get("natures_of_control", [])) or "—"
    ceased = p.get("ceased_on")
    notified = p.get("notified_on", "—")
    nationality = p.get("nationality", "")
    country = p.get("country_of_residence", "")

    status = f"ceased {ceased}" if ceased else "active"
    location = ", ".join(x for x in [nationality, country] if x) or "—"
    return (
        f"{idx}. **{name}** [{kind}] — {status}\n"
        f"   Notified: {notified} | Location: {location}\n"
        f"   Control: {natures}\n"
    )


# ---------------------------------------------------------------------------
# Tool registration helper — injected by server.py
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
    async def company_search(params: CompanySearchInput) -> str:
        """Search the Companies House register by company name or keyword.

        Returns a paginated list of matching companies with name, number,
        status, SIC codes, incorporation date, and registered address.
        Use company_profile for the full record once you have the company number.

        Args:
            params (CompanySearchInput): Validated input containing:
                - query (str): Company name or keyword
                - company_status (Optional[CompanyStatus]): Status filter
                - company_type (Optional[CompanyType]): Type filter
                - items_per_page (int): Results per page (default 20, max 100)
                - start_index (int): Pagination offset
                - response_format (ResponseFormat): 'markdown' or 'json'

        Returns:
            str: Paginated list of matching companies in requested format.
        """
        qs: dict[str, Any] = {
            "q": params.query,
            "items_per_page": params.items_per_page,
            "start_index": params.start_index,
        }
        if params.company_status:
            qs["status"] = params.company_status.value
        if params.company_type:
            qs["type"] = params.company_type.value

        try:
            async with companies_house_client() as client:
                resp = await _request_with_retry(client, "GET", "/search/companies", params=qs)
                data = resp.json()
        except Exception as exc:
            return format_api_error(exc, "company_search")

        items = data.get("items", [])
        total = data.get("total_results", 0)
        has_more = (params.start_index + params.items_per_page) < total

        if params.response_format == ResponseFormat.JSON:
            return json.dumps(
                {
                    "total_results": total,
                    "returned": len(items),
                    "start_index": params.start_index,
                    "has_more": has_more,
                    "items": items,
                },
                indent=2,
            )

        if not items:
            return f"No companies found matching **{params.query}**."

        lines = [
            f"## Companies House Search: '{params.query}'\n",
            f"**{total:,} total results** — showing {params.start_index + 1}–"
            f"{params.start_index + len(items)} | "
            f"{'More results available.' if has_more else 'End of results.'}\n",
        ]
        for item in items:
            lines.append(_format_company_summary(item))
        return "\n".join(lines)

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
    async def company_profile(params: CompanyProfileInput) -> str:
        """Retrieve the full Companies House profile for a specific company number.

        Returns corporate status, registered address, SIC codes, accounts and
        confirmation statement status (overdue flags), charges count, and
        incorporation date. Accounts overdue and high charge counts are early
        distress signals.

        Args:
            params (CompanyProfileInput): Validated input containing:
                - company_number (str): CH company number, e.g. '12345678'
                - response_format (ResponseFormat): 'markdown' or 'json'

        Returns:
            str: Full company profile with filing compliance signals.
        """
        try:
            async with companies_house_client() as client:
                resp = await _request_with_retry(
                    client, "GET", f"/company/{params.company_number}"
                )
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return (
                    f"Company number **{params.company_number}** not found in "
                    "Companies House. Check the number and try again."
                )
            return format_api_error(exc, "company_profile")
        except Exception as exc:
            return format_api_error(exc, "company_profile")

        if params.response_format == ResponseFormat.JSON:
            return json.dumps(data, indent=2)

        # Signals
        accs = data.get("accounts", {})
        conf = data.get("confirmation_statement", {})
        charges = data.get("has_charges", False)
        accs_overdue = accs.get("overdue", False)
        conf_overdue = conf.get("overdue", False)
        last_accounts_made_up = accs.get("last_accounts", {}).get("made_up_to", "—")
        next_accounts_due = accs.get("next_due", "—")
        next_conf_due = conf.get("next_due", "—")

        addr = _address_str(data.get("registered_office_address", {}))
        sic = ", ".join(data.get("sic_codes", [])) or "—"

        return (
            f"## {data.get('company_name', 'Unknown')}\n"
            f"**Number:** {data.get('company_number', '—')}  \n"
            f"**Status:** {data.get('company_status', '—')}  \n"
            f"**Type:** {data.get('company_type', '—')}  \n"
            f"**Incorporated:** {data.get('date_of_creation', '—')}  \n"
            f"**SIC Codes:** {sic}  \n"
            f"**Registered Address:** {addr}  \n\n"
            f"### Filing Compliance\n"
            f"{_flag(not accs_overdue, 'Accounts')} — last made up to {last_accounts_made_up}, "
            f"next due {next_accounts_due}  \n"
            f"{_flag(not conf_overdue, 'Confirmation statement')} — next due {next_conf_due}  \n"
            f"{'🚩 Has active charges registered' if charges else '✅ No charges registered'}  \n"
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
    async def company_officers(params: CompanyOfficersInput) -> str:
        """List directors and officers for a Companies House company number.

        Returns names, roles, appointment dates, nationality, and total
        appointment count. Directors with a high appointment count (≥10 other
        companies) are flagged as a risk signal — a common trait in nominee
        director fraud and phoenix company structures.

        Args:
            params (CompanyOfficersInput): Validated input containing:
                - company_number (str): CH company number
                - include_resigned (bool): Include resigned officers (default False)
                - response_format (ResponseFormat): 'markdown' or 'json'

        Returns:
            str: Officer list with risk flags for high-appointment-count directors.
        """
        qs: dict[str, Any] = {"items_per_page": 100}
        if not params.include_resigned:
            qs["register_view"] = "true"

        try:
            async with companies_house_client() as client:
                resp = await _request_with_retry(
                    client, "GET",
                    f"/company/{params.company_number}/officers",
                    params=qs,
                )
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return f"No officers found for company **{params.company_number}**."
            return format_api_error(exc, "company_officers")
        except Exception as exc:
            return format_api_error(exc, "company_officers")

        items = data.get("items", [])
        total = data.get("total_results", 0)

        if params.response_format == ResponseFormat.JSON:
            return json.dumps(
                {"company_number": params.company_number, "total": total, "officers": items},
                indent=2,
            )

        if not items:
            return f"No officers found for company **{params.company_number}**."

        high_count_flags = [
            o for o in items
            if o.get("appointment_count", 0) >= HIGH_APPOINTMENT_COUNT and not o.get("resigned_on")
        ]

        lines = [
            f"## Officers — {params.company_number}\n",
            f"**{total} total officers** ({'including resigned' if params.include_resigned else 'active only'})\n",
        ]
        if high_count_flags:
            lines.append(
                f"🚩 **{len(high_count_flags)} director(s) with ≥{HIGH_APPOINTMENT_COUNT} appointments** "
                "(nominee/phoenix risk signal)\n"
            )
        lines.append("")
        for i, officer in enumerate(items, 1):
            lines.append(_format_officer(officer, i))

        return "\n".join(lines)

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
    async def company_psc(params: CompanyPSCInput) -> str:
        """Retrieve Persons with Significant Control (PSC) for a company.

        PSC data reveals beneficial ownership — individuals or corporate entities
        holding >25% shares, voting rights, or appointment power. Corporate PSC
        entries with overseas registration addresses are a key flag in beneficial
        ownership investigations.

        Args:
            params (CompanyPSCInput): Validated input containing:
                - company_number (str): CH company number
                - response_format (ResponseFormat): 'markdown' or 'json'

        Returns:
            str: Beneficial owner list with nature of control and domicile.
        """
        try:
            async with companies_house_client() as client:
                resp = await _request_with_retry(
                    client, "GET",
                    f"/company/{params.company_number}/persons-with-significant-control",
                )
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return f"No PSC data found for company **{params.company_number}**."
            return format_api_error(exc, "company_psc")
        except Exception as exc:
            return format_api_error(exc, "company_psc")

        items = data.get("items", [])
        total = data.get("total_results", 0)

        if params.response_format == ResponseFormat.JSON:
            return json.dumps(
                {"company_number": params.company_number, "total": total, "psc": items},
                indent=2,
            )

        if not items:
            return (
                f"No PSC entries for **{params.company_number}**. "
                "This may indicate exempt status or an unresolved exemption notice."
            )

        overseas_flags = [
            p for p in items
            if p.get("kind") in ("corporate-entity-person-with-significant-control",
                                  "legal-person-person-with-significant-control")
            and p.get("identification", {}).get("place_registered", "").upper()
            not in ("", "ENGLAND AND WALES", "SCOTLAND", "NORTHERN IRELAND", "WALES", "ENGLAND")
        ]

        lines = [
            f"## Persons with Significant Control — {params.company_number}\n",
            f"**{total} PSC entries**\n",
        ]
        if overseas_flags:
            lines.append(
                f"🚩 **{len(overseas_flags)} overseas corporate PSC(s)** — beneficial ownership chain extends offshore\n"
            )
        lines.append("")
        for i, psc in enumerate(items, 1):
            lines.append(_format_psc_entry(psc, i))

        return "\n".join(lines)
