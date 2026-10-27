"""/mcp is stateless POST-only on the real FastMCP 4 app: GET is refused with 405.

The synthetic GET stream was removed after UK Legal interoperability testing showed
the clients tested handshake, discover tools and call them with a native 405. The
Accept normaliser is kept as a separate concern and is exercised here too.
"""

import pytest
from fastmcp.server.http import create_streamable_http_app
from starlette.testclient import TestClient

import server

INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
}


def _client():
    app = server._AcceptNormalizer(create_streamable_http_app(
        server.mcp, streamable_http_path="/mcp", json_response=True, stateless_http=True,
    ))
    return TestClient(app)


def test_get_mcp_is_refused_with_405_and_allows_post():
    with _client() as c:
        resp = c.get("/mcp", headers={"Accept": "text/event-stream"})

    assert resp.status_code == 405
    assert "POST" in resp.headers["allow"]


def test_post_initialize_succeeds_without_any_get_stream():
    with _client() as c:
        resp = c.post(
            "/mcp", json=INIT,
            headers={"Accept": "application/json, text/event-stream", "Content-Type": "application/json"},
        )

    assert resp.status_code == 200
    assert resp.json()["result"]["serverInfo"]["name"] == "uk_due_diligence_mcp"


def test_accept_normaliser_still_admits_a_bare_accept_header():
    with _client() as c:
        resp = c.post(
            "/mcp", json=INIT,
            headers={"Accept": "*/*", "Content-Type": "application/json"},
        )

    assert resp.status_code == 200


def test_health_reports_status_only():
    with _client() as c:
        resp = c.get("/health")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
