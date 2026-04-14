"""
Live tool runner — calls a real MCP tool in-process and reports context cost.

The full response is written to disk, never to stdout, so the assistant
running this script does not pull the response text into its own context
window. Only metrics (char/byte/token counts) are printed.

Token counts use tiktoken's cl100k_base encoding as a proxy for Claude's
tokenizer. It is not exact — expect ~10% drift — but is good enough for
context-budget planning.

IMPORTANT: this repo's tools accept flat Annotated[] parameters (not nested
Pydantic input models), so args are passed flat: {"query": "x"} — NOT
wrapped in a {"params": ...} envelope like uk-legal-mcp.

Most tools also take a `response_format: "markdown" | "json"` parameter.
json mode is the context-heavy path — use it when measuring worst-case
token cost (that's what we're hunting).

Usage:
    python -m tests.live.run_tool                             # defaults
    python -m tests.live.run_tool --query "tesco"
    python -m tests.live.run_tool --tool charity_search --args '{"query": "oxfam", "response_format": "json"}'
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

import tiktoken
from fastmcp import Client

from server import mcp

FIXTURES_DIR = Path(__file__).parent / "fixtures"
ENCODER = tiktoken.get_encoding("cl100k_base")


def _extract_llm_visible_text(call_result: Any) -> str:
    parts: list[str] = []
    for block in call_result.content or []:
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(text)
    return "\n".join(parts)


async def run(tool_name: str, tool_args: dict) -> dict:
    async with Client(mcp) as client:
        result = await client.call_tool(tool_name, tool_args)

    llm_text = _extract_llm_visible_text(result)
    token_count = len(ENCODER.encode(llm_text))

    args_hash = hashlib.sha256(
        json.dumps(tool_args, sort_keys=True, default=str).encode()
    ).hexdigest()[:12]
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    fixture_path = FIXTURES_DIR / f"{tool_name}__{args_hash}.json"

    fixture = {
        "tool": tool_name,
        "args": tool_args,
        "is_error": result.is_error,
        "structured_content": result.structured_content,
        "content_blocks": [
            {"type": type(b).__name__, "text": getattr(b, "text", None)}
            for b in (result.content or [])
        ],
    }
    fixture_path.write_text(json.dumps(fixture, indent=2, default=str))

    return {
        "tool": tool_name,
        "args": tool_args,
        "is_error": result.is_error,
        "content_blocks": len(result.content or []),
        "llm_visible_chars": len(llm_text),
        "llm_visible_bytes": len(llm_text.encode("utf-8")),
        "llm_visible_tokens_cl100k": token_count,
        "fixture_path": str(fixture_path.relative_to(Path.cwd())),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run an MCP tool and report context cost.")
    parser.add_argument("--tool", default="company_search")
    parser.add_argument("--query", default="tesco")
    parser.add_argument("--format", default="json", choices=["markdown", "json"])
    parser.add_argument(
        "--args",
        help="JSON-encoded tool args dict (overrides --query/--format). "
             "Example: '{\"query\": \"X\", \"response_format\": \"json\"}'",
    )
    ns = parser.parse_args()

    if ns.args:
        tool_args = json.loads(ns.args)
    else:
        tool_args = {"query": ns.query, "response_format": ns.format}

    metrics = asyncio.run(run(ns.tool, tool_args))
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
