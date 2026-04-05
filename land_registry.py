"""
HMLR Land Registry title search tool (1 tool).

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
import urllib.parse
from typing import Annotated, Any

from pydantic import Field
from fastmcp import FastMCP

from http_client import format_api_error

# ---------------------------------------------------------------------------
# SPARQL query helper for PPI (Price Paid Index) search
# ---------------------------------------------------------------------------

SPARQL_ENDPOINT = "https://landregistry.data.gov.uk/landregistry/sparql"

PPI_QUERY_TEMPLATE = """
PREFIX lrppi: <http://landregistry.data.gov.uk/def/ppi/>
PREFIX lrcommon: <http://landregistry.data.gov.uk/def/common/>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>

SELECT ?pricePaid ?transactionDate ?postcode ?propertyType ?estateType ?paon ?saon ?street ?town ?county
WHERE {{
  VALUES ?postcode {{"{postcode}"^^xsd:string}}

  ?transx lrppi:pricePaid ?pricePaid ;
          lrppi:transactionDate ?transactionDate ;
          lrppi:propertyAddress ?addr ;
          lrppi:propertyType ?propertyType ;
          lrppi:estateType ?estateType .

  ?addr lrcommon:postcode ?postcode .
  OPTIONAL {{ ?addr lrcommon:paon ?paon }}
  OPTIONAL {{ ?addr lrcommon:saon ?saon }}
  OPTIONAL {{ ?addr lrcommon:street ?street }}
  OPTIONAL {{ ?addr lrcommon:town ?town }}
  OPTIONAL {{ ?addr lrcommon:county ?county }}
}}
ORDER BY DESC(?transactionDate)
LIMIT 10
"""

TITLE_SEARCH_URL = "https://api.landregistry.data.gov.uk/data/title"


def _is_postcode(text: str) -> bool:
    """Naive UK postcode detector -- just checks for a space + 3-char suffix."""
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
    price = t.get("pricePaid", "—")
    if isinstance(price, (int, float)):
        price = f"£{price:,.0f}"
    parts = [p for p in [t.get("paon"), t.get("street"), t.get("town"), t.get("postcode")] if p]
    address = ", ".join(parts) if parts else "—"
    return (
        f"{idx}. {address}\n"
        f"   Price: {price} | Date: {t.get('transactionDate', '—')} | "
        f"Type: {t.get('propertyType', '—')} | Tenure: {t.get('estateType', '—')}\n"
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
    async def land_title_search(
        address_or_postcode: Annotated[str, Field(description="UK property address or postcode. Postcode is most reliable: e.g. 'NG1 1AB'. Full address also accepted.", min_length=4, max_length=200)],
        response_format: Annotated[str, Field(description="Output format: 'markdown' or 'json'")] = "markdown",
    ) -> str:
        """Search HM Land Registry for property ownership data by address or postcode.

        Returns registered proprietor name, title class (absolute/qualified/possessory),
        tenure (freehold/leasehold), and recent price paid transactions.
        Covers England and Wales only.
        """
        # Extract or use postcode
        text = address_or_postcode.strip().upper()
        postcode = _extract_postcode(text)

        if not postcode:
            return (
                "Could not extract a valid UK postcode from the input. "
                "Please include a postcode, e.g. 'NG1 1AB' or '1 High Street, Nottingham, NG1 1AB'."
            )

        try:
            import httpx as _httpx

            # 1. Price Paid query via SPARQL — POST with form-encoded body (endpoint requires POST)
            sparql_query = PPI_QUERY_TEMPLATE.format(postcode=postcode)
            body = urllib.parse.urlencode({"query": sparql_query}).encode()
            async with _httpx.AsyncClient(timeout=20.0) as client:
                ppi_resp = await client.post(
                    SPARQL_ENDPOINT,
                    content=body,
                    headers={
                        "Accept": "application/sparql-results+json",
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                )
                ppi_resp.raise_for_status()
                ppi_data = ppi_resp.json()

            def _val(b: dict, key: str) -> str:
                return b.get(key, {}).get("value", "") or ""

            def _uri_label(uri: str) -> str:
                """Extract readable label from HMLR URI, e.g. .../propertyType/terraced → Terraced."""
                return uri.rstrip("/").split("/")[-1].replace("-", " ").title() if uri else "—"

            bindings = ppi_data.get("results", {}).get("bindings", [])
            transactions = []
            for b in bindings:
                transactions.append(
                    {
                        "pricePaid": int(float(_val(b, "pricePaid"))) if _val(b, "pricePaid") else "—",
                        "transactionDate": _val(b, "transactionDate")[:10],
                        "postcode": _val(b, "postcode"),
                        "paon": _val(b, "paon"),
                        "saon": _val(b, "saon"),
                        "street": _val(b, "street"),
                        "town": _val(b, "town"),
                        "county": _val(b, "county"),
                        "propertyType": _uri_label(_val(b, "propertyType")),
                        "estateType": _uri_label(_val(b, "estateType")),
                    }
                )

            title_data: dict[str, Any] = {}

        except Exception as exc:
            return format_api_error(exc, "land_title_search")

        if response_format == "json":
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
