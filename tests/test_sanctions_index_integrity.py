"""Sanctions index integrity — a screen that loaded nothing must not read as clean.

Covers the two ways `sanctions_screen` used to report an unverified check as a
confident negative, both reachable through an ordinary upstream outage:

  A. `get_index` guarded cold start with `if not ok and _INDEX is not None`, so a
     first-ever total failure cached `{}` and stamped `_LOADED_MONO`. The empty
     index then served match_count=0 / is_error=False for the full 24h TTL, *after
     upstream recovered*. `warm_cache` is a startup handler, so a deploy during an
     outage poisoned the cache with no user call involved.
  B. `_build_index` appended a list's label to `ok` unconditionally, so a list that
     downloaded and parsed to zero records was reported in `lists_screened` as
     successfully screened.

All tests are offline. `_SOURCES` holds direct function references built at import,
so these patch `sanctions._SOURCES` (and the `_EXPECTED_LISTS` derived from it) —
patching `sanctions._parse_ofac` has no effect and would pass for the wrong reason.
"""

from __future__ import annotations

import asyncio
import time

import pytest
import pytest_asyncio
from fastmcp import Client
from fastmcp.exceptions import ToolError

import sanctions
from mcpfleet_obs import parse_error_payload
from server import mcp


@pytest.fixture(autouse=True)
def reset_index(monkeypatch):
    """Clear the module-level cache between tests.

    `get_index` short-circuits on a populated `_INDEX`, so without this the first
    test to build an index leaks into every later one. `_LOCK` is reset too: an
    uncontended asyncio.Lock survives across event loops but a contended one binds
    to the loop that contended it, and pytest-asyncio gives each test its own —
    a leaked bound lock fails other files that import `server`.
    """
    monkeypatch.setattr(sanctions, "_INDEX", None)
    monkeypatch.setattr(sanctions, "_AS_AT", None)
    monkeypatch.setattr(sanctions, "_LOADED_MONO", None)
    monkeypatch.setattr(sanctions, "_LISTS_OK", [])
    # raising=False so this fixture also runs against a tree without the cooldown,
    # letting these tests fail on their assertions rather than erroring in setup.
    monkeypatch.setattr(sanctions, "_FAILED_MONO", None, raising=False)
    monkeypatch.setattr(sanctions, "_LOCK", asyncio.Lock())


@pytest_asyncio.fixture
async def mcp_client():
    async with Client(mcp) as c:
        yield c


def _record(source: str, name: str = "ACME TRADING LTD"):
    return sanctions.Record(source, name, False, "entity", "Russia", "REF1", "2022-03-01")


def _yields(source: str, name: str = "ACME TRADING LTD"):
    """A parser that produces one record, ignoring the path."""

    def parser(_path):
        yield _record(source, name)

    return parser


def _yields_nothing(_path):
    """A parser that produces no records — the shape a broken download leaves."""
    return iter(())


def _use_sources(monkeypatch, *sources):
    monkeypatch.setattr(sanctions, "_SOURCES", list(sources))
    # raising=False for the same reason as _FAILED_MONO above: these tests must be
    # able to run, and fail on their assertions, against a tree without the fix.
    monkeypatch.setattr(
        sanctions, "_EXPECTED_LISTS", [label for label, _, _ in sources], raising=False
    )


def _download_raising(monkeypatch, flag=None):
    """Patch the downloader to fail, optionally toggled by a one-element list."""

    async def download(_url):
        if flag is None or flag[0]:
            raise RuntimeError("upstream unreachable")
        return "/nonexistent/sanctions"  # unlink is guarded by os.path.exists

    monkeypatch.setattr(sanctions, "_download_to_temp", download)


async def _download_ok(_url):
    return "/nonexistent/sanctions"


@pytest.mark.asyncio
async def test_total_failure_on_cold_start_raises_instead_of_caching_empty(monkeypatch):
    """No list loaded and no previous index -> transient error, nothing cached.

    Pre-fix this returned an empty index and stamped _LOADED_MONO, which is the
    poisoning; assert _INDEX is untouched, not merely that the call failed.
    """
    _use_sources(monkeypatch, ("OFSI (UK)", "u1", _yields("OFSI (UK)")))
    _download_raising(monkeypatch)

    with pytest.raises(ToolError) as exc_info:
        await sanctions.get_index()

    payload = parse_error_payload(str(exc_info.value))
    assert payload is not None, f"error message did not parse as a FleetErrorPayload: {exc_info.value}"
    assert payload.error_category == "transient"
    assert payload.is_retryable is True
    assert sanctions._INDEX is None
    assert sanctions._AS_AT is None
    assert sanctions._LOADED_MONO is None


