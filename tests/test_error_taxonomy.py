"""
Structured error taxonomy tests — offline, no live API calls.

Verifies that anticipated failure paths raise fleet-standard structured
ToolErrors (mcpfleet_obs.errors) rather than bare ValueError/RuntimeError,
so the message survives mask_error_details=True verbatim to the client.

Paths exercised:
  - vat_validate with a non-UK VAT number -> validation, before any HTTP call
    (the EU-prefix check happens before the HMRC bearer-token request).
  - company_profile with CH_API_KEY unset -> configuration, before any HTTP
    call (the client factory raises on missing env var before opening a
    connection).
  - company_profile against a mocked 404 upstream -> not_found (exercises
    the full _request_with_retry -> raise_http_tool_error path end to end,
    via httpx.MockTransport — no real network, no respx dependency).
  - _request_with_retry against a persistently-429 mocked upstream ->
    transient + is_retryable=True, exhausting all retries with no
    exception ever raised by httpx (status inspected on the response
    directly) — this is the case that used to fall through with
    last_exc=None.
  - land_title_search with no extractable postcode -> validation, with the
    original message text intact in the payload description.
  - disqualified_search with CH_API_KEY unset -> configuration (B3: the
    bare `except Exception: data = {}` used to swallow this into an empty
    success instead).
  - gazette_insolvency against a mocked persistent 503 -> transient +
    is_retryable=True (B3: the bare `except Exception: pass` used to
    swallow this into an empty notices list instead).
"""

from __future__ import annotations

import httpx
import pytest
import pytest_asyncio
from fastmcp import Client
from fastmcp.exceptions import ToolError

import companies_house
import disqualified
import gazette
import http_client
from mcpfleet_obs import parse_error_payload
from server import mcp


@pytest_asyncio.fixture
async def client():
    async with Client(mcp) as c:
        yield c


def _mock_client_factory(handler):
    """Build a stand-in for companies_house_client() backed by httpx.MockTransport."""

    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=http_client.CH_BASE,
            transport=httpx.MockTransport(handler),
        )

    return factory


