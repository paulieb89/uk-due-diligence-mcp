# uk-biz-intel-mcp

**UK Business Intelligence MCP Server**

Five legally-mandated public registers. Zero paywalls. One cross-registry reasoning layer.

> *"Run due diligence on Acme Ltd"* → the agent calls five registries, surfaces a director with 60 appointments, finds a winding-up petition from six months ago, and notes the VAT number doesn't match the trading address.

---

## The Pitch

This isn't a Companies House wrapper — dozens exist. It's a **due diligence layer**.

Every data source is a publicly mandated register with a free official API. The value isn't any individual API — it's what you get when they're unified under a single MCP server and an agent can reason across all five simultaneously.

**Data sources:**

| Register | API | Auth |
|----------|-----|------|
| Companies House | `api.company-information.service.gov.uk` | API key (free) |
| Charity Commission | `api.charitycommission.gov.uk` | API key (free) |
| HMLR Land Registry | `landregistry.data.gov.uk` (SPARQL + REST) | None |
| The Gazette | `thegazette.co.uk/all-notices` (Linked Data) | None |
| HMRC VAT | `api.service.hmrc.gov.uk` | None |

---

## Tools

### Layer 1 — Raw Registry Tools

| Tool | Register | Description |
|------|----------|-------------|
| `company_search` | Companies House | Search by name/keyword with status/type filters |
| `company_profile` | Companies House | Full profile: status, filing compliance, charges |
| `company_officers` | Companies House | Directors with high-appointment-count risk flag |
| `company_psc` | Companies House | Beneficial owners, PSC chain, offshore flags |
| `charity_search` | Charity Commission | Search by name, filter by registration status |
| `charity_profile` | Charity Commission | Full record: trustees, finances, governing doc |
| `land_title_search` | HMLR | Property ownership via SPARQL PPI query |
| `gazette_insolvency` | The Gazette | Corporate insolvency notices (codes 2441–2460) |
| `vat_validate` | HMRC VAT | Trading name + address as registered for VAT |

### Layer 2 — Composite

| Tool | Description |
|------|-------------|
| `entity_due_diligence` | **The flagship tool.** One call → five registers → structured risk report |

---

## Risk Signals Surfaced

`entity_due_diligence` flags:

- 🚨 Company status in distress set (dissolved, liquidation, administration, receivership)
- 🚩 Accounts overdue
- 🚩 Confirmation statement overdue
- 🚩 Active charges registered
- 🚩 Directors with ≥10 other appointments (nominee/phoenix signal)
- 🚩 Offshore corporate PSC (beneficial ownership chain extends overseas)
- 🚨 Active Gazette insolvency notices (sorted by severity)
- ℹ️ Land Registry price paid transactions (optional, `include_property=True`)
- ℹ️ Charity Commission cross-match (optional, `include_charity=True`)

---

## Setup

### API Keys

| Key | Where to get it |
|-----|----------------|
| `CH_API_KEY` | [developer.company-information.service.gov.uk](https://developer.company-information.service.gov.uk) — free |
| `CHARITY_API_KEY` | [api-portal.charitycommission.gov.uk](https://api-portal.charitycommission.gov.uk) — free |
| `MCP_SERVER_KEY` | Generate yourself — used for client auth to the MCP server |

HMLR, Gazette, and HMRC VAT require no API key.

### Local development

```bash
git clone https://github.com/you/uk-biz-intel-mcp
cd uk-biz-intel-mcp

# Create .env
cat > .env <<EOF
CH_API_KEY=your_ch_key_here
CHARITY_API_KEY=your_charity_key_here
MCP_SERVER_KEY=your_server_key_here
EOF

# Install
pip install -e .

# Run
python server.py
```

Server starts at `http://localhost:8080/mcp`.

### Fly.io deployment

```bash
fly launch --name uk-biz-intel-mcp --region lhr
fly secrets set CH_API_KEY=xxx CHARITY_API_KEY=xxx MCP_SERVER_KEY=xxx
fly deploy
```

Server available at `https://uk-biz-intel-mcp.fly.dev/mcp`.

---

## Connecting to Claude

Add to your MCP client config:

```json
{
  "mcpServers": {
    "uk-biz-intel": {
      "url": "https://uk-biz-intel-mcp.fly.dev/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_MCP_SERVER_KEY"
      }
    }
  }
}
```

---

## The Demo Query

```
Run full due diligence on Acme Construction Ltd
```

Expected agent flow:
1. `entity_due_diligence("Acme Construction Ltd")` →
2. Internally: `company_search` → resolves company number
3. `company_profile` → checks status, overdue flags, charges
4. `company_officers` → scans appointment counts
5. `company_psc` → maps beneficial ownership chain
6. `gazette_insolvency` → scans all 14 corporate notice codes
7. Returns: structured risk report with traffic-light score

---

## Project Structure

```
uk-biz-intel-mcp/
├── server.py              # FastMCP init, tool registration, transport config
├── tools/
│   ├── companies_house.py # company_search, company_profile, company_officers, company_psc
│   ├── charity.py         # charity_search, charity_profile
│   ├── land_registry.py   # land_title_search (SPARQL + REST)
│   ├── gazette.py         # gazette_insolvency (JSON-LD, notice codes 2441–2460)
│   ├── hmrc_vat.py        # vat_validate
│   └── composite.py       # entity_due_diligence
├── clients/
│   └── http.py            # Shared httpx clients, retry backoff, error formatting
├── models/
│   └── inputs.py          # Pydantic v2 input models for all tools
├── fly.toml
├── Dockerfile
├── pyproject.toml
└── README.md
```

---

## Technical Notes

### The Gazette API
Uses a REST+RDF linked-data pattern. Corporate insolvency notice codes span 2441–2460.
The `entity_due_diligence` tool scans all 14 codes per entity. The read API is
unauthenticated; auth is write-only (for placing notices).

### HMLR Land Registry
Free endpoint at `api.landregistry.data.gov.uk`. Returns RDF/Turtle by default —
the SPARQL endpoint is used for Price Paid Index queries with `Accept: application/sparql-results+json`.
Covers England and Wales only.

### High-Appointment-Count Signal
The threshold is set at 10 (configurable via `HIGH_APPOINTMENT_COUNT` in `tools/companies_house.py`).
A director appearing on 40+ companies is a common pattern in nominee director operations and
phoenix company structures.

---

## Licence

MIT
