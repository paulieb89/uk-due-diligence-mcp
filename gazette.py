"""
The Gazette corporate-insolvency tools and per-notice text resource.

Notice labels are keyed to The Gazette's authoritative current notice-code
register. Search results preserve the notice code and a source-faithful label;
the full notice wording comes from the per-notice XHTML+RDFa page
(GET /notice/{id}, Accept: application/xml).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Annotated, Any
from xml.parsers.expat import ExpatError

from pydantic import Field
from fastmcp import FastMCP

from fastmcp.exceptions import ToolError
from http_client import GAZETTE_BASE, _json_or_error, _request_with_retry, gazette_client
from mcpfleet_obs import raise_http_tool_error, raise_tool_error
from models import GazetteInsolvencyResult, GazetteNotice, GazetteNoticeResult

# ---------------------------------------------------------------------------
# Notice code taxonomy
# ---------------------------------------------------------------------------

# Corporate insolvency codes from The Gazette's authoritative notice-code register.
# gazette_insolvency is corporate-only: it requests category 24 and returns only
# these codes. Personal insolvency is out of scope here and belongs to the
# separate opt-in tools tracked in #84 (gazette_personal_insolvency) and #85
# (individual_insolvency_search). Any other code the feed returns is counted in
# skipped_unrecognised / unrecognised_codes rather than dropped silently.
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
    return re.sub(r"<[^>]+>", " ", html).strip()


def _notice_numeric_id(notice_uri: str) -> str:
    """Extract the numeric notice ID from a Gazette notice URI.

    e.g. 'https://www.thegazette.co.uk/id/notice/5122793' → '5122793'
    """
    return notice_uri.rstrip("/").split("/")[-1]


def _extract_notices(
    entries: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int, list[str]]:
    """Pull structured insolvency notice records from the Gazette feed entry array.

    Returns (notices, skipped_unrecognised, unrecognised_codes): the corporate
    notices sorted by severity then date, the number of entries dropped because
    their code is not in NOTICE_LABELS, and those codes sorted and distinct.
    """
    notices = []
    skipped = 0
    unrecognised: set[str] = set()
    for entry in entries:
        code = str(entry.get("f:notice-code", ""))
        if code not in ALL_CORPORATE_INSOLVENCY_CODES:
            skipped += 1
            unrecognised.add(code)
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
    return notices, skipped, sorted(unrecognised)


# The Notice Feed caps results-page-size at 100.
FEED_PAGE_SIZE = 100


def _feed_entries(raw: Any) -> list[dict[str, Any]]:
    """Return the feed's entries as a list (the feed sends a lone entry as a dict)."""
    entries = raw.get("entry", []) if isinstance(raw, dict) else []
    if isinstance(entries, dict):
        return [entries]
    if not isinstance(entries, list):
        return []
    return [e for e in entries if isinstance(e, dict)]


def _feed_total(raw: Any) -> int | None:
    """Parse the feed's top-level f:total as a non-negative int.

    Absent, null or malformed is None (unknown), never 0. A genuine 0 stays 0.
    """
    if not isinstance(raw, dict):
        return None
    value = raw.get("f:total")
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str):
        text = value.strip()
        # ASCII digits only: str.isdigit() also accepts e.g. "²", which int()
        # rejects. Any conversion failure (including Python's int-string
        # length limit) means an unknown total, never a lost page.
        if text and text.isascii() and text.isdigit():
            try:
                return int(text)
            except ValueError:
                return None
    return None


# ---------------------------------------------------------------------------
# Shared fetch helper
# ---------------------------------------------------------------------------

# Notice IDs: numeric (4420724) or legacy (L-57793-2040, E-26343-2431-168).
_NUMERIC_NOTICE_ID = re.compile(r"[0-9]+")
_LEGACY_NOTICE_ID = re.compile(r"[A-Z]-[0-9]+(?:-[0-9]+)+")

XHTML_NS = "http://www.w3.org/1999/xhtml"
_X = f"{{{XHTML_NS}}}"
# Element nesting deeper than this is refused before ElementTree builds a tree.
# Real notices nest about 15 deep.
NOTICE_MAX_DEPTH = 500


def _validate_notice_id(notice_id: str) -> str:
    """The stripped notice ID, or a validation error before any request is sent."""
    nid = notice_id.strip()
    if not (_NUMERIC_NOTICE_ID.fullmatch(nid) or _LEGACY_NOTICE_ID.fullmatch(nid)):
        raise_tool_error(
            "validation",
            is_retryable=False,
            attempted=f"gazette_notice({notice_id!r})",
            description=(
                f"Invalid Gazette notice ID {notice_id!r}: expected a numeric ID "
                "(e.g. '4420724') or a legacy ID (e.g. 'L-57793-2040', 'E-26343-2431-168')."
            ),
        )
    return nid


