"""
Consolidated sanctions screening (1 tool: sanctions_screen).

Screens a name against four official consolidated lists:
  - OFSI (UK, HM Treasury)   ConList.csv     — CSV
  - OFAC (US Treasury)       SDN.XML         — XML
  - EU  (FSF)                full list       — XML
  - UN  (Security Council)   consolidated    — XML

Unlike the other registers in this server, these sources are NOT per-entity query
APIs — they are bulk files with no name-search endpoint. So this module keeps an
in-memory index (normalised name -> list entries) built from all four lists, lazily
loaded on first use and refreshed on a TTL. To fit the 256 MB instance, each list is
streamed to a temp file and parsed incrementally (never held whole in memory) into a
compact tuple index; the raw files are discarded.

Matching is deterministic: normalise (upper-case, strip accents/punctuation, collapse
whitespace) then exact-match on primary names AND aliases. Entity/company legal names
match reliably; person names with transliteration variants may not. An empty result is
a screening aid, not a compliance clearance.
"""

from __future__ import annotations

import asyncio
import csv
import os
import re
import sys
import tempfile
import time
import unicodedata
import xml.etree.ElementTree as ET
from collections import namedtuple
from datetime import datetime, timezone
from typing import Annotated, Iterator

from pydantic import Field
from fastmcp import FastMCP

from http_client import (
    EU_FSF_URL,
    OFAC_SDN_URL,
    OFSI_CONLIST_URL,
    UN_CONSOLIDATED_URL,
    sanctions_client,
)
from mcpfleet_obs import raise_tool_error
from models import SanctionsHit, SanctionsScreenResult

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

TTL_SECONDS = 24 * 60 * 60  # lists update on designation; a daily refresh is ample
# After a load that produced nothing there is no index to serve, so every call
# would otherwise re-download all four bulk lists inside _LOCK. _download_to_temp
# does not go through _request_with_retry and sanctions_client's read timeout is
# 120s, so an unbounded retry costs ~8 minutes per caller during an outage.
FAILURE_COOLDOWN_SECONDS = 5 * 60
VALID_ENTITY_TYPES = {"person", "entity"}

# A single index entry. Tuples (not dicts/models) keep the index compact on a 256 MB VM.
Record = namedtuple(
    "Record", "source matched_name is_alias entity_type regime reference listed_on"
)

# ---------------------------------------------------------------------------
# Module-level cache (single-instance deployment -> one in-process index)
# ---------------------------------------------------------------------------

_INDEX: dict[str, list[Record]] | None = None
_AS_AT: str | None = None
_LOADED_MONO: float | None = None
_LISTS_OK: list[str] = []
_FAILED_MONO: float | None = None  # when a build last produced zero lists
_LOCK = asyncio.Lock()


