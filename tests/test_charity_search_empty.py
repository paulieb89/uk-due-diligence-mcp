"""Regression test for charity_search's zero-match behavior.

searchCharityName returns HTTP 404 when a query matches zero charities,
rather than 200 with an empty list. A search with no results must be a
successful empty result (charities: []), not a not_found error — this
mirrors how disqualified_search already handles the equivalent case
correctly.
"""

from __future__ import annotations

import httpx
import pytest
from fastmcp.exceptions import ToolError

import charity


def _mock_client_factory(handler):
    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url="https://api.charitycommission.gov.uk/register/api",
            transport=httpx.MockTransport(handler),
        )

    return factory


@pytest.mark.asyncio
async def test_zero_matches_returns_empty_result_not_error(monkeypatch):
    """A 404 from searchCharityName must surface as an empty CharitySearchResult,
    not raise a not_found ToolError."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert "/searchCharityName/" in str(request.url)
        return httpx.Response(404, json={"errors": [{"error": "not-found"}]})

    monkeypatch.setattr(charity, "charity_client", _mock_client_factory(handler))

    result = await charity._search_charities("zzqxplorpqz nonsense charity", 0, 20)

    assert result.total == 0
    assert result.returned == 0
    assert result.charities == []
    assert result.has_more is False


@pytest.mark.asyncio
async def test_real_matches_still_return_normally(monkeypatch):
    """The common case must be unaffected: a 200 with real matches still
    returns a populated result."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "reg_charity_number": 202918,
                    "charity_name": "OXFAM",
                    "reg_status": "R",
                    "date_of_registration": "1965-09-07T00:00:00",
                }
            ],
        )

    monkeypatch.setattr(charity, "charity_client", _mock_client_factory(handler))

    result = await charity._search_charities("Oxfam", 0, 20)

    assert result.total == 1
    assert result.charities[0].charity_name == "OXFAM"
    assert result.charities[0].charity_number == "202918"


@pytest.mark.asyncio
async def test_non_not_found_error_still_propagates(monkeypatch):
    """A genuine failure (e.g. auth) must not be silently swallowed as an
    empty result — only the zero-matches 404 gets that treatment."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"errors": [{"error": "unauthorized"}]})

    monkeypatch.setattr(charity, "charity_client", _mock_client_factory(handler))

    with pytest.raises(ToolError):
        await charity._search_charities("Oxfam", 0, 20)


def _capture_raw_path(monkeypatch) -> list[bytes]:
    seen: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.raw_path)
        return httpx.Response(200, json=[])

    monkeypatch.setattr(charity, "charity_client", _mock_client_factory(handler))
    return seen


_PREFIX = b"/register/api/searchCharityName/"


@pytest.mark.asyncio
async def test_query_with_url_delimiters_is_one_encoded_segment(monkeypatch):
    """'/', '?', '#' and '%' in the query must be searched for, not alter the
    request URL."""
    seen = _capture_raw_path(monkeypatch)
    query = "a/b?c=1#d%e"

    result = await charity._search_charities(query, 0, 20)

    assert seen == [_PREFIX + b"a%2Fb%3Fc%3D1%23d%25e"]
    assert result.query == query
    assert result.total == 0


@pytest.mark.asyncio
async def test_no_query_string_or_fragment_introduced(monkeypatch):
    urls: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(request.url)
        return httpx.Response(200, json=[])

    monkeypatch.setattr(charity, "charity_client", _mock_client_factory(handler))

    await charity._search_charities("x?y=1#frag", 0, 20)

    assert urls[0].query == b""
    assert urls[0].fragment == ""
    assert urls[0].raw_path == _PREFIX + b"x%3Fy%3D1%23frag"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("Oxfam", b"Oxfam"),
        ("British Red Cross", b"British%20Red%20Cross"),
        ("Café Ünïcode", b"Caf%C3%A9%20%C3%9Cn%C3%AFcode"),
        ("%2F", b"%252F"),
        ("..", b"%2E%2E"),
        (".", b"%2E"),
    ],
)
async def test_query_encoding_cases(monkeypatch, query, expected):
    seen = _capture_raw_path(monkeypatch)

    result = await charity._search_charities(query, 0, 20)

    assert seen == [_PREFIX + expected]
    assert result.query == query
