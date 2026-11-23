"""
Consolidated sanctions screening (1 tool: sanctions_screen).

Screens a name against four official consolidated lists:
  - UK  (FCDO UK Sanctions List) XML        — XML (label "OFSI (UK)")
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
import os
import re
import sys
import tempfile
import time
import unicodedata
import xml.etree.ElementTree as ET
from collections import namedtuple
from datetime import datetime, timezone
from typing import Annotated, Iterable, Iterator, Literal, NamedTuple

from pydantic import Field
from fastmcp import FastMCP

from http_client import (
    EU_FSF_URL,
    FCDO_UK_SANCTIONS_URL,
    OFAC_SDN_URL,
    UN_CONSOLIDATED_URL,
    _stream_to_file_with_retry,
    sanctions_client,
)
from mcpfleet_obs import raise_tool_error
from models import SanctionsHit, SanctionsScreenResult

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

TTL_SECONDS = 24 * 60 * 60  # lists update on designation; a daily refresh is ample
# Per list: after a refresh of a list fails, that list is not retried until this has
# passed; the current index (with the list's previous records, if any, marked stale)
# is served meanwhile. Without it every call during an outage would re-download the
# failed bulk lists inside _LOCK. _download_to_temp retries each list on 429/503 and
# network errors (_stream_to_file_with_retry, the shared policy) and sanctions_client's
# read timeout is 120s, so an unbounded retry would cost minutes per caller during an
# outage.
FAILURE_COOLDOWN_SECONDS = 5 * 60
# A list whose last successful load is older than this is dropped from the index and
# reported unavailable, rather than served stale indefinitely.
MAX_STALE_SECONDS = 7 * 24 * 60 * 60
VALID_ENTITY_TYPES = {"person", "entity"}

# A single index entry. Tuples (not dicts/models) keep the index compact on a 256 MB VM.
Record = namedtuple(
    "Record", "source matched_name is_alias entity_type regime reference listed_on"
)

# ---------------------------------------------------------------------------
# Module-level cache (single-instance deployment -> one in-process index)
# ---------------------------------------------------------------------------

# The published index. None until some list has loaded at least once; afterwards a
# dict (possibly empty, if every list has since expired). Never mutated once published:
# a refresh or expiry builds a new dict and swaps it in, so a caller mid-screen keeps a
# consistent view.
_INDEX: dict[str, list[Record]] | None = None
# Per list, keyed by label. A label is present in _LIST_LOADED_MONO / _LIST_AS_AT only
# while its records are in _INDEX (last successful load, at most MAX_STALE_SECONDS old).
_LIST_AS_AT: dict[str, str] = {}  # ISO 8601 UTC time of the last successful load
_LIST_LOADED_MONO: dict[str, float] = {}  # monotonic time of the last successful load
# Monotonic time of the last failed refresh of each list. Cleared on success, so a
# label present here means the most recent attempt at that list failed.
_LIST_FAILED_MONO: dict[str, float] = {}
_LOCK = asyncio.Lock()
# Strong references to in-flight warm tasks: the event loop only holds weak
# references, so an unreferenced task can be garbage-collected mid-flight.
_WARM_TASKS: set[asyncio.Task[None]] = set()


def _log(msg: str) -> None:
    print(f"[uk_due_diligence_mcp.sanctions] {msg}", file=sys.stderr)


# Clock seams, so tests can drive freshness without patching the time module that
# the event loop itself uses.
def _clock() -> float:
    return time.monotonic()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Normalisation + small helpers
# ---------------------------------------------------------------------------

# `\w` includes `_`, so it is added explicitly: an underscore separates words like
# any other punctuation ("Jal_Vahini" and "Jal Vahini" must share an index key).
_PUNCT_RE = re.compile(r"[^\w\s]|_", re.UNICODE)
_WS_RE = re.compile(r"\s+")


def normalize(name: str | None) -> str:
    """Upper-case, strip diacritics + punctuation (underscore included), collapse whitespace."""
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
    """Stream `url` to a new temp file and return its path; the caller deletes it.

    Transient failures (429/503, network errors) are retried under the shared policy;
    a final failure raises a structured ToolError. On any failure, cancellation
    included, the temp file is removed before the exception propagates.
    """
    fd, path = tempfile.mkstemp(suffix=".sanctions")
    os.close(fd)
    try:
        async with sanctions_client() as client:
            await _stream_to_file_with_retry(client, url, path)
    except BaseException:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise
    return path


# ---------------------------------------------------------------------------
# Per-list parsers (each yields Record; parse from disk, clear as we go)
# ---------------------------------------------------------------------------

# A valid but partial UK file parses cleanly, so the parser also checks its size. The
# published list is about 6,400 designations. The floor is for a cold load, which has no
# earlier load in this process to compare against; it sits well below the published count
# so ordinary delistings do not trip it. Lower it only after a genuine, reviewed shrink.
FCDO_MIN_DESIGNATIONS = 5000
# Within one process, a refresh that loses more than this fraction of the last good load
# is rejected and the previous index is kept. Restarts lose this baseline, so the floor is
# the only check then.
FCDO_MAX_DROP_FRACTION = 0.10
_FCDO_LAST_DESIGNATIONS: int | None = None


def _parse_fcdo(path: str) -> Iterator[Record]:
    """FCDO UK Sanctions List XML — <Designation> with UniqueID, Names/Name, IndividualEntityShip.

    Keeps the `OFSI (UK)` label: this is the consolidated UK list, now published by FCDO.
    Ships carry no person/entity type, so they map to None and pass both entity filters.
    Raises ValueError when the document is not a complete export (see the guards above).
    """
    global _FCDO_LAST_DESIGNATIONS
    events = ET.iterparse(path, events=("start", "end"))
    _, root = next(events)
    if _local(root.tag) != "Designations":
        raise ValueError(f"unexpected root element {root.tag!r}; not an FCDO UK Sanctions List")
    generated = ""
    seen_ids: set[str] = set()
    designations = 0
    for ev, elem in events:
        if ev != "end":
            continue
        if _local(elem.tag) == "DateGenerated":
            generated = (elem.text or "").strip()
            continue
        if _local(elem.tag) != "Designation":
            continue
        ref = _child_text(elem, "UniqueID")
        if not ref:
            raise ValueError("designation without a UniqueID")
        if ref in seen_ids:
            raise ValueError(f"duplicate UniqueID {ref}")
        seen_ids.add(ref)
        designations += 1
        kind = _child_text(elem, "IndividualEntityShip")
        etype = "person" if kind == "Individual" else "entity" if kind == "Entity" else None
        regime = _child_text(elem, "RegimeName") or None
        listed_on = _iso_date(_child_text(elem, "DateDesignated"))

        names: list[tuple[str, bool]] = []
        for block in _children(elem, "Names"):
            for n in _children(block, "Name"):
                full = _WS_RE.sub(" ", " ".join(
                    _child_text(n, f"Name{k}") for k in range(1, 7)
                )).strip()
                if full:
                    names.append((full, _child_text(n, "NameType").lower() != "primary name"))
        if not names:
            raise ValueError(f"designation {ref} has no names")
        # Primary names first, so an identical alias spelling does not take the primary's flag.
        # Dedupe on the exact spelling only, as the other parsers do: case and punctuation
        # variants are distinct list entries, and hit.matched_name must stay the listed name.
        names.sort(key=lambda t: t[1])
        seen: set[str] = set()
        for full, is_alias in names:
            if full in seen:
                continue
            seen.add(full)
            yield Record("OFSI (UK)", full, is_alias, etype, regime, ref, listed_on)
        elem.clear()

    if not generated:
        raise ValueError("no DateGenerated; not a complete FCDO export")
    if designations < FCDO_MIN_DESIGNATIONS:
        raise ValueError(
            f"only {designations} designations; expected at least {FCDO_MIN_DESIGNATIONS}"
        )
    if _FCDO_LAST_DESIGNATIONS and designations < _FCDO_LAST_DESIGNATIONS * (1 - FCDO_MAX_DROP_FRACTION):
        raise ValueError(
            f"{designations} designations is more than {FCDO_MAX_DROP_FRACTION:.0%} below the "
            f"previous load of {_FCDO_LAST_DESIGNATIONS}"
        )
    _FCDO_LAST_DESIGNATIONS = designations


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


# (label, url, parser)
_SOURCES = [
    ("OFAC (US)", OFAC_SDN_URL, _parse_ofac),
    ("EU", EU_FSF_URL, _parse_eu),
    ("UN", UN_CONSOLIDATED_URL, _parse_un),
    ("OFSI (UK)", FCDO_UK_SANCTIONS_URL, _parse_fcdo),
]

# Every list this server screens against, loaded or not. Derived, never
# hand-maintained: a fifth source must not need a second edit here.
_EXPECTED_LISTS = [label for label, _, _ in _SOURCES]


# ---------------------------------------------------------------------------
# Index build + lazy/TTL cache
# ---------------------------------------------------------------------------

def _source_order(rec: Record) -> int:
    try:
        return _EXPECTED_LISTS.index(rec.source)
    except ValueError:
        return len(_EXPECTED_LISTS)


def _merge(
    old: dict[str, list[Record]],
    new: dict[str, list[Record]],
    replace: set[str],
) -> dict[str, list[Record]]:
    """A new index: `old` without any record from `replace`, plus every record in `new`.

    Neither input is mutated, so a caller still screening against `old` is unaffected.
    Record objects (and untouched per-key lists) are reused, not copied.
    """
    merged: dict[str, list[Record]] = {}
    for key, recs in old.items():
        if replace and any(r.source in replace for r in recs):
            kept = [r for r in recs if r.source not in replace]
            if kept:
                merged[key] = kept
        else:
            merged[key] = recs
    for key, recs in new.items():
        if key in merged:
            # Keep hits in list order regardless of which list refreshed last.
            merged[key] = sorted(merged[key] + recs, key=_source_order)
        else:
            merged[key] = list(recs)
    return merged


async def _build_index(
    labels: Iterable[str] | None = None,
) -> tuple[dict[str, list[Record]], list[str], dict[str, tuple[str, float]]]:
    """Fetch + parse the given lists (default: all) into a normalised-name index.

    Best-effort per list. Each list is staged on its own and only reaches the returned
    index once its parser has completed and produced records, so a list that fails
    part-way contributes nothing. Returns (index, loaded labels, label -> (ISO UTC,
    monotonic) time its parse completed).
    """
    wanted = None if labels is None else set(labels)
    index: dict[str, list[Record]] = {}
    ok: list[str] = []
    loaded_at: dict[str, tuple[str, float]] = {}
    for label, url, parser in _SOURCES:
        if wanted is not None and label not in wanted:
            continue
        path = None
        staged: dict[str, list[Record]] = {}
        try:
            path = await _download_to_temp(url)
            count = 0
            for rec in parser(path):
                key = normalize(rec.matched_name)
                if not key:
                    continue
                staged.setdefault(key, []).append(rec)
                count += 1
            if count == 0:
                # A national sanctions list is never legitimately empty: a zero
                # parse means the download or the parser broke, not that nobody
                # is designated. Withhold the label rather than claim a screen.
                _log(f"REJECTED {label}: parsed 0 name entries")
                continue
            for key, recs in staged.items():
                index.setdefault(key, []).extend(recs)
            ok.append(label)
            loaded_at[label] = (_utc_now().isoformat(), _clock())
            _log(f"loaded {label}: {count} name entries")
        except Exception as exc:  # one bad list must not sink the others
            # Records staged before the failure are discarded with `staged`.
            _log(f"FAILED to load {label}: {type(exc).__name__}: {exc}")
        finally:
            if path and os.path.exists(path):
                try:
                    os.unlink(path)
                except OSError:
                    pass
    return index, ok, loaded_at


def _fresh(label: str, now: float) -> bool:
    """True while `label`'s last successful load is younger than the TTL."""
    loaded = _LIST_LOADED_MONO.get(label)
    return loaded is not None and (now - loaded) < TTL_SECONDS