@pytest.mark.asyncio
async def test_index_recovers_once_upstream_returns(monkeypatch):
    """The regression that actually proves the fix.

    A test asserting only that a cold-start failure raises passes against the
    poisoned cache too, because that code raised nothing — it silently succeeded.
    Only recovery distinguishes them: pre-fix the second call never re-fetched and
    kept serving the empty index for the whole TTL.
    """
    failing = [True]
    _use_sources(monkeypatch, ("OFSI (UK)", "u1", _yields("OFSI (UK)")))
    _download_raising(monkeypatch, failing)

    with pytest.raises(ToolError):
        await sanctions.get_index()

    failing[0] = False
    monkeypatch.setattr(sanctions, "_FAILED_MONO", None, raising=False)  # past the cooldown

    index, as_at, lists_ok = await sanctions.get_index()
    assert lists_ok == ["OFSI (UK)"]
    assert index[sanctions.normalize("ACME TRADING LTD")]
    assert as_at is not None


@pytest.mark.asyncio
async def test_total_failure_with_warm_index_keeps_serving_stale(monkeypatch):
    """A refresh that loads nothing must not discard a good index, or raise.

    Guards the regression the cold-start fix could introduce: the raise belongs
    only to the case where there is nothing to fall back on.
    """
    _use_sources(monkeypatch, ("OFSI (UK)", "u1", _yields("OFSI (UK)")))
    monkeypatch.setattr(sanctions, "_download_to_temp", _download_ok)
    _, first_as_at, _ = await sanctions.get_index()

    # Expire the TTL, then take the lists away.
    monkeypatch.setattr(sanctions, "_LOADED_MONO", time.monotonic() - sanctions.TTL_SECONDS - 1)
    _download_raising(monkeypatch)

    index, as_at, lists_ok = await sanctions.get_index()
    assert lists_ok == ["OFSI (UK)"]
    assert as_at == first_as_at, "stale serve must not restamp as_at"
    assert index[sanctions.normalize("ACME TRADING LTD")]


@pytest.mark.asyncio
async def test_list_parsing_to_zero_is_not_reported_as_screened(monkeypatch, mcp_client):
    """A list that downloads but parses to zero records was not screened.

    Zero is never a legitimate parse of a national sanctions list, so the label is
    withheld — pre-fix it was appended to `ok` unconditionally and surfaced in
    lists_screened as a successful screen.
    """
    _use_sources(
        monkeypatch,
        ("OFAC (US)", "u1", _yields("OFAC (US)")),
        ("OFSI (UK)", "u2", _yields_nothing),
    )
    monkeypatch.setattr(sanctions, "_download_to_temp", _download_ok)

    result = await mcp_client.call_tool("sanctions_screen", {"name": "ACME TRADING LTD"})

    data = result.structured_content
    assert data["lists_screened"] == ["OFAC (US)"]
    assert data["lists_unavailable"] == ["OFSI (UK)"]
    assert data["is_partial"] is True
    assert data["match_count"] == 1, "the healthy list must still screen"


@pytest.mark.asyncio
async def test_every_list_parsing_to_zero_raises_on_cold_start(monkeypatch):
    """Downloads that all succeed but parse to nothing is still a failed build."""
    _use_sources(
        monkeypatch,
        ("OFAC (US)", "u1", _yields_nothing),
        ("OFSI (UK)", "u2", _yields_nothing),
    )
    monkeypatch.setattr(sanctions, "_download_to_temp", _download_ok)

    with pytest.raises(ToolError):
        await sanctions.get_index()
    assert sanctions._INDEX is None


