"""Sanctions bulk-list downloads use the shared retry policy (#68).

`_download_to_temp` used to stream with `client.stream("GET", url)` directly, so a
transient 429/503 or a timeout on one of the four bulk lists was never retried.
It now goes through `http_client._stream_to_file_with_retry`, which mirrors
`_request_with_retry` (MAX_RETRIES attempts, BACKOFF_BASE ** attempt sleeps,
last-failure classification) while still streaming the body to disk.

All tests are offline: httpx.MockTransport stands in for the network and
`http_client.asyncio.sleep` is replaced by a recorder, so no test waits on backoff.
`_build_index` catches a failed list, so the error-shape tests call the downloader
and the helper directly.
"""

from __future__ import annotations

import asyncio
import io
import os
import tempfile

import httpx
import pytest
from fastmcp.exceptions import ToolError

import http_client
import sanctions
from mcpfleet_obs import parse_error_payload

URL = "https://lists.example.test/list.xml?token=secret"
GOOD_BODY = b"<list>" + b"x" * 5000 + b"</list>"


class TrackedStream(httpx.AsyncByteStream):
    """A response body that yields `chunks`, optionally then raises, and records closure."""

    def __init__(self, chunks: list[bytes], exc: Exception | None = None) -> None:
        self.chunks = chunks
        self.exc = exc
        self.yielded = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            self.yielded += 1
            yield chunk
        if self.exc is not None:
            raise self.exc

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def sleeps(monkeypatch):
    """Record backoff durations instead of sleeping."""
    recorded: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        recorded.append(seconds)

    monkeypatch.setattr(http_client.asyncio, "sleep", fake_sleep)
    return recorded


@pytest.fixture
def temp_paths(monkeypatch):
    """Record every temp file _download_to_temp creates."""
    created: list[str] = []
    real_mkstemp = tempfile.mkstemp

    def recording_mkstemp(*args, **kwargs):
        fd, path = real_mkstemp(*args, **kwargs)
        created.append(path)
        return fd, path

    monkeypatch.setattr(sanctions.tempfile, "mkstemp", recording_mkstemp)
    yield created
    for path in created:
        if os.path.exists(path):
            os.unlink(path)


def _scripted(events):
    """A MockTransport handler that plays `events` in order, one per request.

    Each event is an int status (empty body), bytes (a 200 with that body, in two
    chunks), an exception instance (raised before any response), or a
    TrackedStream (a 200 with that body). Every response body is a TrackedStream
    so the test can assert it was closed before the next request.
    """
    state = {"requests": 0, "streams": [], "open_at_request": []}
    queue = list(events)

    def handler(request: httpx.Request) -> httpx.Response:
        state["open_at_request"].append(sum(1 for s in state["streams"] if not s.closed))
        state["requests"] += 1
        event = queue.pop(0)
        if isinstance(event, Exception):
            raise event
        if isinstance(event, int):
            stream = TrackedStream([b"upstream error page"])
            state["streams"].append(stream)
            return httpx.Response(event, stream=stream)
        if isinstance(event, bytes):
            event = TrackedStream([event[: len(event) // 2], event[len(event) // 2:]])
        state["streams"].append(event)
        return httpx.Response(200, stream=event)

    return handler, state


def _use_transport(monkeypatch, handler):
    """Point sanctions.sanctions_client at a MockTransport, keeping its real settings."""

    def factory() -> httpx.AsyncClient:
        real = http_client.sanctions_client()
        return httpx.AsyncClient(
            timeout=real.timeout,
            follow_redirects=real.follow_redirects,
            headers=real.headers,
            transport=httpx.MockTransport(handler),
        )

    monkeypatch.setattr(sanctions, "sanctions_client", factory)


def _payload(exc_info):
    payload = parse_error_payload(str(exc_info.value))
    assert payload is not None, f"not a FleetErrorPayload: {exc_info.value}"
    return payload


def _read(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# _download_to_temp
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 503])
async def test_retryable_status_then_200_succeeds(monkeypatch, sleeps, temp_paths, status):
    handler, state = _scripted([status, GOOD_BODY])
    _use_transport(monkeypatch, handler)

    path = await sanctions._download_to_temp(URL)

    assert state["requests"] == 2
    assert _read(path) == GOOD_BODY
    assert sleeps == [http_client.BACKOFF_BASE ** 0]
    assert state["open_at_request"] == [0, 0], "the 429/503 response must be closed before retrying"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 503])
