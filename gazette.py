"""
The Gazette corporate-insolvency tools and linked-data notice resource.

Notice labels are keyed to The Gazette's authoritative current notice-code
register. Search results preserve the notice code and a source-faithful label;
full legal semantics remain available from the per-notice linked-data endpoint.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field
from fastmcp import FastMCP

import httpx
from fastmcp.exceptions import ToolError
from http_client import _request_with_retry, gazette_client
from mcpfleet_obs import raise_http_tool_error, raise_tool_error
from models import GazetteInsolvencyResult, GazetteNotice

# ---------------------------------------------------------------------------
# Notice code taxonomy
# ---------------------------------------------------------------------------

# Corporate insolvency codes from The Gazette's authoritative notice-code register.
# Personal insolvency codes (2500+) are excluded.
NOTICE_LABELS: dict[str, str] = {
    "2401": "Moratorium – coming into force",
    "2402": "Moratorium – coming to an end",
    "2403": "Re-use of a prohibited name",
    "2404": "Cross-border insolvencies",
    "2405": "Overseas territories and dependencies",
    "2406": "Notice of intended dividend",
    "2407": "Notice of dividend",
    "2408": "Other corporate insolvency notices",
    "2409": "Qualifying decision procedure",
    "2410": "Appointment of administrators",
    "2411": "Administration orders",
    "2412": "Meetings of creditors (administration)",
    "2413": "Notices to members (administration)",
    "2414": "Deemed consent (administration)",
    "2421": "Appointment of administrative receivers",
    "2422": "Meetings of creditors (receivership)",
    "2423": "Appointment of receivers",
    "2424": "Deemed consent (administrative receivership)",
    "2431": "Resolution for winding up (members' voluntary)",
    "2432": "Appointment of liquidators (members' voluntary)",
    "2433": "Notices to creditors (members' voluntary)",
    "2434": "Annual liquidation meetings (members' voluntary)",
    "2435": "Final meetings (members' voluntary)",
    "2441": "Resolution for winding up (creditors' voluntary)",
    "2442": "Meetings of creditors (creditors' voluntary)",
    "2443": "Appointment of liquidators (creditors' voluntary)",
    "2444": "Annual liquidation meetings (creditors' voluntary)",
    "2445": "Final meetings (creditors' voluntary)",
    "2446": "Notice to creditors (creditors' voluntary)",
    "2447": "Deemed consent (creditors' voluntary)",
    "2450": "Petitions to wind up (companies)",
    "2451": "Petitions to wind up (partnerships)",
    "2452": "Winding up order (companies)",
    "2453": "Winding up order (partnerships)",
    "2454": "Appointment of liquidators (court winding up)",
    "2455": "Meetings of creditors (court winding up)",
    "2456": "Notice of intended dividend (court winding up)",
    "2457": "Notice of dividend (court winding up)",
    "2458": "Final meetings (court winding up)",
    "2459": "Release of liquidator",
    "2460": "Notice to creditors (court winding up)",
    "2461": "Dismissal of winding up petition",
    "2462": "Service of petition",
    "2463": "Annual meeting",
    "2464": "Public examinations",
    "2465": "Deemed consent (court winding up)",
}

ALL_CORPORATE_INSOLVENCY_CODES = set(NOTICE_LABELS)

# Internal DD severity ordering. This is deliberately separate from the
# authoritative Gazette label mapping above: severity is a BOUCH/MCP judgement,
# while notice labels are source facts.
SEVERITY: dict[str, int] = {
    "2452": 10, "2453": 10,
    "2410": 9, "2411": 9, "2421": 9, "2423": 9, "2454": 9,
    "2441": 8, "2443": 8,
    "2450": 6, "2451": 6, "2462": 6,
    "2401": 4, "2402": 2,
    "2446": 3, "2455": 3, "2460": 3,
    "2442": 2, "2447": 2, "2464": 2, "2465": 2,
    "2431": 1, "2432": 1, "2433": 1,
    "2461": 0,
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
        name: Annotated[str | None, Field(description="Company or individual name to search for in Gazette insolvency notices", min_length=2, max_length=200)] = None,
        query: Annotated[str | None, Field(description="Alias for name.", min_length=2, max_length=200)] = None,
        entity_name: Annotated[str | None, Field(description="Deprecated alias for name.", min_length=2, max_length=200)] = None,
        notice_type: Annotated[str | None, Field(description="Filter by Gazette notice code (e.g. '2450' petition to wind up a company, '2452' winding-up order, '2410' appointment of administrators). Omit to search all.")] = None,
        start_date: Annotated[str | None, Field(description="Filter notices from this date (YYYY-MM-DD)")] = None,
        end_date: Annotated[str | None, Field(description="Filter notices up to this date (YYYY-MM-DD)")] = None,
        max_notices: Annotated[int, Field(description="Cap on notices returned, applied after severity/date sort. Default 20. The Gazette insolvency feed returns up to 100 results per search — raise to 100 to see the full set.", ge=1, le=100)] = 20,
    ) -> GazetteInsolvencyResult:
        """Search The Gazette's insolvency notice index by entity name.

        Searches The Gazette's corporate-insolvency notice index using the
        authoritative Gazette notice-code taxonomy. Results are sorted by an
        internal DD severity score; the notice label itself remains a source fact.

        Each result includes a notice_numeric_id. Read the full legal wording
        via the notice://{notice_numeric_id} resource.

        The Gazette is the official UK public record. A notice here means
        the event has been formally published and is legally effective.
        """
        entity_name = name or query or entity_name
        if not entity_name:
            raise_tool_error(
                "validation",
                is_retryable=False,
                attempted="gazette_insolvency(name=None)",
                description="Provide 'name' (or 'query') — the company or individual name to search for.",
            )
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
            except ToolError:
                # Already a structured fleet error (e.g. persistent 429/503) —
                # propagate as-is rather than masking it as an empty,
                # isError=False notices list.
                raise
            except Exception as exc:
                raise_http_tool_error(exc, attempted="GET /insolvency/notice/data.json")

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
