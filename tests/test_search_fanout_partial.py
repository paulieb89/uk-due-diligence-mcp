"""`search` must not report dead registers as a clean sweep.

`search` fans out across four registers with `asyncio.gather(return_exceptions=True)`
and used to discard every failure with a bare `continue`. With all four down it
returned `{"ids": []}`, is_error=False — byte-identical to a genuine "nothing found
in any UK register". This feeds the ChatGPT deep-research search/fetch contract, so
it is often the only search an agent runs.

This is the same defect class `b3eb9ce` closed in disqualified.py and gazette.py
(there called "a compliance false-negative"). That commit touched this very line but
only to widen `Exception` -> `BaseException` so a CancelledError could not escape
into `ids`; the swallow itself was never the subject.

`_REGISTERS` holds direct function references built at import, so patching
`search_fetch._gazette_ids` alone has no effect on the current code and would pass
for the wrong reason. `_use_registers` patches the table and the helpers together,
which also keeps these tests runnable against a tree that predates the table.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
import pytest_asyncio
from fastmcp import Client
from fastmcp.exceptions import ToolError

import charity
import search_fetch
from mcpfleet_obs import parse_error_payload, raise_tool_error
from server import mcp

_LABELS = ("company", "charity", "disqualification", "notice")


@pytest_asyncio.fixture
async def mcp_client():
    async with Client(mcp) as c:
        yield c


_HELPERS = ("_company_ids", "_charity_ids", "_disqualified_ids", "_gazette_ids")


def _use_registers(monkeypatch, company, charity_, disqualification, notice):
    """Point every register at a stand-in, preserving the real label order.

    Patches both the _REGISTERS table and the underlying _*_ids globals it was
    built from. The table alone is enough for the current code, but the helpers
    keep these tests runnable — and failing on their assertions rather than
    erroring in setup — against a tree that predates the table.
    """
    fns = (company, charity_, disqualification, notice)
    monkeypatch.setattr(
        search_fetch, "_REGISTERS", tuple(zip(_LABELS, fns, strict=True)), raising=False
    )
    for name, fn in zip(_HELPERS, fns, strict=True):
        monkeypatch.setattr(search_fetch, name, fn)


async def _none(_query):
    return []


async def _one_company(_query):
    return ["company:12345"]


async def _network_down(_query):
    raise httpx.ConnectError("register unreachable")


async def _unset_api_key(_query):
    raise_tool_error(
        "configuration",
        is_retryable=False,
        attempted="search",
        description="CH_API_KEY is not set",
    )


def _mock_client_factory(handler):
    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url="https://api.company-information.service.gov.uk",
            transport=httpx.MockTransport(handler),
        )

    return factory


@pytest.mark.asyncio
async def test_all_registers_down_raises_instead_of_reporting_empty(monkeypatch, mcp_client):
    """Zero registers answering is an upstream failure, not a zero-result search.

    Pre-fix this returned {"ids": []} with is_error=False, indistinguishable from
    a real empty result.
    """
    _use_registers(monkeypatch, _network_down, _network_down, _network_down, _network_down)

    with pytest.raises(ToolError) as exc_info:
        await mcp_client.call_tool("search", {"query": "CARILLION PLC"})

    payload = parse_error_payload(str(exc_info.value))
    assert payload is not None, f"error message did not parse as a FleetErrorPayload: {exc_info.value}"
    assert payload.error_category == "transient"
    assert payload.is_retryable is True


@pytest.mark.asyncio
async def test_all_registers_failing_on_config_keeps_the_configuration_category(
    monkeypatch, mcp_client
):
    """A missing API key must not be reported as retryable.

    The commonest way all four fail at once is an unset CH_API_KEY, which _get_env
    reports as configuration/not-retryable. Flattening that to transient would tell
    the caller to retry something retrying can never fix — the same class of false
    signal this whole change removes.
    """
    _use_registers(monkeypatch, _unset_api_key, _unset_api_key, _unset_api_key, _unset_api_key)

    with pytest.raises(ToolError) as exc_info:
        await mcp_client.call_tool("search", {"query": "CARILLION PLC"})

    payload = parse_error_payload(str(exc_info.value))
    assert payload is not None, f"error message did not parse as a FleetErrorPayload: {exc_info.value}"
    assert payload.error_category == "configuration"
    assert payload.is_retryable is False


@pytest.mark.asyncio
async def test_one_register_down_is_reported_as_partial(monkeypatch, mcp_client):
    """A live register's results still return, but the gap is named."""
    _use_registers(monkeypatch, _one_company, _none, _none, _network_down)

    result = await mcp_client.call_tool("search", {"query": "CARILLION PLC"})

    data = result.structured_content
    assert data["ids"] == ["company:12345"]
    assert data["registers_searched"] == ["company", "charity", "disqualification"]
    assert data["registers_unavailable"] == ["notice"]
    assert data["is_partial"] is True


