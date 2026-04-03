# HMLR Land Registry — Endpoint Fix

## Root Cause (three issues)

`land_title_search` returned zero results for all postcodes due to three compounding bugs:

| # | Issue | Broken | Fixed |
|---|-------|--------|-------|
| 1 | Endpoint URL | `/landregistry/query` | `/landregistry/sparql` |
| 2 | HTTP method | GET with `params=` | POST with `application/x-www-form-urlencoded` body |
| 3 | Query pattern | `rdfs:label` path joins + inline postcode | `VALUES` clause + plain URI bindings |

## Why It Was Silent

- The wrong GET endpoint returned HTTP 200 with empty `results.bindings` (not a 4xx/5xx)
- The `rdfs:label` joins also silently return empty results on this endpoint
- Code interpreted empty bindings as "no transactions found" rather than a misconfiguration

## Fix

Changes in `land_registry.py`:
- Line 36: endpoint URL `/query` → `/sparql`
- Lines 38–62: query template rewritten — `VALUES ?postcode {"X"^^xsd:string}` + plain property/estateType URI bindings (no `rdfs:label`)
- Lines 136–144: `client.get(..., params=)` → `client.post(..., content=body, Content-Type: form-urlencoded)`
- Bindings parsing updated to match new field names (`pricePaid`, `transactionDate`, `estateType`)

## Source of Truth

Confirmed against `property_shared/property_core/ppd_client.py` which uses POST + `/sparql` and is in production use.
