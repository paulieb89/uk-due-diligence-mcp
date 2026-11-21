"""Per-list freshness: a failed list keeps its last load (stale), and failed lists cool down.

Regressions for #26 and #28:

  #26. A refresh where one list failed replaced the whole index, so the failed list's
       previous designations vanished for up to 24 h. A failed list now keeps its last
       verified records, reported in `lists_stale`, for up to MAX_STALE_SECONDS.
  #28. Once a stale index existed, the failure cooldown was never read, so every call
       re-downloaded every list inside _LOCK during an outage.

All offline. The clock is driven through `sanctions._clock` / `sanctions._utc_now`,
never `time.monotonic` itself (the event loop uses that).
"""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from fastmcp import Client

import sanctions
from server import mcp

LABELS = ["OFAC (US)", "EU", "UN", "OFSI (UK)"]
URLS = {label: f"u-{i}" for i, label in enumerate(LABELS)}
LABEL_BY_URL = {url: label for label, url in URLS.items()}
NAME = "ACME TRADING LTD"
EPOCH = datetime(2026, 11, 1, tzinfo=timezone.utc)

TTL = sanctions.TTL_SECONDS
COOLDOWN = sanctions.FAILURE_COOLDOWN_SECONDS
MAX_STALE = sanctions.MAX_STALE_SECONDS


class Clock:
    def __init__(self) -> None:
        self.t = 1_000.0

    def iso(self, t: float | None = None) -> str:
        return (EPOCH + timedelta(seconds=self.t if t is None else t)).isoformat()


class Upstream:
    """Mocked downloads: per-list failure switches and a per-list download counter."""

    def __init__(self) -> None:
        self.failing: set[str] = set()
        self.calls: Counter[str] = Counter()
        self.extra: dict[str, list[str]] = {}

    async def download(self, url: str) -> str:
        label = LABEL_BY_URL[url]
        self.calls[label] += 1
        await asyncio.sleep(0)  # yield, so concurrent callers really interleave
        if label in self.failing:
            raise RuntimeError("upstream unreachable")
        return "/nonexistent/sanctions"

    def parser(self, label: str):
        def parse(_path):
            yield sanctions.Record(label, NAME, False, "entity", "Synthetic", f"{label}-1", None)
            for name in self.extra.get(label, []):
                yield sanctions.Record(label, name, False, "entity", "Synthetic", f"{label}-2", None)

        return parse


@pytest.fixture
def clock(monkeypatch) -> Clock:
    c = Clock()
    monkeypatch.setattr(sanctions, "_clock", lambda: c.t)
    monkeypatch.setattr(sanctions, "_utc_now", lambda: EPOCH + timedelta(seconds=c.t))
    return c


@pytest.fixture
def upstream(monkeypatch) -> Upstream:
    up = Upstream()
    monkeypatch.setattr(sanctions, "_INDEX", None)
    monkeypatch.setattr(sanctions, "_LIST_AS_AT", {})
    monkeypatch.setattr(sanctions, "_LIST_LOADED_MONO", {})
    monkeypatch.setattr(sanctions, "_LIST_FAILED_MONO", {})
    monkeypatch.setattr(sanctions, "_LOCK", asyncio.Lock())
    monkeypatch.setattr(
        sanctions, "_SOURCES", [(label, URLS[label], up.parser(label)) for label in LABELS]
    )
    monkeypatch.setattr(sanctions, "_EXPECTED_LISTS", list(LABELS))
    monkeypatch.setattr(sanctions, "_download_to_temp", up.download)
    return up


@pytest_asyncio.fixture
async def mcp_client():
    async with Client(mcp) as c:
        yield c


async def _screen(client, name: str = NAME) -> dict:
    result = await client.call_tool("sanctions_screen", {"name": name})
    return result.structured_content


@pytest.mark.asyncio
async def test_failed_list_keeps_its_previous_records_marked_stale(clock, upstream, mcp_client):
    """UN fails a refresh while the other three load: UN's last load still screens."""
    t0 = clock.t
    first = await _screen(mcp_client)
    assert first["lists_screened"] == LABELS
    assert first["lists_stale"] == []
    assert first["is_partial"] is False
    assert first["list_as_at"] == {label: clock.iso(t0) for label in LABELS}

    clock.t += TTL + 1
    upstream.failing = {"UN"}
    data = await _screen(mcp_client)

    assert data["match_count"] == 4
    assert {h["list_source"] for h in data["hits"]} == set(LABELS)
    assert data["lists_screened"] == LABELS
    assert data["lists_stale"] == ["UN"]
    assert data["lists_unavailable"] == []
    assert data["is_partial"] is True
    assert data["list_as_at"]["UN"] == clock.iso(t0), "a failed list keeps its earlier load time"
    for label in ("OFAC (US)", "EU", "OFSI (UK)"):
        assert data["list_as_at"][label] == clock.iso()
    assert data["as_at"] == clock.iso(t0)


