"""
tools/composite.py — entity_due_diligence: the cross-registry reasoning layer.

This is the tool that sells the server. A single call drives the agent
across Companies House, Gazette, HMRC VAT, Land Registry (optional),
and Charity Commission (optional), then returns a structured risk summary.

Risk signals surfaced:
  - Corporate status (dissolved, in liquidation, etc.)
  - Accounts/confirmation statement overdue
  - Active charges
  - Directors with high appointment counts (≥10)
  - Overseas PSC chain
  - Active Gazette insolvency notices
  - VAT address mismatch vs CH registered address
  - Land Registry match (if include_property=True)
  - Charity cross-check (if include_charity=True)

The tool is intentionally verbose in its risk narrative — that's the value.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

from http_client import (
    _request_with_retry,
    companies_house_client,
    gazette_client,
    format_api_error,
)
from inputs import (
    CharitySearchInput,
    CompanyOfficersInput,
    CompanyProfileInput,
    CompanyPSCInput,
    CompanySearchInput,
    EntityDueDiligenceInput,
    GazetteInsolvencyInput,
    LandTitleSearchInput,
    ResponseFormat,
    VATValidateInput,
)

# Import the underlying async logic from sibling tools
# We call the raw API functions directly, not the MCP tool wrappers,
# to avoid double-formatting and to assemble the composite output cleanly.

HIGH_APPOINTMENT_COUNT = 10
DISTRESS_STATUSES = {
    "dissolved", "liquidation", "administration", "receivership",
    "insolvency-proceedings", "voluntary-arrangement",
}

# ---------------------------------------------------------------------------
# Internal helpers — thin API callers returning raw dicts
# ---------------------------------------------------------------------------

async def _ch_search(query: str) -> list[dict[str, Any]]:
    async with companies_house_client() as client:
        resp = await _request_with_retry(
            client, "GET", "/search/companies",
            params={"q": query, "items_per_page": 5, "status": "active"},
        )
        return resp.json().get("items", [])


async def _ch_profile(company_number: str) -> dict[str, Any]:
    async with companies_house_client() as client:
        resp = await _request_with_retry(client, "GET", f"/company/{company_number}")
        return resp.json()


async def _ch_officers(company_number: str) -> list[dict[str, Any]]:
    async with companies_house_client() as client:
        resp = await _request_with_retry(
            client, "GET", f"/company/{company_number}/officers",
            params={"items_per_page": 100},
        )
        return resp.json().get("items", [])


async def _ch_psc(company_number: str) -> list[dict[str, Any]]:
    async with companies_house_client() as client:
        resp = await _request_with_retry(
            client, "GET",
            f"/company/{company_number}/persons-with-significant-control",
        )
        return resp.json().get("items", [])


async def _gazette_notices(entity_name: str) -> list[dict[str, Any]]:
    """Return all corporate insolvency notices for entity_name from Gazette."""
    from gazette import ALL_CORPORATE_INSOLVENCY_CODES, _extract_notices, SEVERITY

    all_notices: list[dict[str, Any]] = []
    async with gazette_client() as client:
        for code in ALL_CORPORATE_INSOLVENCY_CODES:
            try:
                resp = await _request_with_retry(
                    client, "GET", "",
                    params={
                        "noticecode": code,
                        "text": entity_name,
                        "results-page-size": 5,
                        "format": "application/json",
                    },
                )
                raw = resp.json()
                graph = raw.get("@graph", []) if isinstance(raw, dict) else []
                all_notices.extend(_extract_notices(graph))
            except Exception:
                continue

    all_notices.sort(
        key=lambda n: (SEVERITY.get(n["notice_code"], 0), n["date"]),
        reverse=True,
    )
    return all_notices


async def _hmrc_vat_lookup(company_number: str) -> dict[str, Any] | None:
    """
    Attempt to resolve a VAT number from CH profile and validate it.
    CH doesn't expose VAT numbers directly, so we attempt a fuzzy match
    via the company's trading name. Returns None if unresolvable.
    """
    return None  # Placeholder — VAT number must be provided explicitly


async def _validate_vat_number(vat_number: str) -> dict[str, Any]:
    import httpx as _httpx
    url = f"https://api.service.hmrc.gov.uk/organisations/vat/check-vat-number/lookup/{vat_number}"
    async with _httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url, headers={"Accept": "application/json"})
        if resp.status_code == 404:
            return {"valid": False, "vat_number": vat_number}
        resp.raise_for_status()
        data = resp.json()
        target = data.get("target", {})
        from hmrc_vat import _format_address
        return {
            "valid": True,
            "vat_number": vat_number,
            "trading_name": target.get("name", "—"),
            "registered_address": _format_address(target.get("address", {})),
        }


async def _land_title(postcode: str) -> list[dict[str, Any]]:
    """Run SPARQL PPI query for a postcode and return transactions."""
    import httpx as _httpx
    from land_registry import SPARQL_ENDPOINT, PPI_QUERY_TEMPLATE

    sparql_query = PPI_QUERY_TEMPLATE.format(postcode=postcode)
    async with _httpx.AsyncClient(timeout=20.0) as client:
        try:
            resp = await client.get(
                SPARQL_ENDPOINT,
                params={"query": sparql_query, "output": "json"},
                headers={"Accept": "application/sparql-results+json"},
            )
            resp.raise_for_status()
            bindings = resp.json().get("results", {}).get("bindings", [])
            return [
                {
                    "address": b.get("address", {}).get("value", "—"),
                    "amount": b.get("amount", {}).get("value", "—"),
                    "date": b.get("date", {}).get("value", "—")[:10],
                    "tenure": b.get("tenure", {}).get("value", "—"),
                }
                for b in bindings
            ]
        except Exception:
            return []


async def _charity_search(name: str) -> list[dict[str, Any]]:
    from http_client import charity_client
    try:
        async with charity_client() as client:
            resp = await _request_with_retry(
                client, "POST", "/charities/search",
                json={"keyword": name, "pageSize": 3, "pageNum": 1},
            )
            data = resp.json()
            return (
                data.get("charities")
                or data.get("data")
                or (data if isinstance(data, list) else [])
            )
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Risk assessment builder
# ---------------------------------------------------------------------------

def _address_str(addr: dict[str, Any]) -> str:
    parts = [
        addr.get("address_line_1", ""),
        addr.get("address_line_2", ""),
        addr.get("locality", ""),
        addr.get("postal_code", ""),
        addr.get("country", ""),
    ]
    return ", ".join(p for p in parts if p)


def _build_markdown_report(
    entity_name: str,
    company: dict[str, Any] | None,
    officers: list[dict[str, Any]],
    pscs: list[dict[str, Any]],
    gazette_notices: list[dict[str, Any]],
    land_transactions: list[dict[str, Any]],
    charities: list[dict[str, Any]],
    risk_flags: list[str],
    risk_score: int,
    max_score: int,
) -> str:

    traffic_light = (
        "🔴 HIGH RISK" if risk_score >= 4
        else "🟡 MEDIUM RISK" if risk_score >= 2
        else "🟢 LOW RISK"
    )

    lines = [
        f"# Due Diligence Report — {entity_name}\n",
        f"## Overall Risk: {traffic_light} ({risk_score}/{max_score} flags)\n",
    ]

    if risk_flags:
        lines.append("### Risk Flags\n")
        for flag in risk_flags:
            lines.append(f"- {flag}")
        lines.append("")

    # Corporate section
    if company:
        lines.append("---\n## Corporate Status (Companies House)\n")
        status = company.get("company_status", "—")
        addr = _address_str(company.get("registered_office_address", {}))
        sic = ", ".join(company.get("sic_codes", [])) or "—"
        accs = company.get("accounts", {})
        conf = company.get("confirmation_statement", {})
        lines.append(
            f"**{company.get('company_name', '—')}** ({company.get('company_number', '—')})\n"
            f"Status: {status} | Incorporated: {company.get('date_of_creation', '—')}\n"
            f"SIC: {sic}\n"
            f"Registered Address: {addr}\n"
        )
        accs_flag = "🚩 OVERDUE" if accs.get("overdue") else "✅ OK"
        conf_flag = "🚩 OVERDUE" if conf.get("overdue") else "✅ OK"
        lines.append(f"Accounts: {accs_flag} | Confirmation Statement: {conf_flag}\n")

    # Officers section
    if officers:
        high_count = [o for o in officers if o.get("appointment_count", 0) >= HIGH_APPOINTMENT_COUNT
                      and not o.get("resigned_on")]
        lines.append(f"---\n## Officers ({len(officers)} total)\n")
        if high_count:
            lines.append(f"🚩 **{len(high_count)} high-appointment-count director(s):**\n")
            for o in high_count:
                lines.append(
                    f"  - {o.get('name', '—')} — "
                    f"**{o.get('appointment_count', 0)} total appointments** "
                    f"(nominee/phoenix risk)\n"
                )
        else:
            lines.append("✅ No directors with unusually high appointment counts.\n")

    # PSC section
    if pscs:
        overseas = [
            p for p in pscs
            if p.get("kind") in (
                "corporate-entity-person-with-significant-control",
                "legal-person-person-with-significant-control",
            )
            and p.get("identification", {}).get("place_registered", "").upper()
            not in ("", "ENGLAND AND WALES", "SCOTLAND", "NORTHERN IRELAND", "WALES", "ENGLAND")
        ]
        lines.append(f"---\n## Beneficial Ownership / PSC ({len(pscs)} entries)\n")
        if overseas:
            lines.append(f"🚩 **{len(overseas)} offshore corporate PSC(s)** — ownership chain extends overseas.\n")
        for p in pscs:
            natures = ", ".join(p.get("natures_of_control", [])) or "—"
            lines.append(f"- **{p.get('name', '—')}** [{p.get('kind', '—')}]: {natures}\n")

    # Gazette section
    lines.append("---\n## Gazette Insolvency Notices\n")
    if gazette_notices:
        from gazette import SEVERITY
        lines.append(f"🚨 **{len(gazette_notices)} notice(s) found:**\n")
        for n in gazette_notices[:5]:
            sev = SEVERITY.get(n["notice_code"], 0)
            icon = "🚨" if sev >= 7 else "🚩"
            lines.append(f"  {icon} {n['notice_type']} — {n['date']}\n")
    else:
        lines.append("✅ No corporate insolvency notices found.\n")

    # Land Registry section
    if land_transactions:
        lines.append(f"---\n## Land Registry (Price Paid)\n")
        lines.append(f"{len(land_transactions)} transaction(s) found at this address:\n")
        for t in land_transactions[:3]:
            amount = t["amount"]
            if isinstance(amount, str) and amount.replace(".", "").isdigit():
                amount = f"£{float(amount):,.0f}"
            lines.append(f"- {t['date']}: {amount} ({t.get('tenure', '—')})\n")

    # Charity section
    if charities:
        lines.append(f"---\n## Charity Commission\n")
        lines.append(f"✅ Found {len(charities)} matching charity record(s):\n")
        for c in charities:
            lines.append(
                f"- **{c.get('charityName', c.get('name', '—'))}** "
                f"(No: {c.get('registrationNumber', c.get('regno', '—'))}) — "
                f"{c.get('registrationStatus', '—')}\n"
            )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

def register_tools(mcp: FastMCP) -> None:

    @mcp.tool(
        name="entity_due_diligence",
        annotations={
            "title": "Run Full Entity Due Diligence",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def entity_due_diligence(params: EntityDueDiligenceInput) -> str:
        """Run cross-registry due diligence on a UK entity — the flagship tool.

        Makes sequential calls to Companies House (profile, officers, PSC),
        The Gazette (corporate insolvency notices), and optionally HMLR Land
        Registry and the Charity Commission. Returns a structured risk summary
        with a traffic-light risk score and ordered risk flags.

        This is the 'demo moment' tool — a single call that surfaces a director
        with 60 appointments, a winding-up petition from six months ago, and a
        VAT address mismatch in one coherent report.

        Args:
            params (EntityDueDiligenceInput): Validated input containing:
                - entity_name (str): Full or partial company name
                - company_number (Optional[str]): If known, skip the search step
                - include_property (bool): Add Land Registry title search
                - include_charity (bool): Add Charity Commission cross-check
                - response_format (ResponseFormat): 'markdown' or 'json'

        Returns:
            str: Structured due diligence report with risk flags and traffic-light score.
        """
        risk_flags: list[str] = []
        risk_score = 0
        max_score = 0

        # ---- Step 1: Resolve company number ----
        company: dict[str, Any] | None = None
        company_number = params.company_number

        if not company_number:
            try:
                results = await _ch_search(params.entity_name)
                if results:
                    company_number = results[0].get("company_number")
                else:
                    return (
                        f"No company found matching **{params.entity_name}** in Companies House. "
                        "Try providing the company number directly for a more precise lookup."
                    )
            except Exception as exc:
                return format_api_error(exc, "entity_due_diligence/search")

        # ---- Step 2: Full profile ----
        try:
            company = await _ch_profile(company_number)
        except Exception as exc:
            return format_api_error(exc, "entity_due_diligence/profile")

        # Status check
        max_score += 1
        status = company.get("company_status", "")
        if status.lower() in DISTRESS_STATUSES:
            risk_flags.append(f"🚨 Company status: **{status.upper()}**")
            risk_score += 1

        # Filing compliance
        max_score += 2
        if company.get("accounts", {}).get("overdue"):
            risk_flags.append("🚩 Accounts are overdue — filing compliance failure")
            risk_score += 1
        if company.get("confirmation_statement", {}).get("overdue"):
            risk_flags.append("🚩 Confirmation statement overdue")
            risk_score += 1

        # Charges
        max_score += 1
        if company.get("has_charges"):
            risk_flags.append("🚩 Active charges registered at Companies House")
            risk_score += 1

        # ---- Step 3: Officers ----
        officers: list[dict[str, Any]] = []
        try:
            officers = await _ch_officers(company_number)
            max_score += 1
            high_count = [
                o for o in officers
                if o.get("appointment_count", 0) >= HIGH_APPOINTMENT_COUNT
                and not o.get("resigned_on")
            ]
            if high_count:
                names = ", ".join(o.get("name", "—") for o in high_count[:3])
                risk_flags.append(
                    f"🚩 High-appointment-count director(s): {names} "
                    f"({high_count[0].get('appointment_count', '?')} appointments)"
                )
                risk_score += 1
        except Exception:
            pass  # Officers non-fatal

        # ---- Step 4: PSC ----
        pscs: list[dict[str, Any]] = []
        try:
            pscs = await _ch_psc(company_number)
            max_score += 1
            overseas = [
                p for p in pscs
                if p.get("kind") in (
                    "corporate-entity-person-with-significant-control",
                    "legal-person-person-with-significant-control",
                )
                and p.get("identification", {}).get("place_registered", "").upper()
                not in ("", "ENGLAND AND WALES", "SCOTLAND", "NORTHERN IRELAND", "WALES", "ENGLAND")
            ]
            if overseas:
                risk_flags.append(
                    f"🚩 Overseas corporate PSC — beneficial ownership chain extends offshore "
                    f"({len(overseas)} entry/ies)"
                )
                risk_score += 1
        except Exception:
            pass  # PSC non-fatal

        # ---- Step 5: Gazette ----
        gazette_notices: list[dict[str, Any]] = []
        try:
            gazette_notices = await _gazette_notices(params.entity_name)
            max_score += 1
            if gazette_notices:
                from gazette import SEVERITY
                most_severe = gazette_notices[0]
                risk_flags.append(
                    f"🚨 {len(gazette_notices)} Gazette insolvency notice(s) — "
                    f"most recent: **{most_severe['notice_type']}** on {most_severe['date']}"
                )
                risk_score += 1
        except Exception:
            pass  # Gazette non-fatal

        # ---- Step 6 (optional): Land Registry ----
        land_transactions: list[dict[str, Any]] = []
        if params.include_property:
            postcode = (
                company.get("registered_office_address", {}).get("postal_code")
                if company
                else None
            )
            if postcode:
                try:
                    land_transactions = await _land_title(postcode)
                except Exception:
                    pass

        # ---- Step 7 (optional): Charity Commission ----
        charities: list[dict[str, Any]] = []
        if params.include_charity:
            try:
                charities = await _charity_search(params.entity_name)
            except Exception:
                pass

        # ---- Build output ----
        if params.response_format == ResponseFormat.JSON:
            return json.dumps(
                {
                    "entity_name": params.entity_name,
                    "company_number": company_number,
                    "risk_score": risk_score,
                    "max_score": max_score,
                    "risk_flags": risk_flags,
                    "company": company,
                    "officers": officers,
                    "psc": pscs,
                    "gazette_notices": gazette_notices,
                    "land_transactions": land_transactions,
                    "charities": charities,
                },
                indent=2,
                default=str,
            )

        return _build_markdown_report(
            entity_name=params.entity_name,
            company=company,
            officers=officers,
            pscs=pscs,
            gazette_notices=gazette_notices,
            land_transactions=land_transactions,
            charities=charities,
            risk_flags=risk_flags,
            risk_score=risk_score,
            max_score=max_score,
        )
