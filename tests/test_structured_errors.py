"""structured_errors: classifier branches and middleware behaviour (#41).

The classifier turns an exception that escaped a tool without a fleet payload
into one. The middleware runs it on every tool-call failure, except FastMCP's
NotFoundError for an unregistered tool name, which mcpfleet-obs folds into the
`_unknown_` metric label by exception type.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET

import httpx
import pytest
from fastmcp import Client
from fastmcp.exceptions import NotFoundError, ToolError
from fastmcp.exceptions import ValidationError as FastMCPValidationError
from mcpfleet_obs import parse_error_payload, raise_tool_error
from prometheus_client import REGISTRY
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

import structured_errors
from server import mcp
from structured_errors import StructuredErrorMiddleware, raise_structured_from_unhandled

METRIC = "uk_due_diligence_tool_calls_total"


def _classify(exc: BaseException, attempted: str = "some_tool"):
    with pytest.raises(ToolError) as exc_info:
        raise_structured_from_unhandled(exc, attempted=attempted)
    payload = parse_error_payload(str(exc_info.value))
    assert payload is not None, str(exc_info.value)
    assert payload.attempted == attempted
    return payload, exc_info.value


def _pydantic_error() -> PydanticValidationError:
    class Args(BaseModel):
        name: str

    try:
        Args(name=None)
    except PydanticValidationError as exc:
        return exc
    raise AssertionError("expected a pydantic ValidationError")


def _payload_tool_error(category: str = "transient") -> ToolError:
    try:
        raise_tool_error(category, is_retryable=True, attempted="GET https://x/y",
                         description="forced")
    except ToolError as exc:
        return exc
    raise AssertionError("raise_tool_error must raise")


def _chained(outer: BaseException, inner: BaseException) -> BaseException:
    outer.__cause__ = inner
    return outer


# --- classifier: one test per branch ----------------------------------------

def test_existing_payload_in_the_chain_is_reraised_verbatim():
    inner = _payload_tool_error("not_found")
    outer = _chained(RuntimeError("wrapped"), inner)
    payload, raised = _classify(outer, attempted="GET https://x/y")
    assert str(raised) == str(inner)
    assert payload.error_category == "not_found"
    assert raised.__cause__ is outer


def test_payload_wins_over_a_classifiable_link_earlier_in_the_chain():
    inner = _payload_tool_error("auth_required")
    outer = _chained(httpx.ReadTimeout("slow"), inner)
    payload, _ = _classify(outer, attempted="GET https://x/y")
    assert payload.error_category == "auth_required"


def test_pydantic_validation_error_is_validation_with_its_message():
    pyd = _pydantic_error()
    payload, _ = _classify(pyd)
    assert payload.error_category == "validation"
    assert payload.is_retryable is False
    assert payload.description == f"Arguments rejected by the tool's input schema: {pyd}"


def test_fastmcp_validation_error_is_classified_from_its_pydantic_cause():
    pyd = _pydantic_error()
    outer = _chained(FastMCPValidationError(str(pyd)), pyd)
    payload, _ = _classify(outer)
    assert payload.error_category == "validation"
    assert payload.is_retryable is False


def test_not_found_error_is_not_found():
    payload, _ = _classify(NotFoundError("Unknown tool: 'nope'"))
    assert payload.error_category == "not_found"
    assert payload.is_retryable is False
    assert "Unknown tool: 'nope'" in payload.description


def test_json_decode_error_is_transient():
    try:
        json.loads("<html>")
    except json.JSONDecodeError as exc:
        payload, _ = _classify(exc)
    assert payload.error_category == "transient"
    assert payload.is_retryable is True
    assert "JSONDecodeError" in payload.description


def test_elementtree_parse_error_is_transient():
    try:
        ET.fromstring("<html><body>")
    except ET.ParseError as exc:
        payload, _ = _classify(exc)
    assert payload.error_category == "transient"
    assert payload.is_retryable is True
    assert "ParseError" in payload.description


def test_httpx_timeout_is_transient_via_the_fleet_http_taxonomy():
    request = httpx.Request("GET", "https://x/y")
    payload, _ = _classify(httpx.ReadTimeout("slow", request=request))
    assert payload.error_category == "transient"
    assert payload.is_retryable is True


def test_httpx_error_deep_in_the_chain_is_found():
    request = httpx.Request("GET", "https://x/y")
    outer = _chained(ToolError("Error calling tool"), httpx.ConnectError("refused", request=request))
    payload, _ = _classify(outer)
    assert payload.error_category == "transient"


def test_anything_else_is_unknown_with_type_and_message():
    payload, _ = _classify(KeyError("missing"))
    assert payload.error_category == "unknown"
    assert payload.is_retryable is False
    assert payload.description == "Unexpected error: KeyError: 'missing'"


# --- classifier: chain traversal --------------------------------------------

def test_context_links_are_walked_when_there_is_no_cause():
    pyd = _pydantic_error()
    outer = RuntimeError("during handling")
    outer.__context__ = pyd
    payload, _ = _classify(outer)
    assert payload.error_category == "validation"


def test_a_cyclic_chain_terminates():
    a = RuntimeError("a")
    b = RuntimeError("b")
    a.__cause__ = b
    b.__cause__ = a
    payload, _ = _classify(a)
    assert payload.error_category == "unknown"


def test_links_past_the_bound_are_not_inspected():
    request = httpx.Request("GET", "https://x/y")
    deepest: BaseException = httpx.ReadTimeout("slow", request=request)
    for i in range(structured_errors.MAX_CHAIN):
        deepest = _chained(RuntimeError(f"wrap {i}"), deepest)
    payload, _ = _classify(deepest)
    assert payload.error_category == "unknown"

    # One link fewer and the timeout is within reach.
    payload, _ = _classify(deepest.__cause__)
    assert payload.error_category == "transient"


# --- classifier: category order across a mixed chain -----------------------

def test_inner_pydantic_error_beats_an_outer_httpx_error():
    request = httpx.Request("GET", "https://x/y")
    outer = _chained(httpx.ReadTimeout("slow", request=request), _pydantic_error())
    payload, _ = _classify(outer)
    assert payload.error_category == "validation"
    assert payload.is_retryable is False


def test_inner_not_found_beats_an_outer_json_decode_error():
    try:
        json.loads("<html>")
    except json.JSONDecodeError as exc:
        outer = _chained(exc, NotFoundError("Unknown tool: 'nope'"))
    payload, _ = _classify(outer)
    assert payload.error_category == "not_found"
    assert payload.is_retryable is False


def test_inner_parse_error_beats_an_outer_httpx_error():
    request = httpx.Request("GET", "https://x/y")
    response = httpx.Response(404, request=request)
    outer = _chained(
        httpx.HTTPStatusError("not found", request=request, response=response),
        ET.ParseError("bad xml"),
    )
    payload, _ = _classify(outer)
    assert payload.error_category == "transient"
    assert "ParseError" in payload.description


def test_inner_pydantic_error_beats_outer_not_found_and_parse_errors():
    inner = _chained(ET.ParseError("bad xml"), _pydantic_error())
    outer = _chained(NotFoundError("Unknown tool: 'nope'"), inner)
    payload, _ = _classify(outer)
    assert payload.error_category == "validation"


def test_category_order_respects_the_chain_bound():
    request = httpx.Request("GET", "https://x/y")
    deepest: BaseException = _pydantic_error()
    for i in range(structured_errors.MAX_CHAIN - 1):
        deepest = _chained(RuntimeError(f"wrap {i}"), deepest)
    outer = _chained(httpx.ReadTimeout("slow", request=request), deepest)
    # The pydantic error is link MAX_CHAIN + 1, out of reach: the timeout wins.
    payload, _ = _classify(outer)
    assert payload.error_category == "transient"


# --- middleware ---------------------------------------------------------------

class _Message:
    name = "some_tool"


class _Context:
    message = _Message()


async def _run_middleware(exc: BaseException):
    async def call_next(_context):
        raise exc

    return await StructuredErrorMiddleware().on_call_tool(_Context(), call_next)


@pytest.mark.asyncio
async def test_middleware_returns_a_successful_result_unchanged():
    sentinel = object()

    async def call_next(_context):
        return sentinel

    assert await StructuredErrorMiddleware().on_call_tool(_Context(), call_next) is sentinel


@pytest.mark.asyncio
async def test_middleware_passes_not_found_through():
    exc = NotFoundError("Unknown tool: 'nope'")
    with pytest.raises(NotFoundError) as exc_info:
        await _run_middleware(exc)
    assert exc_info.value is exc


@pytest.mark.asyncio
async def test_middleware_passes_a_structured_tool_error_through_unchanged():
    exc = _payload_tool_error()
    with pytest.raises(ToolError) as exc_info:
        await _run_middleware(exc)
    assert exc_info.value is exc


@pytest.mark.asyncio
async def test_middleware_classifies_a_prose_tool_error():
    request = httpx.Request("GET", "https://x/y")
    exc = _chained(ToolError("Error calling tool 'x'"), httpx.ReadTimeout("slow", request=request))
    with pytest.raises(ToolError) as exc_info:
        await _run_middleware(exc)
    payload = parse_error_payload(str(exc_info.value))
    assert payload is not None
    assert payload.error_category == "transient"
    assert payload.attempted == "some_tool"


@pytest.mark.asyncio
async def test_middleware_classifies_a_bare_exception_as_unknown():
    with pytest.raises(ToolError) as exc_info:
        await _run_middleware(ValueError("boom"))
    payload = parse_error_payload(str(exc_info.value))
    assert payload is not None
    assert payload.error_category == "unknown"
    assert payload.attempted == "some_tool"


@pytest.mark.asyncio
async def test_middleware_does_not_intercept_base_exceptions():
    class _Stop(BaseException):
        pass

    with pytest.raises(_Stop):
        await _run_middleware(_Stop())


# --- through the real server ------------------------------------------------

def _count(tool: str, category: str) -> float:
    """Sum tool_calls_total over every client/ip_hash/source for tool+category."""
    total = 0.0
    for family in REGISTRY.collect():
        for sample in family.samples:
            if (sample.name == METRIC
                    and sample.labels.get("tool") == tool
                    and sample.labels.get("error_category") == category):
                total += sample.value
    return total


@pytest.mark.asyncio
async def test_unregistered_tool_name_still_raises_fastmcp_not_found():
    with pytest.raises(NotFoundError):
        await mcp.call_tool("no_such_tool", {})


@pytest.mark.asyncio
async def test_unregistered_tool_name_is_not_a_fleet_payload_to_the_client():
    before = _count("_unknown_", "not_found")
    async with Client(mcp) as client:
        result = await client.call_tool("no_such_tool", {}, raise_on_error=False)
    assert result.is_error
    assert parse_error_payload(result.content[0].text) is None
    assert _count("_unknown_", "not_found") == before + 1


@pytest.mark.asyncio
async def test_schema_rejection_is_counted_as_validation():
    before = _count("sanctions_screen", "validation")
    async with Client(mcp) as client:
        result = await client.call_tool(
            "sanctions_screen", {"name": "Rosneft", "entity_type": "company"},
            raise_on_error=False,
        )
    assert result.is_error
    payload = parse_error_payload(result.content[0].text)
    assert payload is not None
    assert payload.error_category == "validation"
    assert payload.is_retryable is False
    assert _count("sanctions_screen", "validation") == before + 1
