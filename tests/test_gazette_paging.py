"""gazette_insolvency reads one feed page and reports the feed total (#83).

The Notice Feed returns at most 100 entries per page plus a top-level
`f:total`. The tool must say how many notices match (`upstream_total`), which
page it read, and whether that page is all of them (`is_partial`), instead of
presenting the first 100 as the full set. Offline: the Gazette client is
replaced with an httpx MockTransport that records every request.
"""

from __future__ import annotations

from collections import deque

import httpx
import pytest
import pytest_asyncio
from fastmcp import Client
from fastmcp.exceptions import ToolError

import gazette
import http_client
from server import mcp

FEED_PATH = "/insolvency/notice/data.json"


@pytest_asyncio.fixture
async def mcp_client():
    async with Client(mcp) as c:
        yield c


@pytest.fixture(autouse=True)
def fresh_gazette_pacing(monkeypatch):
    monkeypatch.setattr(http_client, "_gazette_sent", deque())
    monkeypatch.setattr(http_client, "_gazette_blocked_until", 0.0)


def _entry(n: int, code: str = "2443", date: str = "2024-04-26") -> dict:
    return {
        "f:notice-code": code,
        "id": f"https://www.thegazette.co.uk/id/notice/{4600000 + n}",
        "published": f"{date}T01:05:01Z",
        "title": f"COMPANY {n} LIMITED",
        "content": "<p>Example notice</p>",
    }


class _Feed:
    """Stand-in for the Notice Feed: serves one fixed body, records requests."""

    def __init__(self, body: dict) -> None:
        self.body = body
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.url.path == FEED_PATH
        return httpx.Response(200, json=self.body)


def _use_feed(monkeypatch, body: dict) -> _Feed:
    feed = _Feed(body)

    def make() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=http_client.GAZETTE_BASE, transport=httpx.MockTransport(feed)
        )

    monkeypatch.setattr(gazette, "gazette_client", make)
    return feed


async def _call(mcp_client, **args) -> dict:
    result = await mcp_client.call_tool("gazette_insolvency", {"name": "carillion", **args})
    return result.structured_content


# ---------------------------------------------------------------------------
# upstream_total / is_partial
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_total_above_page_is_partial(monkeypatch, mcp_client):
    feed = _use_feed(monkeypatch, {"f:total": 198, "entry": [_entry(i) for i in range(100)]})

    data = await _call(mcp_client)

    assert data["upstream_total"] == 198
    assert data["is_partial"] is True
    assert data["page"] == 1
    assert len(feed.requests) == 1


@pytest.mark.asyncio
async def test_total_equal_to_entries_is_not_partial(monkeypatch, mcp_client):
    _use_feed(monkeypatch, {"f:total": 3, "entry": [_entry(i) for i in range(3)]})

    data = await _call(mcp_client)

    assert data["upstream_total"] == 3
    assert data["is_partial"] is False
    assert data["total_notices"] == 3


