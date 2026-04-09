"""
Charity Commission API tools (2 tools).

Covers:
  - charity_search   -> search by name/keyword
  - charity_profile  -> full charity record with trustees, finances, filing history
"""

from __future__ import annotations

import json
from typing import Annotated, Any

import httpx
from pydantic import Field
from fastmcp import FastMCP

from http_client import (
    _request_with_retry,
    charity_client,
    format_api_error,
)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

_STATUS_LABELS = {"R": "Registered", "RM": "Removed"}


def _format_charity_summary(item: dict[str, Any]) -> str:
    reg_num = item.get("reg_charity_number", "—")
    raw_status = item.get("reg_status", "—")
    status = _STATUS_LABELS.get(raw_status, raw_status)
    name = item.get("charity_name", "Unknown")
    date_reg = (item.get("date_of_registration") or "")[:10] or "—"
    return (
        f"**{name}**\n"
        f"  Charity No: {reg_num} | Status: {status} | Registered: {date_reg}\n"
    )


def _format_finances(item: dict[str, Any]) -> str:
    income = item.get("latest_income")
    expenditure = item.get("latest_expenditure")
    income_str = f"£{income:,.0f}" if isinstance(income, (int, float)) else "—"
    exp_str = f"£{expenditure:,.0f}" if isinstance(expenditure, (int, float)) else "—"
    return f"Income: {income_str} | Expenditure: {exp_str}"


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

def register_tools(mcp: FastMCP) -> None:

    # ------------------------------------------------------------------ #
    # 1. charity_search
    # ------------------------------------------------------------------ #
    @mcp.tool(
        name="charity_search",
        annotations={
            "title": "Search Charity Commission Register",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def charity_search(
        query: Annotated[str, Field(description="Charity name or keyword to search for", min_length=2, max_length=200)],
        registration_status: Annotated[str | None, Field(description="Filter by registration status: 'Registered' or 'Removed'. Default: Registered.")] = "Registered",
        page_size: Annotated[int, Field(description="Number of results per page (max 100)", ge=1, le=100)] = 20,
        page_num: Annotated[int, Field(description="Page number (1-indexed)", ge=1)] = 1,
        response_format: Annotated[str, Field(description="Output format: 'markdown' or 'json'")] = "markdown",
    ) -> str:
        """Search the Charity Commission register of England and Wales by name or keyword.

        Returns matching charities with registration number, status, and
        activities summary. Use charity_profile for full details once you
        have the charity number.
        """
        try:
            async with charity_client() as client:
                resp = await _request_with_retry(
                    client, "GET",
                    f"/searchCharityName/{query}",
                )
                data = resp.json()
        except Exception as exc:
            return format_api_error(exc, "charity_search")

        # API returns a list of charity objects directly
        charities = data if isinstance(data, list) else []
        total = len(charities)

        if response_format == "json":
            return json.dumps(
                {
                    "total": total,
                    "page": page_num,
                    "page_size": page_size,
                    "charities": charities,
                },
                indent=2,
            )

        if not charities:
            return f"No charities found matching **{query}**."

        lines = [
            f"## Charity Commission Search: '{query}'\n",
            f"**{total:,} total results** — page {page_num}\n",
        ]
        for item in charities:
            lines.append(_format_charity_summary(item))

        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # 2. charity_profile
    # ------------------------------------------------------------------ #
    @mcp.tool(
        name="charity_profile",
        annotations={
            "title": "Get Full Charity Profile",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def charity_profile(
        charity_number: Annotated[str, Field(description="Charity Commission registration number, e.g. '1234567'", min_length=6, max_length=12)],
        response_format: Annotated[str, Field(description="Output format: 'markdown' or 'json'")] = "markdown",
    ) -> str:
        """Retrieve the full Charity Commission profile for a registered charity.

        Returns trustees, income/expenditure, filing history, governing document
        type, area of operation, and beneficiary description. Useful for
        verifying charitable status and governance quality.
        """
        try:
            async with charity_client() as client:
                # allcharitydetails/{RegNumber}/{suffix} — suffix 0 = main charity
                suffix = "0"
                if "-" in charity_number:
                    parts = charity_number.split("-", 1)
                    charity_number = parts[0]
                    suffix = parts[1]
                resp = await _request_with_retry(
                    client, "GET",
                    f"/allcharitydetails/{charity_number}/{suffix}",
                )
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return (
                    f"Charity number **{charity_number}** not found. "
                    "Check the number or use charity_search to locate the charity first."
                )
            return format_api_error(exc, "charity_profile")
        except Exception as exc:
            return format_api_error(exc, "charity_profile")

        if response_format == "json":
            return json.dumps(data, indent=2)

        name = data.get("charity_name", "Unknown")
        reg_num = data.get("reg_charity_number", charity_number)
        raw_status = data.get("reg_status", "—")
        status = _STATUS_LABELS.get(raw_status, raw_status)
        charity_type = data.get("charity_type", "—")
        co_number = data.get("charity_co_reg_number", "")
        date_reg = (data.get("date_of_registration") or "")[:10] or "—"
        insolvent = data.get("insolvent", False)
        in_admin = data.get("in_administration", False)

        # Trustees
        trustee_list = data.get("trustee_names", [])
        trustee_names = [t.get("trustee_name", "—") for t in trustee_list] if trustee_list else []

        # Who/What/Where classification
        www = data.get("who_what_where", [])
        what_items = [w["classification_desc"] for w in www if w.get("classification_type") == "What"]
        who_items = [w["classification_desc"] for w in www if w.get("classification_type") == "Who"]
        where_items = [w["classification_desc"] for w in www if w.get("classification_type") == "Where"]

        # Countries of operation
        countries = data.get("CharityAoOCountryContinent", [])
        country_names = [c.get("country", "") for c in countries[:10]]

        finances = _format_finances(data)

        # Address
        addr_parts = [data.get(f"address_line_{w}") for w in ["one", "two", "three", "four", "five"]]
        addr_parts = [p for p in addr_parts if p]
        postcode = data.get("address_post_code", "")
        address = ", ".join(addr_parts + ([postcode] if postcode else [])) or "—"

        # Risk flags
        flags = []
        if insolvent:
            flags.append("INSOLVENT")
        if in_admin:
            flags.append("IN ADMINISTRATION")
        flag_str = f"  \n**Flags:** {', '.join(flags)}" if flags else ""

        lines = [
            f"## {name}",
            f"**Charity No:** {reg_num} | **Status:** {status} | **Type:** {charity_type}  ",
            f"**Registered:** {date_reg}" + (f" | **Companies House:** {co_number}" if co_number else "") + "  ",
            f"**Address:** {address}  ",
            f"**Finances:** {finances}  ",
            f"**Trustees ({len(trustee_names)}):** {', '.join(trustee_names) if trustee_names else '—'}  ",
            flag_str,
        ]
        if what_items:
            lines.append(f"\n### What\n{', '.join(what_items)}")
        if who_items:
            lines.append(f"\n### Who\n{', '.join(who_items)}")
        if where_items:
            lines.append(f"\n### Where\n{', '.join(where_items)}")
        if country_names:
            lines.append(f"\n### Countries of Operation\n{', '.join(country_names)}")

        return "\n".join(lines)
