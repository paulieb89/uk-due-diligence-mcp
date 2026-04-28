"""
The Gazette linked-data API tool (1 tool) + notice resource.

The Gazette's API is unauthenticated and uses an Atom-style JSON feed.
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

The search feed returns JSON with a top-level `entry` array (not JSON-LD @graph).
Feed endpoint: /data.json?noticecode=XXXX&text=NAME&start-date=YYYY-MM-DD&end-date=YYYY-MM-DD
Per-notice endpoint: https://www.thegazette.co.uk/notice/{id}/data.json?view=linked-data
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field
from fastmcp import FastMCP

import httpx
from http_client import _request_with_retry, gazette_client
from models import GazetteInsolvencyResult, GazetteNotice

# ---------------------------------------------------------------------------
# Notice code taxonomy
# ---------------------------------------------------------------------------

# Corporate insolvency codes returned by the Gazette /insolvency/notice endpoint.
# Includes 2431-2433 (legacy company codes) and 2440-2460 range.
# Personal insolvency codes (2500+) are excluded.
ALL_CORPORATE_INSOLVENCY_CODES = {
    "2431", "2432", "2433",  # older corporate winding-up / liquidator codes
    "2441", "2442", "2443", "2444", "2445", "2446",
    "2447", "2448", "2449", "2450", "2452", "2455", "2456", "2460",
}

NOTICE_LABELS: dict[str, str] = {
    "2431": "Resolutions for Winding-Up",
    "2432": "Appointment of Liquidators",
    "2433": "Notice to Creditors / Contributories",
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
    "2432": 7,   # Appointment of Liquidators (legacy)
    "2431": 6,   # Resolutions for Winding-Up (legacy)
    "2441": 6,   # Petition (not yet order)
    "2460": 5,   # Striking-Off
    "2455": 4,   # Voluntary Winding-Up Resolution
    "2450": 3,   # Moratorium
    "2446": 2,
    "2447": 2,
    "2433": 2,
    "2442": 1,
    "2444": 1,
}


def _strip_html(html: str) -> str:
    import re
    return re.sub(r"<[^>]+>", " ", html).strip()


def _notice_numeric_id(notice_uri: str) -> str:
    """Extract the numeric notice ID from a Gazette notice URI.

    e.g. 'https://www.thegazette.co.uk/id/notice/5122793' → '5122793'
    """
    return notice_uri.rstrip("/").split("/")[-1]


def _extract_notices(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pull structured insolvency notice records from the Gazette feed entry array."""
    notices = []
    for entry in entries:
        code = str(entry.get("f:notice-code", ""))
        if code not in ALL_CORPORATE_INSOLVENCY_CODES:
            continue
        notice_uri = entry.get("id", "")
        raw_content = entry.get("content", "")
        notices.append(
            {
                "notice_id": notice_uri,
                "notice_numeric_id": _notice_numeric_id(notice_uri) if notice_uri else None,
                "notice_code": code,
                "notice_type": NOTICE_LABELS.get(code, "Corporate Notice"),
                "date": (entry.get("published") or "")[:10] or None,
                "title": entry.get("title") or None,
                "content": _strip_html(raw_content) if raw_content else None,
            }
        )
    # Sort by severity descending, then date descending
    notices.sort(
        key=lambda n: (SEVERITY.get(n["notice_code"], 0), n["date"] or ""),
        reverse=True,
    )
    return notices


# ---------------------------------------------------------------------------
# Shared fetch helper
# ---------------------------------------------------------------------------

async def _fetch_gazette_notice(notice_id: str) -> dict:
    url = f"https://www.thegazette.co.uk/notice/{notice_id.strip()}/data.json?view=linked-data"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, headers={"Accept": "application/json"})
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

def register_tools(mcp: FastMCP) -> None:

    @mcp.tool(
        name="gazette_notice",
        annotations={
            "title": "Get Gazette Notice Full Text",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def gazette_notice(
        notice_id: Annotated[str, Field(
            description="Numeric Gazette notice ID. Returned as notice_numeric_id by gazette_insolvency.",
            min_length=1, max_length=20,
        )],
    ) -> dict:
        """Fetch the full legal wording of a Gazette notice by numeric notice ID.

        Returns the complete JSON-LD linked-data record for the notice: parties,
        legal basis, court, and full text. Use gazette_insolvency first to find
        notice_numeric_id values.
        """
        return await _fetch_gazette_notice(notice_id)

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
        max_notices: Annotated[int, Field(description="Cap on notices returned, applied after severity/date sort. Default 20. The Gazette insolvency feed returns up to 100 results per search — raise to 100 to see the full set.", ge=1, le=100)] = 20,
    ) -> GazetteInsolvencyResult:
        """Search The Gazette's insolvency notice index by entity name.

        Searches the Gazette's insolvency endpoint which covers corporate
        notice codes: winding-up orders (2443), administration orders (2448),
        liquidator appointments (2452), striking-off notices (2460), and more.
        Results are sorted by severity — winding-up orders and administration
        orders appear first.

        Each result includes a notice_numeric_id. Read the full legal wording
        via the notice://{notice_numeric_id} resource.

        The Gazette is the official UK public record. A notice here means
        the event has been formally published and is legally effective.
        """
        qs: dict[str, Any] = {
            "text": entity_name,
            "results-page-size": 100,
        }
        if start_date:
            qs["start-publish-date"] = start_date
        if end_date:
            qs["end-publish-date"] = end_date

        all_notices: list[dict[str, Any]] = []

        async with gazette_client() as client:
            try:
                resp = await _request_with_retry(
                    client, "GET", "/insolvency/notice/data.json", params=qs
                )
                raw = resp.json()
                entries = raw.get("entry", []) if isinstance(raw, dict) else []
                if isinstance(entries, dict):
                    entries = [entries]
                all_notices = _extract_notices(entries)
            except Exception:
                pass

        # Filter by specific notice type if requested
        if notice_type:
            all_notices = [n for n in all_notices if n["notice_code"] == notice_type]

        # Apply global cap (already sorted by _extract_notices)
        all_notices = all_notices[:max_notices]

        notice_models: list[GazetteNotice] = []
        for n in all_notices:
            code = n.get("notice_code")
            notice_models.append(
                GazetteNotice(
                    notice_id=n.get("notice_id"),
                    notice_numeric_id=n.get("notice_numeric_id"),
                    notice_code=code,
                    notice_type=n.get("notice_type"),
                    severity=SEVERITY.get(code or "", 0),
                    date=n.get("date"),
                    title=n.get("title"),
                    content=n.get("content"),
                )
            )

        return GazetteInsolvencyResult(
            entity_name=entity_name,
            notice_type_filter=notice_type,
            start_date=start_date,
            end_date=end_date,
            total_notices=len(notice_models),
            max_notices_cap=max_notices,
            notices=notice_models,
        )


# ---------------------------------------------------------------------------
# Resource registration
# ---------------------------------------------------------------------------

def register_resources(mcp: FastMCP) -> None:

    @mcp.resource(
        "notice://{notice_id}",
        name="gazette_notice",
        description=(
            "Full content of a Gazette notice by numeric notice ID. "
            "Use the notice_numeric_id returned by gazette_insolvency. "
            "Returns JSON-LD linked-data view of the notice."
        ),
        mime_type="application/json",
    )
    async def gazette_notice_resource(notice_id: str) -> str:
        import json
        data = await _fetch_gazette_notice(notice_id)
        return json.dumps(data)
