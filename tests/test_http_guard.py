"""
Offline tests for the bounded GET /mcp stream guard in server.py.

Drives the ASGI wrapper directly with fake receive/send, so no server or network
is involved. Time-based behaviour uses short real durations.
"""

from __future__ import annotations

import asyncio

import pytest

from server import _GetStreamBudget, _HttpGuard


def _scope(method: str, path: str = "/mcp") -> dict:
    return {"type": "http", "method": method, "path": path, "headers": []}


class _Conn:
    """One client connection: a receive queue the test controls, and the sent messages."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue = asyncio.Queue()
        self.sent: list[dict] = []
        self.queue.put_nowait({"type": "http.request", "body": b"", "more_body": False})

    async def receive(self) -> dict:
        return await self.queue.get()

    async def send(self, message: dict) -> None:
        self.sent.append(message)

    def disconnect(self) -> None:
        self.queue.put_nowait({"type": "http.disconnect"})

    def body(self) -> bytes:
        return b"".join(m.get("body", b"") for m in self.sent if m["type"] == "http.response.body")

    def closed(self) -> bool:
        return any(m["type"] == "http.response.body" and not m.get("more_body", False)
                   for m in self.sent)


async def _wait_for(predicate, timeout: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("timed out waiting for condition")
        await asyncio.sleep(0.005)


def _guard(budget: _GetStreamBudget, **overrides) -> _HttpGuard:
    async def app(scope, receive, send):  # downstream FastMCP; must not see GET /mcp
        raise AssertionError("GET /mcp must be handled by the guard")

    options = {"ttl": 60.0, "ping": 60.0, "retry_ms": 5000}
    options.update(overrides)
    return _HttpGuard(app, budget=budget, **options)


@pytest.mark.asyncio
async def test_get_opens_event_stream_with_retry_hint_and_releases_on_disconnect():
    budget = _GetStreamBudget(cap=5)
    guard = _guard(budget)
    conn = _Conn()

    task = asyncio.create_task(guard(_scope("GET"), conn.receive, conn.send))
    await _wait_for(lambda: budget.open == 1)

    assert conn.sent[0]["status"] == 200
    assert (b"content-type", b"text/event-stream") in conn.sent[0]["headers"]
    assert conn.body() == b"retry: 5000\n\n"
    assert not conn.closed()

    conn.disconnect()
    await asyncio.wait_for(task, 2.0)
    assert budget.open == 0


@pytest.mark.asyncio
async def test_cap_evicts_the_oldest_stream_and_keeps_the_newest():
    budget = _GetStreamBudget(cap=2)
    guard = _guard(budget)
    first, second, third = _Conn(), _Conn(), _Conn()

    t1 = asyncio.create_task(guard(_scope("GET"), first.receive, first.send))
    await _wait_for(lambda: budget.open == 1)
    t2 = asyncio.create_task(guard(_scope("GET"), second.receive, second.send))
    await _wait_for(lambda: budget.open == 2)
    t3 = asyncio.create_task(guard(_scope("GET"), third.receive, third.send))

    await asyncio.wait_for(t1, 2.0)           # the oldest is closed cleanly
    assert first.closed()
    assert budget.open == 2                   # never above the cap
    assert not second.closed() and not third.closed()

    second.disconnect()
    third.disconnect()
    await asyncio.wait_for(asyncio.gather(t2, t3), 2.0)
    assert budget.open == 0


@pytest.mark.asyncio
async def test_stream_expires_after_ttl_and_is_closed():
    budget = _GetStreamBudget(cap=5)
    guard = _guard(budget, ttl=0.05, ping=10.0)
    conn = _Conn()

    await asyncio.wait_for(guard(_scope("GET"), conn.receive, conn.send), 2.0)

    assert conn.closed()
    assert budget.open == 0


@pytest.mark.asyncio
async def test_idle_stream_gets_ping_comments():
    budget = _GetStreamBudget(cap=5)
    guard = _guard(budget, ttl=0.2, ping=0.02)
    conn = _Conn()

    await asyncio.wait_for(guard(_scope("GET"), conn.receive, conn.send), 2.0)

    assert b": ping\n\n" in conn.body()


@pytest.mark.asyncio
async def test_delete_is_still_405_with_allow_post():
    budget = _GetStreamBudget(cap=5)
    guard = _guard(budget)
    conn = _Conn()

    await guard(_scope("DELETE"), conn.receive, conn.send)

    assert conn.sent[0]["status"] == 405
    assert (b"allow", b"POST") in conn.sent[0]["headers"]
    assert budget.open == 0


@pytest.mark.asyncio
async def test_other_paths_pass_through_to_the_app():
    seen = []

    async def app(scope, receive, send):
        seen.append((scope["method"], scope["path"]))

    budget = _GetStreamBudget(cap=5)
    guard = _HttpGuard(app, budget=budget)
    conn = _Conn()

    await guard(_scope("GET", "/health"), conn.receive, conn.send)
    await guard(_scope("POST", "/mcp"), conn.receive, conn.send)

    assert seen == [("GET", "/health"), ("POST", "/mcp")]
    assert budget.open == 0
