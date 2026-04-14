# uk-due-diligence-mcp

Companies House, Charity Commission, The Gazette, HMLR Land Registry, HMRC VAT,
and disqualified directors — FastMCP server with 11 tools across 5 UK public
registers.

- **GitHub:** `paulieb89/uk-due-diligence-mcp`
- **Deployed:** `https://uk-due-diligence-mcp.fly.dev/mcp`
- **Shape:** flat layout — `server.py` + `companies_house.py`, `charity.py`, `gazette.py`, `hmrc_vat.py`, `disqualified.py`, `land_registry.py`

## Not to be confused with

**`uk-business-mcp`** is a DIFFERENT repo at `/home/bch/dev/00_RELEASE/uk-business-mcp`,
deployed at `https://uk-business-mcp.fly.dev/mcp`. That repo is the **Ledgerhall proxy** —
it mounts `govuk-mcp`, `uk-legal-mcp`, `uk-due-diligence-mcp` (this repo), and
`property-shared` behind a single URL. It has no domain tools of its own.

If a task mentions "uk-business-mcp" without qualification, it almost always
means the Ledgerhall proxy, not this due-diligence server.

This folder was previously misleadingly named `/home/bch/dev/uk-business-mcp`
until 2026-04-14, which was the source of repeated cross-repo confusion.
