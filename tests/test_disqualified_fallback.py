"""Regression test for the natural -> corporate disqualified-officer fallback.

_fetch_disqualified_profile tries the natural-officer endpoint first, and
is meant to fall back to the corporate-officer endpoint on a 404. This
test proves whether that fallback actually works.
"""

from __future__ import annotations

import httpx
import pytest

import disqualified


def _mock_client_factory(handler):
    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url="https://api.company-information.service.gov.uk",
            transport=httpx.MockTransport(handler),
        )

    return factory


@pytest.mark.asyncio
async def test_corporate_disqualification_fallback_when_natural_404s(monkeypatch):
    """A corporate disqualified officer (natural endpoint 404s, corporate
    endpoint has the record) must resolve via the corporate endpoint, not
    raise on the natural endpoint's 404."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "/disqualified-officers/natural/" in str(request.url):
            return httpx.Response(404, json={"errors": [{"error": "not-found"}]})
        if "/disqualified-officers/corporate/" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "name": "SOME CORPORATE OFFICER LTD",
                    "disqualifications": [
                        {
                            "disqualified_from": "2020-01-01",
                            "disqualified_until": "2025-01-01",
                            "reason": {"description": "example"},
                            "company_names": ["EXAMPLE LTD"],
                        }
                    ],
                },
            )
        raise AssertionError(f"unexpected request: {request.url}")

    monkeypatch.setattr(disqualified, "companies_house_client", _mock_client_factory(handler))
    result = await disqualified._fetch_disqualified_profile("CORP123")

    assert result.officer_kind == "corporate"
    assert result.name == "SOME CORPORATE OFFICER LTD"
    assert len(result.disqualifications) == 1


@pytest.mark.asyncio
async def test_natural_disqualification_resolves_without_trying_corporate(monkeypatch):
    """The common case must still work: a natural officer resolves on the
    first endpoint and the corporate endpoint is never called."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "/disqualified-officers/natural/" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "forename": "Jane",
                    "surname": "Smith",
                    "disqualifications": [],
                },
            )
        raise AssertionError(f"unexpected request to corporate endpoint: {request.url}")

    monkeypatch.setattr(disqualified, "companies_house_client", _mock_client_factory(handler))
    result = await disqualified._fetch_disqualified_profile("NAT456")

    assert result.officer_kind == "natural"
    assert result.name == "Jane Smith"


@pytest.mark.asyncio
async def test_disqualification_not_found_on_either_endpoint_raises_lookup_error(monkeypatch):
    """When neither endpoint has the record, the original LookupError
    behavior must be preserved — not a raw ToolError leaking through."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"errors": [{"error": "not-found"}]})

    monkeypatch.setattr(disqualified, "companies_house_client", _mock_client_factory(handler))

    with pytest.raises(LookupError):
        await disqualified._fetch_disqualified_profile("NOBODY789")
