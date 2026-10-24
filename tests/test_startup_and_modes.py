"""Startup warm-up runs inside the real FastMCP 4 app lifespan, and GET_STREAM_MODE=405
passes GET /mcp through to FastMCP instead of holding a stream.

The sanctions warm-up is replaced with a recorder, so no network is used.
"""

from __future__ import annotations

import pytest
from fastmcp.server.http import create_streamable_http_app

import server
from server import _HttpGuard, _run_at_startup


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


@pytest.mark.asyncio
async def test_405_mode_passes_get_through_to_the_app():
    seen: list[str] = []

    async def app(scope, receive, send):
        seen.append(scope["method"])
        await send({"type": "http.response.start", "status": 405, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    guard = _HttpGuard(app, mode="405")
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await guard({"type": "http", "method": "GET", "path": "/mcp", "headers": []}, receive, send)

    assert seen == ["GET"]
    assert sent[0]["status"] == 405


def test_default_mode_is_bounded():
    assert server.GET_STREAM_MODE == "bounded"