class TestErrorTaxonomy:
    @pytest.mark.asyncio
    async def test_vat_validate_non_uk_is_validation_error(self, client: Client):
        with pytest.raises(ToolError) as exc_info:
            await client.call_tool("vat_validate", {"vat_number": "EE102090374"})

        payload = parse_error_payload(str(exc_info.value))
        assert payload is not None, f"error message did not parse as a FleetErrorPayload: {exc_info.value}"
        assert payload.error_category == "validation"
        assert payload.is_retryable is False

    @pytest.mark.asyncio
    async def test_company_profile_missing_api_key_is_configuration_error(self, client: Client, monkeypatch):
        monkeypatch.delenv("CH_API_KEY", raising=False)

        with pytest.raises(ToolError) as exc_info:
            await client.call_tool("company_profile", {"company_number": "00000006"})

        payload = parse_error_payload(str(exc_info.value))
        assert payload is not None, f"error message did not parse as a FleetErrorPayload: {exc_info.value}"
        assert payload.error_category == "configuration"
        assert payload.is_retryable is False

    @pytest.mark.asyncio
    async def test_company_profile_404_is_not_found_error(self, client: Client, monkeypatch):
        """Mocked 404 through a real domain tool -> not_found.

        Exercises the full chain: company_profile -> _fetch_company_profile ->
        _request_with_retry -> raise_http_tool_error (C1 fix: the
        HTTPStatusError caught inside the retry loop must route through the
        taxonomy immediately instead of a bare `raise` escaping the function).
        """
        monkeypatch.setenv("CH_API_KEY", "dummy-test-key-not-real")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"errors": [{"error": "company-profile-not-found"}]})

        monkeypatch.setattr(companies_house, "companies_house_client", _mock_client_factory(handler))

        with pytest.raises(ToolError) as exc_info:
            await client.call_tool("company_profile", {"company_number": "00000006"})

        payload = parse_error_payload(str(exc_info.value))
        assert payload is not None, f"error message did not parse as a FleetErrorPayload: {exc_info.value}"
        assert payload.error_category == "not_found"
        assert payload.is_retryable is False

    @pytest.mark.asyncio
    async def test_persistent_429_is_transient_and_retryable(self, monkeypatch):
        """Persistent 429 through _request_with_retry -> transient, is_retryable=True.

        C2 fix: a status-code-inspected 429/503 never raises an exception
        inside the retry loop (it's a normal response, not a caught
        httpx error), so last_exc stayed None on retry exhaustion pre-fix.
        Speeds past the real backoff sleeps (BACKOFF_BASE**attempt seconds)
        by patching asyncio.sleep to a no-op — the retry *count* still runs
        MAX_RETRIES times, only the wall-clock wait is removed.
        """
        async def no_sleep(_seconds: float) -> None:
            return None

        monkeypatch.setattr(http_client.asyncio, "sleep", no_sleep)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, json={"error": "rate limited"})

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(base_url="https://example.test", transport=transport) as test_client:
            with pytest.raises(ToolError) as exc_info:
                await http_client._request_with_retry(test_client, "GET", "/thing")

        payload = parse_error_payload(str(exc_info.value))
        assert payload is not None, f"error message did not parse as a FleetErrorPayload: {exc_info.value}"
        assert payload.error_category == "transient"
        assert payload.is_retryable is True

    @pytest.mark.asyncio
    async def test_requesterror_then_persistent_429_is_transient_and_retryable(self, monkeypatch):
        """M2: a network-level RequestError on an early attempt followed by a
        persistent 429 on the remaining attempts must classify by the MOST
        RECENT event (the 429 -> transient/is_retryable=True), not by
        whichever tracker (last_exc vs last_retryable_status) happened to be
        checked first on retry exhaustion.

        Pre-fix, `if last_exc is not None: raise_http_tool_error(last_exc, ...)`
        always won whenever any RequestError occurred during the loop, even
        if a later 429/503 was the actual terminal state. httpx.ReadError
        (not ConnectError/TimeoutException) is used deliberately: it maps to
        error_category="unknown"/is_retryable=False via raise_http_tool_error,
        which is distinguishable from the correct transient/True outcome —
        unlike ConnectError, which happens to also map to transient/True and
        so wouldn't discriminate between the buggy and fixed code paths.
        """
        async def no_sleep(_seconds: float) -> None:
            return None

        monkeypatch.setattr(http_client.asyncio, "sleep", no_sleep)

        call_count = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise httpx.ReadError("connection reset", request=request)
            return httpx.Response(429, json={"error": "rate limited"})

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(base_url="https://example.test", transport=transport) as test_client:
            with pytest.raises(ToolError) as exc_info:
                await http_client._request_with_retry(test_client, "GET", "/thing")

        payload = parse_error_payload(str(exc_info.value))
        assert payload is not None, f"error message did not parse as a FleetErrorPayload: {exc_info.value}"
        assert payload.error_category == "transient"
        assert payload.is_retryable is True

    @pytest.mark.asyncio
    async def test_vat_validate_upstream_5xx_is_structured_error(self, client: Client, monkeypatch):
        """C4: hmrc_vat.py's raw VAT-lookup GET call bypassed the taxonomy
        entirely pre-fix (a bare httpx.HTTPStatusError from
        resp.raise_for_status() propagated straight out of vat_validate).
        _get_bearer_token is monkeypatched to skip the HMRC OAuth hop
        entirely (no real credentials needed); httpx.AsyncClient is
        monkeypatched so the VAT-lookup GET is served by a MockTransport
        returning 500 — no real network call anywhere in this test.
        """
        import hmrc_vat

        async def fake_token() -> str:
            return "fake-test-token"

        monkeypatch.setattr(hmrc_vat, "_get_bearer_token", fake_token)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": "internal server error"})

        class _FakeAsyncClient(httpx.AsyncClient):
            def __init__(self, *args, **kwargs):
                kwargs["transport"] = httpx.MockTransport(handler)
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

        with pytest.raises(ToolError) as exc_info:
            await client.call_tool("vat_validate", {"vat_number": "123456789"})

        payload = parse_error_payload(str(exc_info.value))
        assert payload is not None, f"error message did not parse as a FleetErrorPayload: {exc_info.value}"
        assert payload.error_category == "unknown"
        assert payload.is_retryable is False

    @pytest.mark.asyncio
    async def test_land_title_search_missing_postcode_is_validation_error(self, client: Client):
        """C3: land_registry's postcode-extraction ValueError -> validation,
        with the original message text surviving verbatim in the payload."""
        with pytest.raises(ToolError) as exc_info:
            await client.call_tool(
                "land_title_search", {"address_or_postcode": "no postcode anywhere in here"}
            )

        payload = parse_error_payload(str(exc_info.value))
        assert payload is not None, f"error message did not parse as a FleetErrorPayload: {exc_info.value}"
        assert payload.error_category == "validation"
        assert payload.is_retryable is False
        assert "Could not extract a valid UK postcode" in payload.description

    @pytest.mark.asyncio
    async def test_disqualified_search_missing_api_key_is_configuration_error(self, client: Client, monkeypatch):
        """B3: disqualified_search's bare `except Exception: data = {}` used to
        swallow the ToolError raised by companies_house_client() when
        CH_API_KEY is unset, turning a configuration failure into an empty
        `{"total_results": 0, ...}` success (isError=False). Must now
        propagate the structured configuration error instead."""
        monkeypatch.delenv("CH_API_KEY", raising=False)

        with pytest.raises(ToolError) as exc_info:
            await client.call_tool("disqualified_search", {"query": "Richard Howson"})

        payload = parse_error_payload(str(exc_info.value))
        assert payload is not None, f"error message did not parse as a FleetErrorPayload: {exc_info.value}"
        assert payload.error_category == "configuration"
        assert payload.is_retryable is False

    @pytest.mark.asyncio
    async def test_gazette_insolvency_persistent_503_is_transient_error(self, client: Client, monkeypatch):
        """B3: gazette_insolvency's bare `except Exception: pass` used to
        swallow the ToolError raised by _request_with_retry on a persistent
        503, returning an empty notices list (isError=False) instead of a
        transient/is_retryable=True structured error."""
        async def no_sleep(_seconds: float) -> None:
            return None

        monkeypatch.setattr(http_client.asyncio, "sleep", no_sleep)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, json={"error": "service unavailable"})

        def mock_gazette_client() -> httpx.AsyncClient:
            return httpx.AsyncClient(
                base_url=http_client.GAZETTE_BASE,
                transport=httpx.MockTransport(handler),
            )

        monkeypatch.setattr(gazette, "gazette_client", mock_gazette_client)

        with pytest.raises(ToolError) as exc_info:
            await client.call_tool("gazette_insolvency", {"name": "Example Corp Ltd"})

        payload = parse_error_payload(str(exc_info.value))
        assert payload is not None, f"error message did not parse as a FleetErrorPayload: {exc_info.value}"
        assert payload.error_category == "transient"
        assert payload.is_retryable is True