async def test_persistent_retryable_status_is_structured_transient(monkeypatch, sleeps, temp_paths, status):
    handler, state = _scripted([status] * http_client.MAX_RETRIES)
    _use_transport(monkeypatch, handler)

    with pytest.raises(ToolError) as exc_info:
        await sanctions._download_to_temp(URL)

    payload = _payload(exc_info)
    assert payload.error_category == "transient"
    assert payload.is_retryable is True
    assert payload.attempted == "GET https://lists.example.test/list.xml", "query string must be stripped"
    assert str(status) in payload.description
    assert state["requests"] == http_client.MAX_RETRIES
    assert sleeps == [http_client.BACKOFF_BASE ** a for a in range(http_client.MAX_RETRIES)]
    assert all(s.closed for s in state["streams"])
    assert len(temp_paths) == 1 and not os.path.exists(temp_paths[0]), "failed download leaked its temp file"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "category"),
    [(500, "unknown"), (404, "not_found"), (403, "auth_required"), (400, "validation")],
)
async def test_non_retryable_status_is_one_attempt(monkeypatch, sleeps, temp_paths, status, category):
    handler, state = _scripted([status, GOOD_BODY])
    _use_transport(monkeypatch, handler)

    with pytest.raises(ToolError) as exc_info:
        await sanctions._download_to_temp(URL)

    payload = _payload(exc_info)
    assert payload.error_category == category
    assert payload.is_retryable is False
    assert state["requests"] == 1
    assert sleeps == []
    assert all(s.closed for s in state["streams"])
    assert not os.path.exists(temp_paths[0])


@pytest.mark.asyncio
async def test_timeout_mid_body_then_200_keeps_only_the_successful_body(monkeypatch, sleeps, temp_paths):
    partial = TrackedStream([b"PARTIAL-" * 1000], exc=httpx.ReadTimeout("read timed out"))
    handler, state = _scripted([partial, GOOD_BODY])
    _use_transport(monkeypatch, handler)

    path = await sanctions._download_to_temp(URL)

    assert state["requests"] == 2
    assert partial.yielded == 1 and partial.closed
    assert _read(path) == GOOD_BODY, "the half-written body from the timed-out attempt must be truncated"
    assert sleeps == [http_client.BACKOFF_BASE ** 0]
    assert state["open_at_request"] == [0, 0]


@pytest.mark.asyncio
async def test_connect_timeout_then_200_succeeds(monkeypatch, sleeps, temp_paths):
    handler, state = _scripted([httpx.ConnectTimeout("connect timed out"), GOOD_BODY])
    _use_transport(monkeypatch, handler)

    path = await sanctions._download_to_temp(URL)

    assert state["requests"] == 2
    assert _read(path) == GOOD_BODY


@pytest.mark.asyncio
async def test_persistent_timeout_is_structured_transient(monkeypatch, sleeps, temp_paths):
    handler, state = _scripted([httpx.ReadTimeout("slow")] * http_client.MAX_RETRIES)
    _use_transport(monkeypatch, handler)

    with pytest.raises(ToolError) as exc_info:
        await sanctions._download_to_temp(URL)

    payload = _payload(exc_info)
    assert payload.error_category == "transient"
    assert payload.is_retryable is True
    assert state["requests"] == http_client.MAX_RETRIES
    assert sleeps == [http_client.BACKOFF_BASE ** a for a in range(http_client.MAX_RETRIES)]
    assert not os.path.exists(temp_paths[0])


@pytest.mark.asyncio
async def test_body_is_streamed_to_disk_chunk_by_chunk(monkeypatch, sleeps, temp_paths):
    """Memory stays bounded: each chunk is written as it arrives, not buffered."""
    sizes_seen: list[int] = []
    # Larger than Python's file buffer, so each write reaches the file immediately.
    chunk_size = 4 * io.DEFAULT_BUFFER_SIZE
    chunks = [b"a" * chunk_size, b"b" * chunk_size, b"c" * chunk_size]

    class Observing(TrackedStream):
        async def __aiter__(self):
            for chunk in self.chunks:
                # Before yielding the next chunk, everything earlier is already on disk.
                sizes_seen.append(os.path.getsize(temp_paths[0]))
                yield chunk

    stream = Observing(chunks)
    handler, _ = _scripted([stream])
    _use_transport(monkeypatch, handler)

    path = await sanctions._download_to_temp(URL)

    assert _read(path) == b"".join(chunks)
    assert sizes_seen == [0, chunk_size, 2 * chunk_size]
    assert stream.closed


