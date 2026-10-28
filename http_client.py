"""
clients/http.py — Shared async HTTP client for uk-due-diligence-mcp.

Provides a single httpx.AsyncClient instance per data source with:
- Per-source base URL and auth header injection
- Automatic retry with exponential backoff on 429 / 503
- Consistent error formatting for agent consumption
"""

from __future__ import annotations

import asyncio
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from typing import Any, Optional

import httpx

from mcpfleet_obs import raise_http_tool_error, raise_tool_error

# ---------------------------------------------------------------------------
# Base URLs
# ---------------------------------------------------------------------------
CH_BASE = "https://api.company-information.service.gov.uk"
CHARITY_BASE = "https://api.charitycommission.gov.uk/register/api"
GAZETTE_BASE = "https://www.thegazette.co.uk"

# Consolidated sanctions lists. Unlike the other registers these are NOT per-entity
# query APIs — they are bulk files (CSV/XML, 2-25 MB) fetched whole and indexed by
# sanctions.py. No auth: the EU list carries a fixed public token in the URL; the UN
# list 302-redirects to a time-limited blob (hence follow_redirects on the client).
# The UK list is the FCDO UK Sanctions List: OFSI's ConList endpoint now returns 409.
FCDO_UK_SANCTIONS_URL = "https://sanctionslist.fcdo.gov.uk/docs/UK-Sanctions-List.xml"
OFAC_SDN_URL = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.XML"
EU_FSF_URL = "https://webgate.ec.europa.eu/fsd/fsf/public/files/xmlFullSanctionsList_1_1/content?token=dG9rZW4tMjAxNw"
UN_CONSOLIDATED_URL = "https://scsanctions.un.org/resources/xml/en/consolidated.xml"

# ---------------------------------------------------------------------------
# Retry config
# ---------------------------------------------------------------------------
MAX_RETRIES = 3
BACKOFF_BASE = 1.5  # seconds


def _get_env(key: str, required: bool = True) -> Optional[str]:
    """Read an env var; raise a structured configuration error if missing and required."""
    val = os.environ.get(key)
    if required and not val:
        raise_tool_error(
            "configuration",
            is_retryable=False,
            attempted=key,
            description=(
                f"Missing required environment variable: {key}. "
                f"Set it via --env or fly.toml [env] section."
            ),
        )
    return val


async def _request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    **kwargs: Any,
) -> httpx.Response:
    """Make an HTTP request with exponential backoff on 429/503."""
    attempted = f"{method} {url.split('?', 1)[0]}"
    last_exc: Optional[Exception] = None
    last_retryable_status: Optional[int] = None
    # Which tracker fired most recently — decides which one classifies the
    # final failure when retries exhaust with both set (e.g. a RequestError
    # on an early attempt followed by a persistent 429/503 on the last one).
    last_was_status = False
    for attempt in range(MAX_RETRIES):
        try:
            resp = await client.request(method, url, **kwargs)
            if resp.status_code in (429, 503):
                last_retryable_status = resp.status_code
                last_was_status = True
                wait = BACKOFF_BASE ** attempt
                await asyncio.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        except httpx.HTTPStatusError as exc:
            # raise_for_status() above is only ever reached for a status
            # code NOT already in (429, 503) — those are handled by the
            # branch above without raising. So any HTTPStatusError caught
            # here is terminal for this loop: route it through the fleet
            # error taxonomy immediately instead of retrying or re-raising
            # the raw httpx exception.
            raise_http_tool_error(exc, attempted=attempted)
        except httpx.RequestError as exc:
            last_exc = exc
            last_was_status = False
            await asyncio.sleep(BACKOFF_BASE ** attempt)

    # Retries exhausted. Either every attempt raised a network-level
    # RequestError (last_exc set), every attempt returned a persistent
    # 429/503 response with no exception ever raised (last_retryable_status
    # set instead — there's nothing to hand raise_http_tool_error), or the
    # attempts were a mix of both — in which case the most recent attempt
    # (last_was_status) decides which classification wins.
    if last_was_status:
        raise_tool_error(
            "transient",
            is_retryable=True,
            attempted=attempted,
            description=(
                f"Upstream returned {last_retryable_status} on every attempt "
                f"({MAX_RETRIES} retries) — retry later."
            ),
        )
    if last_exc is not None:
        raise_http_tool_error(last_exc, attempted=attempted)
    raise_tool_error(
        "transient",
        is_retryable=True,
        attempted=attempted,
        description=(
            f"Upstream returned {last_retryable_status} on every attempt "
            f"({MAX_RETRIES} retries) — retry later."
        ),
    )


# ---------------------------------------------------------------------------
# Client factories
# ---------------------------------------------------------------------------

def companies_house_client() -> httpx.AsyncClient:
    api_key = _get_env("CH_API_KEY")
    return httpx.AsyncClient(
        base_url=CH_BASE,
        auth=(api_key, ""),          # CH uses HTTP Basic, key as username
        headers={"Accept": "application/json"},
        timeout=15.0,
    )


def charity_client() -> httpx.AsyncClient:
    api_key = _get_env("CHARITY_API_KEY")
    return httpx.AsyncClient(
        base_url=CHARITY_BASE,
        headers={
            "Accept": "application/json",
            "Ocp-Apim-Subscription-Key": api_key,
        },
        timeout=15.0,
    )


