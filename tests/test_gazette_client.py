"""
Offline tests for the urllib-backed Gazette client and gazette_notice fetch.

thegazette.co.uk answers 403 to httpx from the Fly machine but 200 to urllib,
and the notice endpoint needs `Accept: application/json` (without it: 429
text/html). These tests run against a local HTTP stub, so they check what the
client sends and how failures map to the fleet error taxonomy, without
touching the real Gazette site.
"""

from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable
from urllib.parse import parse_qs, urlparse

import pytest
from fastmcp.exceptions import ToolError
from mcpfleet_obs import parse_error_payload

import gazette
import http_client
from http_client import _GazetteClient, _request_with_retry


class _Stub:
    """Local HTTP server; `respond` decides each reply and `seen` records requests."""

    def __init__(self) -> None:
        self.seen: list[dict] = []
        self.respond: Callable[[dict], tuple[int, bytes, str]] = lambda _req: (
            200, b"{}", "application/json",
        )
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args) -> None:  # keep test output quiet
                pass

            def do_GET(self) -> None:  # noqa: N802 - stdlib naming
                parsed = urlparse(self.path)
                req = {
                    "path": parsed.path,
                    "query": {k: v[0] for k, v in parse_qs(parsed.query).items()},
                    "accept": self.headers.get("Accept"),
                }
                stub.seen.append(req)
                code, body, ctype, *rest = stub.respond(req)
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                for name, value in (rest[0] if rest else {}).items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(body)

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self._server.server_port}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def stub():
    s = _Stub()
    yield s
    s.close()


@pytest.fixture(autouse=True)
def fresh_gazette_pacing(monkeypatch):
    # The pacing window and Retry-After block are process-wide; start each test clean.
    http_client._gazette_sent.clear()
    monkeypatch.setattr(http_client, "_gazette_blocked_until", 0.0)
    yield
    http_client._gazette_sent.clear()


@pytest.fixture
def no_backoff(monkeypatch):
    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(http_client.asyncio, "sleep", no_sleep)


