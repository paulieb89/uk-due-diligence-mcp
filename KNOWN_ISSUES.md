# Known Issues

## Resolved

### HMLR land_title_search returned empty results (fixed in v1.0.3)

**Symptom:** `land_title_search` returned "No price paid transactions found" for all postcodes including busy residential areas.

**Root cause:** Wrong SPARQL endpoint URL — `/landregistry/query` instead of `/landregistry/sparql`.

**Fix:** One-line change in `land_registry.py:36`. See `HMLR_FIX.md` for full details.

## Known Limitations

### Title ownership data unavailable

The HMLR title ownership REST endpoint (`/data/title/title-search.json`) does not return results. The tool falls back to Price Paid Index data only. This is expected behaviour — registered proprietor data is not available via the public API.

### England and Wales only

HMLR covers England and Wales only. Land Register of Scotland and Land & Property Services NI are separate registers not covered by this tool.
