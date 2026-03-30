"""
tools/charity.py — Charity Commission API tools (2 tools).

Covers:
  - charity_search   → search by name/keyword
  - charity_profile  → full charity record with trustees, finances, filing history
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

from http_client import (
    _request_with_retry,
    charity_client,
    format_api_error,
)
from inputs import (
    CharityProfileInput,
    CharitySearchInput,
    ResponseFormat,
)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _format_charity_summary(item: dict[str, Any]) -> str:
    reg_num = item.get("registrationNumber", item.get("regno", "—"))
    status = item.get("registrationStatus", "—")
    activities = item.get("charityActivities", item.get("activities", ""))
    activities_short = (activities[:120] + "…") if len(activities) > 120 else activities
    return (
        f"**{item.get('charityName', item.get('name', 'Unknown'))}**\n"
        f"  Charity No: {reg_num} | Status: {status}\n"
        f"  Activities: {activities_short or '—'}\n"
    )


def _format_finances(item: dict[str, Any]) -> str:
    income = item.get("income", item.get("latestIncome", None))
    expenditure = item.get("expenditure", item.get("latestExpenditure", None))
    if income is not None:
        income_str = f"£{income:,.0f}" if isinstance(income, (int, float)) else str(income)
    else:
        income_str = "—"
    if expenditure is not None:
        exp_str = f"£{expenditure:,.0f}" if isinstance(expenditure, (int, float)) else str(expenditure)
    else:
        exp_str = "—"
    return f"Income: {income_str} | Expenditure: {exp_str}"


# ---------------------------------------------------------------------------
# Tool registration helper
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
    async def charity_search(params: CharitySearchInput) -> str:
        """Search the Charity Commission register of England and Wales by name or keyword.

        Returns matching charities with registration number, status, and
        activities summary. Use charity_profile for full details once you
        have the charity number.

        Args:
            params (CharitySearchInput): Validated input containing:
                - query (str): Charity name or keyword
                - registration_status (Optional[CharityRegistrationStatus]): Status filter
                - page_size (int): Results per page (default 20, max 100)
                - page_num (int): Page number (default 1)
                - response_format (ResponseFormat): 'markdown' or 'json'

        Returns:
            str: Paginated list of matching charities.
        """
        payload = {
            "keyword": params.query,
            "pageSize": params.page_size,
            "pageNum": params.page_num,
        }
        if params.registration_status:
            payload["registrationStatus"] = params.registration_status.value

        try:
            async with charity_client() as client:
                resp = await _request_with_retry(
                    client, "POST",
                    "/charities/search",
                    json=payload,
                )
                data = resp.json()
        except Exception as exc:
            return format_api_error(exc, "charity_search")

        # Charity Commission API returns different shapes; handle both
        charities = (
            data.get("charities")
            or data.get("data")
            or (data if isinstance(data, list) else [])
        )
        total = data.get("total", data.get("totalMatches", len(charities)))

        if params.response_format == ResponseFormat.JSON:
            return json.dumps(
                {
                    "total": total,
                    "page": params.page_num,
                    "page_size": params.page_size,
                    "charities": charities,
                },
                indent=2,
            )

        if not charities:
            return f"No charities found matching **{params.query}**."

        lines = [
            f"## Charity Commission Search: '{params.query}'\n",
            f"**{total:,} total results** — page {params.page_num}\n",
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
    async def charity_profile(params: CharityProfileInput) -> str:
        """Retrieve the full Charity Commission profile for a registered charity.

        Returns trustees, income/expenditure, filing history, governing document
        type, area of operation, and beneficiary description. Useful for
        verifying charitable status and governance quality.

        Args:
            params (CharityProfileInput): Validated input containing:
                - charity_number (str): Charity Commission registration number
                - response_format (ResponseFormat): 'markdown' or 'json'

        Returns:
            str: Full charity profile including finances and governance details.
        """
        try:
            async with charity_client() as client:
                resp = await _request_with_retry(
                    client, "GET",
                    f"/charities/{params.charity_number}",
                )
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return (
                    f"Charity number **{params.charity_number}** not found. "
                    "Check the number or use charity_search to locate the charity first."
                )
            return format_api_error(exc, "charity_profile")
        except Exception as exc:
            return format_api_error(exc, "charity_profile")

        if params.response_format == ResponseFormat.JSON:
            return json.dumps(data, indent=2)

        name = data.get("charityName", data.get("name", "Unknown"))
        reg_num = data.get("registrationNumber", data.get("regno", params.charity_number))
        status = data.get("registrationStatus", "—")
        obj = data.get("charityActivities", data.get("objects", "—"))
        doc_type = data.get("governingDocumentDescription", "—")
        area = data.get("areaOfBenefit", "—")
        trustees = data.get("trustees", [])
        trustee_count = len(trustees) if isinstance(trustees, list) else data.get("numTrustees", "—")

        finances = _format_finances(data)

        return (
            f"## {name}\n"
            f"**Charity No:** {reg_num} | **Status:** {status}  \n"
            f"**Governing Document:** {doc_type}  \n"
            f"**Area of Benefit:** {area}  \n"
            f"**Trustees:** {trustee_count}  \n"
            f"**Finances:** {finances}  \n\n"
            f"### Objects / Activities\n{obj or '—'}\n"
        )
