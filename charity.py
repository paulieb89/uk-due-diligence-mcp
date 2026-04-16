"""
Charity Commission API tools (2 tools).

Covers:
  - charity_search   -> search by name/keyword
  - charity_profile  -> full charity record with trustees, finances, filing history
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field
from fastmcp import FastMCP

from http_client import _request_with_retry, charity_client
from models import (
    CharityClassification,
    CharityProfile,
    CharitySearchItem,
    CharitySearchResult,
    CharityTrustee,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_STATUS_LABELS = {"R": "Registered", "RM": "Removed"}


def _coerce_number(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _build_address(data: dict[str, Any]) -> str | None:
    addr_parts = [data.get(f"address_line_{w}") for w in ["one", "two", "three", "four", "five"]]
    addr_parts = [p for p in addr_parts if p]
    postcode = data.get("address_post_code", "")
    joined = ", ".join(addr_parts + ([postcode] if postcode else []))
    return joined or None


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
        offset: Annotated[int, Field(description="Number of items to skip before this page. Default 0.", ge=0, le=10000)] = 0,
        limit: Annotated[int, Field(description="Max items to return in this page. Default 20; raise to 100 for bulk views.", ge=1, le=100)] = 20,
    ) -> CharitySearchResult:
        """Search the Charity Commission register of England and Wales by name or keyword.

        Returns matching charities with registration number, status, and
        registration date. Use charity_profile for full details once you
        have the charity number. The upstream `searchCharityName` endpoint
        returns the full list in one shot — pagination is applied
        client-side via offset/limit.
        """
        async with charity_client() as client:
            resp = await _request_with_retry(
                client, "GET",
                f"/searchCharityName/{query}",
            )
            data = resp.json()

        # API returns a list of charity objects directly
        all_charities = data if isinstance(data, list) else []
        total = len(all_charities)

        page_slice = all_charities[offset : offset + limit]

        items: list[CharitySearchItem] = []
        for raw in page_slice:
            raw_status = raw.get("reg_status")
            items.append(
                CharitySearchItem(
                    charity_number=str(raw.get("reg_charity_number")) if raw.get("reg_charity_number") is not None else None,
                    charity_name=raw.get("charity_name"),
                    reg_status=raw_status,
                    reg_status_label=_STATUS_LABELS.get(raw_status or "", raw_status),
                    date_of_registration=(raw.get("date_of_registration") or "")[:10] or None,
                )
            )

        has_more = (offset + len(items)) < total

        return CharitySearchResult(
            query=query,
            total=total,
            offset=offset,
            limit=limit,
            returned=len(items),
            has_more=has_more,
            charities=items,
        )

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
        max_trustees: Annotated[int, Field(description="Cap on the number of trustees returned. Prolific charities have 50+ trustees on file. Default 30.", ge=1, le=500)] = 30,
        max_classifications: Annotated[int, Field(description="Cap on the number of Who/What/Where classification entries returned. Large charities have 100+. Default 50.", ge=1, le=500)] = 50,
    ) -> CharityProfile:
        """Retrieve the full Charity Commission profile for a registered charity.

        Returns trustees, income/expenditure, filing history, governing
        document type, area of operation, and beneficiary description.
        Useful for verifying charitable status and governance quality.
        Trustee and classification lists are capped via max_trustees and
        max_classifications to keep responses bounded.
        """
        async with charity_client() as client:
            # allcharitydetails/{RegNumber}/{suffix} — suffix 0 = main charity
            suffix = "0"
            lookup_number = charity_number
            if "-" in charity_number:
                parts = charity_number.split("-", 1)
                lookup_number = parts[0]
                suffix = parts[1]
            resp = await _request_with_retry(
                client, "GET",
                f"/allcharitydetails/{lookup_number}/{suffix}",
            )
            data = resp.json()

        reg_num = str(data.get("reg_charity_number") or lookup_number)
        raw_status = data.get("reg_status")

        # Trustees — cap per max_trustees
        raw_trustees = data.get("trustee_names") or []
        trustees_total = len(raw_trustees)
        trustees_truncated = trustees_total > max_trustees
        trustees_slice = raw_trustees[:max_trustees]
        trustees = [
            CharityTrustee(trustee_name=t.get("trustee_name"))
            for t in trustees_slice
            if isinstance(t, dict)
        ]

        # Who/What/Where classifications — cap per max_classifications
        raw_www = data.get("who_what_where") or []
        www_total = len(raw_www)
        www_truncated = www_total > max_classifications
        www_slice = raw_www[:max_classifications]
        classifications = [
            CharityClassification(
                classification_type=w.get("classification_type"),
                classification_desc=w.get("classification_desc"),
            )
            for w in www_slice
            if isinstance(w, dict)
        ]

        # Countries of operation — upstream is already truncated to 10
        countries_raw = data.get("CharityAoOCountryContinent") or []
        countries = [c.get("country") for c in countries_raw[:10] if isinstance(c, dict) and c.get("country")]

        return CharityProfile(
            charity_number=reg_num,
            charity_name=data.get("charity_name"),
            reg_status=raw_status,
            reg_status_label=_STATUS_LABELS.get(raw_status or "", raw_status),
            charity_type=data.get("charity_type"),
            charity_co_reg_number=data.get("charity_co_reg_number") or None,
            date_of_registration=(data.get("date_of_registration") or "")[:10] or None,
            address=_build_address(data),
            latest_income=_coerce_number(data.get("latest_income")),
            latest_expenditure=_coerce_number(data.get("latest_expenditure")),
            insolvent=bool(data.get("insolvent", False)),
            in_administration=bool(data.get("in_administration", False)),
            trustee_names=trustees,
            trustee_names_truncated=trustees_truncated,
            trustee_names_total=trustees_total,
            who_what_where=classifications,
            who_what_where_truncated=www_truncated,
            who_what_where_total=www_total,
            countries_of_operation=countries,
            raw=data,
        )