class _UnsafeXML(Exception):
    """The notice body declares entities or nests too deeply to parse safely."""


def _preflight_xml(body: bytes) -> None:
    """Reject entity declarations and excessive nesting before ElementTree parses.

    Expat decodes the declared encoding (UTF-16 included), so a declaration is
    caught however the bytes are encoded, and before any entity is expanded.
    A plain DOCTYPE without entity declarations is allowed.
    """
    from xml.parsers import expat

    parser = expat.ParserCreate()
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    depth = 0

    def entity_decl(*_args: Any) -> None:
        raise _UnsafeXML("the body declares an XML entity")

    def unparsed_entity_decl(*_args: Any) -> None:
        raise _UnsafeXML("the body declares an unparsed XML entity")

    def external_entity_ref(*_args: Any) -> int:
        raise _UnsafeXML("the body references an external XML entity")

    def start(*_args: Any) -> None:
        nonlocal depth
        depth += 1
        if depth > NOTICE_MAX_DEPTH:
            raise _UnsafeXML(f"the body nests elements deeper than {NOTICE_MAX_DEPTH}")

    def end(*_args: Any) -> None:
        nonlocal depth
        depth -= 1

    parser.EntityDeclHandler = entity_decl
    parser.UnparsedEntityDeclHandler = unparsed_entity_decl
    parser.ExternalEntityRefHandler = external_entity_ref
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.Parse(body, True)


def _classes(elem: ET.Element) -> list[str]:
    return (elem.get("class") or "").split()


def _is_rdfa_data(elem: ET.Element) -> bool:
    return elem.tag == f"{_X}div" and "rdfa-data" in _classes(elem)


def _visible_elements(root: ET.Element) -> list[ET.Element]:
    """Every element under root in document order, skipping div.rdfa-data subtrees."""
    out: list[ET.Element] = []
    stack = [root]
    while stack:
        elem = stack.pop()
        if _is_rdfa_data(elem):
            continue
        out.append(elem)
        stack.extend(reversed(list(elem)))
    return out


def _visible_text(root: ET.Element) -> str:
    """root's text in document order, skipping div.rdfa-data, whitespace collapsed.

    Iterative, so nesting depth cannot exhaust the stack. A skipped element's
    tail (the text after it) is kept.
    """
    parts: list[str] = []
    stack: list[ET.Element | str] = [root]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            parts.append(item)
            continue
        if _is_rdfa_data(item):
            continue
        if item.text:
            parts.append(item.text)
        for child in reversed(list(item)):
            if child.tail:
                stack.append(child.tail)
            stack.append(child)
    return " ".join("".join(parts).split())


def _or_none(value: str | None) -> str | None:
    """Stripped value, or None when absent or blank (unknown is null, never "")."""
    if value is None:
        return None
    value = " ".join(value.split())
    return value or None


def _by_property(elements: list[ET.Element], prop: str) -> ET.Element | None:
    for elem in elements:
        if prop in (elem.get("property") or "").split():
            return elem
    return None


def _property_value(elements: list[ET.Element], prop: str) -> str | None:
    """First element carrying the RDFa property: its text, else its @content."""
    elem = _by_property(elements, prop)
    if elem is None:
        return None
    return _or_none(_visible_text(elem)) or _or_none(elem.get("content"))


def _dd_label(elements: list[ET.Element], ui_class: str) -> str | None:
    for elem in elements:
        if elem.tag == f"{_X}dd" and elem.get("data-ui-class") == ui_class:
            return _or_none(_visible_text(elem))
    return None


_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def _iso_date(value: str | None) -> str | None:
    if not value:
        return None
    date = value.strip()[:10]
    return date if _ISO_DATE.fullmatch(date) else None


def _content_div(root: ET.Element) -> ET.Element | None:
    """The notice body: the first div.content inside body//article."""
    body = root.find(f"{_X}body")
    if body is None:
        return None
    for article in body.iter(f"{_X}article"):
        for div in article.iter(f"{_X}div"):
            if "content" in _classes(div):
                return div
    return None


