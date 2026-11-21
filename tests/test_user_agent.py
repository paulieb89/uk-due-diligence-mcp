"""Every upstream client identifies the server with one User-Agent (#86).

The Gazette path is covered in test_gazette_client.py (urllib, checked on the
wire); here the httpx client factories are checked by their default headers.
"""

from __future__ import annotations

import pytest

import http_client


def test_user_agent_names_package_version_and_project_url():
    ua = http_client.USER_AGENT
    assert ua.startswith(f"uk-due-diligence-mcp/{http_client.project_version()} (")
    assert "+https://github.com/paulieb89/uk-due-diligence-mcp" in ua


@pytest.mark.parametrize(
    "factory",
    [
        "companies_house_client",
        "charity_client",
        "land_registry_client",
        "sanctions_client",
    ],
)
@pytest.mark.asyncio
async def test_httpx_clients_send_the_project_user_agent(monkeypatch, factory):
    monkeypatch.setenv("CH_API_KEY", "test-key")
    monkeypatch.setenv("CHARITY_API_KEY", "test-key")
    async with getattr(http_client, factory)() as client:
        assert client.headers["User-Agent"] == http_client.USER_AGENT
