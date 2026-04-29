"""
runner.py — Generic MCP matrix runner engine.

Copy this file into tests/live/ of any FastMCP server.
Define your test cases in matrix.py using the Case dataclass.

Usage in matrix.py:
    from runner import Case, run, print_table

    async def main():
        async with Client(mcp) as client:
            rows = await run(client, cases)
        print_table(rows)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import tiktoken
from fastmcp import Client
from fastmcp.exceptions import ToolError

FIXTURES_DIR = Path(__file__).parent / "fixtures"
CSV_PATH = Path(__file__).parent / "context_costs.csv"
ENCODER = tiktoken.get_encoding("cl100k_base")


# ---------------------------------------------------------------------------
# Case definition
# ---------------------------------------------------------------------------

@dataclass
class Case:
    """A single tool call in the matrix."""
    tool: str
    args: dict = field(default_factory=dict)
    # If set, called with the parsed payload of this result.
    # Returns a dict that is merged into the NEXT case's args.
    chain: Callable[[Any], dict] | None = None
    # Label shown in output instead of tool name (optional)
    label: str | None = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _llm_text(result: Any) -> str:
    parts = []
    for block in result.content or []:
        t = getattr(block, "text", None)
        if t is not None:
            parts.append(t)
    return "\n".join(parts)


def _write_fixture(tool: str, args: dict, result: Any) -> None:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    args_hash = hashlib.sha256(
        json.dumps(args, sort_keys=True, default=str).encode()
    ).hexdigest()[:12]
    path = FIXTURES_DIR / f"{tool}__{args_hash}.json"
    path.write_text(json.dumps({
        "tool": tool,
        "args": args,
        "is_error": result.is_error,
        "structured_content": result.structured_content,
        "content_blocks": [
            {"type": type(b).__name__, "text": getattr(b, "text", None)}
            for b in (result.content or [])
        ],
    }, indent=2, default=str))


def _parse_payload(result: Any) -> Any:
    sc = result.structured_content
    if isinstance(sc, dict) and set(sc.keys()) == {"result"} and isinstance(sc["result"], str):
        try:
            return json.loads(sc["result"])
        except Exception:
            return sc["result"]
    if sc is not None:
        return sc
    text = _llm_text(result)
    if text.lstrip().startswith(("{", "[")):
        try:
            return json.loads(text)
        except Exception:
            return text
    return text


def find_first(obj: Any, *keys: str) -> Any:
    """Depth-first search for the first non-null value matching any key.

    Useful in chain= lambdas:
        chain=lambda p: {"company_number": find_first(p, "company_number")}
    """
    if isinstance(obj, dict):
        for k in keys:
            if k in obj and obj[k] is not None:
                return obj[k]
        for v in obj.values():
            found = find_first(v, *keys)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = find_first(item, *keys)
            if found is not None:
                return found
    return None


# ---------------------------------------------------------------------------
# Core runner
# ---------------------------------------------------------------------------

async def run(client: Client, cases: list[Case]) -> list[dict]:
    """Execute a list of Cases sequentially, chaining args between steps.

    Returns a list of metric dicts suitable for print_table / write_csv.
    """
    rows: list[dict] = []
    pending_args: dict = {}  # injected from previous case's chain output

    for case in cases:
        args = {**case.args, **pending_args}
        pending_args = {}

        label = case.label or case.tool
        t0 = time.perf_counter()
        try:
            result = await client.call_tool(case.tool, args)
        except ToolError as e:
            elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
            print(f"[SKIP] {label}: {e}")
            rows.append({
                "tool": label, "tokens": 0, "chars": 0,
                "blocks": 0, "ms": elapsed_ms, "error": True,
            })
            continue

        elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
        _write_fixture(case.tool, args, result)
        text = _llm_text(result)
        payload = _parse_payload(result)

        rows.append({
            "tool": label,
            "tokens": len(ENCODER.encode(text)),
            "chars": len(text),
            "blocks": len(result.content or []),
            "ms": elapsed_ms,
            "error": bool(result.is_error),
        })

        if case.chain is not None:
            try:
                pending_args = case.chain(payload) or {}
            except Exception as e:
                print(f"[CHAIN] {label}: chain function raised {e}")

    return rows


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def print_table(rows: list[dict]) -> None:
    if not rows:
        print("No results.")
        return
    rows_sorted = sorted(rows, key=lambda x: x["tokens"], reverse=True)
    name_w = max(len(r["tool"]) for r in rows_sorted)
    header = f"{'tool':<{name_w}}  {'tokens':>8}  {'chars':>8}  {'blocks':>6}  {'ms':>8}  err"
    print(header)
    print("-" * len(header))
    total_tokens = total_chars = 0
    for r in rows_sorted:
        total_tokens += r["tokens"]
        total_chars += r["chars"]
        err = "x" if r["error"] else ""
        print(f"{r['tool']:<{name_w}}  {r['tokens']:>8}  {r['chars']:>8}  {r['blocks']:>6}  {r['ms']:>8}  {err}")
    print("-" * len(header))
    print(f"{'TOTAL':<{name_w}}  {total_tokens:>8}  {total_chars:>8}")
    print(f"{'% of 200k ctx':<{name_w}}  {total_tokens / 200_000 * 100:>7.1f}%")


def write_csv(rows: list[dict], path: Path = CSV_PATH) -> None:
    with path.open("w") as f:
        f.write("tool,tokens,chars,blocks,ms,error\n")
        for r in rows:
            f.write(f"{r['tool']},{r['tokens']},{r['chars']},{r['blocks']},{r['ms']},{int(r['error'])}\n")
    print(f"\nwrote {path.relative_to(Path.cwd())}")
