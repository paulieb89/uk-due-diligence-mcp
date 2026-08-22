"""
Companies House Document API — filing document retrieval.

Covers:
  - company_filing_document (tool)     -> resolves a document_metadata_url (from
                                           CompanyFiling.links.document_metadata) to a
                                           company-document:// resource reference
  - company-document://{document_id}   (resource) -> the authoritative binary (PDF/etc)

Deliberately separate from companies_house.py: this is binary-content protocol
mechanics (MCP resource_link / BlobResourceContents, a different upstream host,
credential-safe redirect handling), not company-record field mapping.

The tool NEVER returns document bytes or base64 — only a resource_link content
block plus a plain-JSON structured summary. Actual bytes are served exclusively
by the company-document:// resource, fetched via resources/read, per the MCP
2026-07-28 spec's resource_link mechanism (confirmed against the installed
FastMCP 3.2.4 / mcp SDK source: a tool returning raw bytes gets silently
base64-encoded into a TextContent block by FastMCP's default conversion —
exactly the anti-pattern this module exists to avoid).
"""

from __future__ import annotations

import logging
from typing import Annotated, Any
from urllib.parse import quote, urlsplit

import httpx
import mcp.types
from fastmcp import FastMCP
from fastmcp.resources import ResourceContent
from fastmcp.tools import ToolResult
from pydantic import Field

from http_client import _request_with_retry, companies_house_client
from mcpfleet_obs import raise_http_tool_error, raise_tool_error
from models import CompanyFilingDocumentResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DOCUMENT_API_HOST = "document-api.company-information.service.gov.uk"
DOCUMENT_API_BASE = f"https://{DOCUMENT_API_HOST}"

# Confirmed live: CH's /content endpoint 302s to a pre-signed S3 URL
# (X-Amz-Expires=60 observed). Only ever follow a redirect whose host is
# under this suffix, and always as a fresh, unauthenticated request —
# CH_API_KEY must never be attached cross-origin.
_SAFE_REDIRECT_HOST_SUFFIX = ".amazonaws.com"

# Advisory-only threshold (mirrors company_filing_history's note= pattern).
# Largest document seen in live probing was ~320KB; this flags a genuine
# outlier without ever refusing to serve what was asked for.
LARGE_DOCUMENT_ADVISORY_THRESHOLD_BYTES = 10_000_000  # 10 MB

_DOCUMENT_METADATA_PATH_RE_PREFIX = "/document/"


# ---------------------------------------------------------------------------
# URL validation — the credential-safety boundary
# ---------------------------------------------------------------------------