def _parse_notice(nid: str, body: bytes, attempted: str) -> GazetteNoticeResult:
    """Parse a notice's XHTML+RDFa body, or raise a retryable transient error."""

    def fail(problem: str) -> None:
        raise_tool_error(
            "transient",
            is_retryable=True,
            attempted=attempted,
            description=f"The Gazette returned an unusable notice body: {problem}. Retry later.",
        )

    try:
        _preflight_xml(body)
        root = ET.fromstring(body)
    except _UnsafeXML as exc:
        fail(str(exc))
    except (ET.ParseError, ExpatError) as exc:
        fail(f"the XML is not well-formed ({exc})")
    except RecursionError:
        fail("the XML nests too deeply to parse")

    try:
        content = _content_div(root)
        if content is None:
            fail("no div.content (notice body) inside the article")
        elements = _visible_elements(root)
        head = root.find(f"{_X}head")
        title_elem = head.find(f"{_X}title") if head is not None else None
        title = _or_none(_visible_text(title_elem)) if title_elem is not None else None

        pub = _by_property(elements, "gaz:hasPublicationDate")
        publication_date = _iso_date(pub.get("content")) if pub is not None else None
        if publication_date is None:
            first_time = next((e for e in elements if e.tag == f"{_X}time"), None)
            if first_time is not None:
                publication_date = _iso_date(first_time.get("datetime"))

        return GazetteNoticeResult(
            notice_id=nid,
            notice_url=f"{GAZETTE_BASE}/notice/{nid}",
            title=title,
            notice_code=_property_value(elements, "gaz:hasNoticeCode"),
            notice_type=_dd_label(elements, "notice-type"),
            category=_dd_label(elements, "category"),
            publication_date=publication_date,
            company_number=_property_value(elements, "gazorg:companyNumber"),
            company_name=_property_value(elements, "gazorg:name"),
            previous_company_name=_property_value(elements, "gazorg:previousCompanyName"),
            text=_visible_text(content),
        )
    except RecursionError:
        fail("the XML nests too deeply to parse")


async def _fetch_gazette_notice(notice_id: str) -> GazetteNoticeResult:
    """Fetch one notice: GET /notice/{id} with Accept: application/xml (#98).

    robots.txt allows this path; it disallows the data.* formats and
    ?view=linked-data, so neither is ever requested. One request per notice,
    through the shared Gazette client (#86 User-Agent, 5-per-10s pacing and the
    #107 Retry-After pause). Backs the gazette_notice tool, the notice://
    resource and fetch("notice:...").
    """
    nid = _validate_notice_id(notice_id)
    path = f"/notice/{nid}"
    attempted = f"GET {path}"
    async with gazette_client() as client:
        try:
            resp = await _request_with_retry(
                client, "GET", path, headers={"Accept": "application/xml"}
            )
        except ToolError:
            raise
        except Exception as exc:
            raise_http_tool_error(exc, attempted=attempted)

    media_type = resp.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "application/xml":
        raise_tool_error(
            "transient",
            is_retryable=True,
            attempted=attempted,
            description=(
                f"The Gazette answered HTTP {resp.status_code} with content-type "
                f"{resp.headers.get('content-type', 'unknown')}, not application/xml; "
                "likely a site or error page. Retry later."
            ),
        )
    return _parse_notice(nid, resp.content, attempted)


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

