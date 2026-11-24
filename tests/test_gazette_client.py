"""
Offline tests for the urllib-backed Gazette client and the gazette_notice fetch.

thegazette.co.uk answers 403 to httpx from the Fly machine but 200 to urllib.
gazette_notice reads GET /notice/{id} with Accept: application/xml (#98); the
robots-disallowed data.* and ?view=linked-data routes are never requested, and
a 429 still opens the shared #107 pause. These tests run against a local HTTP
stub, so they check what the client sends and how failures map to the fleet
error taxonomy, without touching the real Gazette site.
"""

from __future__ import annotations

import json
import logging
import re
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
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
                    "raw_path": self.path,
                    "path": parsed.path,
                    "query": {k: v[0] for k, v in parse_qs(parsed.query).items()},
                    "accept": self.headers.get("Accept"),
                    "user_agent": self.headers.get("User-Agent"),
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
async def test_client_sends_project_user_agent_alongside_caller_headers(stub):
    # Gazette fair-use policy: identify the client (#86). The caller's Accept
    # must survive the merge, and urllib's default "Python-urllib/x" must not.
    async with _GazetteClient() as client:
        await client.request(
            "GET", f"{stub.base}/insolvency/notice/data.json",
            headers={"Accept": "application/json"},
        )
        await client.request("GET", f"{stub.base}/notice/1")

    assert [r["user_agent"] for r in stub.seen] == [http_client.USER_AGENT] * 2
    assert stub.seen[0]["accept"] == "application/json"


@pytest.mark.asyncio
async def test_relative_path_joins_gazette_base(stub, monkeypatch):
    monkeypatch.setattr(http_client, "GAZETTE_BASE", stub.base)

    async with _GazetteClient() as client:
        resp = await client.request("GET", "/notice/12345", params={"q": "1"})

    assert resp.status_code == 200
    assert stub.seen[0]["path"] == "/notice/12345"


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


PAUSE_RE = re.compile(
    r"^Gazette requests paused by this server after an upstream 429; "
    r"retry after (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z) \((\d+)s\)\.$"
)


def _assert_local_pause(exc: ToolError, attempted: str) -> int:
    """Assert a structured local-pause error and return its stated seconds."""
    from datetime import datetime, timezone

    payload = parse_error_payload(str(exc))
    assert payload is not None, f"not a structured fleet error: {exc}"
    assert payload.error_category == "transient"
    assert payload.is_retryable is True
    assert payload.attempted == attempted
    assert "on every attempt" not in payload.description
    match = PAUSE_RE.match(payload.description)
    assert match, payload.description
    retry_at = datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    seconds = int(match.group(2))
    # The stated time is about `seconds` from now, never earlier than the pause end.
    delta = (retry_at - datetime.now(timezone.utc)).total_seconds()
    assert seconds - 2 <= delta <= seconds + 2
    return seconds


@pytest.mark.asyncio
async def test_retry_after_fails_fast_and_sends_nothing_during_window(stub, recorded_sleeps):
    stub.respond = lambda _req: (429, b"", "text/html", {"Retry-After": "900"})

    async with _GazetteClient() as client:
        with pytest.raises(ToolError) as exc_info:
            await _request_with_retry(client, "GET", f"{stub.base}/x", params={})
    # One real request reached Gazette; its 429 opened a 900-second pause, and
    # the retry inside the pause reports the pause instead of a made-up 429.
    seconds = _assert_local_pause(exc_info.value, f"GET {stub.base}/x")
    assert 899 <= seconds <= 900
    assert len(stub.seen) == 1
    # Waiting stays short: no sleep anywhere near the Retry-After period.
    assert max(recorded_sleeps, default=0.0) < 10.0

    # A later call inside the pause fails fast and sends nothing.
    async with _GazetteClient() as client:
        with pytest.raises(ToolError) as exc_info:
            await _request_with_retry(client, "GET", f"{stub.base}/y", params={"q": "1"})
    seconds = _assert_local_pause(exc_info.value, f"GET {stub.base}/y")
    assert 1 <= seconds <= 900
    assert len(stub.seen) == 1


