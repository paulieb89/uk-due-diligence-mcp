"""
Live matrix runner — calls all 11 due-diligence tools in-process and prints a
context-cost table.

Forces response_format="json" throughout to measure the worst-case payload
(that's the mode yesterday's context explosion was using).

Response bodies are written to tests/live/fixtures/ (gitignored). Only
per-tool metrics print to stdout.

Usage:
    python -m tests.live.run_matrix
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path
from typing import Any

import tiktoken
from fastmcp import Client

from server import mcp

FIXTURES_DIR = Path(__file__).parent / "fixtures"
CSV_PATH = Path(__file__).parent / "context_costs.csv"
ENCODER = tiktoken.get_encoding("cl100k_base")


def _llm_text(result: Any) -> str:
    parts = []
    for block in result.content or []:
        t = getattr(block, "text", None)
        if t is not None:
            parts.append(t)
    return "\n".join(parts)


def _write_fixture(tool: str, args: dict, result: Any) -> Path:
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
    return path


def _parse_payload(result: Any) -> Any:
    """Return the response body as Python (dict/list) without printing."""
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


def _find_first(obj: Any, *keys: str) -> Any:
    """Depth-first search for the first non-null value matching any of the given keys."""
    if isinstance(obj, dict):
        for k in keys:
            if k in obj and obj[k] is not None:
                return obj[k]
        for v in obj.values():
            found = _find_first(v, *keys)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_first(item, *keys)
            if found is not None:
                return found
    return None


async def _call(client: Client, tool: str, args: dict) -> tuple[dict, Any]:
    t0 = time.perf_counter()
    result = await client.call_tool(tool, args)
    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
    _write_fixture(tool, args, result)
    text = _llm_text(result)
    metrics = {
        "tool": tool,
        "tokens": len(ENCODER.encode(text)),
        "chars": len(text),
        "blocks": len(result.content or []),
        "ms": elapsed_ms,
        "error": result.is_error,
    }
    return metrics, _parse_payload(result)


JSON = {"response_format": "json"}


async def main() -> None:
    rows: list[dict] = []

    async with Client(mcp) as client:
        # ---- companies house ----
        m, payload = await _call(client, "company_search", {"query": "tesco", **JSON})
        rows.append(m)
        company_number = _find_first(payload, "company_number") or "00445790"  # Tesco PLC fallback

        m, _ = await _call(client, "company_profile", {"company_number": company_number, **JSON})
        rows.append(m)
        m, _ = await _call(client, "company_officers", {"company_number": company_number, **JSON})
        rows.append(m)
        m, _ = await _call(client, "company_psc", {"company_number": company_number, **JSON})
        rows.append(m)

        # ---- disqualified ----
        m, payload = await _call(client, "disqualified_search", {"query": "smith", **JSON})
        rows.append(m)
        officer_id = _find_first(payload, "officer_id", "person_number", "id")
        if officer_id:
            m, _ = await _call(client, "disqualified_profile", {"officer_id": officer_id, **JSON})
            rows.append(m)

        # ---- charity ----
        m, payload = await _call(client, "charity_search", {"query": "oxfam", **JSON})
        rows.append(m)
        charity_number = str(_find_first(
            payload, "reg_charity_number", "charity_number", "registered_charity_number"
        ) or "202918")  # Oxfam fallback
        m, _ = await _call(client, "charity_profile", {"charity_number": charity_number, **JSON})
        rows.append(m)

        # ---- land registry ----
        m, _ = await _call(client, "land_title_search", {"address_or_postcode": "SW1A 1AA", **JSON})
        rows.append(m)

        # ---- gazette ----
        m, _ = await _call(client, "gazette_insolvency", {"entity_name": "carillion", **JSON})
        rows.append(m)

        # ---- hmrc vat ----
        m, _ = await _call(client, "vat_validate", {"vat_number": "220430231", **JSON})
        rows.append(m)

    # ---- print table ----
    rows.sort(key=lambda x: x["tokens"], reverse=True)
    name_w = max(len(r["tool"]) for r in rows)
    header = f"{'tool':<{name_w}}  {'tokens':>8}  {'chars':>8}  {'blocks':>6}  {'ms':>8}  err"
    print(header)
    print("-" * len(header))
    total_tokens = 0
    total_chars = 0
    for r in rows:
        total_tokens += r["tokens"]
        total_chars += r["chars"]
        err = "x" if r["error"] else ""
        print(f"{r['tool']:<{name_w}}  {r['tokens']:>8}  {r['chars']:>8}  {r['blocks']:>6}  {r['ms']:>8}  {err}")
    print("-" * len(header))
    print(f"{'TOTAL':<{name_w}}  {total_tokens:>8}  {total_chars:>8}")
    print(f"{'% of 200k ctx':<{name_w}}  {total_tokens / 200_000 * 100:>7.1f}%")

    with CSV_PATH.open("w") as f:
        f.write("tool,tokens,chars,blocks,ms,error\n")
        for r in rows:
            f.write(f"{r['tool']},{r['tokens']},{r['chars']},{r['blocks']},{r['ms']},{int(r['error'])}\n")
    print(f"\nwrote {CSV_PATH.relative_to(Path.cwd())}")


if __name__ == "__main__":
    asyncio.run(main())