@pytest.fixture
def recorded_sleeps(monkeypatch):
    """Replace asyncio.sleep with a recorder, so tests can assert how long code waited."""
    sleeps: list[float] = []

    async def record(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(http_client.asyncio, "sleep", record)
    return sleeps


def _error_category(exc: ToolError) -> str:
    payload = parse_error_payload(str(exc))
    assert payload is not None, f"not a structured fleet error: {exc}"
    return payload.error_category


@pytest.mark.asyncio
async def test_client_sends_accept_and_params_and_returns_json(stub):
    stub.respond = lambda _req: (200, json.dumps({"entry": []}).encode(), "application/json")

    async with _GazetteClient() as client:
        resp = await client.request(
            "GET", f"{stub.base}/insolvency/notice/data.json",
            params={"text": "TESCO PLC", "results-page-size": 100},
            headers={"Accept": "application/json"},
        )

    assert resp.status_code == 200
    assert resp.json() == {"entry": []}
    [req] = stub.seen
    assert req["query"] == {"text": "TESCO PLC", "results-page-size": "100"}
    assert req["accept"] == "application/json"


@pytest.mark.asyncio
async def test_relative_path_joins_gazette_base(stub, monkeypatch):
    monkeypatch.setattr(http_client, "GAZETTE_BASE", stub.base)

    async with _GazetteClient() as client:
        resp = await client.request("GET", "/notice/12345/data.json", params={"view": "linked-data"})

    assert resp.status_code == 200
    assert stub.seen[0]["path"] == "/notice/12345/data.json"


@pytest.mark.asyncio
async def test_403_is_auth_required_not_retried(stub, no_backoff):
    stub.respond = lambda _req: (403, b'{"denied": true}', "application/json")

    async with _GazetteClient() as client:
        with pytest.raises(ToolError) as exc_info:
            await _request_with_retry(client, "GET", f"{stub.base}/x", params={})

    assert _error_category(exc_info.value) == "auth_required"
    assert len(stub.seen) == 1


@pytest.mark.asyncio
async def test_429_then_200_is_retried(stub, no_backoff):
    # Retry-After: 0 keeps the block window at zero, so the retry is actually sent.
    replies = iter([
        (429, b"", "text/html", {"Retry-After": "0"}),
        (200, b'{"ok": true}', "application/json"),
    ])
    stub.respond = lambda _req: next(replies)

    async with _GazetteClient() as client:
        resp = await _request_with_retry(client, "GET", f"{stub.base}/x", params={})

    assert resp.json() == {"ok": True}
    assert len(stub.seen) == 2


@pytest.mark.asyncio
async def test_retry_after_fails_fast_and_sends_nothing_during_window(stub, recorded_sleeps):
    stub.respond = lambda _req: (429, b"", "text/html", {"Retry-After": "900"})

    async with _GazetteClient() as client:
        with pytest.raises(ToolError) as exc_info:
            await _request_with_retry(client, "GET", f"{stub.base}/x", params={})
    assert _error_category(exc_info.value) == "transient"
    # One real request reached Gazette; its 429 opened a 900-second block.
    assert len(stub.seen) == 1
    # Waiting stays short: no sleep anywhere near the Retry-After period.
    assert max(recorded_sleeps, default=0.0) < 10.0

    # A later call inside the block answers locally and sends nothing.
    async with _GazetteClient() as client:
        with pytest.raises(ToolError) as exc_info:
            await _request_with_retry(client, "GET", f"{stub.base}/y", params={})
    assert _error_category(exc_info.value) == "transient"
    assert len(stub.seen) == 1


@pytest.mark.asyncio
async def test_pacing_holds_requests_to_the_window_limit(stub, monkeypatch):
    monkeypatch.setattr(http_client, "GAZETTE_MAX_REQUESTS", 2)
    monkeypatch.setattr(http_client, "GAZETTE_WINDOW_SECONDS", 0.5)

    import time
    start = time.monotonic()
    async with _GazetteClient() as client:
        for i in range(3):
            await client.request("GET", f"{stub.base}/p{i}", params={})
    elapsed = time.monotonic() - start

    # Two requests go out at once; the third must wait for the 0.5 s window to roll.
    assert len(stub.seen) == 3
    assert elapsed >= 0.4


def test_retry_after_parsing():
    from httpx import Headers

    assert http_client._gazette_retry_after(Headers({"retry-after": "30"})) == 30.0
    assert http_client._gazette_retry_after(Headers({})) == http_client.GAZETTE_DEFAULT_RETRY_AFTER
    date_form = Headers({"retry-after": "Wed, 21 Oct 2015 07:28:00 GMT"})
    assert http_client._gazette_retry_after(date_form) == http_client.GAZETTE_DEFAULT_RETRY_AFTER


@pytest.mark.asyncio
async def test_refused_connection_is_transient(no_backoff):
    # Bind then release a port so nothing listens on it.
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    async with _GazetteClient() as client:
        with pytest.raises(ToolError) as exc_info:
            await _request_with_retry(client, "GET", f"http://127.0.0.1:{port}/x", params={})

    payload = parse_error_payload(str(exc_info.value))
    assert payload is not None
    assert payload.error_category == "transient"
    assert payload.is_retryable is True


@pytest.mark.asyncio
async def test_fetch_gazette_notice_sends_accept_and_returns_notice(stub, monkeypatch):
    monkeypatch.setattr(http_client, "GAZETTE_BASE", stub.base)
    notice = {"title": "Official Gazette notice", "f:notice-code": "2452"}
    stub.respond = lambda _req: (200, json.dumps(notice).encode(), "application/json")

    result = await gazette._fetch_gazette_notice("12345")

    assert result == notice
    [req] = stub.seen
    assert req["path"] == "/notice/12345/data.json"
    assert req["query"] == {"view": "linked-data"}
    assert req["accept"] == "application/json"


@pytest.mark.asyncio
async def test_fetch_gazette_notice_403_is_structured_error(stub, monkeypatch, no_backoff):
    monkeypatch.setattr(http_client, "GAZETTE_BASE", stub.base)
    stub.respond = lambda _req: (403, b"", "text/html")

    with pytest.raises(ToolError) as exc_info:
        await gazette._fetch_gazette_notice("12345")

    assert _error_category(exc_info.value) == "auth_required"