def _cooling_down(label: str, now: float) -> bool:
    """True while a recent refresh of `label` failed and it should not be retried yet."""
    failed = _LIST_FAILED_MONO.get(label)
    return failed is not None and (now - failed) < FAILURE_COOLDOWN_SECONDS


def _due(now: float) -> list[str]:
    """Lists to attempt now: missing or past the TTL, and not cooling down."""
    return [
        label for label in _EXPECTED_LISTS
        if not _fresh(label, now) and not _cooling_down(label, now)
    ]


def _expire(now: float) -> None:
    """Drop every list whose last successful load is older than MAX_STALE_SECONDS."""
    global _INDEX
    expired = {
        label for label, loaded in _LIST_LOADED_MONO.items()
        if (now - loaded) > MAX_STALE_SECONDS
    }
    if not expired:
        return
    for label in expired:
        _LIST_LOADED_MONO.pop(label, None)
        _LIST_AS_AT.pop(label, None)
    if _INDEX is not None:
        _INDEX = _merge(_INDEX, {}, expired)
    _log(f"dropped lists older than {MAX_STALE_SECONDS}s: {sorted(expired)}")


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


class IndexSnapshot(NamedTuple):
    """One coherent view of the index and its per-list provenance."""

    index: dict[str, list[Record]]
    lists_screened: list[str]
    lists_unavailable: list[str]
    lists_stale: list[str]
    list_as_at: dict[str, str]
    as_at: str | None
    is_partial: bool


