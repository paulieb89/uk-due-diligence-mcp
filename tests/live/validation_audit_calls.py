"""Re-run the 2026-11-17 validation audit's staging calls against a deployed server.

The audit recorded what each call returned before the fixes (BEFORE); each row
here checks the fixed behaviour (AFTER) against the live server and prints
both, so a run is the before/after evidence for a release.

Usage:
    uv run --no-sync python tests/live/validation_audit_calls.py
    uv run --no-sync python tests/live/validation_audit_calls.py --url https://uk-due-diligence-mcp.fly.dev/mcp

Exits 1 if any AFTER check fails. Not a pytest module (no test_ prefix): it
needs a deployed server and real upstream registers.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx
from fastmcp import Client
from mcpfleet_obs import parse_error_payload

DEFAULT_URL = "https://uk-due-diligence-mcp-staging.fly.dev/mcp"


@dataclass
class Outcome:
    is_error: bool
    data: Any  # structured content on success
    text: str  # raw text content (the error payload on failure)

    @property
    def category(self) -> str | None:
        if not self.is_error:
            return None
        payload = parse_error_payload(self.text)
        return payload.error_category if payload else None


@dataclass
class Call:
    label: str
    tool: str
    args: dict
    before: str  # the audit's recorded result
    after: str  # what the fixed server must return
    check: Callable[[Outcome], tuple[bool, str]]  # -> (passed, actual summary)


def _err(o: Outcome) -> str:
    cat = o.category
    return f"error category={cat}" if cat else f"unstructured error: {o.text[:80]!r}"


def _expect_category(category: str) -> Callable[[Outcome], tuple[bool, str]]:
    def check(o: Outcome) -> tuple[bool, str]:
        if not o.is_error:
            return False, f"success: {json.dumps(o.data)[:80]}"
        return o.category == category, _err(o)

    return check


def _expect_rejected(o: Outcome) -> tuple[bool, str]:
    """Rejected before running: schema validation or a fleet validation error."""
    if not o.is_error:
        filt = (o.data or {}).get("entity_type_filter")
        return False, f"success, entity_type_filter={filt!r}, hits={(o.data or {}).get('match_count')}"
    if o.category == "validation":
        return True, _err(o)
    return "validation" in o.text.lower(), f"rejected: {o.text[:80]!r}"


def _expect_carillion(o: Outcome) -> tuple[bool, str]:
    if o.is_error:
        return False, _err(o)
    name, number = o.data.get("company_name"), o.data.get("company_number")
    return number == "03782379" and "CARILLION" in (name or "").upper(), f"{name} ({number})"


def _expect_overdue_unknown(o: Outcome) -> tuple[bool, str]:
    if o.is_error:
        return False, _err(o)
    acc = o.data.get("accounts") or {}
    conf = o.data.get("confirmation_statement") or {}
    summary = (
        f"accounts.overdue={acc.get('overdue')!r} "
        f"(made_up_to={acc.get('last_accounts_made_up_to')!r}, next_due={acc.get('next_due')!r}); "
        f"confirmation.overdue={conf.get('overdue')!r}"
    )
    # Companies House sends no accounts block for this entity (both dates null),
    # so the only honest value is None. False would be the old all-clear.
    return acc.get("overdue") is None, summary


def _expect_filtered(field: str, value: str) -> Callable[[Outcome], tuple[bool, str]]:
    def check(o: Outcome) -> tuple[bool, str]:
        if o.is_error:
            return False, _err(o)
        items = o.data.get("items") or []
        values = sorted({i.get(field) for i in items}, key=str)
        applied = o.data.get("filters_applied")
        ok = all(i.get(field) == value for i in items) and applied == {field: value}
        return ok, (
            f"{len(items)} items, {field} values={values}, filters_applied={applied}, "
            f"is_partial={o.data.get('is_partial')}, upstream_total={o.data.get('upstream_total')}"
        )

    return check


CALLS = [
    Call("profile unknown number", "company_profile", {"company_number": "99999999"},
         "not_found", "not_found", _expect_category("not_found")),
    Call("profile leading space", "company_profile", {"company_number": " 3782379"},
         "not_found", "Carillion plc (03782379)", _expect_carillion),
    Call("profile short number", "company_profile", {"company_number": "3782379"},
         "Carillion plc", "Carillion plc (03782379)", _expect_carillion),
    Call("profile malformed", "company_profile", {"company_number": "ABC"},
         "(not in audit) not_found", "validation", _expect_category("validation")),
    Call("profile overseas entity", "company_profile", {"company_number": "OE000002"},
         "overdue: false, both accounts dates null", "accounts.overdue null (unknown)",
         _expect_overdue_unknown),
    Call("search status filter", "company_search",
         {"query": "carillion plc", "company_status": "active"},
         "3 results, all in liquidation", "every item active; filters_applied echoed",
         _expect_filtered("company_status", "active")),
    Call("search type filter", "company_search",
         {"query": "overseas entity limited", "company_type": "registered-overseas-entity"},
         "3 results, all ltd", "every item registered-overseas-entity; filters_applied echoed",
         _expect_filtered("company_type", "registered-overseas-entity")),
    Call("sanctions bad entity_type", "sanctions_screen",
         {"name": "Rosneft", "entity_type": "company"},
         "unfiltered, entity_type_filter null, 2 hits", "validation error", _expect_rejected),
]


async def _health(url: str) -> dict:
    parts = urlsplit(url)
    async with httpx.AsyncClient(timeout=15) as http:
        resp = await http.get(f"{parts.scheme}://{parts.netloc}/health")
        try:
            return resp.json()
        except ValueError:
            return {"status": f"HTTP {resp.status_code}", "body": resp.text[:80]}


async def main(url: str) -> int:
    health = await _health(url)
    print(f"server: {url}")
    print(f"health: version={health.get('version', '?')} git_sha={health.get('git_sha', '?')} "
          f"status={health.get('status')}")
    print()

    rows = []
    async with Client(url, timeout=120) as client:
        for call in CALLS:
            try:
                result = await client.call_tool(call.tool, call.args, raise_on_error=False)
                text = "\n".join(getattr(b, "text", "") for b in result.content or [])
                outcome = Outcome(result.is_error, result.structured_content, text)
            except Exception as exc:  # schema rejection can surface as a client-side raise
                outcome = Outcome(True, None, f"{type(exc).__name__}: {exc}")
            passed, actual = call.check(outcome)
            rows.append((call, passed, actual))

    failures = 0
    for call, passed, actual in rows:
        failures += not passed
        print(f"[{'PASS' if passed else 'FAIL'}] {call.label}: {call.tool}({json.dumps(call.args)})")
        print(f"    before: {call.before}")
        print(f"    after : {call.after}")
        print(f"    actual: {actual}")
    print()
    print(f"{len(rows) - failures}/{len(rows)} AFTER checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default=DEFAULT_URL)
    sys.exit(asyncio.run(main(parser.parse_args().url)))
