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
from fastmcp.exceptions import ToolError

from http_client import (
    _request_with_retry,
    companies_house_client,
    gazette_client,
)
from mcpfleet_obs import parse_error_payload, raise_tool_error
from companies_house import _fetch_company_profile, _normalise_company_number
from charity import _fetch_charity_profile, _search_charities
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
    # Delegates to charity.py rather than calling the API directly: only this
    # register 404s on zero matches (the CH endpoints return 200 with an empty
    # `items`, Gazette 200 with no `entry` key), and _search_charities already
    # maps that not_found back to an empty result. A second HTTP path here
    # drifted from that fix and reported "no charities" as a failed register.
    result = await _search_charities(query, 0, 5)
    return [f"charity:{c.charity_number}" for c in result.charities if c.charity_number]


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


def _raise_all_registers_failed(query: str, failures: list[tuple[str, Exception]]) -> None:
    """Every register failed, so there is no result to report — only a cause.

    Re-raises the upstream ToolError rather than synthesising a transient one:
    the commonest way all four fail at once is an unset CH_API_KEY, which
    _get_env reports as configuration/not-retryable. Telling a caller to retry
    that is the same false signal this whole path exists to remove.
    """
    payloads = [(label, parse_error_payload(str(exc))) for label, exc in failures
                if isinstance(exc, ToolError)]
    if len(payloads) == len(failures):
        for (_, exc), (_, payload) in zip(failures, payloads, strict=True):
            if payload is not None and payload.error_category != "not_found":
                raise exc

    # Mixed or unstructured causes. Name them by type — a ToolError's str() is
    # already a FleetErrorPayload JSON dump and would nest inside description.
    detail = "; ".join(f"{label}: {type(exc).__name__}" for label, exc in failures)
    raise_tool_error(
        "transient",
        is_retryable=True,
        attempted=f"search({query!r})",
        description=(
            f"All {len(failures)} registers failed ({detail}) — no register was "
            f"searched. This is an upstream failure, not a zero-result search."
        ),
    )


# Labels are the ID prefixes these helpers emit, not register names: the same
# payload carries `ids` prefixed this way, so a caller can conclude "no charity:
# IDs and charity in registers_unavailable -> unknown, not absent" without
# knowing a separate naming scheme.
_REGISTERS = (
    ("company", _company_ids),
    ("charity", _charity_ids),
    ("disqualification", _disqualified_ids),
    ("notice", _gazette_ids),
)


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

        `registers_searched` names the registers that actually answered and
        `registers_unavailable` those that failed; `is_partial` is true whenever
        the latter is non-empty. An empty `ids` with `is_partial` true means
        UNRESOLVED, not "nothing on record". If no register answers at all the
        call raises rather than returning an empty result.
        """
        results = await asyncio.gather(
            *(fn(query) for _, fn in _REGISTERS), return_exceptions=True
        )

        ids: list[str] = []
        seen: set[str] = set()
        searched: list[str] = []
        failures: list[tuple[str, Exception]] = []
        for (label, _), result in zip(_REGISTERS, results, strict=True):
            if isinstance(result, BaseException):
                if not isinstance(result, Exception):
                    # CancelledError/SystemExit — a teardown, not a dead register.
                    # Re-raising also keeps it out of the `for id_ in result` loop
                    # below, which is what b3eb9ce's BaseException widening fixed.
                    # Outer cancellation never lands here: gather(return_exceptions=True)
                    # re-raises that itself.
                    raise result
                failures.append((label, result))
                continue
            searched.append(label)
            for id_ in result:
                if id_ not in seen:
                    seen.add(id_)
                    ids.append(id_)

        if not searched:
            _raise_all_registers_failed(query, failures)

        return {
            "ids": ids,
            "registers_searched": searched,
            "registers_unavailable": [label for label, _ in failures],
            "is_partial": bool(failures),
        }

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
            raise_tool_error(
                "validation",
                is_retryable=False,
                attempted=f"fetch({id!r})",
                description=f"Invalid ID format {id!r} — expected prefix:value",
            )

        if prefix == "company":
            co = await _fetch_company_profile(_normalise_company_number(value))
            return {
                "id": id,
                "title": co.company_name or value,
                "content": co.model_dump_json(),
                "metadata": {
                    "source": "companies_house",
                    "status": co.company_status,
                    "company_type": co.company_type,
                    "date_of_creation": co.date_of_creation,
                },
            }

        if prefix == "charity":
            ch = await _fetch_charity_profile(value)
            return {
                "id": id,
                "title": ch.charity_name or value,
                "content": ch.model_dump_json(),
                "metadata": {
                    "source": "charity_commission",
                    "status": ch.reg_status_label,
                    "date_of_registration": ch.date_of_registration,
                },
            }

        if prefix == "disqualification":
            dq = await _fetch_disqualified_profile(value)
            return {
                "id": id,
                "title": dq.name or value,
                "content": dq.model_dump_json(),
                "metadata": {
                    "source": "companies_house_disqualified",
                    "officer_kind": dq.officer_kind,
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

        raise_tool_error(
            "validation",
            is_retryable=False,
            attempted=f"fetch({id!r})",
            description=(
                f"Unknown ID prefix {prefix!r}. Valid prefixes: company, charity, disqualification, notice"
            ),
        )