def _snapshot(now: float) -> IndexSnapshot:
    """Capture the published state. Synchronous, so it cannot interleave with a publish."""
    if _INDEX is None:
        # No list has ever loaded: nothing to serve, and an empty screen is not clean.
        _raise_no_index()
    screened = [label for label in _EXPECTED_LISTS if label in _LIST_LOADED_MONO]
    unavailable = [label for label in _EXPECTED_LISTS if label not in _LIST_LOADED_MONO]
    stale = [label for label in screened if label in _LIST_FAILED_MONO]
    list_as_at = {label: _LIST_AS_AT[label] for label in screened}
    # The oldest *reported* UTC time, not the earliest monotonic load: the wall clock
    # can step between loads, and as_at must never look fresher than any list_as_at.
    as_at = min(list_as_at.values(), key=datetime.fromisoformat, default=None)
    past_ttl = any(not _fresh(label, now) for label in screened)
    return IndexSnapshot(
        index=_INDEX,
        lists_screened=screened,
        lists_unavailable=unavailable,
        lists_stale=stale,
        list_as_at=list_as_at,
        as_at=as_at,
        is_partial=bool(unavailable or stale or past_ttl),
    )


async def get_index() -> IndexSnapshot:
    """Return a snapshot of the index, refreshing any list that is missing or past the TTL.

    Freshness is per list. A refresh replaces only the lists that loaded; a list that
    fails keeps its previous records (reported stale) until they are older than
    MAX_STALE_SECONDS, and is not retried until FAILURE_COOLDOWN_SECONDS has passed.
    Lists that are fresh are never re-downloaded because another list failed.

    Raises rather than returning an empty index when no list has ever loaded: caching
    a load that produced nothing would serve a clean screen, outliving the outage that
    caused it.
    """
    global _INDEX
    now = _clock()
    _expire(now)
    if not _due(now):
        return _snapshot(now)
    async with _LOCK:
        # Another caller may have refreshed while this one waited for the lock.
        now = _clock()
        _expire(now)
        due = _due(now)
        if due:
            index, ok, loaded_at = await _build_index(due)
            now = _clock()
            failed = [label for label in due if label not in ok]
            for label in failed:
                _LIST_FAILED_MONO[label] = now
                if label in _LIST_LOADED_MONO:
                    _log(f"refresh of {label} failed; serving its previous load (stale)")
            if ok:
                for label in ok:
                    _LIST_FAILED_MONO.pop(label, None)
                    _LIST_AS_AT[label], _LIST_LOADED_MONO[label] = loaded_at[label]
                _INDEX = _merge(_INDEX or {}, index, set(ok))
            elif _INDEX is None:
                # Nothing to fall back on. Do not cache {} and do not stamp a load time:
                # a screen that loaded no list is unresolved, not clean.
                _log("build loaded 0 lists and no previous index exists; refusing to serve")
            _expire(now)
        return _snapshot(now)


