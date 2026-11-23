"""
structured_errors.py — classify exceptions that escape a tool unstructured (#41).

Ported from uk-legal-mcp (src/deps.py raise_structured_from_unhandled and
src/gateway.py StructuredErrorMiddleware, #105 there), minus its curl_cffi,
lxml and LegislationUpstreamError cases, which do not exist here.

The main case is arguments the input schema rejects: FastMCP 4.1.8 wraps the
pydantic error as fastmcp.exceptions.ValidationError and re-raises it, so the
client gets pydantic's plain text with isError and no error_category. The
middleware turns that, and anything else that reaches it without a fleet
payload, into a structured fleet error.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from typing import NoReturn

import httpx
from fastmcp.exceptions import NotFoundError, ToolError
from fastmcp.server.middleware import Middleware, MiddlewareContext
from mcpfleet_obs import parse_error_payload, raise_http_tool_error, raise_tool_error
from pydantic import ValidationError as PydanticValidationError

# How many __cause__/__context__ links to inspect, the exception itself included.
MAX_CHAIN = 8


def _chain(exc: BaseException) -> list[BaseException]:
    """exc and its cause/context links, bounded and cycle-safe."""
    chain: list[BaseException] = []
    cur: BaseException | None = exc
    while cur is not None and len(chain) < MAX_CHAIN and not any(cur is c for c in chain):
        chain.append(cur)
        cur = cur.__cause__ or cur.__context__
    return chain


def _first(
    chain: list[BaseException], types: type[BaseException] | tuple[type[BaseException], ...]
) -> BaseException | None:
    """The outermost link in chain that is an instance of types, if any."""
    return next((link for link in chain if isinstance(link, types)), None)


def raise_structured_from_unhandled(exc: BaseException, *, attempted: str) -> NoReturn:
    """Re-raise an exception that escaped a tool as a structured fleet error.

    Walks the cause/context chain (FastMCP keeps the pydantic error as the
    __cause__ of its own ValidationError). Precedence across the whole chain:

    1. a link whose text is already a fleet payload -> re-raised verbatim
    2. pydantic ValidationError -> validation, not retryable
    3. FastMCP NotFoundError -> not_found, not retryable
    4. JSONDecodeError / ElementTree.ParseError -> transient, retryable
       (an unparseable upstream body, typically an error page served as 200)
    5. httpx.HTTPError -> raise_http_tool_error's taxonomy
    6. otherwise -> unknown, not retryable, with the type name and message
    """
    chain = _chain(exc)
    # A payload raised deeper down is the real answer: surface it unchanged.
    for link in chain:
        if parse_error_payload(str(link)) is not None:
            raise ToolError(str(link)) from exc
    # Each category in turn across the whole chain, so an inner link of a
    # higher-precedence category beats an outer link of a lower one.
    pyd = _first(chain, PydanticValidationError)
    if pyd is not None:
        raise_tool_error(
            "validation",
            is_retryable=False,
            attempted=attempted,
            description=f"Arguments rejected by the tool's input schema: {pyd}",
        )
    not_found = _first(chain, NotFoundError)
    if not_found is not None:
        raise_tool_error(
            "not_found",
            is_retryable=False,
            attempted=attempted,
            description=f"{not_found} — check the name against the server's tool list.",
        )
    unparseable = _first(chain, (json.JSONDecodeError, ET.ParseError))
    if unparseable is not None:
        raise_tool_error(
            "transient",
            is_retryable=True,
            attempted=attempted,
            description=(
                f"Upstream returned a body that could not be parsed "
                f"({type(unparseable).__name__}) — typically an error or maintenance page "
                "served with status 200. Retry shortly."
            ),
        )
    http_error = _first(chain, httpx.HTTPError)
    if http_error is not None:
        raise_http_tool_error(http_error, attempted=attempted)
    raise_tool_error(
        "unknown",
        is_retryable=False,
        attempted=attempted,
        description=f"Unexpected error: {type(exc).__name__}: {exc}",
    )


class StructuredErrorMiddleware(Middleware):
    """Give every tool-call failure a fleet error payload.

    Registered in server.py directly after mcpfleet_obs.install(), so it sits
    inside FleetMetricsMiddleware and tool_calls_total records the classified
    category.
    """

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        try:
            return await call_next(context)
        except NotFoundError:
            # An unregistered tool name: mcpfleet-obs folds these into the
            # `_unknown_` label by exception type, so leave it untouched.
            raise
        except ToolError as exc:
            if parse_error_payload(str(exc)) is not None:
                raise
            raise_structured_from_unhandled(exc, attempted=context.message.name)
        except Exception as exc:
            raise_structured_from_unhandled(exc, attempted=context.message.name)
