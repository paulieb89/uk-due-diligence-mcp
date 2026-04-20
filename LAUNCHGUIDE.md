# UK Due Diligence

## Tagline
Companies House, Charity Commission, Land Registry, Gazette, and HMRC VAT — one MCP server.

## Description
UK Due Diligence gives AI agents access to five official UK government registers for business and individual checks. Search and profile UK companies, check directors and persons of significant control, look up disqualified directors, verify VAT numbers, search insolvency notices in The Gazette, look up charity records, and search Land Registry title ownership. No API key required — all data comes directly from public government APIs.

## Setup Requirements
No API keys or environment variables required. All data is sourced directly from official UK government register APIs.

## Category
Business Tools

## Features
- Search and profile any UK company via Companies House
- Look up directors, secretaries, and officers for any company
- Identify persons of significant control (PSC/beneficial owners)
- Check if an individual is a disqualified director
- Verify UK VAT registration numbers via HMRC
- Search insolvency and winding-up notices in The Gazette
- Search and profile UK charities via the Charity Commission
- Look up Land Registry title ownership by address or title number
- No API key required — eleven tools across five public registers
- All data from authoritative government sources, not web scraping

## Getting Started
- "Search Companies House for Acme Ltd and give me a full company profile"
- "Who are the directors of company number 12345678?"
- "Check if John Smith is a disqualified director"
- "Validate VAT number GB123456789"
- "Are there any insolvency notices for this company in The Gazette?"
- "Look up the Land Registry title for 14 High Street, London EC1A 1BB"
- Tool: company_search — Find companies by name on Companies House
- Tool: company_profile — Full company details including status, accounts, and SIC codes
- Tool: company_officers — Directors, secretaries, and officers for a company
- Tool: company_psc — Persons of significant control (beneficial owners)
- Tool: disqualified_search — Check if an individual is a disqualified director
- Tool: vat_validate — Verify a UK VAT registration number
- Tool: gazette_insolvency — Search insolvency notices in The Gazette
- Tool: charity_search — Search UK charities via the Charity Commission
- Tool: land_title_search — Land Registry title ownership lookup

## Tags
uk-companies-house, uk-due-diligence, vat-validation, disqualified-directors, land-registry, charity-commission, gazette-insolvency, psc, beneficial-owner, company-search, uk-business, compliance, mcp, no-api-key

## Documentation URL
https://bouch.dev

## Health Check URL
https://uk-due-diligence-mcp.fly.dev/health
