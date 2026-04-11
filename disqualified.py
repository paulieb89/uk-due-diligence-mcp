"""
Disqualified Directors tools (2 tools).

Covers:
  - disqualified_search   -> search disqualified officers by name
  - disqualified_profile  -> full disqualification record by officer ID

Uses the same Companies House REST API and API key as companies_house.py.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

import httpx
from pydantic import Field
from fastmcp import FastMCP

from http_client import (
    _request_with_retry,
    companies_house_client,
    format_api_error,
)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _format_disqualification_summary(item: dict[str, Any]) -> str:
    title = item.get("title", "Unknown")
    dob = item.get("date_of_birth", "—")
    snippet = item.get("snippet", "")
    address = _address_str(item.get("address", {}))
    links = item.get("links", {})
    officer_id = links.get("self", "").rstrip("/").rsplit("/", 1)[-1] if links else "—"
    return (
        f"**{title}**\n"
        f"  Officer ID: {officer_id}\n"
        f"  Date of birth: {dob}\n"
        f"  Address: {address}\n"
        f"  {snippet}\n"
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


def _format_disqualification(d: dict[str, Any], idx: int) -> str:
    reason = d.get("reason", {})
    act = reason.get("act", "—")
    section = reason.get("section", "—")
    description = reason.get("description_identifier", "—")
    from_date = d.get("disqualified_from", "—")
    to_date = d.get("disqualified_until", "—")
    company = d.get("company_names", [])
    company_str = ", ".join(company) if company else "—"
    address = _address_str(d.get("address", {}))

    return (
        f"{idx}. **{description}**\n"
        f"   Period: {from_date} to {to_date}\n"
        f"   Act: {act}, Section {section}\n"
        f"   Companies: {company_str}\n"
        f"   Address: {address}\n"
    )


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

def register_tools(mcp: FastMCP) -> None:

    # ------------------------------------------------------------------ #
    # 1. disqualified_search
    # ------------------------------------------------------------------ #
    @mcp.tool(
        name="disqualified_search",
        annotations={
            "title": "Search Disqualified Directors",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def disqualified_search(
        query: Annotated[str, Field(description="Name of the person to search for", min_length=2, max_length=200)],
        items_per_page: Annotated[int, Field(description="Results per page (max 100)", ge=1, le=100)] = 20,
        start_index: Annotated[int, Field(description="Pagination offset (0-based)", ge=0)] = 0,
        response_format: Annotated[str, Field(description="Output format: 'markdown' or 'json'")] = "markdown",
    ) -> str:
        """
        Search Companies House for disqualified directors by name.

        Returns names, dates of birth, disqualification periods, and officer IDs
        that can be used with disqualified_profile for full details.

        Use this to check whether an individual has been disqualified from acting
        as a company director in the UK.
        """
        try:
            async with companies_house_client() as client:
                resp = await _request_with_retry(
                    client, "GET", "/search/disqualified-officers",
                    params={
                        "q": query.strip(),
                        "items_per_page": items_per_page,
                        "start_index": start_index,
                    },
                )
                data = resp.json()
        except Exception as exc:
            return format_api_error(exc, "disqualified_search")

        items = data.get("items", [])
        total = data.get("total_results", 0)

        if response_format == "json":
            return json.dumps(data, indent=2, default=str)

        if not items:
            return f"No disqualified officers found matching **{query}**."

        lines = [f"### Disqualified Officers — \"{query}\" ({total} total)\n"]
        for item in items:
            lines.append(_format_disqualification_summary(item))
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # 2. disqualified_profile
    # ------------------------------------------------------------------ #
    @mcp.tool(
        name="disqualified_profile",
        annotations={
            "title": "Disqualified Director Profile",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def disqualified_profile(
        officer_id: Annotated[str, Field(description="Officer ID from disqualified_search results", min_length=5, max_length=50)],
        response_format: Annotated[str, Field(description="Output format: 'markdown' or 'json'")] = "markdown",
    ) -> str:
        """
        Get the full disqualification record for a disqualified director.

        Returns all disqualification orders: reason, Act and section, period,
        associated companies, and undertaking details.

        The officer_id comes from the disqualified_search results.
        Tries the natural person endpoint first, then the corporate officer endpoint.
        """
        oid = officer_id.strip()

        for endpoint in [f"/disqualified-officers/natural/{oid}", f"/disqualified-officers/corporate/{oid}"]:
            try:
                async with companies_house_client() as client:
                    resp = await _request_with_retry(client, "GET", endpoint)
                    data = resp.json()
                    break
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    continue
                return format_api_error(exc, "disqualified_profile")
            except Exception as exc:
                return format_api_error(exc, "disqualified_profile")
        else:
            return f"No disqualification record found for officer ID **{oid}**."

        if response_format == "json":
            return json.dumps(data, indent=2, default=str)

        name = data.get("forename", "") + " " + data.get("surname", "")
        name = name.strip() or data.get("name", "Unknown")
        dob = data.get("date_of_birth", "—")
        nationality = data.get("nationality", "—")
        disqualifications = data.get("disqualifications", [])

        lines = [
            f"### Disqualified Director — {name}\n",
            f"**Date of birth:** {dob}",
            f"**Nationality:** {nationality}",
            f"**Disqualifications:** {len(disqualifications)}\n",
        ]

        for idx, d in enumerate(disqualifications, 1):
            lines.append(_format_disqualification(d, idx))

        return "\n".join(lines)
