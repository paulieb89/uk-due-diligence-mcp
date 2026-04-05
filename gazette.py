"""
The Gazette linked-data API tool (1 tool).

The Gazette's linked-data read API is unauthenticated and uses a REST+RDF pattern.
Corporate insolvency notice codes span the 2440-2460 range:

  2441 -- Winding-Up Petition
  2442 -- Dismissal of Winding-Up Petition
  2443 -- Winding-Up Order
  2444 -- Stay of Winding-Up Order
  2445 -- Appointment of Provisional Liquidator
  2446 -- Notice to Creditors
  2447 -- Notice to Contributories
  2448 -- Administration Order
  2449 -- Appointment of Administrative Receiver
  2450 -- Moratorium
  2452 -- Appointment of Liquidator
  2455 -- Notice of Voluntary Winding-Up Resolution
  2456 -- Creditors' Voluntary Liquidation
  2460 -- Striking-Off Notice

The read API returns JSON-LD. We parse the @graph array.
Query params: ?noticecode=XXXX&text=NAME&start-date=YYYY-MM-DD&end-date=YYYY-MM-DD
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from pydantic import Field
from fastmcp import FastMCP

from http_client import (
    _request_with_retry,
    gazette_client,
    format_api_error,
)

# ---------------------------------------------------------------------------
# Notice code taxonomy
# ---------------------------------------------------------------------------
ALL_CORPORATE_INSOLVENCY_CODES = [
    "2441", "2442", "2443", "2444", "2445", "2446",
    "2447", "2448", "2449", "2450", "2452", "2455", "2456", "2460",
]

NOTICE_LABELS: dict[str, str] = {
    "2441": "Winding-Up Petition",
    "2442": "Dismissal of Winding-Up Petition",
    "2443": "Winding-Up Order",
    "2444": "Stay of Winding-Up Order",
    "2445": "Appointment of Provisional Liquidator",
    "2446": "Notice to Creditors",
    "2447": "Notice to Contributories",
    "2448": "Administration Order",
    "2449": "Appointment of Administrative Receiver",
    "2450": "Moratorium",
    "2452": "Appointment of Liquidator",
    "2455": "Notice of Voluntary Winding-Up Resolution",
    "2456": "Creditors' Voluntary Liquidation",
    "2460": "Striking-Off Notice",
}

# Severity ordering -- higher = more serious
SEVERITY: dict[str, int] = {
    "2443": 10,  # Winding-Up Order
    "2448": 9,   # Administration Order
    "2449": 9,   # Administrative Receiver
    "2456": 8,   # Creditors' Voluntary Liquidation
    "2445": 7,   # Provisional Liquidator
    "2452": 7,   # Liquidator appointed
    "2441": 6,   # Petition (not yet order)
    "2460": 5,   # Striking-Off
    "2455": 4,   # Voluntary Winding-Up Resolution
    "2450": 3,   # Moratorium
    "2446": 2,
    "2447": 2,
    "2442": 1,
    "2444": 1,
}


def _extract_notices(graph: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pull structured notice records from a JSON-LD @graph array."""
    notices = []
    for node in graph:
        if "@type" not in node:
            continue
        types = node["@type"] if isinstance(node["@type"], list) else [node["@type"]]
        if any("Notice" in t or "notice" in t for t in types):
            code = str(node.get("noticeCode", node.get("@type", [""])[-1]))
            notices.append(
                {
                    "notice_id": node.get("@id", "—"),
                    "notice_code": code,
                    "notice_type": NOTICE_LABELS.get(code, "Corporate Notice"),
                    "date": node.get("publicationDate", node.get("noticeDate", "—")),
                    "edition": node.get("edition", "—"),
                    "title": node.get("noticeTitle", node.get("title", "—")),
                    "content": node.get("content", node.get("noticeContent", "")),
                }
            )
    # Sort by severity descending, then date descending
    notices.sort(
        key=lambda n: (SEVERITY.get(n["notice_code"], 0), n["date"]),
        reverse=True,
    )
    return notices


