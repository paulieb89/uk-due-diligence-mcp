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

from mcpfleet_obs import raise_http_tool_error, raise_tool_error

# ---------------------------------------------------------------------------
# Base URLs
# ---------------------------------------------------------------------------
CH_BASE = "https://api.company-information.service.gov.uk"
CHARITY_BASE = "https://api.charitycommission.gov.uk/register/api"
GAZETTE_BASE = "https://www.thegazette.co.uk"
HMRC_VAT_BASE = "https://api.service.hmrc.gov.uk/organisations/vat/check-vat-number/lookup"

# Consolidated sanctions lists. Unlike the other registers these are NOT per-entity
# query APIs — they are bulk files (CSV/XML, 2-25 MB) fetched whole and indexed by
# sanctions.py. No auth: the EU list carries a fixed public token in the URL; the UN
# list 302-redirects to a time-limited blob (hence follow_redirects on the client).
OFSI_CONLIST_URL = "https://ofsistorage.blob.core.windows.net/publishlive/2022format/ConList.csv"
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

    # last_exc is always set by this point — the loop only exits without
    # returning after at least one failed attempt.
    attempted = f"{method} {url.split('?', 1)[0]}"
    raise_http_tool_error(last_exc, attempted=attempted)  # type: ignore[arg-type]


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


def hmrc_vat_client() -> httpx.AsyncClient:
    """HMRC VAT validation — no auth required."""
    return httpx.AsyncClient(
        base_url=HMRC_VAT_BASE,
        headers={"Accept": "application/json"},
        timeout=10.0,
    )


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
    "hmrc_vat_client",
    "sanctions_client",
    "_request_with_retry",
    "CH_BASE",
    "CHARITY_BASE",
    "GAZETTE_BASE",
    "HMRC_VAT_BASE",
    "OFSI_CONLIST_URL",
    "OFAC_SDN_URL",
    "EU_FSF_URL",
    "UN_CONSOLIDATED_URL",
]