@pytest.mark.asyncio
async def test_charity_zero_matches_counts_as_searched_not_failed(monkeypatch, mcp_client):
    """Charity 404s on zero matches — that is an empty result, not a dead register.

    _charity_ids used to call the API directly and never got charity.py's
    404 -> empty mapping. The resulting ToolError was invisible while failures were
    silently swallowed; the moment is_partial existed it would have fired on every
    query matching no charity, making the new signal noise from day one.
    """
    def ch_handler(request: httpx.Request) -> httpx.Response:
        if "/search/companies" in request.url.path:
            return httpx.Response(200, json={"items": []})
        if "/search/disqualified-officers" in request.url.path:
            return httpx.Response(200, json={"items": []})
        raise AssertionError(f"unexpected request: {request.url}")

    def charity_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"errors": [{"error": "not-found"}]})

    def gazette_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    monkeypatch.setattr(search_fetch, "companies_house_client", _mock_client_factory(ch_handler))
    monkeypatch.setattr(charity, "charity_client", _mock_client_factory(charity_handler))
    monkeypatch.setattr(search_fetch, "gazette_client", _mock_client_factory(gazette_handler))

    result = await mcp_client.call_tool("search", {"query": "NOSUCHCHARITY LTD"})

    data = result.structured_content
    assert data["ids"] == []
    assert data["registers_unavailable"] == []
    assert data["is_partial"] is False, "a genuine zero-match charity search is not a failure"
    assert data["registers_searched"] == list(_LABELS)


@pytest.mark.asyncio
async def test_cancellation_propagates_and_never_enters_ids(monkeypatch):
    """A CancelledError is a teardown, not a dead register.

    Treating it as a failure would let a torn-down request trip the all-four-dead
    branch and emit a fabricated transient error. Re-raising also keeps it out of
    the `for id_ in result` loop, which is the non-iterable crash b3eb9ce's
    BaseException widening fixed.

    Driven against the tool function rather than through Client(mcp): propagating
    a CancelledError through the in-memory transport tears down the session.
    """
    async def cancelled(_query):
        raise asyncio.CancelledError()

    _use_registers(monkeypatch, _one_company, _none, _none, cancelled)

    search_tool = await mcp.get_tool("search")
    with pytest.raises(asyncio.CancelledError):
        await search_tool.run({"query": "CARILLION PLC"})


@pytest.mark.asyncio
async def test_healthy_search_reports_every_register_and_keeps_ids_first(
    monkeypatch, mcp_client
):
    """The additive keys must survive the tool boundary, with `ids` still primary.

    tests/live/run_matrix.py does find_first(payload, "ids"), and uk-business-mcp
    proxies this tool, so `ids` staying top-level is a contract, not a preference.
    """
    _use_registers(monkeypatch, _one_company, _none, _none, _none)

    result = await mcp_client.call_tool("search", {"query": "CARILLION PLC"})

    data = result.structured_content
    assert list(data)[0] == "ids"
    assert data["ids"] == ["company:12345"]
    assert data["registers_searched"] == list(_LABELS)
    assert data["registers_unavailable"] == []
    assert data["is_partial"] is False