def _parse_document_id_from_metadata_url(url: str, *, attempted: str) -> str:
    """Strictly validate a caller-supplied document_metadata_url and extract
    the document ID.

    The extracted ID is the ONLY thing carried forward — the canonical
    metadata/content URLs are reconstructed from it (see
    _document_metadata_url), never built from or by re-fetching the
    caller-supplied URL string itself. That, plus this validation running
    before any HTTP request, is what keeps CH_API_KEY from ever being
    attachable to a caller-controlled host: even a validated URL is
    discarded after this function returns.

    Rejects anything that isn't exactly:
      https://document-api.company-information.service.gov.uk/document/{id}
    — no userinfo, no non-default port, no query string, no fragment, no
    path traversal (the id-charset regex below can't match '..' or '/').
    """
    parts = urlsplit(url)

    if parts.scheme != "https":
        raise_tool_error(
            "validation", is_retryable=False, attempted=attempted,
            description=f"document_metadata_url must use https — got {parts.scheme!r}.",
        )
    if parts.hostname != DOCUMENT_API_HOST:
        raise_tool_error(
            "validation", is_retryable=False, attempted=attempted,
            description=(
                f"document_metadata_url must point at {DOCUMENT_API_HOST} — got "
                f"{parts.hostname!r}. Use the document_metadata link from "
                f"company_filing_history verbatim."
            ),
        )
    if parts.username or parts.password:
        raise_tool_error(
            "validation", is_retryable=False, attempted=attempted,
            description="document_metadata_url must not contain userinfo (username/password).",
        )
    if parts.port is not None:
        raise_tool_error(
            "validation", is_retryable=False, attempted=attempted,
            description=f"document_metadata_url must not specify a port — got {parts.port}.",
        )
    if parts.query:
        raise_tool_error(
            "validation", is_retryable=False, attempted=attempted,
            description="document_metadata_url must not contain a query string.",
        )
    if parts.fragment:
        raise_tool_error(
            "validation", is_retryable=False, attempted=attempted,
            description="document_metadata_url must not contain a fragment.",
        )

    path = parts.path
    if not path.startswith(_DOCUMENT_METADATA_PATH_RE_PREFIX):
        raise_tool_error(
            "validation", is_retryable=False, attempted=attempted,
            description=f"document_metadata_url path must be exactly /document/{{id}} — got {path!r}.",
        )
    document_id = path[len(_DOCUMENT_METADATA_PATH_RE_PREFIX):]
    if not document_id or "/" in document_id or not document_id.replace("-", "").replace("_", "").isalnum():
        raise_tool_error(
            "validation", is_retryable=False, attempted=attempted,
            description=f"document_metadata_url path must be exactly /document/{{id}} — got {path!r}.",
        )
    return document_id


def _document_metadata_url(document_id: str) -> str:
    """Canonical metadata URL, reconstructed from a validated document_id —
    never the caller-supplied URL string itself."""
    return f"{DOCUMENT_API_BASE}/document/{document_id}"


def _normalise_mime_type(content_type: str | None) -> str:
    """Strip parameters (e.g. '; charset=...') for a provenance comparison."""
    if not content_type:
        return ""
    return content_type.split(";", 1)[0].strip().lower()


def _resolve_mime_type(resources: dict[str, Any], requested: str | None, *, attempted: str) -> str:
    """Auto-select when unambiguous, require an explicit choice otherwise.

    Every document observed live (TAS/MEL/Tesco/a 2009 filing, 2007-2026)
    had exactly one entry (application/pdf) — but `resources` is upstream's
    own open dict, never assumed to be single-valued.
    """
    available = sorted(resources.keys())
    if requested is not None:
        if requested not in resources:
            raise_tool_error(
                "validation", is_retryable=False, attempted=attempted,
                description=f"mime_type {requested!r} is not available for this document. Available: {available}.",
            )
        return requested
    if len(available) == 1:
        return available[0]
    if not available:
        raise_tool_error(
            "not_found", is_retryable=False, attempted=attempted,
            description="This document has no available content representations.",
        )
    raise_tool_error(
        "validation", is_retryable=False, attempted=attempted,
        description=(
            f"This document has multiple content representations — specify mime_type. "
            f"Available: {available}."
        ),
    )


# ---------------------------------------------------------------------------
# Shared fetch helpers (used by both the tool and the resource)
# ---------------------------------------------------------------------------


async def _fetch_document_metadata(document_id: str) -> dict[str, Any]:
    url = _document_metadata_url(document_id)
    async with companies_house_client() as client:
        resp = await _request_with_retry(client, "GET", url)
        return resp.json()