@pytest.mark.asyncio
async def test_healthy_screen_reports_no_partiality(monkeypatch, mcp_client):
    """The new fields must serialise through the tool boundary, not just exist.

    lists_unavailable/is_partial are what a proxied caller branches on —
    uk-business-mcp mounts this server and mount() drops server instructions, so
    the payload is the only channel that survives the hop.
    """
    _use_sources(
        monkeypatch,
        ("OFAC (US)", "u1", _yields("OFAC (US)")),
        ("OFSI (UK)", "u2", _yields("OFSI (UK)")),
    )
    monkeypatch.setattr(sanctions, "_download_to_temp", _download_ok)

    result = await mcp_client.call_tool("sanctions_screen", {"name": "ACME TRADING LTD"})

    data = result.structured_content
    assert data["lists_screened"] == ["OFAC (US)", "OFSI (UK)"]
    assert data["lists_unavailable"] == []
    assert data["is_partial"] is False
    assert data["match_count"] == 2


@pytest.mark.asyncio
async def test_failed_build_is_not_retried_until_the_cooldown_expires(monkeypatch):
    """Without a cooldown every call re-downloads four bulk lists inside _LOCK.

    _download_to_temp bypasses _request_with_retry and sanctions_client's read
    timeout is 120s, so an unbounded retry costs minutes per caller during an
    outage — a fast wrong answer traded for a slow hang.
    """
    attempts = {"n": 0}

    async def download(_url):
        attempts["n"] += 1
        raise RuntimeError("upstream unreachable")

    _use_sources(monkeypatch, ("OFSI (UK)", "u1", _yields("OFSI (UK)")))
    monkeypatch.setattr(sanctions, "_download_to_temp", download)

    with pytest.raises(ToolError):
        await sanctions.get_index()
    assert attempts["n"] == 1

    with pytest.raises(ToolError):
        await sanctions.get_index()
    assert attempts["n"] == 1, "second call inside the cooldown must not re-download"


# ---------------------------------------------------------------------------
# normalize(): underscore is a separator (#29)
# ---------------------------------------------------------------------------


def test_underscore_normalizes_like_a_space():
    assert sanctions.normalize("Jal_Vahini") == sanctions.normalize("Jal Vahini") == "JAL VAHINI"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("A__B", "A B"),
        ("_Leading and trailing_", "LEADING AND TRAILING"),
        ("Société Générale", "SOCIETE GENERALE"),
        ("Ñandú Müller", "NANDU MULLER"),
        ("O'Brien & Co., Ltd.", "O BRIEN CO LTD"),
        ("Al-Qaida / AQ", "AL QAIDA AQ"),
        ("  Acme \t Trading\n Ltd  ", "ACME TRADING LTD"),
        ("Петров 123", "ПЕТРОВ 123"),
        ("PETREL TEST 8", "PETREL TEST 8"),
        ("", ""),
        (None, ""),
        ("___", ""),
        ("_ - _", ""),
    ],
)
def test_normalize_cases(raw, expected):
    assert sanctions.normalize(raw) == expected


def test_underscore_only_query_has_no_hits():
    index = {sanctions.normalize("ACME TRADING LTD"): [_record("OFAC (US)")]}
    assert sanctions._screen(index, "___", None) == []


@pytest.mark.asyncio
async def test_underscore_only_record_is_not_indexed(monkeypatch):
    _use_sources(
        monkeypatch,
        ("OFAC (US)", "u1", lambda _p: iter([_record("OFAC (US)", "___"), _record("OFAC (US)")])),
    )
    monkeypatch.setattr(sanctions, "_download_to_temp", _download_ok)

    index, ok = await sanctions._build_index()

    assert ok == ["OFAC (US)"]
    assert "" not in index
    assert list(index) == ["ACME TRADING LTD"]


# Minimal, structurally valid XML for each real parser. Synthetic names only.

def _ofac_xml(primary: str, alias: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<sdnList xmlns="http://tempuri.org/sdnList.xsd">'
        "<sdnEntry><uid>9001</uid>"
        f"<lastName>{primary}</lastName><sdnType>Entity</sdnType>"
        "<programList><program>SYNTHETIC</program></programList>"
        f"<akaList><aka><uid>9002</uid><type>a.k.a.</type><lastName>{alias}</lastName></aka></akaList>"
        "</sdnEntry></sdnList>"
    )


