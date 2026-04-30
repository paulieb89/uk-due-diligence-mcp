# uk-due-diligence-mcp

<!-- mcp-name: io.github.paulieb89/uk-due-diligence-mcp -->

Tools across five UK public registers. Zero paywalls. All official APIs.

Give an agent a company name and it pulls corporate status, filing compliance, director networks, beneficial ownership chains, disqualification checks, insolvency notices, VAT validation, and property transactions.

Every data source is a legally-mandated register with a free official API.

[![PyPI](https://img.shields.io/pypi/v/uk-due-diligence-mcp)](https://pypi.org/project/uk-due-diligence-mcp/)
[![SafeSkill](https://safeskill.dev/api/badge/paulieb89-uk-due-diligence-mcp)](https://safeskill.dev/scan/paulieb89-uk-due-diligence-mcp)
[![uk-due-diligence-mcp MCP server](https://glama.ai/mcp/servers/paulieb89/uk-due-diligence-mcp/badges/card.svg)](https://glama.ai/mcp/servers/paulieb89/uk-due-diligence-mcp)
[![smithery badge](https://smithery.ai/badge/bouch/uk-due-diligence)](https://smithery.ai/servers/bouch/uk-due-diligence)

---

## Data Sources

| Register | API | Auth |
|----------|-----|------|
| Companies House | `api.company-information.service.gov.uk` | API key (free) |
| Charity Commission | `api.charitycommission.gov.uk` | API key (free) |
| HMLR Land Registry | `landregistry.data.gov.uk` (SPARQL + REST) | None |
| The Gazette | `thegazette.co.uk/all-notices` (Linked Data) | None |
| HMRC VAT | `api.service.hmrc.gov.uk` | None |

---

## Tools

| Tool | Register | Description |
|------|----------|-------------|
| `company_search` | Companies House | Search by name/keyword with status/type filters |
| `company_profile` | Companies House | Full profile: status, filing compliance, charges |
| `company_officers` | Companies House | Directors with high-appointment-count risk flag |
| `company_psc` | Companies House | Beneficial owners, PSC chain, offshore flags |
| `disqualified_search` | Companies House | Search disqualified directors by name |
| `disqualified_profile` | Companies House | Full disqualification record, period, Act, companies |
| `charity_search` | Charity Commission | Search by name, filter by registration status |
| `charity_profile` | Charity Commission | Full record: trustees, finances, governing doc |
| `land_title_search` | HMLR | Property ownership via SPARQL PPI query |
| `gazette_insolvency` | The Gazette | Corporate insolvency notices (codes 2441-2460) |
| `gazette_notice` | The Gazette | Full legal wording of a specific insolvency notice |
| `vat_validate` | HMRC VAT | Trading name + address as registered for VAT |
| `search` | All registers | Fan-out search across all registers — returns IDs for ChatGPT deep research |
| `fetch` | All registers | Fetch a structured record by ID returned from `search` |

---

## Prompts

Three workflow prompts orchestrate multi-step investigations. Available via `get_prompt` on tool-only clients (ChatGPT) and natively on protocol-aware clients (Claude, Inspector).

| Prompt | Description |
|--------|-------------|
| `due_diligence` | Full DD check — company, officers, PSC, gazette, disqualification |
| `charity_due_diligence` | Charity profile + insolvency check |
| `director_check` | Disqualification status check for an individual |

---

## Connect

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

```bash
export CH_API_KEY=your_key
export CHARITY_API_KEY=your_key
```

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

### API Keys

| Key | Where to get it |
|-----|----------------|
| `CH_API_KEY` | [developer.company-information.service.gov.uk](https://developer.company-information.service.gov.uk) — free |
| `CHARITY_API_KEY` | [api-portal.charitycommission.gov.uk](https://api-portal.charitycommission.gov.uk) — free |

HMLR, Gazette, and HMRC VAT require no API key.

---

## Demo

```
Run due diligence on Carillion PLC
```

The agent calls `company_search` to resolve the company number, then `company_profile`, `company_officers`, `company_psc`, and `gazette_insolvency` — reasoning across all five registries to surface risk signals.

---

## Project Structure

```
uk-due-diligence-mcp/
├── server.py           # FastMCP init, tool registration, transport config
├── companies_house.py  # company_search, company_profile, company_officers, company_psc
├── disqualified.py     # disqualified_search, disqualified_profile
├── charity.py          # charity_search, charity_profile
├── land_registry.py    # land_title_search (SPARQL + REST)
├── gazette.py          # gazette_insolvency (JSON-LD, notice codes 2441-2460)
├── hmrc_vat.py         # vat_validate
├── http_client.py      # Shared httpx clients, retry backoff, error formatting
├── inputs.py           # Pydantic v2 input models
├── fly.toml
├── Dockerfile
├── pyproject.toml
└── .env.example
```

---

## Technical Notes

### The Gazette API
REST+RDF linked-data pattern. Corporate insolvency notice codes span 2441-2460.
The read API is unauthenticated; auth is write-only (for placing notices).

### HMLR Land Registry
Free endpoint at `api.landregistry.data.gov.uk`. Returns RDF/Turtle by default —
the SPARQL endpoint is used for Price Paid Index queries. Covers England and Wales only.

### High-Appointment-Count Signal
Directors with 10+ other active appointments are flagged. A director on 40+ companies
is a common pattern in nominee director operations and phoenix company structures.

---

## Licence

MIT

<!-- mcp-name: io.github.paulieb89/uk-due-diligence-mcp -->
