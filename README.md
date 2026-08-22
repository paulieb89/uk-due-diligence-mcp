# uk-due-diligence-mcp

<!-- mcp-name: io.github.paulieb89/uk-due-diligence-mcp -->

**Official-source UK due-diligence data for AI agents.**

Search Companies House, Charity Commission, The Gazette, HMLR price-paid data and HMRC VAT records, plus screen names against the OFSI, OFAC, EU and UN sanctions lists. Exposes atomic MCP tools for company ownership, officers, cross-company appointment history, secured charges, insolvency notices and related registry evidence — the consuming agent decides how to investigate, not the server.

Every data source is a legally-mandated register with a free official API. Zero paywalls.

[![PyPI](https://img.shields.io/pypi/v/uk-due-diligence-mcp)](https://pypi.org/project/uk-due-diligence-mcp/)
[![SafeSkill](https://safeskill.dev/api/badge/paulieb89-uk-due-diligence-mcp)](https://safeskill.dev/scan/paulieb89-uk-due-diligence-mcp)
[![Glama](https://img.shields.io/badge/Glama-listed-orange?style=flat-square)](https://glama.ai/mcp/connectors/io.github.paulieb89/uk-due-diligence-mcp)
[![smithery badge](https://smithery.ai/badge/bouch/uk-due-diligence)](https://smithery.ai/servers/bouch/uk-due-diligence)
[![Install in VS Code](https://img.shields.io/badge/VS_Code-Install_Server-0098FF?style=flat-square&logo=visualstudiocode&logoColor=white)](https://vscode.dev/redirect/mcp/install?name=uk-due-diligence&config=%7B%22type%22%3A%22http%22%2C%22url%22%3A%22https%3A%2F%2Fuk-due-diligence-mcp.fly.dev%2Fmcp%22%7D)
[![Install in VS Code Insiders](https://img.shields.io/badge/VS_Code_Insiders-Install_Server-24bfa5?style=flat-square&logo=visualstudiocode&logoColor=white)](https://insiders.vscode.dev/redirect/mcp/install?name=uk-due-diligence&config=%7B%22type%22%3A%22http%22%2C%22url%22%3A%22https%3A%2F%2Fuk-due-diligence-mcp.fly.dev%2Fmcp%22%7D&quality=insiders)
[![Install in Cursor](https://img.shields.io/badge/Cursor-Install_Server-000000?style=flat-square&logoColor=white)](https://cursor.com/en/install-mcp?name=uk-due-diligence&config=eyJ0eXBlIjoiaHR0cCIsInVybCI6Imh0dHBzOi8vdWstZHVlLWRpbGlnZW5jZS1tY3AuZmx5LmRldi9tY3AifQ==)

---

## Data sources

| Register | API | Auth | Coverage |
|----------|-----|------|----------|
| Companies House | `api.company-information.service.gov.uk` | API key (free) | UK-wide |
| Charity Commission | `api.charitycommission.gov.uk` | API key (free) | England & Wales |
| HMLR Land Registry | `landregistry.data.gov.uk` (SPARQL) | None | England & Wales |
| The Gazette | `thegazette.co.uk` (Linked Data) | None (read) | UK-wide |
| HMRC VAT | `api.service.hmrc.gov.uk` | OAuth2 client credentials | UK-wide |
| OFSI / OFAC / EU / UN sanctions | consolidated list files | None | International |

---

## Quick start

### Hosted (no install)

```json
{
  "mcpServers": {
    "uk-due-diligence": {
      "type": "http",
      "url": "https://uk-due-diligence-mcp.fly.dev/mcp"
    }
  }
}
```

### Local (uvx)

```json
{
  "mcpServers": {
    "uk-due-diligence": {
      "type": "stdio",
      "command": "uvx",
      "args": ["uk-due-diligence-mcp"]
    }
  }
}
```

See [Configuration](#configuration) for the environment variables it needs.

---

## Tools

**Companies House**

| Tool | Description |
|------|-------------|
| `company_search` | Search by name/keyword, filter by status/type |
| `company_profile` | Status, filing compliance, registered address, `has_charges` summary |
| `company_officers` | Directors/secretaries; each carries an `officer_id` |
| `company_psc` | Beneficial owners, PSC chain, overseas-corporate-PSC flag |
| `officer_appointments` | Full appointment history for a person by `officer_id` — including dissolved or insolvent companies not named anywhere else |
| `company_charges` | Complete secured-charge history: status, dates, secured parties, what each charge covers |
| `disqualified_search` | Search disqualified directors by name |
| `disqualified_profile` | Full disqualification record: period, Act, associated companies |

**Charity Commission**

| Tool | Description |
|------|-------------|
| `charity_search` | Search by name, filter by registration status |
| `charity_profile` | Full record: trustees, income/expenditure, governing document |

**HMLR Land Registry**

| Tool | Description |
|------|-------------|
| `land_title_search` | Price Paid Index sale transactions by postcode — not title ownership (see [Limitations](#limitations)) |

**The Gazette**

| Tool | Description |
|------|-------------|
| `gazette_insolvency` | Corporate insolvency notices across the Gazette's notice-code taxonomy (codes 2401-2465) |
| `gazette_notice` | Full legal wording of a specific notice |

**HMRC / Sanctions**

| Tool | Description |
|------|-------------|
| `vat_validate` | Trading name + address as registered for VAT |
| `sanctions_screen` | Screen a name against the OFSI/OFAC/EU/UN consolidated lists |

**Cross-register**

| Tool | Description |
|------|-------------|
| `search` | Fan-out search across all registers — returns IDs (for ChatGPT deep research) |
| `fetch` | Fetch a structured record by ID returned from `search` |

---

## Examples

```
Resolve "TAS Engineering" to a company number and check whether it has any outstanding charges.
```
→ `company_search` to find the company number, then `company_charges` for the full secured-debt picture (not just the `has_charges` summary).

```
Has Gareth Davies (director of company 06333469) been connected to any other companies, including ones that no longer exist?
```
→ `company_officers` to get his `officer_id`, then `officer_appointments` to surface every company he's held an appointment at, current or historic.

```
Is "MEL Precision Limited" in the middle of insolvency proceedings, and what does that actually mean legally?
```
→ `gazette_insolvency` to find the notices, then `gazette_notice` to read the full legal wording before drawing conclusions from the notice label alone.

```
Screen "Acme Trading Ltd" and its officers against sanctions lists.
```
→ `company_officers` for the officer names, then `sanctions_screen` against the company name and each officer.

---

## Limitations

Things worth knowing before trusting output:

- **`land_title_search` returns Price Paid transactions, not ownership.** It does not return current proprietor/title data — HMLR's Price Paid Index only records historic sale transactions.
- **Sanctions screening is exact/alias matching, not compliance clearance.** A company/entity legal name matches reliably; person names with transliteration variants may not. An empty result is not a guarantee of clearance, and a hit on a common name may need disambiguation.
- **`has_charges: null` means the check could not be confidently completed** — a charges-endpoint outage, or a charge with an unrecognized status. Treat it as unresolved, not as "no charges."
- **`officer_appointments` returns historical relationships.** Companies House doesn't auto-resign a director when a company enters insolvency — check `company_status` on each appointment, not just `resigned_on`.
- **Official-source data can be incomplete or delayed.** These are the same registers a human would check, with the same latency and gaps.

---

## Configuration

| Variable | Required for | Where to get it |
|----------|--------------|------------------|
| `CH_API_KEY` | All Companies House tools | [developer.company-information.service.gov.uk](https://developer.company-information.service.gov.uk) — free |
| `CHARITY_API_KEY` | Charity Commission tools | [api-portal.charitycommission.gov.uk](https://api-portal.charitycommission.gov.uk) — free |
| `HMRC_CLIENT_ID` / `HMRC_CLIENT_SECRET` | `vat_validate` | HMRC Developer Hub (developer.service.hmrc.gov.uk) — free, OAuth2 client-credentials app |
| `HMRC_ENV` | `vat_validate` (optional) | `sandbox` or `production` — defaults to `production` |

HMLR, The Gazette, and the sanctions lists require no credentials.

---

## Project structure

```
uk-due-diligence-mcp/
├── server.py           # FastMCP init, tool/resource registration, transport config
├── companies_house.py  # company_search/profile/officers/psc, officer_appointments, company_charges
├── disqualified.py     # disqualified_search, disqualified_profile
├── charity.py          # charity_search, charity_profile
├── land_registry.py    # land_title_search (SPARQL Price Paid Index)
├── gazette.py          # gazette_insolvency, gazette_notice
├── hmrc_vat.py         # vat_validate (OAuth2 client-credentials)
├── sanctions.py        # sanctions_screen (OFSI/OFAC/EU/UN consolidated lists)
├── search_fetch.py     # search, fetch (cross-register fan-out)
├── models.py           # Pydantic v2 output models
├── http_client.py      # Shared httpx clients, retry backoff, error formatting
├── fly.toml
├── Dockerfile
├── pyproject.toml
└── .env.example
```

---

## Licence

MIT

<!-- mcp-name: io.github.paulieb89/uk-due-diligence-mcp -->