def register_tools(mcp: FastMCP) -> None:

    @mcp.tool(
        name="gazette_notice",
        annotations={
            "title": "Get Gazette Notice Text and Fields",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def gazette_notice(
        notice_id: Annotated[str, Field(
            description=(
                "Gazette notice ID: numeric (e.g. '4420724') or legacy "
                "(e.g. 'L-57793-2040', 'E-26343-2431-168'). Returned as "
                "notice_numeric_id by gazette_insolvency."
            ),
            min_length=1,
        )],
    ) -> GazetteNoticeResult:
        """Fetch a Gazette notice's full legal wording and labelled fields by notice ID.

        Returns text (the full notice wording), title, notice_code, notice_type,
        category, publication_date (YYYY-MM-DD), company_number, company_name,
        previous_company_name and notice_url. Fields the notice does not carry
        are null. Legacy notices (L-.../E-... IDs) usually carry no structured
        company number: a null company_number means the notice has no structured
        number, not that it concerns a different company; read the text. Use
        gazette_insolvency first to find notice_numeric_id values.
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
        name: Annotated[str | None, Field(description="Company name to search for in Gazette corporate insolvency notices", min_length=2, max_length=200)] = None,
        query: Annotated[str | None, Field(description="Alias for name: the company name to search for.", min_length=2, max_length=200)] = None,
        entity_name: Annotated[str | None, Field(description="Deprecated alias for name: the company name to search for.", min_length=2, max_length=200)] = None,
        notice_type: Annotated[str | None, Field(description="Filter by Gazette notice code (e.g. '2450' petition to wind up a company, '2452' winding-up order, '2410' appointment of administrators). Omit to search all.")] = None,
        start_date: Annotated[str | None, Field(description="Filter notices from this date (YYYY-MM-DD)")] = None,
        end_date: Annotated[str | None, Field(description="Filter notices up to this date (YYYY-MM-DD)")] = None,
        max_notices: Annotated[int, Field(description="Cap on notices returned from this page, applied after the severity/date sort. Default 20. One call reads one page of at most 100 feed entries, so 100 removes the cap for that page (notice_type and code filters still apply); it does not reach every match: compare upstream_total and use page to read further.", ge=1, le=100)] = 20,
        page: Annotated[int, Field(description="1-based page of the Gazette feed to read (100 entries per page). Default 1. Use with upstream_total to read past the first 100 matches.", ge=1)] = 1,
    ) -> GazetteInsolvencyResult:
        """Search The Gazette's corporate insolvency notices by company name.

        Searches The Gazette's corporate-insolvency notice index (category 24)
        using the authoritative Gazette notice-code taxonomy. Results are sorted
        by an internal DD severity score; the notice label itself remains a
        source fact.

        Corporate only. Personal insolvency (bankruptcy, IVAs, sequestrations,
        trust deeds) is not covered, so an empty result says nothing about any
        individual, such as a director or PSC. The Individual Insolvency
        Register covers England and Wales only; Scottish sequestrations and
        trust deeds are on the Accountant in Bankruptcy register, and Northern
        Ireland has its own register.

        One call reads one page of at most 100 feed entries (page, 1-based);
        it never fetches further pages. upstream_total is the feed's count of
        matching corporate notices (null when the feed does not say), and
        is_partial is true when matches remain after this page (the count
        exceeds the entries on this and earlier pages), or when the count is
        unknown. The severity sort and max_notices cap apply within the page,
        so the most severe notices shown are the most severe on this page,
        not across every match.

        skipped_unrecognised counts entries on this page whose notice code is
        not a known corporate code, and unrecognised_codes lists those codes.
        They are a diagnostic for codes the taxonomy does not label yet, not a
        sign of personal insolvency coverage.

        Each result includes a notice_numeric_id. Read the full legal wording
        via the notice://{notice_numeric_id} resource.

        The Gazette is the official UK public record. A notice here means
        the event has been formally published. This tool does not establish its legal effect.
        """
        entity_name = name or query or entity_name
        if not entity_name:
            raise_tool_error(
                "validation",
                is_retryable=False,
                attempted="gazette_insolvency(name=None)",
                description="Provide 'name' (or 'query'): the company name to search for.",
            )
        qs: dict[str, Any] = {
            # `text` is not in the Notice Feed parameter table, but it works
            # in production; keep it as is.
            "text": entity_name,
            # Corporate insolvency only, so f:total counts notices this tool
            # can return (category 25, personal insolvency, is excluded).
            "categorycode": 24,
            "results-page-size": FEED_PAGE_SIZE,
            "results-page": page,
        }
        if start_date:
            qs["start-publish-date"] = start_date
        if end_date:
            qs["end-publish-date"] = end_date

        all_notices: list[dict[str, Any]] = []
        entries_fetched = 0
        skipped_unrecognised = 0
        unrecognised_codes: list[str] = []
        upstream_total: int | None = None

        async with gazette_client() as client:
            try:
                resp = await _request_with_retry(
                    client, "GET", "/insolvency/notice/data.json", params=qs
                )
                raw = _json_or_error(resp)
                entries = _feed_entries(raw)
                # Count raw feed entries before the code, notice_type and
                # max_notices filters: is_partial is about the feed, not the filters.
                entries_fetched = len(entries)
                upstream_total = _feed_total(raw)
                all_notices, skipped_unrecognised, unrecognised_codes = _extract_notices(entries)
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
            upstream_total=upstream_total,
            page=page,
            # Partial while matches remain after this page; an unknown total is
            # conservatively partial, never claimed complete.
            is_partial=(
                upstream_total is None
                or upstream_total > (page - 1) * FEED_PAGE_SIZE + entries_fetched
            ),
            skipped_unrecognised=skipped_unrecognised,
            unrecognised_codes=unrecognised_codes,
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
            "Full text and labelled fields of a Gazette notice by notice ID. "
            "Use the notice_numeric_id returned by gazette_insolvency (numeric or legacy L-/E- form). "
            "Returns the notice's text (full wording), title, notice_code, notice_type, category, "
            "publication_date, company_number, company_name, previous_company_name and notice_url; "
            "absent fields are null."
        ),
        mime_type="application/json",
    )
    async def gazette_notice_resource(notice_id: str) -> str:
        result = await _fetch_gazette_notice(notice_id)
        return result.model_dump_json()
