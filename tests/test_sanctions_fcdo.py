"""FCDO UK Sanctions List behind the `OFSI (UK)` label: parser and failure-path tests.

Offline tests read tests/fixtures/fcdo/uk_sanctions_sample.xml (synthetic names,
official element structure). The real-file test reads FCDO_UK_SANCTIONS_XML and is
skipped unless it points at a downloaded copy of the official XML.
"""

from __future__ import annotations

import asyncio
import os
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import pytest_asyncio
from fastmcp import Client

import sanctions
from server import mcp

FIXTURE = Path(__file__).parent / "fixtures" / "fcdo" / "uk_sanctions_sample.xml"
REAL_XML = os.environ.get("FCDO_UK_SANCTIONS_XML")


def _fixture_parser(_path):
    return sanctions._parse_fcdo(str(FIXTURE))


def _fixture_records():
    return list(sanctions._parse_fcdo(str(FIXTURE)))


@pytest.fixture(autouse=True)
def reset_index(monkeypatch):
    monkeypatch.setattr(sanctions, "_INDEX", None)
    monkeypatch.setattr(sanctions, "_AS_AT", None)
    monkeypatch.setattr(sanctions, "_LOADED_MONO", None)
    monkeypatch.setattr(sanctions, "_LISTS_OK", [])
    monkeypatch.setattr(sanctions, "_FAILED_MONO", None)
    monkeypatch.setattr(sanctions, "_LOCK", asyncio.Lock())


@pytest_asyncio.fixture
async def mcp_client():
    async with Client(mcp) as c:
        yield c


def _use_fixture_source(monkeypatch):
    monkeypatch.setattr(sanctions, "_SOURCES", [("OFSI (UK)", "u1", _fixture_parser)])
    monkeypatch.setattr(sanctions, "_EXPECTED_LISTS", ["OFSI (UK)"])


async def _download_ok(_url):
    return "/nonexistent/sanctions"


def test_fixture_emits_one_record_per_distinct_spelling():
    recs = _fixture_records()
    assert len(recs) == 10
    assert {r.reference for r in recs} == {"SYN0001", "SYN0002", "SYN0003", "SYN0004"}


def test_entity_person_and_ship_map_to_entity_person_and_none():
    types: dict[str, set] = {}
    for r in _fixture_records():
        types.setdefault(r.reference, set()).add(r.entity_type)
    assert types == {
        "SYN0001": {"entity"},
        "SYN0002": {"person"},
        "SYN0003": {None},
        "SYN0004": {"person"},
    }


def test_individual_name_parts_are_joined_in_order():
    names = {r.matched_name for r in _fixture_records() if r.reference == "SYN0004"}
    assert names == {"JANE SAMPLE DOE"}


def test_alias_flag_follows_name_type_not_the_exact_label():
    flags = {r.matched_name: r.is_alias for r in _fixture_records() if r.reference == "SYN0002"}
    assert flags == {"IVAN EXAMPLE": False, "IVAN EXAMPLEV": True, "I. EXAMPLE": True}


def test_identical_spelling_is_emitted_once_and_case_variants_are_kept():
    names = [r.matched_name for r in _fixture_records() if r.reference == "SYN0001"]
    assert names == ["ACME TRADING LTD", "ACME TRADING LIMITED", "Acme Trading Ltd"]


def test_listing_fields_come_from_the_designation():
    rec = next(r for r in _fixture_records() if r.reference == "SYN0001")
    assert rec.source == "OFSI (UK)"
    assert rec.regime == "Synthetic Regime (Sanctions) Regulations 2020"
    assert rec.listed_on == "2012-06-29"


def _fixture_index() -> dict[str, list]:
    index: dict[str, list] = {}
    for r in _fixture_records():
        index.setdefault(sanctions.normalize(r.matched_name), []).append(r)
    return index