@pytest.mark.asyncio
async def test_refresh_failing_part_way_keeps_the_previous_load_not_the_partial_one(
    clock, upstream, monkeypatch
):
    """A list whose parser raises mid-file keeps its last load; the new partial records are discarded."""
    await sanctions.get_index()

    def parse_then_fail(_path):
        yield sanctions.Record("UN", "PARTIAL NEW NAME", False, "entity", None, "UN-9", None)
        raise sanctions.ET.ParseError("truncated")

    monkeypatch.setattr(
        sanctions,
        "_SOURCES",
        [(label, URLS[label], parse_then_fail if label == "UN" else upstream.parser(label))
         for label in LABELS],
    )
    clock.t += TTL + 1
    snap = await sanctions.get_index()

    assert snap.lists_stale == ["UN"]
    assert sanctions.normalize("PARTIAL NEW NAME") not in snap.index
    assert "UN" in {r.source for r in snap.index[sanctions.normalize(NAME)]}


@pytest.mark.asyncio
async def test_list_older_than_max_stale_age_is_dropped_and_reported_unavailable(
    clock, upstream, mcp_client
):
    t0 = clock.t
    await _screen(mcp_client)
    upstream.failing = {"UN"}

    # Exactly at the max age the list is still served (stale).
    clock.t = t0 + MAX_STALE
    data = await _screen(mcp_client)
    assert data["lists_stale"] == ["UN"]
    assert data["list_as_at"]["UN"] == clock.iso(t0)

    # One second later it is dropped, even though UN is cooling down and not retried:
    # the age limit is enforced on the cache-hit path too.
    clock.t += 1
    calls_before = upstream.calls["UN"]
    data = await _screen(mcp_client)
    assert upstream.calls["UN"] == calls_before
    assert data["lists_unavailable"] == ["UN"]
    assert data["lists_stale"] == []
    assert data["lists_screened"] == ["OFAC (US)", "EU", "OFSI (UK)"]
    assert "UN" not in data["list_as_at"]
    assert "UN" not in {h["list_source"] for h in data["hits"]}
    assert data["is_partial"] is True
    assert all(r.source != "UN" for recs in sanctions._INDEX.values() for r in recs)


@pytest.mark.asyncio
async def test_list_expired_during_the_failed_refresh_itself_is_unavailable(clock, upstream, mcp_client):
    t0 = clock.t
    await _screen(mcp_client)
    upstream.failing = {"UN"}
    clock.t = t0 + MAX_STALE + 1

    data = await _screen(mcp_client)
    assert data["lists_unavailable"] == ["UN"]
    assert data["lists_stale"] == []


@pytest.mark.asyncio
async def test_outage_with_stale_index_downloads_each_list_once_per_cooldown(clock, upstream, mcp_client):
    """#28: two calls within the cooldown make one download attempt per list, not two."""
    await _screen(mcp_client)
    assert upstream.calls == Counter({label: 1 for label in LABELS})

    clock.t += TTL + 1
    upstream.failing = set(LABELS)
    first = await _screen(mcp_client)
    assert upstream.calls == Counter({label: 2 for label in LABELS})
    assert first["lists_stale"] == LABELS
    assert first["match_count"] == 4

    clock.t += 60
    second = await _screen(mcp_client)
    assert upstream.calls == Counter({label: 2 for label in LABELS}), "no retry inside the cooldown"
    assert second["lists_stale"] == LABELS
    assert second["is_partial"] is True


@pytest.mark.asyncio
async def test_failed_list_is_retried_after_the_cooldown_and_leaves_stale(clock, upstream, mcp_client):
    t0 = clock.t
    await _screen(mcp_client)
    clock.t += TTL + 1
    t1 = clock.t
    upstream.failing = {"UN"}
    await _screen(mcp_client)
    assert upstream.calls == Counter({label: 2 for label in LABELS})

    # Still inside the cooldown: nothing is downloaded.
    clock.t = t1 + COOLDOWN - 1
    data = await _screen(mcp_client)
    assert upstream.calls["UN"] == 2
    assert data["lists_stale"] == ["UN"]
    assert data["list_as_at"]["UN"] == clock.iso(t0)

    # Cooldown over and UN back: only UN is fetched; fresh lists are not re-downloaded.
    clock.t = t1 + COOLDOWN
    upstream.failing = set()
    data = await _screen(mcp_client)
    assert upstream.calls == Counter({"OFAC (US)": 2, "EU": 2, "UN": 3, "OFSI (UK)": 2})
    assert data["lists_stale"] == []
    assert data["list_as_at"]["UN"] == clock.iso()
    assert data["is_partial"] is False