async def warm_cache() -> None:
    """Fire-and-forget cache warm for server startup (never blocks boot)."""

    async def _run() -> None:
        try:
            await get_index()
            _log("startup warm complete")
        except Exception as exc:
            _log(f"startup warm failed (will lazy-load on first call): {exc}")

    task = asyncio.create_task(_run())
    _WARM_TASKS.add(task)
    task.add_done_callback(_WARM_TASKS.discard)


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
        entity_type: Annotated[Literal["person", "entity"] | None, Field(
            description=(
                "Optional filter: 'person' or 'entity' (a company is an 'entity'). "
                "Any other value is rejected. Omit to screen both."
            ),
        )] = None,
    ) -> SanctionsScreenResult:
        """Screen a name against the UK (OFSI), US (OFAC), EU and UN consolidated sanctions lists.

        Returns every list entry whose primary name or alias matches, with the
        regime, source reference and listing date where the source gives one. Use it to check whether a
        counterparty — or its officers / persons with significant control —
        appears on a sanctions list.

        MATCHING is deterministic: normalised exact + alias match (case-, accent-
        and punctuation-insensitive). A company/entity legal name matches reliably;
        PERSON names with transliteration variants may not (e.g. 'Mohammed' vs
        'Muhamad'). An empty result is therefore NOT a guarantee of clearance, and a
        hit on a common name may be a false positive to disambiguate. This is a
        screening aid, not a compliance determination.

        `lists_screened` reports which of OFSI/OFAC/EU/UN were screened;
        `lists_unavailable` names those that were not. Each list is refreshed on
        its own: if a list's latest refresh failed, its previous load is still
        screened and the list is named in `lists_stale`, for up to 7 days after
        its last successful load (after that it is dropped and reported
        unavailable). `list_as_at` gives each screened list's own load time, and
        `as_at` is the oldest of them. `is_partial` is true whenever a list is
        unavailable or stale, or a screened list is 24 hours old or more. When
        `is_partial` is true an empty `hits` is UNRESOLVED, not clearance. If no
        list has ever loaded the call raises a retryable error rather than
        returning an empty screen.
        """
        snap = await get_index()
        etype = entity_type
        hits = _screen(snap.index, name, etype)
        return SanctionsScreenResult(
            query=name,
            normalized_query=normalize(name),
            entity_type_filter=etype,
            match_count=len(hits),
            lists_screened=snap.lists_screened,
            lists_unavailable=snap.lists_unavailable,
            lists_stale=snap.lists_stale,
            list_as_at=snap.list_as_at,
            is_partial=snap.is_partial,
            as_at=snap.as_at,
            hits=hits,
        )
