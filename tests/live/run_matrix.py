"""
Live matrix runner — calls all 13 due-diligence tools in-process and prints a
context-cost table. Response bodies are written to tests/live/fixtures/.

Usage:
    python -m tests.live.run_matrix
"""

from __future__ import annotations

import asyncio

from fastmcp import Client

from server import mcp
from tests.live.runner import Case, find_first, print_table, run, write_csv

CASES: list[Case] = [
    # ---- companies house ----
    Case(
        "company_search",
        {"query": "tesco"},
        chain=lambda p: {"company_number": find_first(p, "company_number") or "00445790"},
    ),
    Case("company_profile", chain=lambda p: {"company_number": find_first(p, "company_number")}),
    Case("company_officers", chain=lambda p: {"company_number": find_first(p, "company_number")}),
    Case("company_psc"),

    # ---- disqualified ----
    Case(
        "disqualified_search",
        {"query": "smith"},
        chain=lambda p: {"officer_id": find_first(p, "officer_id")} if find_first(p, "officer_id") else {},
    ),
    Case("disqualified_profile"),

    # ---- charity ----
    Case(
        "charity_search",
        {"query": "oxfam"},
        chain=lambda p: {"charity_number": str(find_first(p, "reg_charity_number", "charity_number") or "202918")},
    ),
    Case("charity_profile"),

    # ---- land registry ----
    Case("land_title_search", {"address_or_postcode": "SW1A 1AA"}),

    # ---- gazette ----
    Case(
        "gazette_insolvency",
        {"entity_name": "carillion"},
        chain=lambda p: {"notice_id": str(find_first(p, "notice_numeric_id") or "2948343")},
    ),
    Case("gazette_notice"),

    # ---- search / fetch ----
    Case(
        "search",
        {"query": "carillion"},
        chain=lambda p: {"id": (find_first(p, "ids") or ["company:03782379"])[0]},
    ),
    Case("fetch"),

    # ---- hmrc vat ----
    Case("vat_validate", {"vat_number": "220430231"}),
]


async def main() -> None:
    async with Client(mcp) as client:
        rows = await run(client, CASES)

    print_table(rows)
    write_csv(rows)


if __name__ == "__main__":
    asyncio.run(main())
