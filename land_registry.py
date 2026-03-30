"""
tools/land_registry.py — HMLR title search tool (1 tool).

Uses the Land Registry Linked Data API (api.landregistry.data.gov.uk).
The PPI (Price Paid Index) endpoint is queried with SPARQL via the
REST query interface. For title/ownership data we use the Title Search
endpoint which returns registered proprietor name, title class, tenure,
and charge/mortgage data.

The free API (api.landregistry.data.gov.uk) returns RDF/Turtle by default.
We pass Accept: application/json to get JSON-LD.

Note: The HMLR API covers England and Wales only.
Land Register of Scotland and Land & Property Services NI are separate.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

from mcp.server.fastmcp import FastMCP

from http_client import (
    _request_with_retry,
    hmlr_client,
    format_api_error,
)
from inputs import LandTitleSearchInput, ResponseFormat

# ---------------------------------------------------------------------------
# SPARQL query helper for PPI (Price Paid Index) search
# ---------------------------------------------------------------------------

SPARQL_ENDPOINT = "https://landregistry.data.gov.uk/landregistry/query"

PPI_QUERY_TEMPLATE = """
PREFIX lrppi: <http://landregistry.data.gov.uk/def/ppi/>
PREFIX lrcommon: <http://landregistry.data.gov.uk/def/common/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>

SELECT ?address ?paon ?saon ?street ?town ?county ?postcode ?amount ?date ?propertyType ?tenure
WHERE {{
  ?transx lrppi:propertyAddress ?addr ;
          lrppi:pricePaid ?amount ;
          lrppi:transactionDate ?date ;
          lrppi:propertyType/rdfs:label ?propertyType ;
          lrppi:estateType/rdfs:label ?tenure .
  ?addr lrcommon:postcode "{postcode}"^^xsd:string ;
        rdfs:label ?address .
  OPTIONAL {{ ?addr lrcommon:paon ?paon }}
  OPTIONAL {{ ?addr lrcommon:saon ?saon }}
  OPTIONAL {{ ?addr lrcommon:street ?street }}
  OPTIONAL {{ ?addr lrcommon:town ?town }}
  OPTIONAL {{ ?addr lrcommon:county ?county }}
  OPTIONAL {{ ?addr lrcommon:postcode ?postcode }}
}}
ORDER BY DESC(?date)
LIMIT 10
"""

TITLE_SEARCH_URL = "https://api.landregistry.data.gov.uk/data/title"


def _is_postcode(text: str) -> bool:
    """Naive UK postcode detector — just checks for a space + 3-char suffix."""
    parts = text.strip().upper().split()
    return len(parts) >= 2 and len(parts[-1]) in (3,)


def _extract_postcode(text: str) -> str | None:
    """Try to extract a postcode from a free-text address."""
    import re
    # UK postcode regex
    pattern = r"\b[A-Z]{1,2}\d{1,2}[A-Z]?\s*\d[A-Z]{2}\b"
    match = re.search(pattern, text.upper())
    return match.group(0).replace(" ", " ").strip() if match else None


def _format_transaction(t: dict[str, Any], idx: int) -> str:
    price = t.get("amount", "—")
    if isinstance(price, (int, float)):
        price = f"£{price:,.0f}"
    return (
        f"{idx}. {t.get('address', '—')}\n"
        f"   Price: {price} | Date: {t.get('date', '—')} | "
        f"Type: {t.get('propertyType', '—')} | Tenure: {t.get('tenure', '—')}\n"
    )


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

def register_tools(mcp: FastMCP) -> None:

    @mcp.tool(
        name="land_title_search",
        annotations={
            "title": "Search HMLR Land Registry Title",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def land_title_search(params: LandTitleSearchInput) -> str:
        """Search HM Land Registry for property ownership data by address or postcode.

        Returns registered proprietor name, title class (absolute/qualified/possessory),
        tenure (freehold/leasehold), and recent price paid transactions.
        Useful for identifying who actually owns a property or verifying that a
        company's registered address matches its land ownership.

        Covers England and Wales only. Scotland uses the Land Register of Scotland;
        Northern Ireland uses Land & Property Services NI — neither is covered here.

        Args:
            params (LandTitleSearchInput): Validated input containing:
                - address_or_postcode (str): UK property address or postcode
                - response_format (ResponseFormat): 'markdown' or 'json'

        Returns:
            str: Property ownership details and recent price paid transactions.
        """
        # Extract or use postcode
        text = params.address_or_postcode.strip().upper()
        postcode = _extract_postcode(text)

        if not postcode:
            return (
                "Could not extract a valid UK postcode from the input. "
                "Please include a postcode, e.g. 'NG1 1AB' or '1 High Street, Nottingham, NG1 1AB'."
            )

        postcode_encoded = quote(postcode)

        try:
            import httpx as _httpx

            # 1. Price Paid query via SPARQL REST endpoint
            sparql_query = PPI_QUERY_TEMPLATE.format(postcode=postcode)
            async with _httpx.AsyncClient(timeout=20.0) as client:
                ppi_resp = await client.get(
                    SPARQL_ENDPOINT,
                    params={"query": sparql_query, "output": "json"},
                    headers={"Accept": "application/sparql-results+json"},
                )
                ppi_resp.raise_for_status()
                ppi_data = ppi_resp.json()

            bindings = ppi_data.get("results", {}).get("bindings", [])
            transactions = []
            for b in bindings:
                transactions.append(
                    {
                        "address": b.get("address", {}).get("value", "—"),
                        "amount": b.get("amount", {}).get("value", "—"),
                        "date": b.get("date", {}).get("value", "—")[:10],
                        "propertyType": b.get("propertyType", {}).get("value", "—"),
                        "tenure": b.get("tenure", {}).get("value", "—"),
                    }
                )

            # 2. Title search (ownership) — attempt REST title endpoint
            title_data: dict[str, Any] = {}
            try:
                async with hmlr_client() as title_client:
                    title_resp = await _request_with_retry(
                        title_client, "GET",
                        f"/title-search.json?postcode={postcode_encoded}",
                    )
                    title_data = title_resp.json()
            except Exception:
                # Title endpoint may fail — PPI data still valuable
                pass

        except Exception as exc:
            return format_api_error(exc, "land_title_search")

        if params.response_format == ResponseFormat.JSON:
            return json.dumps(
                {
                    "postcode": postcode,
                    "transactions": transactions,
                    "title_data": title_data,
                },
                indent=2,
            )

        lines = [f"## HMLR Land Registry — {postcode}\n"]

        # Title section
        if title_data:
            titles = title_data.get("results", []) if isinstance(title_data, dict) else []
            if titles:
                lines.append("### Registered Titles\n")
                for t in titles[:5]:
                    lines.append(
                        f"- **{t.get('tenure', '—')}** | Title No: {t.get('title_no', '—')} | "
                        f"Class: {t.get('class_of_title', '—')} | "
                        f"Proprietor: {t.get('proprietor_name', '—')}\n"
                    )
            else:
                lines.append("*No title registration data returned from HMLR title endpoint.*\n")
        else:
            lines.append(
                "*Title ownership data unavailable — HMLR title endpoint did not return results. "
                "Price paid data shown below.*\n"
            )

        # Price Paid section
        lines.append(f"\n### Price Paid Transactions ({postcode})\n")
        if transactions:
            for i, t in enumerate(transactions[:10], 1):
                lines.append(_format_transaction(t, i))
        else:
            lines.append(
                "*No price paid transactions found for this postcode. "
                "The property may be commercially held, exempt, or the postcode may be incorrect.*\n"
            )

        return "\n".join(lines)