def test_case_variant_screens_to_each_listed_spelling_without_repeats():
    """Each distinct listed spelling is one entry; hit.matched_name stays the listed text."""
    hits = sanctions._screen(_fixture_index(), "Acme Trading Ltd", None)
    assert [(h.reference, h.matched_name, h.is_alias) for h in hits] == [
        ("SYN0001", "ACME TRADING LTD", False),
        ("SYN0001", "Acme Trading Ltd", True),
    ]


def test_alias_screens_to_one_hit_flagged_as_alias():
    hits = sanctions._screen(_fixture_index(), "IVAN EXAMPLEV", None)
    assert [(h.reference, h.matched_name, h.is_alias) for h in hits] == [
        ("SYN0002", "IVAN EXAMPLEV", True)
    ]


def test_ship_entries_are_not_excluded_by_the_entity_type_filter():
    """Documents a limitation rather than endorsing it.

    A ship has no person/entity type, so its record carries entity_type=None and
    `_screen` keeps undeclared records under both filters. A person-only or
    entity-only screen therefore still returns ships. Changing that needs a
    screening-model decision, which Level 1 deliberately does not make.
    """
    index: dict[str, list] = {}
    for r in _fixture_records():
        index.setdefault(sanctions.normalize(r.matched_name), []).append(r)
    for etype in ("person", "entity"):
        hits = sanctions._screen(index, "PETREL TEST 8", etype)
        assert [h.reference for h in hits] == ["SYN0003"]


def test_document_without_designations_yields_nothing(tmp_path):
    empty = tmp_path / "empty.xml"
    empty.write_text('<?xml version="1.0" encoding="utf-8"?><Designations/>', encoding="utf-8")
    assert list(sanctions._parse_fcdo(str(empty))) == []