def _log(msg: str) -> None:
    print(f"[uk_due_diligence_mcp.sanctions] {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Normalisation + small helpers
# ---------------------------------------------------------------------------

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+")


def normalize(name: str | None) -> str:
    """Upper-case, strip diacritics + punctuation, collapse whitespace."""
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.upper()
    s = _PUNCT_RE.sub(" ", s)
    return _WS_RE.sub(" ", s).strip()


def _iso_date(s: str | None) -> str | None:
    """Best-effort ISO YYYY-MM-DD. Handles YYYY-MM-DD… and DD/MM/YYYY (OFSI)."""
    s = (s or "").strip()
    if not s:
        return None
    if re.match(r"^\d{4}-\d{2}-\d{2}", s):
        return s[:10]
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", s)
    if m:
        d, mo, y = m.groups()
        return f"{y}-{int(mo):02d}-{int(d):02d}"
    return s or None


def _local(tag: str) -> str:
    """Local name of a (possibly namespaced) XML tag: '{ns}sdnEntry' -> 'sdnEntry'."""
    return tag.rsplit("}", 1)[-1]


def _child_text(elem: ET.Element, name: str) -> str:
    for c in elem:
        if _local(c.tag) == name:
            return (c.text or "").strip()
    return ""


def _children(elem: ET.Element, name: str) -> list[ET.Element]:
    return [c for c in elem if _local(c.tag) == name]


# ---------------------------------------------------------------------------
# Download (stream to disk to bound memory)
# ---------------------------------------------------------------------------

async def _download_to_temp(url: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".sanctions")
    os.close(fd)
    async with sanctions_client() as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with open(path, "wb") as fh:
                async for chunk in resp.aiter_bytes():
                    fh.write(chunk)
    return path


# ---------------------------------------------------------------------------
# Per-list parsers (each yields Record; parse from disk, clear as we go)
# ---------------------------------------------------------------------------

def _parse_ofsi(path: str) -> Iterator[Record]:
    """OFSI ConList.csv — line 1 is 'Last Updated,<date>', line 2 the header."""
    with open(path, encoding="utf-8-sig", errors="replace") as fh:
        reader = csv.reader(fh)
        next(reader, None)  # "Last Updated,<date>"
        header = next(reader, None)
        if not header:
            return
        col = {h.strip(): i for i, h in enumerate(header)}

        def g(row: list[str], name: str) -> str:
            i = col.get(name)
            return row[i].strip() if i is not None and i < len(row) else ""

        for row in reader:
            if not row:
                continue
            full = " ".join(g(row, f"Name {k}") for k in range(1, 7)).strip()
            full = _WS_RE.sub(" ", full)
            if not full:
                continue
            gtype = g(row, "Group Type")
            etype = "person" if gtype == "Individual" else "entity" if gtype == "Entity" else None
            alias_type = g(row, "Alias Type").lower()
            is_alias = alias_type not in ("", "primary name")
            yield Record(
                "OFSI (UK)",
                full,
                is_alias,
                etype,
                g(row, "Regime") or None,
                g(row, "Group ID") or None,
                _iso_date(g(row, "Listed On") or g(row, "UK Sanctions List Date Designated")),
            )


def _parse_ofac(path: str) -> Iterator[Record]:
    """OFAC SDN.XML — <sdnEntry> with firstName/lastName, sdnType, programList, akaList."""
    for _ev, elem in ET.iterparse(path, events=("end",)):
        if _local(elem.tag) != "sdnEntry":
            continue
        sdn_type = _child_text(elem, "sdnType")
        etype = "person" if sdn_type == "Individual" else "entity" if sdn_type == "Entity" else None
        pl = _children(elem, "programList")
        programs = [(c.text or "").strip() for c in pl[0] if _local(c.tag) == "program"] if pl else []
        regime = ", ".join(p for p in programs if p) or None
        ref = _child_text(elem, "uid") or None

        primary = " ".join(
            x for x in (_child_text(elem, "firstName"), _child_text(elem, "lastName")) if x
        ).strip()
        seen: set[str] = set()
        if primary:
            seen.add(primary)
            yield Record("OFAC (US)", primary, False, etype, regime, ref, None)
        for al in _children(elem, "akaList"):
            for aka in _children(al, "aka"):
                nm = " ".join(
                    x for x in (_child_text(aka, "firstName"), _child_text(aka, "lastName")) if x
                ).strip()
                if nm and nm not in seen:
                    seen.add(nm)
                    yield Record("OFAC (US)", nm, True, etype, regime, ref, None)
        elem.clear()


def _parse_eu(path: str) -> Iterator[Record]:
    """EU FSF — <sanctionEntity> with subjectType, nameAlias[], regulation[programme]."""
    for _ev, elem in ET.iterparse(path, events=("end",)):
        if _local(elem.tag) != "sanctionEntity":
            continue
        ref = elem.get("euReferenceNumber") or elem.get("logicalId")
        subj = _children(elem, "subjectType")
        code = subj[0].get("code") if subj else None
        etype = "person" if code == "person" else "entity" if code in ("enterprise", "organisation") else None
        regs = _children(elem, "regulation")
        regime = regs[0].get("programme") if regs else None
        listed_on = _iso_date(regs[0].get("publicationDate") or regs[0].get("entryIntoForceDate")) if regs else None

        seen = set()
        for i, na in enumerate(_children(elem, "nameAlias")):
            whole = na.get("wholeName") or " ".join(
                x for x in (na.get("firstName"), na.get("middleName"), na.get("lastName")) if x
            )
            whole = (whole or "").strip()
            if whole and whole not in seen:
                seen.add(whole)
                yield Record("EU", whole, i > 0, etype, regime, ref, listed_on)
        elem.clear()


def _parse_un(path: str) -> Iterator[Record]:
    """UN consolidated — <INDIVIDUAL> and <ENTITY> under INDIVIDUALS / ENTITIES."""
    for _ev, elem in ET.iterparse(path, events=("end",)):
        tag = _local(elem.tag)
        if tag not in ("INDIVIDUAL", "ENTITY"):
            continue
        ref = _child_text(elem, "REFERENCE_NUMBER") or _child_text(elem, "DATAID") or None
        regime = _child_text(elem, "UN_LIST_TYPE") or None
        listed_on = _iso_date(_child_text(elem, "LISTED_ON"))

        if tag == "INDIVIDUAL":
            etype = "person"
            primary = " ".join(
                p for p in (
                    _child_text(elem, "FIRST_NAME"),
                    _child_text(elem, "SECOND_NAME"),
                    _child_text(elem, "THIRD_NAME"),
                    _child_text(elem, "FOURTH_NAME"),
                ) if p
            ).strip()
            alias_wrappers = _children(elem, "INDIVIDUAL_ALIAS")
        else:
            etype = "entity"
            primary = _child_text(elem, "FIRST_NAME").strip()
            alias_wrappers = _children(elem, "ENTITY_ALIAS")

        seen = set()
        if primary:
            seen.add(primary)
            yield Record("UN", primary, False, etype, regime, ref, listed_on)
        for aw in alias_wrappers:
            nm = _child_text(aw, "ALIAS_NAME")
            if nm and nm not in seen:
                seen.add(nm)
                yield Record("UN", nm, True, etype, regime, ref, listed_on)
        elem.clear()


# (label, url, parser) — order chosen so the big XML lists stream before OFSI's CSV load.
_SOURCES = [
    ("OFAC (US)", OFAC_SDN_URL, _parse_ofac),
    ("EU", EU_FSF_URL, _parse_eu),
    ("UN", UN_CONSOLIDATED_URL, _parse_un),
    ("OFSI (UK)", OFSI_CONLIST_URL, _parse_ofsi),
]

# Every list this server screens against, loaded or not. Derived, never
# hand-maintained: a fifth source must not need a second edit here.
_EXPECTED_LISTS = [label for label, _, _ in _SOURCES]


# ---------------------------------------------------------------------------
# Index build + lazy/TTL cache
# ---------------------------------------------------------------------------

async def _build_index() -> tuple[dict[str, list[Record]], list[str]]:
    """Fetch + parse every list into a normalised-name index. Best-effort per list."""
    index: dict[str, list[Record]] = {}
    ok: list[str] = []
    for label, url, parser in _SOURCES:
        path = None
        try:
            path = await _download_to_temp(url)
            count = 0
            for rec in parser(path):
                key = normalize(rec.matched_name)
                if not key:
                    continue
                index.setdefault(key, []).append(rec)
                count += 1
            if count == 0:
                # A national sanctions list is never legitimately empty: a zero
                # parse means the download or the parser broke, not that nobody
                # is designated. Withhold the label rather than claim a screen.
                _log(f"REJECTED {label}: parsed 0 name entries")
                continue
            ok.append(label)
            _log(f"loaded {label}: {count} name entries")
        except Exception as exc:  # one bad list must not sink the others
            _log(f"FAILED to load {label}: {type(exc).__name__}: {exc}")
        finally:
            if path and os.path.exists(path):
                try:
                    os.unlink(path)
                except OSError:
                    pass
    return index, ok


def _fresh(now: float) -> bool:
    return _INDEX is not None and _LOADED_MONO is not None and (now - _LOADED_MONO) < TTL_SECONDS


def _cooling_down(now: float) -> bool:
    """True while a recent build produced zero lists and there is nothing to serve."""
    return (
        _INDEX is None
        and _FAILED_MONO is not None
        and (now - _FAILED_MONO) < FAILURE_COOLDOWN_SECONDS
    )


def _raise_no_index() -> None:
    raise_tool_error(
        "transient",
        is_retryable=True,
        attempted="build sanctions index",
        description=(
            "None of the consolidated sanctions lists could be loaded, so no screen "
            "was performed. This is an upstream failure, not a clean result — do not "
            "read it as an absence of sanctions."
        ),
    )


async def get_index() -> tuple[dict[str, list[Record]], str | None, list[str]]:
    """Return the cached index, rebuilding if empty or older than the TTL.

    Raises rather than returning an empty index: caching a load that produced
    nothing would serve a clean screen for the whole TTL, outliving the outage
    that caused it.
    """
    global _INDEX, _AS_AT, _LOADED_MONO, _LISTS_OK, _FAILED_MONO
    now = time.monotonic()
    if _fresh(now):
        return _INDEX, _AS_AT, _LISTS_OK
    if _cooling_down(now):
        _raise_no_index()
    async with _LOCK:
        now = time.monotonic()
        if _fresh(now):
            return _INDEX, _AS_AT, _LISTS_OK
        if _cooling_down(now):
            _raise_no_index()
        index, ok = await _build_index()
        if not ok:
            _FAILED_MONO = time.monotonic()
            if _INDEX is not None:
                # Every list failed this refresh — keep serving the previous (stale) index.
                _log("refresh loaded 0 lists; retaining previous index")
                return _INDEX, _AS_AT, _LISTS_OK
            # Nothing to fall back on. Do not cache {} and do not stamp _AS_AT:
            # a screen that loaded no list is unresolved, not clean.
            _log("build loaded 0 lists and no previous index exists; refusing to serve")
            _raise_no_index()
        _INDEX = index
        _LISTS_OK = ok
        _AS_AT = datetime.now(timezone.utc).isoformat()
        _LOADED_MONO = time.monotonic()
        _FAILED_MONO = None
        return _INDEX, _AS_AT, _LISTS_OK


async def warm_cache() -> None:
    """Fire-and-forget cache warm for server startup (never blocks boot)."""

    async def _run() -> None:
        try:
            await get_index()
            _log("startup warm complete")
        except Exception as exc:
            _log(f"startup warm failed (will lazy-load on first call): {exc}")

    asyncio.create_task(_run())


def _screen(index: dict[str, list[Record]], name: str, entity_type: str | None) -> list[SanctionsHit]:
    key = normalize(name)
    if not key:
        return []
    recs = index.get(key, [])
    if entity_type in VALID_ENTITY_TYPES:
        # Keep records that match the requested type OR don't declare one.
        recs = [r for r in recs if r.entity_type == entity_type or r.entity_type is None]
    seen: set[tuple] = set()
    hits: list[SanctionsHit] = []
    for r in recs:
        dedup = (r.source, r.reference, r.matched_name)
        if dedup in seen:
            continue
        seen.add(dedup)
        hits.append(
            SanctionsHit(
                list_source=r.source,
                matched_name=r.matched_name,
                is_alias=r.is_alias,
                entity_type=r.entity_type,
                regime=r.regime,
                reference=r.reference,
                listed_on=r.listed_on,
            )
        )
    return hits


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

def register_tools(mcp: FastMCP) -> None:

    @mcp.tool(
        name="sanctions_screen",
        annotations={
            "title": "Screen a Name Against Sanctions Lists",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def sanctions_screen(
        name: Annotated[str, Field(
            description="Person or company/entity name to screen against the consolidated sanctions lists.",
            min_length=2, max_length=200,
        )],
        entity_type: Annotated[str | None, Field(
            description="Optional filter: 'person' or 'entity'. Omit to screen both.",
        )] = None,
    ) -> SanctionsScreenResult:
        """Screen a name against the UK (OFSI), US (OFAC), EU and UN consolidated sanctions lists.

        Returns every list entry whose primary name or alias matches, with the
        regime, source reference and listing date. Use it to check whether a
        counterparty — or its officers / persons with significant control —
        appears on a sanctions list.

        MATCHING is deterministic: normalised exact + alias match (case-, accent-
        and punctuation-insensitive). A company/entity legal name matches reliably;
        PERSON names with transliteration variants may not (e.g. 'Mohammed' vs
        'Muhamad'). An empty result is therefore NOT a guarantee of clearance, and a
        hit on a common name may be a false positive to disambiguate. This is a
        screening aid, not a compliance determination.

        `lists_screened` reports which of OFSI/OFAC/EU/UN were actually loaded;
        `lists_unavailable` names those that were not, and `is_partial` is true
        whenever it is non-empty. When `is_partial` is true an empty `hits` is
        UNRESOLVED, not clearance. If no list could be loaded at all the call
        raises a retryable error rather than returning an empty screen. `as_at`
        is when the lists were last refreshed on this server.
        """
        index, as_at, lists_ok = await get_index()
        etype = entity_type if entity_type in VALID_ENTITY_TYPES else None
        hits = _screen(index, name, etype)
        unavailable = [label for label in _EXPECTED_LISTS if label not in lists_ok]
        return SanctionsScreenResult(
            query=name,
            normalized_query=normalize(name),
            entity_type_filter=etype,
            match_count=len(hits),
            lists_screened=lists_ok,
            lists_unavailable=unavailable,
            is_partial=bool(unavailable),
            as_at=as_at,
            hits=hits,
        )
