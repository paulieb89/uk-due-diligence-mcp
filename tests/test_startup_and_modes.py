"""Startup warm-up runs inside the real FastMCP 4 app lifespan.

The sanctions warm-up is replaced with a recorder, so no network is used.
"""

from __future__ import annotations

import pytest
from fastmcp.server.http import create_streamable_http_app

import server
from server import _run_at_startup


def _app():
    return create_streamable_http_app(
        server.mcp,
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
    )


@pytest.mark.asyncio
async def test_startup_runs_before_the_app_lifespan_and_the_app_still_starts():
    app = _app()
    order: list[str] = []

    async def startup() -> None:
        order.append("startup")

    _run_at_startup(app, startup)

    async with app.router.lifespan_context(app):
        order.append("serving")

    assert order == ["startup", "serving"]


@pytest.mark.asyncio
async def test_startup_failure_stops_the_app_instead_of_being_skipped():
    app = _app()

    async def broken() -> None:
        raise RuntimeError("warm-up failed")

    _run_at_startup(app, broken)

    with pytest.raises(RuntimeError, match="warm-up failed"):
        async with app.router.lifespan_context(app):
            pass