@pytest.mark.asyncio
async def test_missing_total_is_unknown_and_partial(monkeypatch, mcp_client):
    _use_feed(monkeypatch, {"entry": [_entry(i) for i in range(3)]})

    data = await _call(mcp_client)

    assert data["upstream_total"] is None
    assert data["is_partial"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("total", [None, "lots", "-5", -5, 1.5, True, ""])
async def test_null_or_malformed_total_is_none_never_zero(monkeypatch, mcp_client, total):
    _use_feed(monkeypatch, {"f:total": total, "entry": [_entry(1)]})

    data = await _call(mcp_client)

    assert data["upstream_total"] is None
    assert data["is_partial"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "total",
    ["\u00b2", "\u0661\u0662", "9" * 5000],
    ids=["superscript-two", "arabic-indic", "5000-digits"],
)
async def test_unconvertible_digit_total_keeps_notices(monkeypatch, mcp_client, total):
    # "²" and Arabic-Indic digits pass str.isdigit() but are not ASCII; a
    # 5,000-digit string exceeds Python's int-string conversion limit. Each
    # must give an unknown total, not an error that loses the page.
    _use_feed(monkeypatch, {"f:total": total, "entry": [_entry(i) for i in range(3)]})

    data = await _call(mcp_client)

    assert data["upstream_total"] is None
    assert data["is_partial"] is True
    assert len(data["notices"]) == 3


@pytest.mark.asyncio
async def test_numeric_string_total_is_parsed(monkeypatch, mcp_client):
    _use_feed(monkeypatch, {"f:total": "198", "entry": [_entry(i) for i in range(100)]})

    data = await _call(mcp_client)

    assert data["upstream_total"] == 198
    assert data["is_partial"] is True


@pytest.mark.asyncio
async def test_genuine_zero_total_stays_zero(monkeypatch, mcp_client):
    _use_feed(monkeypatch, {"f:total": 0})

    data = await _call(mcp_client)

    assert data["upstream_total"] == 0
    assert data["is_partial"] is False
    assert data["notices"] == []


@pytest.mark.asyncio
async def test_singleton_entry_dict_is_counted(monkeypatch, mcp_client):
    _use_feed(monkeypatch, {"f:total": "1", "entry": _entry(7)})

    data = await _call(mcp_client)

    assert data["upstream_total"] == 1
    assert data["is_partial"] is False
    assert data["total_notices"] == 1


@pytest.mark.asyncio
async def test_is_partial_ignores_notice_type_filter_and_cap(monkeypatch, mcp_client):
    # Three entries, all on this page: the local filter and the cap drop
    # notices, but the page is still the whole feed, so not partial.
    entries = [_entry(1, "2452"), _entry(2, "2443"), _entry(3, "2443")]
    _use_feed(monkeypatch, {"f:total": 3, "entry": entries})

    filtered = await _call(mcp_client, notice_type="2443", max_notices=1)

    assert filtered["total_notices"] == 1
    assert filtered["upstream_total"] == 3
    assert filtered["is_partial"] is False


@pytest.mark.asyncio
async def test_unrecognised_codes_still_count_as_fetched_entries(monkeypatch, mcp_client):
    # Entries the tool drops (non-corporate codes) were still read from the page.
    entries = [_entry(1, "2443"), _entry(2, "2503")]
    _use_feed(monkeypatch, {"f:total": 2, "entry": entries})

    data = await _call(mcp_client)

    assert data["total_notices"] == 1
    assert data["is_partial"] is False


@pytest.mark.asyncio
async def test_severity_sort_and_cap_still_apply(monkeypatch, mcp_client):
    entries = [
        _entry(1, "2431", "2024-05-01"),
        _entry(2, "2452", "2024-01-01"),
        _entry(3, "2410", "2024-03-01"),
    ]
    _use_feed(monkeypatch, {"f:total": 150, "entry": entries})

    data = await _call(mcp_client, max_notices=2)

    assert [n["notice_code"] for n in data["notices"]] == ["2452", "2410"]
    assert data["max_notices_cap"] == 2
    assert data["is_partial"] is True


# ---------------------------------------------------------------------------
# Request parameters
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_page_two_sends_results_page_and_corporate_category(monkeypatch, mcp_client):
    feed = _use_feed(monkeypatch, {"f:total": 198, "entry": [_entry(i) for i in range(98)]})

    data = await _call(mcp_client, page=2)

    [request] = feed.requests
    params = request.url.params
    assert params["results-page"] == "2"
    assert params["categorycode"] == "24"
    assert params["results-page-size"] == "100"
    assert params["text"] == "carillion"
    assert data["page"] == 2
    # 98 entries on page 2 of 198: compared with this call's entries only.
    assert data["is_partial"] is True


@pytest.mark.asyncio
async def test_default_page_is_one_and_sends_corporate_category(monkeypatch, mcp_client):
    feed = _use_feed(monkeypatch, {"f:total": 1, "entry": [_entry(1)]})

    data = await _call(mcp_client, start_date="2024-01-01", end_date="2024-12-31")

    [request] = feed.requests
    params = request.url.params
    assert params["results-page"] == "1"
    assert params["categorycode"] == "24"
    assert params["results-page-size"] == "100"
    assert params["start-publish-date"] == "2024-01-01"
    assert params["end-publish-date"] == "2024-12-31"
    assert data["page"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("page", [0, -1])
async def test_page_below_one_is_rejected_before_any_request(monkeypatch, mcp_client, page):
    feed = _use_feed(monkeypatch, {"f:total": 1, "entry": [_entry(1)]})

    with pytest.raises(ToolError):
        await _call(mcp_client, page=page)

    assert feed.requests == []