def test_truncated_download_raises_instead_of_parsing_short(tmp_path):
    text = FIXTURE.read_text(encoding="utf-8")
    cut = tmp_path / "truncated.xml"
    cut.write_text(text[: len(text) // 2], encoding="utf-8")
    with pytest.raises(ET.ParseError):
        list(sanctions._parse_fcdo(str(cut)))


@pytest.mark.asyncio
async def test_failed_list_contributes_no_partial_records(monkeypatch):
    """A list that fails part-way must not leave its earlier records in the index."""

    def parse_then_fail(_path):
        yield sanctions.Record("OFAC (US)", "PARTIAL NAME", False, "entity", None, "X1", None)
        raise ET.ParseError("truncated")

    monkeypatch.setattr(
        sanctions,
        "_SOURCES",
        [("OFAC (US)", "u1", parse_then_fail), ("OFSI (UK)", "u2", _fixture_parser)],
    )
    monkeypatch.setattr(sanctions, "_download_to_temp", _download_ok)

    index, ok = await sanctions._build_index()

    assert ok == ["OFSI (UK)"]
    assert all(r.source != "OFAC (US)" for recs in index.values() for r in recs)
    assert index[sanctions.normalize("IVAN EXAMPLE")]


@pytest.mark.asyncio
async def test_failed_refresh_serves_last_index_flagged_partial_with_old_as_at(monkeypatch, mcp_client):
    """Whole-refresh failure keeps the previous index but must not read as complete or current."""
    _use_fixture_source(monkeypatch)
    monkeypatch.setattr(sanctions, "_download_to_temp", _download_ok)
    await sanctions.get_index()
    first_as_at = sanctions._AS_AT

    monkeypatch.setattr(sanctions, "_LOADED_MONO", time.monotonic() - sanctions.TTL_SECONDS - 1)

    async def download_fails(_url):
        raise RuntimeError("upstream unreachable")

    monkeypatch.setattr(sanctions, "_download_to_temp", download_fails)

    result = await mcp_client.call_tool("sanctions_screen", {"name": "IVAN EXAMPLE"})
    data = result.structured_content

    assert data["match_count"] == 1
    assert data["as_at"] == first_as_at
    assert data["is_partial"] is True


@pytest.mark.asyncio
async def test_truncated_uk_xml_is_not_reported_as_screened(monkeypatch, tmp_path):
    """Malformed or incomplete XML must leave the UK list unscreened, never partly indexed."""
    text = FIXTURE.read_text(encoding="utf-8")
    cut = tmp_path / "truncated.xml"
    cut.write_text(text[: len(text) * 2 // 3], encoding="utf-8")

    async def download_cut(_url):
        return str(cut)

    monkeypatch.setattr(sanctions, "_SOURCES", [("OFSI (UK)", "u1", sanctions._parse_fcdo)])
    monkeypatch.setattr(sanctions, "_download_to_temp", download_cut)

    index, ok = await sanctions._build_index()

    assert ok == []
    assert not any(r.source == "OFSI (UK)" for recs in index.values() for r in recs)


@pytest.mark.asyncio
async def test_partial_refresh_reports_the_failed_uk_list_unavailable(monkeypatch, mcp_client):
    """After a good load, a refresh where only the UK list fails must not screen it as loaded."""
    uk_fails = [False]

    def uk_parser(_path):
        if uk_fails[0]:
            raise sanctions.ET.ParseError("truncated")
        return _fixture_records()

    monkeypatch.setattr(
        sanctions,
        "_SOURCES",
        [("OFAC (US)", "u0", lambda _p: iter([sanctions.Record("OFAC (US)", "IVAN EXAMPLE", False, "person", None, "O1", None)])),
         ("OFSI (UK)", "u1", uk_parser)],
    )
    monkeypatch.setattr(sanctions, "_EXPECTED_LISTS", ["OFAC (US)", "OFSI (UK)"])
    monkeypatch.setattr(sanctions, "_download_to_temp", _download_ok)
    await sanctions.get_index()

    uk_fails[0] = True
    monkeypatch.setattr(sanctions, "_LOADED_MONO", time.monotonic() - sanctions.TTL_SECONDS - 1)

    result = await mcp_client.call_tool("sanctions_screen", {"name": "IVAN EXAMPLE"})
    data = result.structured_content

    assert data["lists_screened"] == ["OFAC (US)"]
    assert data["lists_unavailable"] == ["OFSI (UK)"]
    assert data["is_partial"] is True
    assert {h["list_source"] for h in data["hits"]} == {"OFAC (US)"}


@pytest.mark.skipif(not REAL_XML, reason="set FCDO_UK_SANCTIONS_XML to a downloaded copy of the FCDO XML")
def test_real_fcdo_file_loads_every_designation_and_name_without_loss():
    expected_types: dict[str, str] = {}
    expected_names: set[tuple[str, str]] = set()
    for _, el in ET.iterparse(str(REAL_XML), events=("end",)):
        if el.tag != "Designation":
            continue
        ref = el.findtext("UniqueID")
        expected_types[ref] = el.findtext("IndividualEntityShip")
        for n in el.findall("Names/Name"):
            joined = " ".join((n.findtext(f"Name{k}") or "").strip() for k in range(1, 7))
            spelling = " ".join(joined.split())
            if spelling:
                expected_names.add((ref, spelling))
        el.clear()

    recs = list(sanctions._parse_fcdo(str(REAL_XML)))

    assert {r.reference for r in recs} == set(expected_types)
    assert {(r.reference, r.matched_name) for r in recs} == expected_names
    assert len(recs) == len(expected_names)
    assert {r.reference for r in recs if not r.is_alias} == set(expected_types)

    per_type = {
        "Individual": {r.reference for r in recs if r.entity_type == "person"},
        "Entity": {r.reference for r in recs if r.entity_type == "entity"},
        "Ship": {r.reference for r in recs if r.entity_type is None},
    }
    for kind, refs in per_type.items():
        assert refs == {ref for ref, k in expected_types.items() if k == kind}, kind
