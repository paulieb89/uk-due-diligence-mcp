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


def test_ofsi_parser_yields_nothing_for_a_header_only_file(tmp_path):
    """Justifies the count==0 rule: a truncated OFSI download parses, silently empty.

    The XML parsers raise ET.ParseError on a truncated body, but the CSV one does
    not — it reads a header and stops, which is indistinguishable from a real
    upstream response until you count the records.
    """
    path = tmp_path / "conlist.csv"
    path.write_text("Last Updated,01/01/2026\nName 1,Name 2,Name 3,Name 4,Name 5,Name 6,Listed On\n")

    assert list(sanctions._parse_ofsi(str(path))) == []