@pytest.mark.asyncio
async def test_real_429_logs_path_and_retry_after(stub, no_backoff, caplog):
    stub.respond = lambda _req: (429, b"", "text/html", {"Retry-After": "900"})

    with caplog.at_level(logging.WARNING, logger="http_client"):
        async with _GazetteClient() as client:
            with pytest.raises(ToolError):
                await _request_with_retry(
                    client, "GET", f"{stub.base}/notice/1", params={"q": "x"},
                )
            # Locally paused calls send nothing and log nothing.
            with pytest.raises(ToolError):
                await client.request("GET", f"{stub.base}/other", params={})

    warnings = [r for r in caplog.records if r.name == "http_client" and r.levelno == logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "/notice/1" in message
    assert "Retry-After=900" in message
    assert "q=" not in message
    assert len(stub.seen) == 1


@pytest.mark.asyncio
async def test_real_429_without_retry_after_logs_missing(stub, no_backoff, caplog):
    stub.respond = lambda _req: (429, b"", "text/html")

    with caplog.at_level(logging.WARNING, logger="http_client"):
        async with _GazetteClient() as client:
            await client.request("GET", f"{stub.base}/z", params={})

    [record] = [r for r in caplog.records if r.name == "http_client"]
    assert "/z" in record.getMessage()
    assert "Retry-After=<missing>" in record.getMessage()


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


# ---------------------------------------------------------------------------
# gazette_notice: GET /notice/{id}, Accept: application/xml (#98)
# ---------------------------------------------------------------------------

FIXTURES = Path(__file__).parent / "fixtures" / "gazette"
DISALLOWED = re.compile(r"data\.|view=linked-data")
XML = "application/xml;charset=UTF-8"


def _fixture(notice_id: str) -> bytes:
    return (FIXTURES / f"notice-{notice_id}.xml").read_bytes()


def _serve(stub, monkeypatch, body: bytes, ctype: str = XML, code: int = 200) -> None:
    monkeypatch.setattr(http_client, "GAZETTE_BASE", stub.base)
    stub.respond = lambda _req: (code, body, ctype)


def _assert_robots_compliant(stub) -> None:
    for req in stub.seen:
        assert not DISALLOWED.search(req["raw_path"]), req["raw_path"]


async def _fetch_error(notice_id: str):
    with pytest.raises(ToolError) as exc_info:
        await gazette._fetch_gazette_notice(notice_id)
    payload = parse_error_payload(str(exc_info.value))
    assert payload is not None, f"not a structured fleet error: {exc_info.value}"
    return payload


@pytest.mark.asyncio
@pytest.mark.parametrize("notice_id", ["4420724", "L-57793-2040"])
async def test_notice_request_shape(stub, monkeypatch, notice_id):
    _serve(stub, monkeypatch, _fixture(notice_id))

    await gazette._fetch_gazette_notice(f"  {notice_id}\n")

    [req] = stub.seen
    assert req["path"] == f"/notice/{notice_id}"
    assert req["raw_path"] == f"/notice/{notice_id}"  # no query string, not even a bare "?"
    assert req["query"] == {}
    assert req["accept"] == "application/xml"
    assert req["user_agent"] == http_client.USER_AGENT
    _assert_robots_compliant(stub)


def test_modern_notice_gives_every_field():
    result = gazette._parse_notice("4420724", _fixture("4420724"), "GET /notice/4420724")

    assert result.model_dump(exclude={"text"}) == {
        "notice_id": "4420724",
        "notice_url": "https://www.thegazette.co.uk/notice/4420724",
        "title": "WILKO LIMITED",
        "notice_code": "2410",
        "notice_type": "Appointment of Administrators",
        "category": "Corporate Insolvency",
        "publication_date": "2023-08-14",
        "company_number": "00365335",
        "company_name": "WILKO LIMITED",
        "previous_company_name": "Wilko Retail Limited",
    }
    assert result.text.startswith(
        "In the High Court of Justice Business and Property Courts in Birmingham "
        "WILKO LIMITED (Company Number 00365335 )"
    )
    # The hidden rdfa-data enrichment is neither in the text nor a field source.
    assert "HIDDEN" not in result.text
    assert "ZZ1 1ZZ" not in result.text
    assert "99999999" not in result.text


def test_modern_notice_with_doctype_parses():
    result = gazette._parse_notice("4953471", _fixture("4953471"), "GET /notice/4953471")

    assert result.notice_code == "2443"
    assert result.publication_date == "2025-07-01"
    assert result.previous_company_name is None
    assert "HIDDEN" not in result.text
    # Text after the skipped rdfa-data div is kept.
    assert result.text.endswith("liquidation appointment.")


def test_legacy_notice_gives_code_type_date_and_null_company_number():
    result = gazette._parse_notice(
        "L-57793-2040", _fixture("L-57793-2040"), "GET /notice/L-57793-2040",
    )

    assert result.title == "Winding-Up Orders"
    assert result.notice_code == "2452"
    assert result.notice_type == "Winding-Up Orders"
    assert result.category == "Corporate Insolvency"
    # No @content on the property: the first visible <time datetime>, not the hidden one.
    assert result.publication_date == "2005-10-21"
    # Never parsed out of the body text, although the text carries one.
    assert result.company_number is None
    assert result.company_name is None
    assert result.previous_company_name is None
    assert "04980683" in result.text
    assert result.text.startswith("Name of Company— CARILLION PLUMBING & HEATING SERVICES LTD .")
    assert "HIDDEN" not in result.text and "hidden date" not in result.text


def _xhtml(content: str, head: str = "") -> bytes:
    return (
        '<html xmlns="http://www.w3.org/1999/xhtml"><head>' + head + "</head>"
        "<body><article>" + content + "</article></body></html>"
    ).encode()


def test_text_keeps_mixed_content_order_and_skipped_tails():
    body = _xhtml(
        '<div class="notice content">A <b>B <i>C</i> D</b>'
        '<div class="x rdfa-data">hidden <span>deeper</span></div> E  \n F'
        '<span property="gazorg:companyNumber"> </span></div>'
    )
    result = gazette._parse_notice("1", body, "GET /notice/1")

    assert result.text == "A B C D E F"
    # Absent or blank optionals are null, never "".
    assert result.title is None
    assert result.company_number is None
    assert result.notice_code is None
    assert result.publication_date is None


def test_text_separates_adjacent_block_elements():
    # Real notices (e.g. 4953471) put each line in its own <p> with no whitespace
    # between them; the words must not run together ("00365335Name").
    body = _xhtml(
        '<div class="content"><p>Company Number: <span>00365335</span></p>'
        "<p>Name of Company: WL REALISATIONS (2023) LIMITED</p>Trading<br/>Name: Wilko"
        "<ul><li>one</li><li>two</li></ul>in<b>line</b></div>"
    )
    result = gazette._parse_notice("1", body, "GET /notice/1")

    assert result.text == (
        "Company Number: 00365335 Name of Company: WL REALISATIONS (2023) LIMITED "
        "Trading Name: Wilko one two inline"
    )


@pytest.mark.parametrize("ctype", ["application/xml", "Application/XML; charset=utf-8", XML])
@pytest.mark.asyncio
async def test_application_xml_media_type_variants_are_accepted(stub, monkeypatch, ctype):
    _serve(stub, monkeypatch, _fixture("4420724"), ctype)

    result = await gazette._fetch_gazette_notice("4420724")

    assert result.company_number == "00365335"


@pytest.mark.parametrize(
    "ctype", ["text/html", "text/html; charset=utf-8", "application/xhtml+xml", "text/xml",
              "application/xml-foo", "application/json"],
)
@pytest.mark.asyncio
async def test_other_media_types_are_transient(stub, monkeypatch, ctype):
    # Even a notice-shaped body is refused: the HTML site page is not parsed.
    _serve(stub, monkeypatch, _fixture("4420724"), ctype)

    payload = await _fetch_error("4420724")

    assert payload.error_category == "transient"
    assert payload.is_retryable is True
    assert payload.attempted == "GET /notice/4420724"
    assert "application/xml" in payload.description
    assert len(stub.seen) == 1


DEEP = 2000
UTF16_ENTITY = (
    '<?xml version="1.0" encoding="UTF-16"?>'
    '<!DOCTYPE html [<!ENTITY x "EXPANDED">]>'
    '<html xmlns="http://www.w3.org/1999/xhtml"><body><article>'
    '<div class="content">&x;</div></article></body></html>'
).encode("utf-16")
LAUGHS = (
    b'<?xml version="1.0"?><!DOCTYPE html [<!ENTITY a "aaaaaaaaaa">'
    b'<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
    b'<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">]>'
    b'<html xmlns="http://www.w3.org/1999/xhtml"><body><article>'
    b'<div class="content">&c;</div></article></body></html>'
)
EXTERNAL = (
    b'<?xml version="1.0"?><!DOCTYPE html [<!ENTITY ext SYSTEM "file:///etc/passwd">]>'
    b'<html xmlns="http://www.w3.org/1999/xhtml"><body><article>'
    b'<div class="content">&ext;</div></article></body></html>'
)


@pytest.mark.parametrize(
    "body, problem",
    [
        pytest.param(b"<html><body><article><div class='content'>", "not well-formed", id="truncated"),
        pytest.param(b"Service unavailable", "not well-formed", id="not-xml"),
        pytest.param(_xhtml('<div class="summary">no body</div>'), "div.content", id="no-content"),
        pytest.param(
            b'<html><body><article><div class="content">x</div></article></body></html>',
            "div.content", id="no-xhtml-namespace",
        ),
        pytest.param(UTF16_ENTITY, "entity", id="utf16-internal-entity"),
        pytest.param(LAUGHS, "entity", id="billion-laughs"),
        pytest.param(EXTERNAL, "entity", id="external-entity"),
        pytest.param(
            _xhtml('<div class="content">' + "<span>" * DEEP + "x" + "</span>" * DEEP + "</div>"),
            "deeper", id="deep-nesting",
        ),
    ],
)
@pytest.mark.asyncio
async def test_unusable_bodies_are_transient(stub, monkeypatch, body, problem):
    _serve(stub, monkeypatch, body)

    payload = await _fetch_error("4420724")

    assert payload.error_category == "transient"
    assert payload.is_retryable is True
    assert payload.attempted == "GET /notice/4420724"
    assert problem in payload.description
    assert "EXPANDED" not in payload.description


def test_plain_doctype_is_not_an_unsafe_entity():
    body = (
        b'<?xml version="1.0"?><!DOCTYPE html>'
        + _xhtml('<div class="content">ok</div>')
    )
    assert gazette._parse_notice("1", body, "GET /notice/1").text == "ok"


@pytest.mark.parametrize(
    "bad_id", ["abc", "12/34", "  ", "l-57793-2040", "L-57793", "LL-1-2", "4420724x",
               "L-1-2-", "1 2", "../1", "4420724?view=linked-data", "٤٤٢"],
)
@pytest.mark.asyncio
async def test_bad_notice_id_is_validation_and_sends_nothing(stub, monkeypatch, bad_id):
    _serve(stub, monkeypatch, _fixture("4420724"))

    payload = await _fetch_error(bad_id)

    assert payload.error_category == "validation"
    assert payload.is_retryable is False
    assert stub.seen == []


@pytest.mark.asyncio
async def test_tool_accepts_padded_numeric_and_legacy_ids(stub, monkeypatch):
    from fastmcp import Client

    from server import mcp

    replies = {
        "/notice/4420724": _fixture("4420724"),
        "/notice/L-57793-2040": _fixture("L-57793-2040"),
        "/notice/E-26343-2431-168": _fixture("L-57793-2040"),
    }
    monkeypatch.setattr(http_client, "GAZETTE_BASE", stub.base)
    stub.respond = lambda req: (200, replies[req["path"]], XML)

    async with Client(mcp) as client:
        modern = await client.call_tool("gazette_notice", {"notice_id": " 4420724 "})
        legacy = await client.call_tool("gazette_notice", {"notice_id": "L-57793-2040"})
        long_legacy = await client.call_tool(
            "gazette_notice", {"notice_id": "      E-26343-2431-168      "},
        )
        for bad in ("abc", "12/34"):
            with pytest.raises(ToolError) as exc_info:
                await client.call_tool("gazette_notice", {"notice_id": bad})
            assert _error_category(exc_info.value) == "validation"

    assert modern.structured_content["company_number"] == "00365335"
    assert modern.structured_content["notice_id"] == "4420724"
    assert legacy.structured_content["company_number"] is None
    assert legacy.structured_content["publication_date"] == "2005-10-21"
    assert long_legacy.structured_content["notice_id"] == "E-26343-2431-168"
    assert long_legacy.structured_content["notice_url"] == (
        "https://www.thegazette.co.uk/notice/E-26343-2431-168"
    )
    assert [r["raw_path"] for r in stub.seen] == list(replies)
    _assert_robots_compliant(stub)


@pytest.mark.asyncio
async def test_notice_resource_returns_model_json(stub, monkeypatch):
    from fastmcp import Client

    from server import mcp

    _serve(stub, monkeypatch, _fixture("4420724"))

    async with Client(mcp) as client:
        [content] = await client.read_resource("notice://4420724")

    data = json.loads(content.text)
    assert set(data) == set(gazette.GazetteNoticeResult.model_fields)
    assert data["company_number"] == "00365335"
    assert data["text"].startswith("In the High Court of Justice")
    assert "@context" not in data
    [req] = stub.seen
    assert req["raw_path"] == "/notice/4420724"


@pytest.mark.asyncio
async def test_fetch_notice_returns_text_as_content(stub, monkeypatch):
    from fastmcp import Client

    from server import mcp

    _serve(stub, monkeypatch, _fixture("4420724"))

    async with Client(mcp) as client:
        result = await client.call_tool("fetch", {"id": "notice:4420724"})

    data = result.structured_content
    expected_text = gazette._parse_notice(
        "4420724", _fixture("4420724"), "GET /notice/4420724",
    ).text
    assert data["id"] == "notice:4420724"
    assert data["title"] == "Appointment of Administrators"
    assert data["content"] == expected_text
    assert data["metadata"] == {
        "source": "gazette",
        "notice_code": "2410",
        "publication_date": "2023-08-14",
        "company_number": "00365335",
        "notice_url": "https://www.thegazette.co.uk/notice/4420724",
    }
    [req] = stub.seen
    assert req["raw_path"] == "/notice/4420724"


@pytest.mark.asyncio
async def test_fetch_legacy_notice_title_falls_back(stub, monkeypatch):
    from fastmcp import Client

    from server import mcp

    body = _xhtml('<div class="content">wording</div>', head="<title>Legacy title</title>")
    _serve(stub, monkeypatch, body)

    async with Client(mcp) as client:
        titled = await client.call_tool("fetch", {"id": "notice:L-1-2"})
        stub.respond = lambda _req: (200, _xhtml('<div class="content">wording</div>'), XML)
        untitled = await client.call_tool("fetch", {"id": "notice:L-1-2"})

    assert titled.structured_content["title"] == "Legacy title"
    assert untitled.structured_content["title"] == "Gazette notice L-1-2"
    assert untitled.structured_content["metadata"]["company_number"] is None


@pytest.mark.asyncio
async def test_notice_429_still_opens_the_shared_pause(stub, monkeypatch, no_backoff):
    # #107 narrowed, not removed: a 429 on /notice/{id} pauses every Gazette
    # call, and the next one fails locally with the retry time.
    from fastmcp import Client

    from server import mcp

    monkeypatch.setattr(http_client, "GAZETTE_BASE", stub.base)
    stub.respond = lambda _req: (429, b"", "text/html", {"Retry-After": "900"})

    with pytest.raises(ToolError) as exc_info:
        await gazette._fetch_gazette_notice("4420724")
    seconds = _assert_local_pause(exc_info.value, "GET /notice/4420724")
    assert 899 <= seconds <= 900
    assert len(stub.seen) == 1
    assert http_client._gazette_blocked_until > 0

    async with Client(mcp) as client:
        with pytest.raises(ToolError) as exc_info:
            await client.call_tool("gazette_insolvency", {"name": "carillion"})
    _assert_local_pause(exc_info.value, "GET /insolvency/notice/data.json")
    assert len(stub.seen) == 1
    _assert_robots_compliant(stub)


@pytest.mark.asyncio
async def test_notice_404_is_structured(stub, monkeypatch, no_backoff):
    _serve(stub, monkeypatch, b"<html>not found</html>", "text/html", code=404)

    payload = await _fetch_error("4420724")

    assert payload.error_category == "not_found"
    assert len(stub.seen) == 1
