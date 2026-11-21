"""Startup warm-up runs inside the real FastMCP 4 app lifespan.

The sanctions warm-up is replaced with a recorder, so no network is used.
"""

from __future__ import annotations

import asyncio
import gc
import weakref

import pytest
from fastmcp.server.http import create_streamable_http_app

import sanctions
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


# --- sanctions.warm_cache task lifetime --------------------------------------
# The event loop only keeps weak references to tasks, so warm_cache must hold
# the warm task itself until it finishes. get_index is replaced with an
# event-gated fake, so no network is used.


@pytest.fixture
def warm_tasks(monkeypatch):
    tasks: set[asyncio.Task[None]] = set()
    monkeypatch.setattr(sanctions, "_WARM_TASKS", tasks)
    yield tasks
    for task in list(tasks):
        task.cancel()


def _gated_get_index(monkeypatch, gate: asyncio.Event, *, fail: bool = False):
    calls: list[str] = []

    async def fake_get_index():
        calls.append("get_index")
        await gate.wait()
        if fail:
            raise RuntimeError("all lists failed")
        return {}, None, []

    monkeypatch.setattr(sanctions, "get_index", fake_get_index)
    return calls


async def _settle(tasks: set[asyncio.Task[None]]) -> None:
    # The set's discard is registered before asyncio.wait's own callback, so it
    # has run by the time wait returns.
    await asyncio.wait(set(tasks))


@pytest.mark.asyncio
async def test_warm_cache_holds_the_task_while_running_and_releases_it_after(
    monkeypatch, warm_tasks
):
    gate = asyncio.Event()
    calls = _gated_get_index(monkeypatch, gate)

    assert await sanctions.warm_cache() is None  # returns without waiting
    assert len(warm_tasks) == 1
    ref = weakref.ref(next(iter(warm_tasks)))

    await asyncio.sleep(0)  # let the task start and block on the gate
    gc.collect()
    task = ref()
    assert task is not None and not task.done()
    assert task in warm_tasks
    assert calls == ["get_index"]

    gate.set()
    await _settle(warm_tasks)
    assert task.done() and task.exception() is None
    assert warm_tasks == set()


@pytest.mark.asyncio
async def test_warm_cache_retains_each_pending_task_independently(monkeypatch, warm_tasks):
    gate = asyncio.Event()
    calls = _gated_get_index(monkeypatch, gate)

    await sanctions.warm_cache()
    await sanctions.warm_cache()
    refs = [weakref.ref(t) for t in warm_tasks]
    assert len(refs) == 2

    await asyncio.sleep(0)
    gc.collect()
    assert all(r() is not None and r() in warm_tasks for r in refs)
    assert calls == ["get_index", "get_index"]

    gate.set()
    await _settle(warm_tasks)
    assert warm_tasks == set()


@pytest.mark.asyncio
async def test_warm_cache_releases_the_task_after_a_failed_warm(monkeypatch, warm_tasks, capsys):
    gate = asyncio.Event()
    _gated_get_index(monkeypatch, gate, fail=True)

    await sanctions.warm_cache()
    task = next(iter(warm_tasks))
    gate.set()
    await _settle(warm_tasks)

    assert task.done() and task.exception() is None  # failure is logged, not raised
    assert warm_tasks == set()
    assert "startup warm failed" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_warm_cache_releases_the_task_after_cancellation(monkeypatch, warm_tasks):
    gate = asyncio.Event()
    _gated_get_index(monkeypatch, gate)

    await sanctions.warm_cache()
    task = next(iter(warm_tasks))
    await asyncio.sleep(0)
    task.cancel()
    await _settle(warm_tasks)

    assert task.cancelled()
    assert warm_tasks == set()
