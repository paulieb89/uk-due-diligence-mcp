"""
HMRC VAT number validation tool (1 tool).

Uses the HMRC Check a UK VAT Number API:
  GET https://api.service.hmrc.gov.uk/organisations/vat/check-vat-number/lookup/{vatNumber}

This is a free, unauthenticated API. Returns:
  - target.name       -> trading name as registered with HMRC
  - target.address    -> registered trading address
  - target.vatNumber  -> confirmed VAT number
  - consultationNumber -> HMRC reference for the lookup

The address returned by HMRC is the VAT-registered trading address, which may
differ from the Companies House registered address -- that discrepancy is a
due diligence signal.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

import httpx
from pydantic import Field
from fastmcp import FastMCP

from http_client import format_api_error

HMRC_VAT_LOOKUP_BASE = "https://api.service.hmrc.gov.uk/organisations/vat/check-vat-number/lookup"


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
        response_format: Annotated[str, Field(description="Output format: 'markdown' or 'json'")] = "markdown",
    ) -> str:
        """Validate a UK VAT number against the HMRC register.

        Returns the trading name and address as registered with HMRC for VAT purposes.
        The VAT-registered trading address often differs from the Companies House
        registered address -- that discrepancy is a due diligence signal worth noting.
        """
        # Normalise VAT number: strip GB prefix, spaces, hyphens
        clean_vat = vat_number.upper().replace("GB", "").replace(" ", "").replace("-", "")
        if not clean_vat.isdigit() or len(clean_vat) != 9:
            return f"Invalid VAT number format: '{vat_number}'. Must be 9 digits after removing 'GB' prefix and spaces."
        vat_number = clean_vat

        url = f"{HMRC_VAT_LOOKUP_BASE}/{vat_number}"

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, headers={"Accept": "application/json"})

                if resp.status_code == 404:
                    return (
                        f"❌ VAT number **GB{vat_number}** is **not registered** with HMRC. "
                        "The number may be invalid, deregistered, or incorrectly entered."
                    )

                if resp.status_code == 400:
                    return (
                        f"Error 400: Invalid VAT number format **{vat_number}**. "
                        "Ensure the number is 9 digits."
                    )

                resp.raise_for_status()
                data = resp.json()

        except httpx.HTTPStatusError as exc:
            return format_api_error(exc, "vat_validate")
        except Exception as exc:
            return format_api_error(exc, "vat_validate")

        target = data.get("target", {})
        name = target.get("name", "—")
        confirmed_vat = target.get("vatNumber", vat_number)
        address = _format_address(target.get("address", {}))
        consultation_number = data.get("consultationNumber", "—")

        if response_format == "json":
            return json.dumps(
                {
                    "valid": True,
                    "vat_number": f"GB{confirmed_vat}",
                    "trading_name": name,
                    "registered_address": address,
                    "consultation_number": consultation_number,
                },
                indent=2,
            )

        return (
            f"## ✅ VAT Number Valid — GB{confirmed_vat}\n"
            f"**Trading Name (HMRC):** {name}  \n"
            f"**Registered Address (HMRC):** {address}  \n"
            f"**Consultation Reference:** {consultation_number}  \n\n"
            f"*Note: Compare this address with the Companies House registered address — "
            f"discrepancies may indicate trading from an unregistered location.*\n"
        )