def _format_notice(n: dict[str, Any], idx: int) -> str:
    severity = SEVERITY.get(n["notice_code"], 0)
    flag = "🚨" if severity >= 7 else ("🚩" if severity >= 4 else "ℹ️")
    content_preview = n["content"][:200] + "…" if len(n["content"]) > 200 else n["content"]
    return (
        f"{idx}. {flag} **{n['notice_type']}** (code {n['notice_code']})\n"
        f"   Date: {n['date']} | Edition: {n['edition']}\n"
        f"   {content_preview or '(No content preview available)'}\n"
    )


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

def register_tools(mcp: FastMCP) -> None:

    @mcp.tool(
        name="gazette_insolvency",
        annotations={
            "title": "Search Gazette Corporate Insolvency Notices",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def gazette_insolvency(
        entity_name: Annotated[str, Field(description="Company or individual name to search for in Gazette insolvency notices", min_length=2, max_length=200)],
        notice_type: Annotated[str | None, Field(description="Filter by notice code (e.g. '2441' winding-up petition, '2443' winding-up order, '2448' administration order, '2460' striking-off). Omit to search all.")] = None,
        start_date: Annotated[str | None, Field(description="Filter notices from this date (YYYY-MM-DD)")] = None,
        end_date: Annotated[str | None, Field(description="Filter notices up to this date (YYYY-MM-DD)")] = None,
        response_format: Annotated[str, Field(description="Output format: 'markdown' or 'json'")] = "markdown",
    ) -> str:
        """Search The Gazette's linked-data API for corporate insolvency notices.

        Searches notice codes 2441-2460 (winding-up petitions, administration orders,
        liquidation appointments, striking-off notices, etc.) by entity name.
        Results are sorted by severity -- winding-up orders and administration orders
        appear first.

        The Gazette is the official UK public record. A notice here means the event
        has been formally published and is legally effective.
        """
        # Build target notice codes
        if notice_type:
            codes_to_search = [notice_type]
        else:
            codes_to_search = ALL_CORPORATE_INSOLVENCY_CODES

        all_notices: list[dict[str, Any]] = []

        try:
            async with gazette_client() as client:
                for code in codes_to_search:
                    qs: dict[str, Any] = {
                        "noticecode": code,
                        "text": entity_name,
                        "results-page-size": 10,
                        "format": "application/json",
                    }
                    if start_date:
                        qs["start-date"] = start_date
                    if end_date:
                        qs["end-date"] = end_date

                    try:
                        resp = await _request_with_retry(client, "GET", "", params=qs)
                        raw = resp.json()
                        # JSON-LD: top-level dict with @graph array
                        graph = raw.get("@graph", []) if isinstance(raw, dict) else []
                        notices = _extract_notices(graph)
                        all_notices.extend(notices)
                    except Exception:
                        # Per-code failures are non-fatal -- continue scanning
                        continue

        except Exception as exc:
            return format_api_error(exc, "gazette_insolvency")

        # Re-sort combined results
        all_notices.sort(
            key=lambda n: (SEVERITY.get(n["notice_code"], 0), n["date"]),
            reverse=True,
        )

        if response_format == "json":
            return json.dumps(
                {
                    "entity_name": entity_name,
                    "total_notices": len(all_notices),
                    "notices": all_notices,
                },
                indent=2,
            )

        if not all_notices:
            return (
                f"✅ No corporate insolvency notices found in The Gazette for "
                f"**{entity_name}**"
                + (f" since {start_date}" if start_date else "")
                + "."
            )

        lines = [
            f"## Gazette Insolvency Notices — '{entity_name}'\n",
            f"**{len(all_notices)} notice(s) found**",
        ]
        if start_date:
            lines[-1] += f" from {start_date}"
        if end_date:
            lines[-1] += f" to {end_date}"
        lines.append("")

        for i, notice in enumerate(all_notices, 1):
            lines.append(_format_notice(notice, i))

        return "\n".join(lines)
