# UK Due Diligence

## Tagline
Companies House, Charity Commission, Land Registry, Gazette, and sanctions screening — one MCP server.

## Description
UK Due Diligence gives AI agents access to five official UK government registers for business and individual checks. Search and profile UK companies, check directors and persons of significant control, look up disqualified directors, search insolvency notices in The Gazette, look up charity records, search Land Registry title ownership, and screen names against the OFSI, OFAC, EU and UN sanctions lists. All data comes directly from official government APIs.

## Setup Requirements
Two free API keys are required: `CH_API_KEY` (Companies House) and `CHARITY_API_KEY` (Charity Commission). Land Registry, The Gazette and the sanctions lists need no credentials. All data is sourced directly from official UK government register APIs.

## Category
Business Tools

## Features
- Search and profile any UK company via Companies House
- Look up directors, secretaries, and officers for any company
- Identify persons of significant control (PSC/beneficial owners)
- Check if an individual is a disqualified director
- Screen names against the OFSI, OFAC, EU and UN sanctions lists
- Search insolvency and winding-up notices in The Gazette
- Search and profile UK charities via the Charity Commission
- Look up Land Registry title ownership by address or title number
- 18 tools across five official registers
- All data from authoritative government sources, not web scraping

## Getting Started
- "Search Companies House for Acme Ltd and give me a full company profile"
- "Who are the directors of company number 12345678?"
- "Check if John Smith is a disqualified director"
- "Are there any insolvency notices for this company in The Gazette?"
- "Look up the Land Registry title for 14 High Street, London EC1A 1BB"
- Tool: company_search — Find companies by name on Companies House
- Tool: company_profile — Full company details including status, accounts, and SIC codes
- Tool: company_officers — Directors, secretaries, and officers for a company
- Tool: company_psc — Persons of significant control (beneficial owners)
- Tool: disqualified_search — Check if an individual is a disqualified director
- Tool: sanctions_screen — Screen a name against the OFSI/OFAC/EU/UN lists
- Tool: gazette_insolvency — Search insolvency notices in The Gazette
- Tool: charity_search — Search UK charities via the Charity Commission
- Tool: land_title_search — Land Registry title ownership lookup

## Tags
uk-companies-house, uk-due-diligence, sanctions-screening, disqualified-directors, land-registry, charity-commission, gazette-insolvency, psc, beneficial-owner, company-search, uk-business, compliance, mcp

## Documentation URL
https://bouch.dev/products/uk-due-diligence-mcp

## Health Check URL
https://uk-due-diligence-mcp.fly.dev/health
