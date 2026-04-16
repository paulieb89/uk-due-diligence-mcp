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

from typing import Annotated, Any

import httpx
from pydantic import Field
from fastmcp import FastMCP

from models import VATValidationResult

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

        url = f"{HMRC_VAT_LOOKUP_BASE}/{vat_number}"

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers={"Accept": "application/json"})

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