async def _fetch_document_content_bytes(
    document_id: str, mime_type: str, expected_content_length: int | None
) -> bytes:
    """Credential-safe content fetch.

    GET .../content with CH auth, follow_redirects=False (never rely on
    httpx's cross-origin Authorization-stripping — this doesn't let the
    authenticated client see the redirect target at all). Validate the
    302 Location is https and under *.amazonaws.com, then make a second,
    completely separate, unauthenticated request for the actual bytes.
    Finally verify the downloaded content's Content-Type and length match
    what was promised — a freshly signed URL returning HTML/XML/error
    content must never be served as if it were the filed document.
    """
    content_url = f"{_document_metadata_url(document_id)}/content"
    attempted = f"GET {content_url}"

    # Deliberately NOT _request_with_retry here: httpx's raise_for_status()
    # treats ANY non-2xx status as an error, including an unfollowed 3xx —
    # so the shared helper would convert our expected 302 into a generic
    # "unknown" error before we ever got to inspect Location. This request
    # needs to see the raw redirect, so raise_for_status is only invoked
    # (and only then) for a genuine non-302/non-2xx status.
    async with companies_house_client() as client:
        resp = await client.request(
            "GET", content_url,
            headers={"Accept": mime_type},
            follow_redirects=False,
        )

    if resp.status_code != 302:
        if not (200 <= resp.status_code < 300):
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise_http_tool_error(exc, attempted=attempted)
        raise_tool_error(
            "unknown", is_retryable=False, attempted=attempted,
            description=f"Expected a 302 redirect to the signed document URL, got {resp.status_code}.",
        )

    location = resp.headers.get("location")
    if not location:
        raise_tool_error(
            "transient", is_retryable=True, attempted=attempted,
            description="Upstream returned a 302 with no Location header.",
        )

    redirect_parts = urlsplit(location)
    if redirect_parts.scheme != "https":
        raise_tool_error(
            "transient", is_retryable=True, attempted=attempted,
            description=f"Refusing to follow a non-https redirect target: {location!r}.",
        )
    if not redirect_parts.hostname or not redirect_parts.hostname.endswith(_SAFE_REDIRECT_HOST_SUFFIX):
        raise_tool_error(
            "transient", is_retryable=True, attempted=attempted,
            description=f"Refusing to follow redirect to an unexpected host: {redirect_parts.hostname!r}.",
        )

    # Fresh client, no auth, no headers carried over from the CH request —
    # CH_API_KEY is never attached to this or any redirect target.
    async with httpx.AsyncClient(timeout=30.0) as anon_client:
        content_resp = await _request_with_retry(anon_client, "GET", location)

    actual_mime = _normalise_mime_type(content_resp.headers.get("content-type"))
    if actual_mime != mime_type:
        raise_tool_error(
            "transient", is_retryable=True, attempted=attempted,
            description=(
                f"Downloaded content-type {actual_mime!r} does not match the expected "
                f"{mime_type!r} — refusing to serve under false provenance."
            ),
        )

    actual_length = len(content_resp.content)
    if expected_content_length is not None and actual_length != expected_content_length:
        raise_tool_error(
            "transient", is_retryable=True, attempted=attempted,
            description=(
                f"Downloaded {actual_length} bytes but metadata declared "
                f"content_length={expected_content_length} — refusing to serve under false provenance."
            ),
        )

    return content_resp.content


