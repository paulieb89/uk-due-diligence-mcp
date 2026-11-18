# Tool Reference

Compact public reference for the uk-due-diligence-mcp surface. Tool descriptions inside MCP clients carry the detailed guidance.

The tables are generated from the live server (`fastmcp inspect`) by `scripts/gen_reference.py`; edit the prose, not the tables.

## Companies House

Companies House REST API (`CH_API_KEY`). Company numbers are 8 characters; shorter digit-only numbers are zero-padded.

<!-- BEGIN GENERATED: tools prefix=company_,officer_ -->
| Tool | Title | Summary |
|---|---|---|
| `company_search` | Search Companies House | Search the Companies House register by company name or keyword. |
| `company_profile` | Get Company Profile | Fetch the full Companies House profile for a company number. |
| `company_officers` | Get Company Officers | Fetch officers for a Companies House company number. |
| `company_psc` | Get Persons with Significant Control | Fetch Persons with Significant Control (beneficial ownership) for a company. |
| `officer_appointments` | Get Officer Appointment History | Fetch a person's full company appointment history by officer ID. |
| `company_charges` | Get Company Charges | Fetch the complete Companies House charge history for a company. |
| `company_filing_history` | Get Company Filing History | Fetch one page of a company's Companies House filing chronology. |
| `company_filing_document` | Get Companies House Filing Document | Resolve a filing's document_metadata link to its authoritative source document. |
<!-- END GENERATED -->

<!-- BEGIN GENERATED: templates prefix=company://,officer://,company-document:// -->
| Resource | Name | Summary |
|---|---|---|
| `company://{company_number}/profile` | company_profile | Full Companies House profile for a company number: status, address, SIC codes, filing compliance (overdue flags), and active-charges flag. |
| `company://{company_number}/officers` | company_officers | Active officers for a Companies House company number. |
| `company://{company_number}/psc` | company_psc | Persons with Significant Control (beneficial ownership) for a company. |
| `officer://{officer_id}/appointments` | officer_appointments | Full appointment history for a Companies House officer ID: every company they've been a director/secretary of, current and historic, with company status, role, and dates. |
| `company://{company_number}/charges` | company_charges | Complete Companies House charge history for a company number: every registered charge, current and historic, with status, dates, secured parties, and what each charge covers. |
| `company://{company_number}/filing-history` | company_filing_history | First page (up to 100) of a Companies House company's filing chronology, unfiltered. |
| `company-document://{document_id}{?mime_type}` | company_filing_document_content | The authoritative binary content of a Companies House filed document (PDF in every case observed live). |
<!-- END GENERATED -->

## Disqualified directors

<!-- BEGIN GENERATED: tools prefix=disqualified_ -->
| Tool | Title | Summary |
|---|---|---|
| `disqualified_search` | Search Disqualified Directors | Check whether a named individual is banned from acting as a UK company director. |
| `disqualified_profile` | Get Disqualified Director Profile | Fetch the full disqualification record for a director by officer ID. |
<!-- END GENERATED -->

<!-- BEGIN GENERATED: templates prefix=disqualification:// -->
| Resource | Name | Summary |
|---|---|---|
| `disqualification://{officer_id}` | disqualified_profile | Full disqualification record for a director by officer ID. |
<!-- END GENERATED -->

## Charity Commission

<!-- BEGIN GENERATED: tools prefix=charity_ -->
| Tool | Title | Summary |
|---|---|---|
| `charity_search` | Search Charity Commission Register | Search the Charity Commission register of England and Wales by name or keyword. |
| `charity_profile` | Get Charity Profile | Fetch the full Charity Commission profile for a charity number. |
<!-- END GENERATED -->

<!-- BEGIN GENERATED: templates prefix=charity:// -->
| Resource | Name | Summary |
|---|---|---|
| `charity://{charity_number}/profile` | charity_profile | Full Charity Commission profile for a charity number: trustees, income/expenditure, insolvency flags, and area of operation. |
<!-- END GENERATED -->

## The Gazette

<!-- BEGIN GENERATED: tools prefix=gazette_ -->
| Tool | Title | Summary |
|---|---|---|
| `gazette_notice` | Get Gazette Notice Full Text | Fetch the full legal wording of a Gazette notice by numeric notice ID. |
| `gazette_insolvency` | Search Gazette Corporate Insolvency Notices | Search The Gazette's insolvency notice index by entity name. |
<!-- END GENERATED -->

<!-- BEGIN GENERATED: templates prefix=notice:// -->
| Resource | Name | Summary |
|---|---|---|
| `notice://{notice_id}` | gazette_notice | Full content of a Gazette notice by numeric notice ID. |
<!-- END GENERATED -->

## HM Land Registry

<!-- BEGIN GENERATED: tools prefix=land_ -->
| Tool | Title | Summary |
|---|---|---|
| `land_title_search` | Search Price Paid Transactions by Postcode | Search HM Land Registry Price Paid Index by postcode or address. |
<!-- END GENERATED -->

## Sanctions

OFSI (UK), OFAC (US), EU and UN consolidated lists, downloaded and indexed by the server.

<!-- BEGIN GENERATED: tools prefix=sanctions_ -->
| Tool | Title | Summary |
|---|---|---|
| `sanctions_screen` | Screen a Name Against Sanctions Lists | Screen a name against the UK (OFSI), US (OFAC), EU and UN consolidated sanctions lists. |
<!-- END GENERATED -->

## Cross-register search

For clients that use the `search` / `fetch` pattern: `search` fans out across the registers and returns IDs; `fetch` resolves one.

<!-- BEGIN GENERATED: tools prefix=search,fetch -->
| Tool | Title | Summary |
|---|---|---|
| `search` | Search UK Due Diligence Registers | Search across all UK due diligence registers simultaneously. |
| `fetch` | Fetch Full Record from UK Due Diligence Register | Fetch the full record for an ID returned by search. |
<!-- END GENERATED -->