@pytest.mark.asyncio
async def test_retry_that_fails_again_keeps_the_original_load_time(clock, upstream, mcp_client):
    t0 = clock.t
    await _screen(mcp_client)
    upstream.failing = {"UN"}
    clock.t += TTL + 1
    await _screen(mcp_client)
    clock.t += COOLDOWN
    data = await _screen(mcp_client)
    assert upstream.calls["UN"] == 3
    assert data["lists_stale"] == ["UN"]
    assert data["list_as_at"]["UN"] == clock.iso(t0)


@pytest.mark.asyncio
async def test_as_at_is_the_oldest_list_load_time(clock, upstream, mcp_client):
    t0 = clock.t
    upstream.failing = {"UN"}
    data = await _screen(mcp_client)
    assert data["lists_unavailable"] == ["UN"]
    assert data["as_at"] == clock.iso(t0)

    clock.t += COOLDOWN
    upstream.failing = set()
    data = await _screen(mcp_client)
    assert data["list_as_at"]["UN"] == clock.iso()
    assert data["list_as_at"]["OFAC (US)"] == clock.iso(t0)
    assert data["as_at"] == clock.iso(t0), "as_at must be the oldest, not the latest, load"
    assert data["lists_unavailable"] == []
    assert data["is_partial"] is False


@pytest.mark.asyncio
async def test_screened_list_past_the_ttl_is_partial_even_when_not_yet_retried(clock, upstream, monkeypatch):
    await sanctions.get_index()
    clock.t += TTL
    upstream.failing = set(LABELS)
    snap = await sanctions.get_index()
    assert snap.lists_stale == LABELS
    assert snap.is_partial is True


@pytest.mark.asyncio
async def test_every_list_expired_after_a_prior_load_serves_an_empty_partial_screen(
    clock, upstream, mcp_client
):
    """Unlike a cold start, a server that once loaded lists reports them unavailable, not an error."""
    await _screen(mcp_client)
    upstream.failing = set(LABELS)
    clock.t += MAX_STALE + 1

    data = await _screen(mcp_client)
    assert data["lists_screened"] == []
    assert data["lists_unavailable"] == LABELS
    assert data["lists_stale"] == []
    assert data["list_as_at"] == {}
    assert data["as_at"] is None
    assert data["match_count"] == 0
    assert data["is_partial"] is True


@pytest.mark.asyncio
async def test_concurrent_callers_share_one_refresh(clock, upstream):
    """The lock recheck stops a second waiter from repeating the refresh."""
    await sanctions.get_index()
    clock.t += TTL + 1
    upstream.failing = {"UN"}

    a, b = await asyncio.gather(sanctions.get_index(), sanctions.get_index())

    assert upstream.calls == Counter({label: 2 for label in LABELS})
    assert a.lists_stale == b.lists_stale == ["UN"]


@pytest.mark.asyncio
async def test_refresh_does_not_mutate_an_index_a_caller_already_holds(clock, upstream):
    before = await sanctions.get_index()
    held = before.index
    held_keys = {k: list(v) for k, v in held.items()}

    clock.t += TTL + 1
    upstream.failing = {"UN"}
    upstream.extra = {"EU": ["NEW EU NAME"]}
    after = await sanctions.get_index()

    assert after.index is not held
    assert {k: list(v) for k, v in held.items()} == held_keys
    assert sanctions.normalize("NEW EU NAME") in after.index
    assert sanctions.normalize("NEW EU NAME") not in held
    # Hits keep list order whichever list refreshed last.
    assert [r.source for r in after.index[sanctions.normalize(NAME)]] == LABELS


@pytest.mark.asyncio
async def test_new_fields_are_always_serialised(clock, upstream, mcp_client):
    data = await _screen(mcp_client, "NOBODY AT ALL")
    assert data["lists_stale"] == []
    assert set(data["list_as_at"]) == set(LABELS)
    assert data["match_count"] == 0
