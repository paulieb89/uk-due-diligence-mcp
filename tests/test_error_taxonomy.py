"""
Structured error taxonomy tests — offline, no live API calls.

Verifies that anticipated failure paths raise fleet-standard structured
ToolErrors (mcpfleet_obs.errors) rather than bare ValueError/RuntimeError,
so the message survives mask_error_details=True verbatim to the client.

Two paths exercised:
  - vat_validate with a non-UK VAT number -> validation, before any HTTP call
    (the EU-prefix check happens before the HMRC bearer-token request).
  - company_profile with CH_API_KEY unset -> configuration, before any HTTP
    call (the client factory raises on missing env var before opening a
    connection).
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastmcp import Client
from fastmcp.exceptions import ToolError

from mcpfleet_obs import parse_error_payload
from server import mcp


@pytest_asyncio.fixture
async def client():
    async with Client(mcp) as c:
        yield c


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
