"""
clients/http.py — Shared async HTTP client for uk-due-diligence-mcp.

Provides a single httpx.AsyncClient instance per data source with:
- Per-source base URL and auth header injection
- Automatic retry with exponential backoff on 429 / 503
- Consistent error formatting for agent consumption
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Optional

import httpx

# ---------------------------------------------------------------------------
# Base URLs
# ---------------------------------------------------------------------------
CH_BASE = "https://api.company-information.service.gov.uk"
CHARITY_BASE = "https://api.charitycommission.gov.uk/register/api"
GAZETTE_BASE = "https://www.thegazette.co.uk"
HMLR_BASE = "https://api.landregistry.data.gov.uk/data/ppi"
HMRC_VAT_BASE = "https://api.service.hmrc.gov.uk/organisations/vat/check-vat-number/lookup"

# ---------------------------------------------------------------------------
# Retry config
# ---------------------------------------------------------------------------
MAX_RETRIES = 3
BACKOFF_BASE = 1.5  # seconds


def _get_env(key: str, required: bool = True) -> Optional[str]:
    """Read an env var; raise on missing if required=True."""
    val = os.environ.get(key)
    if required and not val:
        raise RuntimeError(
            f"Missing required environment variable: {key}. "
            f"Set it via --env or fly.toml [env] section."
        )
    return val


async def _request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    **kwargs: Any,
) -> httpx.Response:
    """Make an HTTP request with exponential backoff on 429/503."""
    last_exc: Optional[Exception] = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = await client.request(method, url, **kwargs)
            if resp.status_code in (429, 503):
                wait = BACKOFF_BASE ** attempt
                await asyncio.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        except httpx.HTTPStatusError as exc:
            last_exc = exc
            if exc.response.status_code not in (429, 503):
                raise
        except httpx.RequestError as exc:
            last_exc = exc
            await asyncio.sleep(BACKOFF_BASE ** attempt)

    raise last_exc  # type: ignore[misc]


def format_api_error(exc: Exception, context: str = "") -> str:
    """Return a structured, agent-friendly error string."""
    prefix = f"[{context}] " if context else ""
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 400:
            return f"{prefix}Error 400: Bad request — check your input parameters."
        if status == 401:
            return f"{prefix}Error 401: Unauthorised — verify your API key is set correctly."
        if status == 404:
            return f"{prefix}Error 404: Not found — the entity may not exist in this register."
        if status == 429:
            return f"{prefix}Error 429: Rate limit exceeded — reduce request frequency."
        if status == 503:
            return f"{prefix}Error 503: Service unavailable — the register API may be down."
        return f"{prefix}Error {status}: API request failed."
    if isinstance(exc, httpx.TimeoutException):
        return f"{prefix}Timeout: The register API did not respond in time."
    if isinstance(exc, httpx.RequestError):
        return f"{prefix}Network error: {type(exc).__name__} — {exc}"
    return f"{prefix}Unexpected error: {type(exc).__name__} — {exc}"


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


def gazette_client() -> httpx.AsyncClient:
    """Gazette API — no auth required.

    The insolvency search endpoint is sensitive to Accept headers; omitting
    it lets the server negotiate (returns JSON by default for /data.json paths).
    follow_redirects=True needed as the Gazette uses redirects on some paths.
    """
    return httpx.AsyncClient(
        base_url=GAZETTE_BASE,
        timeout=20.0,
        follow_redirects=True,
    )


def hmlr_client() -> httpx.AsyncClient:
    """HMLR Price Paid / title data — no auth required."""
    return httpx.AsyncClient(
        base_url=HMLR_BASE,
        headers={"Accept": "application/json"},
        timeout=20.0,
    )


def hmrc_vat_client() -> httpx.AsyncClient:
    """HMRC VAT validation — no auth required."""
    return httpx.AsyncClient(
        base_url=HMRC_VAT_BASE,
        headers={"Accept": "application/json"},
        timeout=10.0,
    )


# ---------------------------------------------------------------------------
# Re-export helpers for tools
# ---------------------------------------------------------------------------

__all__ = [
    "companies_house_client",
    "charity_client",
    "gazette_client",
    "hmlr_client",
    "hmrc_vat_client",
    "_request_with_retry",
    "format_api_error",
    "CH_BASE",
    "CHARITY_BASE",
    "GAZETTE_BASE",
    "HMLR_BASE",
    "HMRC_VAT_BASE",
]
