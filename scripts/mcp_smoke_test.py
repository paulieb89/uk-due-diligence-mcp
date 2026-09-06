#!/usr/bin/env python3
"""Smoke-test the due-diligence server against a known-good result.

Two modes, same assertions:
    --deployed   hit prod over HTTP (what /verify runs)
    --local      drive the in-process server, no network to our own app

Carillion is the fixture on purpose: it is in liquidation, so its Companies
House record is finished changing. A live query that still returns 03782379
proves the whole path — transport, tool registration, upstream API key,
response shape.

    uv run --no-sync python scripts/mcp_smoke_test.py --deployed
    uv run --no-sync python scripts/mcp_smoke_test.py --local

Exit 0 pass, 1 fail.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from typing import Any

DEPLOYED_URL = "https://uk-due-diligence-mcp.fly.dev/mcp"
QUERY = "Carillion"
EXPECT_COMPANY = "03782379"  # CARILLION PLC


def unwrap(result: Any) -> Any:
    """Undo FastMCP's single-key structured_content wrapper.

    A tool returning a plain str arrives as {"result": "<json string>"}; a tool
    returning a model arrives with real fields. Handle both.
    """
    sc = result.structured_content
    if isinstance(sc, dict) and set(sc) == {"result"} and isinstance(sc["result"], str):
        try:
            return json.loads(sc["result"])
        except ValueError:
            return sc["result"]
    if sc is not None:
        return sc
    text = "\n".join(
        block.text for block in (result.content or []) if getattr(block, "text", None)
    )
    try:
        return json.loads(text)
    except ValueError:
        return text


async def smoke(target: Any, label: str, expect_company: str) -> int:
    from fastmcp import Client

    print(f"target   {label}")
    print(f"call     company_search(query={QUERY!r}, items_per_page=2)")

    t0 = time.perf_counter()
    try:
        async with Client(target) as client:
            # Flat args: this repo's tools take Annotated[] parameters directly,
            # NOT a nested {"params": ...} envelope like uk-legal-mcp.
            result = await client.call_tool(
                "company_search", {"query": QUERY, "items_per_page": 2}
            )
    except Exception as exc:
        print(f"\nFAIL     could not call the server: {type(exc).__name__}: {exc}")
        return 1
    elapsed_ms = (time.perf_counter() - t0) * 1000

    payload = unwrap(result)
    if not isinstance(payload, dict):
        print(f"\nFAIL     unexpected response shape: {type(payload).__name__}")
        print(f"         {str(payload)[:200]}")
        return 1

    total = payload.get("total_results")
    numbers = [item.get("company_number") for item in payload.get("items") or []]
    print(f"took     {elapsed_ms:.0f}ms")
    print(f"got      total_results={total} numbers={numbers}")

    failures: list[str] = []
    if not isinstance(total, int) or total <= 0:
        failures.append(f"expected total_results > 0, got {total!r}")
    if expect_company not in numbers:
        failures.append(f"expected company {expect_company} in results, got {numbers}")

    if failures:
        print("\nFAIL")
        for f in failures:
            print(f"         {f}")
        return 1

    print(f"\nPASS     total_results={total}, {expect_company} present")
    return 0


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--deployed", action="store_true", help=f"probe {DEPLOYED_URL}")
    mode.add_argument("--local", action="store_true", help="probe the in-process server")
    parser.add_argument(
        "--expect-company",
        default=EXPECT_COMPANY,
        help="company number that must appear (override to prove the assertion bites)",
    )
    args = parser.parse_args()

    if args.deployed:
        return await smoke(DEPLOYED_URL, DEPLOYED_URL, args.expect_company)

    from server import mcp  # noqa: E402 — import cost only paid in --local mode

    return await smoke(mcp, "in-process (server.mcp)", args.expect_company)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
