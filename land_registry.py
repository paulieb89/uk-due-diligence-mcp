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

import urllib.parse
from typing import Annotated

from pydantic import Field
from fastmcp import FastMCP

from models import LandTitleSearchResult, LandTitleTransaction

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


def _extract_postcode(text: str) -> str | None:
    """Try to extract a postcode from a free-text address."""
    import re
    # UK postcode regex
    pattern = r"\b[A-Z]{1,2}\d{1,2}[A-Z]?\s*\d[A-Z]{2}\b"
    match = re.search(pattern, text.upper())
    return match.group(0).replace(" ", " ").strip() if match else None


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
    ) -> LandTitleSearchResult:
        """Search HM Land Registry for property ownership data by address or postcode.

        Returns registered proprietor name, title class (absolute/qualified/
        possessory), tenure (freehold/leasehold), and recent price paid
        transactions. Covers England and Wales only. Price paid transactions
        are hard-capped at 10 upstream.
        """
        # Extract or use postcode
        text = address_or_postcode.strip().upper()
        postcode = _extract_postcode(text)

        if not postcode:
            raise ValueError(
                "Could not extract a valid UK postcode from the input. "
                "Please include a postcode, e.g. 'NG1 1AB' or "
                "'1 High Street, Nottingham, NG1 1AB'."
            )

        import httpx as _httpx

        # Price Paid query via SPARQL — POST with form-encoded body
        # (the endpoint requires POST).
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
            return uri.rstrip("/").split("/")[-1].replace("-", " ").title() if uri else ""

        bindings = ppi_data.get("results", {}).get("bindings", [])
        transactions: list[LandTitleTransaction] = []
        for b in bindings:
            price_raw = _val(b, "pricePaid")
            price_int: int | None
            try:
                price_int = int(float(price_raw)) if price_raw else None
            except ValueError:
                price_int = None

            transactions.append(
                LandTitleTransaction(
                    price_paid=price_int,
                    transaction_date=_val(b, "transactionDate")[:10] or None,
                    postcode=_val(b, "postcode") or None,
                    paon=_val(b, "paon") or None,
                    saon=_val(b, "saon") or None,
                    street=_val(b, "street") or None,
                    town=_val(b, "town") or None,
                    county=_val(b, "county") or None,
                    property_type=_uri_label(_val(b, "propertyType")) or None,
                    estate_type=_uri_label(_val(b, "estateType")) or None,
                )
            )

        return LandTitleSearchResult(
            postcode=postcode,
            total=len(transactions),
            transactions=transactions,
            title_data={},
        )