def _urllib_get(
    url: str,
    params: Optional[dict[str, Any]],
    headers: Optional[dict[str, str]] = None,
) -> httpx.Response:
    """Blocking GET via urllib, returned as an httpx.Response.

    thegazette.co.uk answers 403 to httpx and requests from the Fly machine
    (lhr) but 200 to urllib for the same URL (measured 2026-10-23), so the
    Gazette calls go through the stdlib. Transport failures are re-raised as
    httpx exceptions so raise_http_tool_error keeps its taxonomy.
    """
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    request = httpx.Request("GET", url)
    try:
        req = urllib.request.Request(url, headers=headers or {})
        with urllib.request.urlopen(req, timeout=20.0) as resp:
            return httpx.Response(
                resp.status, content=resp.read(), headers=dict(resp.headers), request=request,
            )
    except urllib.error.HTTPError as exc:
        return httpx.Response(
            exc.code, content=exc.read(), headers=dict(exc.headers), request=request,
        )
    except TimeoutError as exc:
        raise httpx.ReadTimeout(str(exc), request=request) from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise httpx.ReadTimeout(str(exc.reason), request=request) from exc
        raise httpx.ConnectError(str(exc.reason), request=request) from exc


# Gazette fair-use policy (https://www.thegazette.co.uk/fair-use-policy): at most
# 5 requests per 10 seconds, and after a 429 wait for Retry-After before sending
# again. Pacing is process-wide because every Gazette call shares one egress IP.
# A blocked client fails fast instead of sleeping for the whole Retry-After: the
# tool returns a retryable error and no request is sent during the window.
GAZETTE_MAX_REQUESTS = 5
GAZETTE_WINDOW_SECONDS = 10.0
GAZETTE_DEFAULT_RETRY_AFTER = 10.0
_gazette_sent: deque[float] = deque()  # monotonic send times inside the window
_gazette_blocked_until = 0.0  # monotonic; no Gazette traffic before this
_gazette_pace_lock = asyncio.Lock()


def _gazette_retry_after(headers: httpx.Headers) -> float:
    """Seconds from a Retry-After header; the HTTP-date form falls back to the default."""
    try:
        return max(0.0, float(headers.get("retry-after", "")))
    except ValueError:
        return GAZETTE_DEFAULT_RETRY_AFTER


async def _gazette_pace() -> None:
    """Wait for a free slot in the 5-per-10-seconds window, then take it."""
    async with _gazette_pace_lock:
        while True:
            now = time.monotonic()
            while _gazette_sent and now - _gazette_sent[0] >= GAZETTE_WINDOW_SECONDS:
                _gazette_sent.popleft()
            if len(_gazette_sent) < GAZETTE_MAX_REQUESTS:
                _gazette_sent.append(now)
                return
            await asyncio.sleep(GAZETTE_WINDOW_SECONDS - (now - _gazette_sent[0]))


class _GazetteClient:
    """Async stand-in for httpx.AsyncClient that sends GETs through urllib.

    Exposes only what the Gazette callers use: `request` (via
    _request_with_retry) and the async context manager.
    """

    async def __aenter__(self) -> "_GazetteClient":
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        return None

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        global _gazette_blocked_until
        if method != "GET":
            raise ValueError(f"_GazetteClient only supports GET, got {method}")
        if not url.startswith("http"):
            url = f"{GAZETTE_BASE}{url}"

        remaining = _gazette_blocked_until - time.monotonic()
        if remaining > 0:
            # Still inside a Retry-After window: answer 429 locally, send nothing.
            return httpx.Response(
                429,
                headers={"Retry-After": str(math.ceil(remaining))},
                request=httpx.Request("GET", url),
            )

        await _gazette_pace()
        resp = await asyncio.to_thread(
            _urllib_get, url, kwargs.get("params"), kwargs.get("headers")
        )
        if resp.status_code == 429:
            _gazette_blocked_until = time.monotonic() + _gazette_retry_after(resp.headers)
        return resp


def gazette_client() -> _GazetteClient:
    """Gazette API — no auth required, read-only.

    Backed by urllib rather than httpx; see _urllib_get for why. urllib follows
    redirects by default, as the old follow_redirects=True client did.
    """
    return _GazetteClient()


def sanctions_client() -> httpx.AsyncClient:
    """Streaming client for the bulk consolidated sanctions files.

    No base_url — each list is a full URL on a different host. No auth. Long read
    timeout for 2-25 MB downloads; follow_redirects for the UN list's blob hop.
    Callers stream the body to a temp file and parse from disk to bound memory.
    """
    return httpx.AsyncClient(
        timeout=httpx.Timeout(120.0, connect=15.0),
        follow_redirects=True,
        headers={"User-Agent": "uk-due-diligence-mcp/sanctions (paul@bouch.dev)"},
    )


# ---------------------------------------------------------------------------
# Re-export helpers for tools
# ---------------------------------------------------------------------------

__all__ = [
    "companies_house_client",
    "charity_client",
    "gazette_client",
    "sanctions_client",
    "_request_with_retry",
    "CH_BASE",
    "CHARITY_BASE",
    "GAZETTE_BASE",
    "FCDO_UK_SANCTIONS_URL",
    "OFAC_SDN_URL",
    "EU_FSF_URL",
    "UN_CONSOLIDATED_URL",
]
