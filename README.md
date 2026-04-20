# uk-due-diligence-mcp

Eleven tools across five UK public registers. Zero paywalls. All official APIs.

Give an agent a company name and it pulls corporate status, filing compliance, director networks, beneficial ownership chains, disqualification checks, insolvency notices, VAT validation, and property transactions.

Every data source is a legally-mandated register with a free official API.

[![PyPI](https://img.shields.io/pypi/v/uk-due-diligence-mcp)](https://pypi.org/project/uk-due-diligence-mcp/)
[![SafeSkill 93/100](https://img.shields.io/badge/SafeSkill-93%2F100_Verified%20Safe-brightgreen)](https://safeskill.dev/scan/paulieb89-uk-due-diligence-mcp)

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
| `vat_validate` | HMRC VAT | Trading name + address as registered for VAT |

---

## Setup

### Install from PyPI

```bash
pip install uk-due-diligence-mcp
```

### API Keys

| Key | Where to get it |
|-----|----------------|
| `CH_API_KEY` | [developer.company-information.service.gov.uk](https://developer.company-information.service.gov.uk) — free |
| `CHARITY_API_KEY` | [api-portal.charitycommission.gov.uk](https://api-portal.charitycommission.gov.uk) — free |

HMLR, Gazette, and HMRC VAT require no API key.

### Local development

```bash
git clone https://github.com/paulieb89/uk-due-diligence-mcp
cd uk-due-diligence-mcp

cp .env.example .env
# Fill in your API keys

pip install -e .
python server.py
```

Server starts at `http://localhost:8080/mcp`.

### Fly.io deployment

```bash
fly launch --name uk-due-diligence-mcp --region lhr
fly secrets set CH_API_KEY=xxx CHARITY_API_KEY=xxx
fly deploy
```

---

## Connecting

### Claude Code / .mcp.json

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

### Claude.ai / other MCP clients

```json
{
  "mcpServers": {
    "uk-due-diligence": {
      "url": "https://uk-due-diligence-mcp.fly.dev/mcp"
    }
  }
}
```

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