@pytest.mark.asyncio
async def test_cancelled_download_removes_its_temp_file(monkeypatch, temp_paths):
    started = asyncio.Event()

    async def hang(request: httpx.Request) -> httpx.Response:
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    _use_transport(monkeypatch, hang)

    task = asyncio.create_task(sanctions._download_to_temp(URL))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(temp_paths) == 1 and not os.path.exists(temp_paths[0])


def test_sanctions_client_keeps_long_read_and_short_connect_timeouts():
    client = http_client.sanctions_client()
    assert client.timeout.read == 120.0
    assert client.timeout.connect == 15.0
    assert client.follow_redirects is True


# ---------------------------------------------------------------------------
# Parity with _request_with_retry
# ---------------------------------------------------------------------------

def _plain_handler(events):
    """Same script as _scripted, as plain buffered responses for _request_with_retry."""
    queue = list(events)

    def handler(request: httpx.Request) -> httpx.Response:
        event = queue.pop(0)
        if isinstance(event, Exception):
            raise event
        if isinstance(event, int):
            return httpx.Response(event, content=b"upstream error page")
        return httpx.Response(200, content=event)

    return handler


PARITY_SCRIPTS = [
    pytest.param([503, 503, 503], id="persistent-503"),
    pytest.param([429, 429, 429], id="persistent-429"),
    pytest.param([500], id="500"),
    pytest.param([503, 500], id="503-then-500"),
    pytest.param(["connect", "connect", "connect"], id="persistent-connect-error"),
    pytest.param(["read", "read", "read"], id="persistent-read-error"),
    pytest.param(["read", "read", 503], id="read-errors-then-503"),
    pytest.param([503, 429, "read"], id="statuses-then-read-error"),
    pytest.param(["connect", 429, GOOD_BODY], id="mixed-then-200"),
]


def _materialise(script):
    out = []
    for event in script:
        if event == "connect":
            out.append(httpx.ConnectError("refused"))
        elif event == "read":
            out.append(httpx.ReadError("reset"))
        else:
            out.append(event)
    return out


async def _outcome(coro, sleeps):
    sleeps.clear()
    try:
        await coro
    except ToolError as exc:
        payload = parse_error_payload(str(exc))
        assert payload is not None
        return ("error", payload.error_category, payload.is_retryable, payload.attempted), list(sleeps)
    return ("ok",), list(sleeps)


@pytest.mark.asyncio
@pytest.mark.parametrize("script", PARITY_SCRIPTS)
async def test_streaming_helper_matches_request_with_retry(sleeps, tmp_path, script):
    url = "https://lists.example.test/list.xml?token=secret"

    async with httpx.AsyncClient(transport=httpx.MockTransport(_plain_handler(_materialise(script)))) as c:
        expected = await _outcome(http_client._request_with_retry(c, "GET", url), sleeps)

    stream_handler, state = _scripted(_materialise(script))
    dest = tmp_path / "out.bin"
    async with httpx.AsyncClient(transport=httpx.MockTransport(stream_handler)) as c:
        actual = await _outcome(http_client._stream_to_file_with_retry(c, url, str(dest)), sleeps)

    assert actual == expected
    assert all(s.closed for s in state["streams"])
    if actual[0] == ("ok",):
        assert dest.read_bytes() == GOOD_BODY


# ---------------------------------------------------------------------------
# _build_index still isolates a failed list
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_build_index_logs_structured_failure_and_keeps_healthy_lists(monkeypatch, sleeps, temp_paths, capsys):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "bad.example.test":
            return httpx.Response(503)
        return httpx.Response(200, content=b"ok")

    _use_transport(monkeypatch, handler)

    parsed_paths: list[str] = []

    def parser(label):
        def parse(path):
            parsed_paths.append(path)
            assert _read(path) == b"ok"
            yield sanctions.Record(label, "ACME TRADING LTD", False, "entity", "Russia", "R1", None)
        return parse

    monkeypatch.setattr(sanctions, "_SOURCES", [
        ("OFAC (US)", "https://good.example.test/sdn.xml", parser("OFAC (US)")),
        ("EU", "https://bad.example.test/eu.xml", parser("EU")),
    ])

    index, ok, loaded_at = await sanctions._build_index()

    assert ok == ["OFAC (US)"]
    assert set(loaded_at) == {"OFAC (US)"}
    assert all(r.source == "OFAC (US)" for recs in index.values() for r in recs)
    err = capsys.readouterr().err
    assert "FAILED to load EU: ToolError:" in err
    assert '"error_category":"transient"' in err
    # Successful download parsed then deleted; failed download never left a file.
    assert len(parsed_paths) == 1
    assert all(not os.path.exists(p) for p in temp_paths)
