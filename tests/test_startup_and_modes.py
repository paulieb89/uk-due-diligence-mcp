"""Startup warm-up runs inside the real FastMCP 4 app lifespan, and stdio mode starts.

The sanctions warm-up is replaced with a recorder, so no network is used.
"""

from __future__ import annotations

import asyncio
import gc
import json
import os
import sys
import tomllib
import weakref
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from fastmcp.server.http import create_streamable_http_app

import sanctions
import server
from http_client import project_version
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


# --- stdio entry point (#51) -------------------------------------------------
# `uvx uk-due-diligence-mcp` runs the console script, which must speak stdio.
# The server runs in a real subprocess. Parent monkeypatches do not reach the
# child, so the child installs its own guard: any outbound connection or DNS
# lookup is recorded to a file and refused, and so is any sanctions index load.

REPO_ROOT = Path(__file__).resolve().parent.parent

_CHILD = """
import json, os, socket

_log = os.environ["UKDD_TEST_GUARD_LOG"]

def _record(kind, detail):
    with open(_log, "a") as fh:
        fh.write(json.dumps({"kind": kind, "detail": repr(detail)}) + "\\n")

def _refuse(kind):
    def guard(*args, **kwargs):
        _record(kind, args[1:] if kind == "connect" else args)
        raise OSError("network disabled in test: " + kind)
    return guard

socket.socket.connect = _refuse("connect")
socket.socket.connect_ex = _refuse("connect")
socket.create_connection = _refuse("create_connection")
socket.getaddrinfo = _refuse("getaddrinfo")

import sanctions

async def _no_index_load(*args, **kwargs):
    _record("sanctions_get_index", args)
    raise RuntimeError("sanctions index load in test")

sanctions.get_index = _no_index_load

import server
server.main_stdio()
"""


def test_console_script_points_at_the_stdio_entry_point():
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    assert pyproject["project"]["scripts"]["uk-due-diligence-mcp"] == "server:main_stdio"


@pytest.mark.asyncio
async def test_main_stdio_serves_initialize_and_tools_list_offline(tmp_path):
    guard_log = tmp_path / "guard.jsonl"
    guard_log.touch()
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"CH_API_KEY", "CHARITY_API_KEY", "PORT"}
    }
    env.update(
        CH_API_KEY="dummy-ch-key",
        CHARITY_API_KEY="dummy-charity-key",
        UKDD_TEST_GUARD_LOG=str(guard_log),
        PYTHONUNBUFFERED="1",
    )
    transport = StdioTransport(
        command=sys.executable,
        args=["-c", _CHILD],
        env=env,
        cwd=str(REPO_ROOT),
        keep_alive=False,
        log_file=tmp_path / "stderr.log",
    )
    client = Client(transport, mode="legacy", timeout=30, init_timeout=30)

    # The client context closes the child on exit, including on failure.
    async with asyncio.timeout(60):
        async with client:
            init = client.initialize_result
            assert init is not None
            assert init.server_info.name == "uk-due-diligence-mcp"
            assert init.server_info.version == project_version()

            names = {tool.name for tool in await client.list_tools()}

    assert {"company_search", "sanctions_screen"} <= names
    attempts = [json.loads(line) for line in guard_log.read_text().splitlines()]
    assert attempts == []