async def _build_filing_document_result(
    document_metadata_url: str, *, mime_type: str | None = None
) -> ToolResult:
    attempted = f"company_filing_document({document_metadata_url!r})"
    document_id = _parse_document_id_from_metadata_url(document_metadata_url, attempted=attempted)
    meta = await _fetch_document_metadata(document_id)

    resources = meta.get("resources") or {}
    selected_mime = _resolve_mime_type(resources, mime_type, attempted=attempted)
    content_length = (resources.get(selected_mime) or {}).get("content_length")

    # The resolved MIME type is baked into the resource URI itself (even in
    # the unambiguous single-resource case) so a later resources/read never
    # has to repeat mime-selection logic against metadata that may have
    # changed — the link's meaning is self-contained and immutable.
    resource_uri = f"company-document://{document_id}?mime_type={quote(selected_mime, safe='')}"

    note = None
    if content_length is not None and content_length > LARGE_DOCUMENT_ADVISORY_THRESHOLD_BYTES:
        note = f"{content_length} bytes — large document, may be slow to transfer."

    filename = meta.get("filename") or document_id
    summary_text = (
        f"Found {meta.get('category')!r} document for company {meta.get('company_number')}: "
        f"{selected_mime}, "
        f"{content_length if content_length is not None else 'unknown'} bytes, "
        f"{meta.get('pages')} page(s). Fetch the file via resource {resource_uri}."
    )

    result = CompanyFilingDocumentResult(
        document_id=document_id,
        company_number=meta.get("company_number"),
        category=meta.get("category"),
        pages=meta.get("pages"),
        filename=meta.get("filename"),
        created_at=meta.get("created_at"),
        mime_type=selected_mime,
        content_length=content_length,
        resource_uri=resource_uri,
        note=note,
    )

    return ToolResult(
        content=[
            mcp.types.TextContent(type="text", text=summary_text),
            mcp.types.ResourceLink(
                type="resource_link",
                uri=resource_uri,
                name=filename,
                description=summary_text,
                mimeType=selected_mime,
                size=content_length,
            ),
        ],
        structured_content=result.model_dump(mode="json"),
    )


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def register_tools(mcp: FastMCP) -> None:

    @mcp.tool(
        name="company_filing_document",
        annotations={
            "title": "Get Companies House Filing Document",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def company_filing_document(
        document_metadata_url: Annotated[
            str,
            Field(
                description=(
                    "The document_metadata URL from a filing's links.document_metadata "
                    "(returned by company_filing_history) — pass it through verbatim, "
                    "not a document_id. Must be an exact "
                    "https://document-api.company-information.service.gov.uk/document/{id} URL."
                ),
                max_length=500,
            ),
        ],
        mime_type: Annotated[
            str | None,
            Field(
                description=(
                    "Which content representation to select, e.g. 'application/pdf'. "
                    "Omit when the document has only one representation (the near-universal "
                    "case) — it is auto-selected. Required if the document has more than one; "
                    "omitting it in that case returns a validation error listing the choices."
                ),
            ),
        ] = None,
    ) -> ToolResult:
        """Resolve a filing's document_metadata link to its authoritative source document.

        Returns a resource_link (never embedded bytes, never base64) pointing
        at a company-document:// MCP resource — fetch it via resources/read
        to get the actual PDF. This tool only reads metadata (category,
        pages, available content types, byte size); it never downloads the
        document itself. Use company_filing_history first to find a filing's
        document_metadata URL.

        Requires a resource-capable MCP client to retrieve the actual bytes
        — a tool-only client can see this result's metadata (company,
        category, page count, size) but cannot obtain the file through this
        tool call alone.
        """
        return await _build_filing_document_result(document_metadata_url, mime_type=mime_type)


# ---------------------------------------------------------------------------
# Resource registration
# ---------------------------------------------------------------------------


def register_resources(mcp: FastMCP) -> None:

    @mcp.resource(
        "company-document://{document_id}{?mime_type}",
        name="company_filing_document_content",
        description=(
            "The authoritative binary content of a Companies House filed document "
            "(PDF in every case observed live). Re-fetched fresh on every read — "
            "the underlying signed source URL expires after ~60 seconds, so nothing "
            "here is cacheable across reads. mime_type is optional: auto-selected "
            "when the document has one representation, required (errors listing "
            "choices) when it has more — normally already present in the URI when "
            "reached via company_filing_document's resource_link."
        ),
        mime_type="application/pdf",
    )
    async def company_document_resource(
        document_id: str, mime_type: str | None = None
    ) -> list[ResourceContent]:
        attempted = f"company-document://{document_id}"
        meta = await _fetch_document_metadata(document_id)
        resources = meta.get("resources") or {}
        selected_mime = _resolve_mime_type(resources, mime_type, attempted=attempted)
        content_length = (resources.get(selected_mime) or {}).get("content_length")
        content = await _fetch_document_content_bytes(document_id, selected_mime, content_length)
        return [ResourceContent(content, mime_type=selected_mime)]
