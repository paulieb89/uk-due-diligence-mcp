"""
search_fetch.py — FastMCP canonical search + fetch tools.

Implements the two tools required for ChatGPT deep research and company
knowledge: `search` (returns {"ids": [...]}) and `fetch` (returns full
record by prefixed ID).

ID scheme:
  company:{company_number}          → Companies House profile
  charity:{charity_number}          → Charity Commission profile
  disqualification:{officer_id}     → Disqualified director profile
  notice:{notice_numeric_id}        → Gazette notice full text
"""

from __future__ import annotations

import asyncio
import json
from typing import Annotated, Any

from pydantic import Field
from fastmcp import FastMCP

from http_client import (
    _request_with_retry,
    companies_house_client,
    charity_client,
    gazette_client,
)
from companies_house import _fetch_company_profile, _normalise_company_number
from charity import _fetch_charity_profile
from disqualified import _fetch_disqualified_profile
from gazette import _fetch_gazette_notice


# ---------------------------------------------------------------------------
# Private ID-extraction helpers (search fan-out)
# ---------------------------------------------------------------------------

async def _company_ids(query: str) -> list[str]:
    async with companies_house_client() as client:
        resp = await _request_with_retry(
            client, "GET", "/search/companies",
            params={"q": query, "items_per_page": 10},
        )
    items = resp.json().get("items") or []
    return [f"company:{item['company_number']}" for item in items if item.get("company_number")]


async def _charity_ids(query: str) -> list[str]:
    async with charity_client() as client:
        resp = await _request_with_retry(
            client, "GET", f"/searchCharityName/{query}",
        )
    data = resp.json()
    all_items = data if isinstance(data, list) else []
    return [
        f"charity:{item['reg_charity_number']}"
        for item in all_items[:5]
        if item.get("reg_charity_number") is not None
    ]


async def _disqualified_ids(query: str) -> list[str]:
    async with companies_house_client() as client:
        resp = await _request_with_retry(
            client, "GET", "/search/disqualified-officers",
            params={"q": query, "items_per_page": 5},
        )
    items = resp.json().get("items") or []
    ids = []
    for item in items:
        links = item.get("links") or {}
        self_link = links.get("self", "") if isinstance(links, dict) else ""
        if self_link:
            officer_id = self_link.rstrip("/").rsplit("/", 1)[-1]
            if officer_id:
                ids.append(f"disqualification:{officer_id}")
    return ids


async def _gazette_ids(query: str) -> list[str]:
    async with gazette_client() as client:
        resp = await _request_with_retry(
            client, "GET", "/insolvency/notice/data.json",
            params={"text": query, "results-page-size": 5},
        )
    raw = resp.json()
    entries = raw.get("entry", []) if isinstance(raw, dict) else []
    if isinstance(entries, dict):
        entries = [entries]
    ids = []
    for entry in entries:
        notice_uri = entry.get("id", "")
        if notice_uri:
            numeric_id = notice_uri.rstrip("/").split("/")[-1]
            if numeric_id:
                ids.append(f"notice:{numeric_id}")
    return ids


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

def register_tools(mcp: FastMCP) -> None:

    @mcp.tool(
        name="search",
        annotations={
            "title": "Search UK Due Diligence Registers",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def search(
        query: Annotated[str, Field(description="Company name, charity name, director name, or keyword to search for across all UK due diligence registers", min_length=2, max_length=200)],
    ) -> dict:
        """Search across all UK due diligence registers simultaneously.

        Searches Companies House, Charity Commission, disqualified directors,
        and Gazette insolvency notices in parallel. Returns a list of result
        IDs — use fetch with each ID to retrieve the full record.
        """
        tasks = [
            _company_ids(query),
            _charity_ids(query),
            _disqualified_ids(query),
            _gazette_ids(query),
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        ids: list[str] = []
        seen: set[str] = set()
        for result in results:
            if isinstance(result, Exception):
                continue
            for id_ in result:
                if id_ not in seen:
                    seen.add(id_)
                    ids.append(id_)

        return {"ids": ids}

    @mcp.tool(
        name="fetch",
        annotations={
            "title": "Fetch Full Record from UK Due Diligence Register",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def fetch(
        id: Annotated[str, Field(description="Prefixed record ID returned by search. Format: company:{number}, charity:{number}, disqualification:{officer_id}, or notice:{notice_id}", min_length=3, max_length=100)],
    ) -> dict:
        """Fetch the full record for an ID returned by search.

        Routes by prefix to the appropriate register:
        - company:{number} → Companies House full profile
        - charity:{number} → Charity Commission full profile
        - disqualification:{officer_id} → Disqualified director full record
        - notice:{notice_id} → Gazette notice full legal text
        """
        prefix, _, value = id.partition(":")
        if not value:
            raise ValueError(f"Invalid ID format {id!r} — expected prefix:value")

        if prefix == "company":
            result = await _fetch_company_profile(_normalise_company_number(value))
            return {
                "id": id,
                "title": result.company_name or value,
                "content": result.model_dump_json(),
                "metadata": {
                    "source": "companies_house",
                    "status": result.company_status,
                    "company_type": result.company_type,
                    "date_of_creation": result.date_of_creation,
                },
            }

        if prefix == "charity":
            result = await _fetch_charity_profile(value)
            return {
                "id": id,
                "title": result.charity_name or value,
                "content": result.model_dump_json(),
                "metadata": {
                    "source": "charity_commission",
                    "status": result.reg_status_label,
                    "date_of_registration": result.date_of_registration,
                },
            }

        if prefix == "disqualification":
            result = await _fetch_disqualified_profile(value)
            return {
                "id": id,
                "title": result.name or value,
                "content": result.model_dump_json(),
                "metadata": {
                    "source": "companies_house_disqualified",
                    "officer_kind": result.officer_kind,
                },
            }

        if prefix == "notice":
            data: dict[str, Any] = await _fetch_gazette_notice(value)
            title = (
                data.get("title")
                or data.get("f:notice-code")
                or f"Gazette notice {value}"
            )
            return {
                "id": id,
                "title": title,
                "content": json.dumps(data),
                "metadata": {"source": "gazette"},
            }

        raise ValueError(
            f"Unknown ID prefix {prefix!r}. Valid prefixes: company, charity, disqualification, notice"
        )
