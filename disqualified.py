"""
Disqualified Directors tools.

Covers:
  - disqualified_search   (tool)     -> search disqualified officers by name
  - disqualified_profile  (tool + resource) -> full disqualification record by officer ID

Uses the same Companies House REST API and API key as companies_house.py.
"""

from __future__ import annotations

from typing import Annotated, Any

import httpx
from pydantic import Field
from fastmcp import FastMCP

from http_client import _request_with_retry, companies_house_client
from models import (
    DisqualificationOrder,
    DisqualifiedProfile,
    DisqualifiedSearchItem,
    DisqualifiedSearchResult,
)


def _extract_officer_id(links: dict[str, Any]) -> str | None:
    self_link = (links or {}).get("self", "") if isinstance(links, dict) else ""
    if not self_link:
        return None
    tail = self_link.rstrip("/").rsplit("/", 1)[-1]
    return tail or None


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
        items_per_page: Annotated[int, Field(description="Results per page (max 100). Default 20.", ge=1, le=100)] = 20,
        start_index: Annotated[int, Field(description="Pagination offset (0-based). Default 0.", ge=0, le=10000)] = 0,
    ) -> DisqualifiedSearchResult:
        """Check whether a named individual is banned from acting as a UK company director.

        Use this tool when asked to check disqualified, banned, or barred directors.
        Query must be an individual's name (e.g. "Richard Howson") — NOT a company
        name, which always returns zero results.

        Returns names, dates of birth, disqualification period snippets, and
        officer IDs that can be used with disqualified_profile for full details.
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
        except Exception:
            data = {}

        raw_items = data.get("items", []) or []
        total_results = int(data.get("total_results", 0) or 0)

        items = [
            DisqualifiedSearchItem(
                officer_id=_extract_officer_id(raw.get("links") or {}),
                title=raw.get("title"),
                date_of_birth=raw.get("date_of_birth"),
                snippet=raw.get("snippet"),
                address=raw.get("address") or {},
                links=raw.get("links") or {},
            )
            for raw in raw_items
        ]

        has_more = (start_index + len(items)) < total_results

        return DisqualifiedSearchResult(
            query=query,
            total_results=total_results,
            start_index=start_index,
            items_per_page=items_per_page,
            returned=len(items),
            has_more=has_more,
            items=items,
        )

    @mcp.tool(
        name="disqualified_profile",
        annotations={
            "title": "Get Disqualified Director Profile",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def disqualified_profile(
        officer_id: Annotated[str, Field(description="Companies House officer ID. Returned by disqualified_search.", min_length=1, max_length=100)],
    ) -> DisqualifiedProfile:
        """Fetch the full disqualification record for a director by officer ID.

        Returns all disqualification orders: reason, Act/section cited,
        disqualification period, and associated company names. Use
        disqualified_search first to find the officer ID.
        """
        return await _fetch_disqualified_profile(officer_id)


# ---------------------------------------------------------------------------
# Shared fetch helper
# ---------------------------------------------------------------------------

async def _fetch_disqualified_profile(officer_id: str) -> DisqualifiedProfile:
    oid = officer_id.strip()
    data: dict[str, Any] | None = None
    officer_kind = "natural"

    for kind, endpoint in [
        ("natural", f"/disqualified-officers/natural/{oid}"),
        ("corporate", f"/disqualified-officers/corporate/{oid}"),
    ]:
        try:
            async with companies_house_client() as client:
                resp = await _request_with_retry(client, "GET", endpoint)
                data = resp.json()
                officer_kind = kind
                break
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                continue
            raise

    if data is None:
        raise LookupError(
            f"No disqualification record found for officer ID {oid!r}."
        )

    raw_orders = data.get("disqualifications", []) or []
    orders: list[DisqualificationOrder] = []
    for raw in raw_orders:
        company_names = list(raw.get("company_names") or [])
        total_companies = len(company_names)
        truncated = total_companies > 20
        if truncated:
            company_names = company_names[:20]

        orders.append(
            DisqualificationOrder(
                disqualified_from=raw.get("disqualified_from"),
                disqualified_until=raw.get("disqualified_until"),
                reason=raw.get("reason") or {},
                company_names=company_names,
                company_names_truncated=truncated,
                company_names_total=total_companies,
                address=raw.get("address") or {},
                case_identifier=raw.get("case_identifier"),
                heard_on=raw.get("heard_on"),
                last_variation=raw.get("last_variation") or {},
                undertaken_on=raw.get("undertaken_on"),
            )
        )

    forename = data.get("forename") or None
    surname = data.get("surname") or None
    composed = " ".join(p for p in [forename, surname] if p).strip() or None
    name = composed or data.get("name")

    return DisqualifiedProfile(
        officer_id=oid,
        officer_kind=officer_kind,
        name=name,
        forename=forename,
        surname=surname,
        date_of_birth=data.get("date_of_birth"),
        nationality=data.get("nationality"),
        disqualifications=orders,
    )


# ---------------------------------------------------------------------------
# Resource registration
# ---------------------------------------------------------------------------

def register_resources(mcp: FastMCP) -> None:

    @mcp.resource(
        "disqualification://{officer_id}",
        name="disqualified_profile",
        description=(
            "Full disqualification record for a director by officer ID. "
            "Returns all orders: reason, Act/section, period, and associated companies."
        ),
        mime_type="application/json",
    )
    async def disqualified_profile_resource(officer_id: str) -> str:
        result = await _fetch_disqualified_profile(officer_id)
        return result.model_dump_json()
