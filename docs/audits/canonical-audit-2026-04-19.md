# Canonical audit — uk-due-diligence-mcp — 2026-04-19

Run via the BOUCH MCP canonical audit prompt
(`bouch-pages/docs/mcp-canonical-audit-prompt.md`).

## 1. Per-surface verdicts

| Surface | Type | Verdict | Notes |
|---|---|---|---|
| `company_search` | tool | ✅ canonical | Verb search. Live 100-result page ~25,142 chars / ~6.3k tokens; bounded by `items_per_page<=100`. |
| `company_profile` | tool | 🔴 anti-pattern | Fetch-by-identifier — wants a resource template. Also ships a raw passthrough blob. See `companies_house.py:146`, `models.py:168`. |
| `company_officers` | tool | 🔴 anti-pattern | Collection-by-identifier; better as `company://{company_number}/officers{?...}`. Live Tesco payload ~7,513 chars / ~1.9k tokens — size fine, primitive choice is the issue. See `companies_house.py:205`. |
| `company_psc` | tool | 🔴 anti-pattern | Noun-by-identifier. Query-param style knobs fit RFC 6570 resource templates cleanly. See `companies_house.py:281`. |
| `disqualified_search` | tool | ✅ canonical | Verb search. Live 100-result page ~24,668 chars / ~6.2k tokens. |
| `disqualified_profile` | tool | 🔴 anti-pattern | Fetch-by-ID; should be a resource template. Live payload ~811 chars / ~203 tokens — shape problem, not size. See `disqualified.py:108`. |
| `charity_search` | tool | ✅ canonical | Verb search. Title/name-first matches the 90% LLM intent. |
| `charity_profile` | tool | 🔴 anti-pattern | Fetch-by-identifier. Live Oxfam payload ~8,851 chars / ~2.2k tokens. Includes raw blob. See `charity.py:120`, `models.py:610`. |
| `land_title_search` | tool | 🟡 improvement | Search-by-address/postcode is a tool, but overpromises title-owner data while returning transactions and `title_data={}`. Wrong primitive shape for an LLM. See `land_registry.py:6`, `:158`, `models.py:682`. |
| `gazette_insolvency` | tool | 🟡 improvement | Search verb is right, but size control is only per-notice `max_content_chars`; no global cap/index/pagination. Can fan out across 14 codes × 10 notices. See `gazette.py:141`. |
| `vat_validate` | tool | ✅ canonical | Verb validate. Small bounded result. Explicit `Accept: application/json` set. |
| (resource templates) | resource | 🔴 anti-pattern | None registered. Repo is tool-only despite five fetch-by-identifier surfaces. Server config has no `ResourcesAsTools` or caching layer. See `server.py:127`. |

## 2. Specific improvements

- 🔴 Replace `company_profile`, `company_officers`, `company_psc`, `disqualified_profile`, `charity_profile` with resource templates; keep only the search/validate surfaces as tools.
- 🔴 Add `ResourcesAsTools` at the server or fleet gateway instead of hand-writing tool wrappers for resource reads (`server.py:127`).
- 🔴 If exposed through the Ledgerhall proxy, register resources at the **mounting gateway** (uk-legal-mcp issue #3 — mounted sub-MCP wildcards silently break). This repo is explicitly mounted behind another server (`CLAUDE.md:13`).
- 🟡 Remove `raw` from `CompanyProfile` and `CharityProfile`; if raw upstream payload is genuinely needed, make it an opt-in `/raw` resource. See `models.py:168`, `models.py:610`.
- 🟡 Fix `land_title_search` so the name/description matches the actual returned primitive. Today it claims proprietor/title-class/tenure but always returns empty `title_data`. See `land_registry.py:90`, `models.py:682`.
- 🟡 Add a global result cap or paginated/indexed shape to `gazette_insolvency`; `max_content_chars` alone is an escape hatch, not navigation. See `gazette.py:129`, `:141`.
- 🟡 Add `ResponseCachingMiddleware` for the read-only/idempotent tools; current middleware stack is logging-only. See `server.py:79`.

## 3. Missing primitives

- Resource templates for the identifier reads:
  - `company://{company_number}/profile`
  - `company://{company_number}/officers{?include_resigned,limit}`
  - `company://{company_number}/psc{?max_nature_chars}`
  - `charity://{charity_number}/profile{?max_trustees,max_classifications}`
  - `disqualification://{officer_id}{?max_companies}`
- `ResourcesAsTools` coverage for tool-only clients once those resources exist
- An opt-in raw-detail resource if upstream passthrough is still needed; raw payload should not ride along on the canonical detail surface
- A structurally bounded Gazette drill-down: search returns notice summaries/IDs first, then a notice-by-ID resource for full content
- A real title-detail primitive for Land Registry, or a narrower tool contract that admits it's only postcode transaction lookup today

## Filed as

paulieb89/uk-due-diligence-mcp#1
