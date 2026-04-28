"""
HMRC VAT number validation tool (1 tool).

Uses the HMRC Check a UK VAT Number API v2:
  GET {base}/organisations/vat/check-vat-number/lookup/{vatNumber}

API v2 is application-restricted and requires a Bearer token obtained via
the client_credentials OAuth2 flow. Set env vars:
  HMRC_CLIENT_ID     — from the HMRC Developer Hub app
  HMRC_CLIENT_SECRET — from the HMRC Developer Hub app
  HMRC_ENV           — 'sandbox' or 'production' (default: production)

Returns:
  - target.name       -> trading name as registered with HMRC
  - target.address    -> registered trading address
  - target.vatNumber  -> confirmed VAT number
  - consultationNumber -> HMRC reference for the lookup

The address returned by HMRC is the VAT-registered trading address, which may
differ from the Companies House registered address -- that discrepancy is a
due diligence signal.
"""

from __future__ import annotations

import os
import time
from typing import Annotated, Any

import httpx
from pydantic import Field
from fastmcp import FastMCP

from models import VATValidationResult

# ---------------------------------------------------------------------------
# Environment-dependent base URLs
# ---------------------------------------------------------------------------

def _hmrc_env() -> str:
    return os.environ.get("HMRC_ENV", "production").lower()


def _hmrc_base() -> str:
    if _hmrc_env() == "sandbox":
        return "https://test-api.service.hmrc.gov.uk"
    return "https://api.service.hmrc.gov.uk"


HMRC_VAT_LOOKUP_PATH = "/organisations/vat/check-vat-number/lookup"

# ---------------------------------------------------------------------------
# Token cache — module-level, avoids re-fetching for the token's lifetime
# ---------------------------------------------------------------------------

_token_cache: dict[str, Any] = {"token": None, "expires_at": 0.0}


async def _get_bearer_token() -> str:
    """Obtain (or return cached) HMRC application-restricted Bearer token."""
    client_id = os.environ.get("HMRC_CLIENT_ID")
    client_secret = os.environ.get("HMRC_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise ValueError(
            "HMRC VAT validation requires application credentials. "
            "Set HMRC_CLIENT_ID and HMRC_CLIENT_SECRET environment variables. "
            "Register your application at https://developer.service.hmrc.gov.uk"
        )

    # Return cached token if still valid (with 60s buffer)
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires_at"] - 60:
        return _token_cache["token"]

    token_url = f"{_hmrc_base()}/oauth/token"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        data = resp.json()

    token = data["access_token"]
    expires_in = int(data.get("expires_in", 14400))
    _token_cache["token"] = token
    _token_cache["expires_at"] = now + expires_in
    return token


def _format_address(addr: dict[str, Any]) -> str:
    lines = [
        addr.get("line1", ""),
        addr.get("line2", ""),
        addr.get("line3", ""),
        addr.get("line4", ""),
        addr.get("postCode", ""),
        addr.get("countryCode", ""),
    ]
    return ", ".join(p for p in lines if p)


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

def register_tools(mcp: FastMCP) -> None:

    @mcp.tool(
        name="vat_validate",
        annotations={
            "title": "Validate UK VAT Number (HMRC)",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def vat_validate(
        vat_number: Annotated[str, Field(description="UK VAT registration number. Accepts: 'GB123456789', '123456789', 'GB 123 456 789'. GB prefix and spaces normalised automatically.", min_length=9, max_length=15)],
    ) -> VATValidationResult:
        """Validate a UK VAT number against the HMRC register.

        Returns the trading name and address as registered with HMRC for VAT
        purposes. The VAT-registered trading address often differs from the
        Companies House registered address — that discrepancy is a due
        diligence signal worth noting.
        """
        # Normalise VAT number: strip GB prefix, spaces, hyphens
        clean_vat = vat_number.upper().replace("GB", "").replace(" ", "").replace("-", "")
        if not clean_vat.isdigit() or len(clean_vat) != 9:
            raise ValueError(
                f"Invalid VAT number format: '{vat_number}'. "
                "Must be 9 digits after removing 'GB' prefix and spaces."
            )
        vat_number = clean_vat

        token = await _get_bearer_token()
        base = _hmrc_base()
        url = f"{base}{HMRC_VAT_LOOKUP_PATH}/{vat_number}"

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                url,
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {token}",
                },
            )

            if resp.status_code == 404:
                return VATValidationResult(
                    valid=False,
                    vat_number=f"GB{vat_number}",
                    trading_name=None,
                    registered_address=None,
                    consultation_number=None,
                )

            resp.raise_for_status()
            data = resp.json()

        target = data.get("target", {})
        name = target.get("name")
        confirmed_vat = target.get("vatNumber", vat_number)
        address = _format_address(target.get("address", {})) or None
        consultation_number = data.get("consultationNumber")

        return VATValidationResult(
            valid=True,
            vat_number=f"GB{confirmed_vat}",
            trading_name=name,
            registered_address=address,
            consultation_number=consultation_number,
        )