def _eu_xml(primary: str, alias: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<export xmlns="http://eu.europa.ec/fpi/fsd/export">'
        '<sanctionEntity euReferenceNumber="EU.9001.01" logicalId="9001">'
        '<regulation programme="SYN" publicationDate="2022-03-01"/>'
        '<subjectType code="enterprise"/>'
        f'<nameAlias wholeName="{primary}"/>'
        f'<nameAlias wholeName="{alias}"/>'
        "</sanctionEntity></export>"
    )


def _un_xml(primary: str, alias: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        "<CONSOLIDATED_LIST><INDIVIDUALS/><ENTITIES><ENTITY>"
        "<DATAID>9001</DATAID><FIRST_NAME>" + primary + "</FIRST_NAME>"
        "<UN_LIST_TYPE>Synthetic</UN_LIST_TYPE><REFERENCE_NUMBER>SYe.001</REFERENCE_NUMBER>"
        "<LISTED_ON>2022-03-01</LISTED_ON>"
        "<ENTITY_ALIAS><QUALITY>Good</QUALITY><ALIAS_NAME>" + alias + "</ALIAS_NAME></ENTITY_ALIAS>"
        "</ENTITY></ENTITIES></CONSOLIDATED_LIST>"
    )


def _fcdo_xml(primary: str, alias: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        "<Designations><DateGenerated>27/10/2026</DateGenerated><Designation>"
        "<DateDesignated>01/03/2022</DateDesignated><UniqueID>SYN9001</UniqueID><Names>"
        f"<Name><Name6>{primary}</Name6><NameType>Primary Name</NameType></Name>"
        f"<Name><Name6>{alias}</Name6><NameType>Alias</NameType></Name>"
        "</Names><RegimeName>Synthetic Regime</RegimeName>"
        "<IndividualEntityShip>Entity</IndividualEntityShip>"
        "</Designation></Designations>"
    )


_REAL_PARSERS = {
    "OFSI (UK)": (sanctions._parse_fcdo, _fcdo_xml),
    "OFAC (US)": (sanctions._parse_ofac, _ofac_xml),
    "EU": (sanctions._parse_eu, _eu_xml),
    "UN": (sanctions._parse_un, _un_xml),
}

_UNDERSCORED = "Jal_Vahini Shipping"
_SPACED = "Jal Vahini Shipping"
_OTHER = "UNRELATED HOLDINGS"


@pytest.mark.asyncio
@pytest.mark.parametrize("position", ["primary", "alias"])
@pytest.mark.parametrize(
    ("listed", "query"),
    [(_UNDERSCORED, _SPACED), (_SPACED, _UNDERSCORED)],
    ids=["underscore-record", "space-record"],
)
@pytest.mark.parametrize("source", list(_REAL_PARSERS))
async def test_underscore_and_space_spellings_match_on_every_list(
    monkeypatch, tmp_path, mcp_client, source, listed, query, position
):
    """Both sides go through normalize(), so either spelling finds the other.

    Exercises each real parser end to end through _build_index and the tool, so a
    parser that indexed a raw or differently-normalised key would fail here.
    """
    parser, make_xml = _REAL_PARSERS[source]
    primary, alias = (listed, _OTHER) if position == "primary" else (_OTHER, listed)
    _use_sources(monkeypatch, (source, "u1", parser))
    monkeypatch.setattr(sanctions, "FCDO_MIN_DESIGNATIONS", 1)
    monkeypatch.setattr(sanctions, "_FCDO_LAST_DESIGNATIONS", None)

    async def download(_url):
        # _build_index unlinks the path, so write a fresh file per download.
        path = tmp_path / f"{source.split()[0].lower()}.xml"
        path.write_text(make_xml(primary, alias), encoding="utf-8")
        return str(path)

    monkeypatch.setattr(sanctions, "_download_to_temp", download)

    result = await mcp_client.call_tool("sanctions_screen", {"name": query})
    data = result.structured_content

    assert data["lists_screened"] == [source]
    assert data["normalized_query"] == "JAL VAHINI SHIPPING"
    assert data["match_count"] == 1
    hit = data["hits"][0]
    assert hit["list_source"] == source
    assert hit["matched_name"] == listed, "hit must carry the listed spelling, not the query"
    assert hit["is_alias"] is (position == "alias")
