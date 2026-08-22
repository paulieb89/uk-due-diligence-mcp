# Staging acceptance sweep — uk-due-diligence-mcp — 2026-08-22

Run against `uk-due-diligence-mcp-staging.fly.dev`, a Fly app created for this
sweep, deployed via `flyctl deploy` directly from the current `main` working
tree (HEAD `b30a970`) — **not** through the PyPI release pipeline, so this
predates any version bump. Dependency pins unchanged since `v1.2.0`.

Trigger: production (`uk-due-diligence-mcp.fly.dev`) was discovered to be
stale — no GitHub release had been cut since `v1.2.0` (2026-07-29) despite
three feature PRs (officer_appointments, company_charges,
company_filing_history, company_filing_document) merging to `main`
afterward. `release.yml` only fires on `release: published`, so none of that
work had shipped. Staging exists to validate the current `main` state before
cutting the release that will actually deploy it.

Executed as seven parallel subagents (one per scenario) against the live
staging MCP connection, plus one direct check from the orchestrating session
for the resource-read hop no subagent's toolset exposed. First pass hit a
second real defect: staging had **zero** secrets configured, so every CH/
Charity/HMRC-backed tool failed identically on `error_category: configuration`
before any business logic executed. `CH_API_KEY` and `CHARITY_API_KEY` were
imported from `.env` (values confirmed identical to prod via matching
secret digests); `HMRC_CLIENT_ID`/`SECRET`/`HMRC_ENV` were **not** present in
`.env` and remain unset, so `vat_validate` stays untested for real OAuth
behaviour (its pre-OAuth format validation was confirmed working
independently). All scenarios below were re-run after the key fix.

## 1. Scenario verdicts

| Scenario | Verdict | Notes |
|---|---|---|
| CH core chain (search/profile, positive+negative) | 🟡 3/4 pass | `company_search` on a nonsense query returns CH's own fuzzy 10,000-result token match, not an empty set — see §3. |
| Officer appointments → related companies | ✅ pass | Correctly surfaced MEL Precision Limited (`liquidation`, `resigned_on: null`) for a TAS Engineering director — the tool's whole reason to exist. Minor `active_count` labeling nit — see §3. |
| Charges (`company_charges`) | ✅ pass, 5/5 | Reproduces the design spec's acceptance case verbatim: Lloyds Bank outstanding floating+fixed charge, satisfied Millers Lane charge with satisfaction transaction. No interpretive commentary present. |
| Filing history → document → ResourceLink → binary | ✅ pass, full chain confirmed | Subagent verified through the ResourceLink hop; orchestrating session completed the final `resources/read` call directly and confirmed a real 714KB, 22-page, valid `%PDF-1.1` document — page count matches metadata exactly. No base64 pollution, no signed-URL leak anywhere in the chain. |
| Other registers (charity, disqualified) | 🟡 mostly pass | `charity_search` returns a `not_found` **error** on zero matches instead of an empty result — real defect, see §3. `charity_profile`, `disqualified_search`, `disqualified_profile` all clean on both positive and negative cases. |
| Other registers (gazette, land, sanctions) — tested pre-key-fix, no auth needed | ✅ pass | All three passed positive and negative cases cleanly on the first run; no overclaiming found in any tool description vs. actual returned data. |
| Error semantics + server surface | ✅ pass | `/health`, `/metrics` both clean (metrics carry only aggregate labels — tool/status/error_category/transport — no leaked keys or PII). No connector manifest at any `/.well-known/*` path (expected). Domain errors share a consistent 4-field envelope across tools. |
| Composite DD task (autonomous tool composition) | 🔴 fail | Never found the actual target company. See §2 — this is the important one. |

## 2. Composite DD task — entity resolution failure

Given the task verbatim from the original BOU-39-style acceptance case
("Review the East Midlands/TAS opportunity...") with **no** company number
or hints, a fresh subagent was asked to compose the primitives itself, the
same way Eve will eventually be tested.

It never queried `company_search` for "TAS Engineering" — the literal
top-hit match every other scenario in this sweep used as its target. Instead
it took "East Midlands" as a hard regional filter, searched variants like
"TAS East Midlands" and "TAS (EAST MIDLANDS)", and landed on **TASPROCO
LIMITED** (06253031, Lincoln) — an unrelated company — producing a full,
well-organized, confidently-written DD report on it: officers, PSC, a
related-company cluster via `officer_appointments`, filing history, sanctions
screening, source-evidence-vs-inference separation, unresolved questions.
The methodology itself was genuinely good — sensible tool sequencing, honest
hedging about which entity was "the opportunity", correct evidence/inference
labeling. The failure is upstream of all that: it silently resolved to the
wrong subject and never surfaced that TAS Engineering Ltd (06333469) — the
company with real, interesting DD substance already verified in this sweep
(outstanding Lloyds Bank charge, a director linked to a company in
liquidation) — exists at all.

Root cause is likely compounding two things already seen elsewhere in this
sweep: `company_search`'s CH-native token matching gets noisy on short/bare
queries (§3's nonsense-query finding is the same underlying behavior), and
Burton-upon-Trent (TAS Engineering's registered address) sits in
Staffordshire, which is ONS-classified West Midlands, not East Midlands —
so a strict regional read of the prompt actively steers away from the
correct entity. A human analyst would likely try "TAS Engineering" as a
literal query regardless of the regional hint; the agent didn't.

**This is the one finding worth treating as a real risk before relying on
autonomous composition for client-facing DD work**: an ambiguous but
realistic prompt can cause a confident, well-formatted report on the wrong
company with no explicit red flag that resolution was uncertain. Worth
deciding whether entity disambiguation needs to be more visible/forced
(e.g. the agent stating multiple candidates and asking, rather than
committing to one) at the prompting/instruction layer — this is not
something the MCP server itself can fix, since `company_search` returned
correct, real data throughout; the composition/judgment layer is what
missed it.

## 3. Other defects found

- **`charity_search` returns an error, not an empty result, on zero
  matches** (`error_category: not_found`). Confirmed structured and
  non-leaky (no stack trace, sanitized URL), but breaks the empty-vs-error
  contract that `disqualified_search` correctly implements for the
  identical "no matches" case. `charity_profile`'s not-found-on-invalid-ID
  behavior is correctly an error — the bug is specifically in
  `charity_search`'s zero-results path.
  **FIXED (2026-08-22)**: `_search_charities` (extracted from the tool body
  into a module-level helper, mirroring `_fetch_charity_profile`) now
  catches the upstream 404 and treats it as an empty result — genuine
  errors (auth, transient, etc.) still propagate. 3 new tests in
  `tests/test_charity_search_empty.py`; full suite (131 tests) green;
  re-verified live against redeployed staging (`total: 0, charities: []`
  on a nonsense query, Oxfam search still returns 3 real results).
  `company_search`'s equivalent fuzzy-match behavior on nonsense queries
  was deliberately left alone — that's Companies House's own search
  semantics, not a wrapper defect, and filtering it inside the MCP would
  cross the source-facts-preservation boundary this repo holds elsewhere.
  `officer_appointments.active_count`'s docstring was also clarified (in
  `models.py`) to state explicitly that it buckets by the officer's own
  resignation status, not the company's trading status — non-blocking,
  done alongside this fix since it was a one-line doc change.
- **`company_search` on a query with no real token overlap returns CH's
  broad fuzzy-token results (10,000+ total, unrelated companies), not an
  empty set.** Not a stack trace or wrapper bug — this is Companies House's
  own search API surfacing through unfiltered — but it doesn't satisfy a
  reasonable "no match → empty" expectation, and per §2 it has downstream
  consequences for autonomous entity resolution.
- **`officer_appointments`'s `active_count` field buckets by resignation
  status, not company status** — a director at a company in `liquidation`
  with `resigned_on: null` counts toward `active_count` alongside a
  genuinely trading company. Accurate per-field, but the label invites
  misreading. Docs/description clarification, not a data bug.
- **A bare subagent's MCP toolset has no generic `resources/read`
  capability for this server** — `company_filing_document` correctly
  returns a `company-document://` ResourceLink, but resolving it to actual
  bytes required the orchestrating session's `ReadMcpResourceTool`, which
  isn't available inside a spawned subagent's own tool surface. Worth
  knowing if any downstream automation plans to resolve document links from
  a subagent context rather than a top-level client.

## 4. What passed clean, worth keeping in mind as a baseline

- CH_API_KEY / CHARITY_API_KEY missing-secret errors were themselves
  well-formed (`error_category`, `is_retryable`, `attempted`, `description`)
  even under total tool failure — error hygiene held up under an unrelated
  infra fault, which is a meaningfully good sign for the error-taxonomy work
  done elsewhere in this repo.
- `company_charges`, `company_filing_history`, `company_filing_document`,
  `officer_appointments` all reproduce their design-spec acceptance facts
  exactly against live TAS Engineering data — no drift, no bugs found in
  any of the newest four primitives.
- `gazette_insolvency`, `land_title_search`, `sanctions_screen` — the tools
  with the least recent scrutiny per the original assessment — all passed
  clean positive/negative cases with no tool-description overclaiming.
- `/health` and `/metrics` are clean; metrics carry no sensitive data.

## 5. Still open

- `vat_validate` untested for real OAuth behavior — `HMRC_CLIENT_ID`/
  `HMRC_CLIENT_SECRET`/`HMRC_ENV` not present in local `.env`, not yet set
  on staging.
- No cross-host confirmation yet (ChatGPT, Codex, Eve) — this sweep only
  covers the Claude/Claude-Code column of the interoperability matrix
  originally proposed.
- No wheel-install/PyPI-artifact test — intentionally out of scope for this
  staging deploy, relevant once an actual release is cut.
- ~~`charity_search` empty-result defect not yet fixed.~~ Fixed — see §3.
- Composite-task entity-resolution risk (§2): deliberately **not** an MCP
  fix — every tool returned correct source data throughout, so the gap is
  at the composition/judgment layer, not the server. Resolution decided:
  an Eve skill/playbook encoding an explicit resolution sequence — (1)
  canonical identifiers (company number > exact legal name > known
  website/address) before fuzzy search, (2) search the literal business
  name before interpreting regional/contextual hints, (3) compare
  name/address/SIC/officers across multiple plausible candidates, (4)
  surface candidates and ask rather than silently committing when
  confidence is materially uncertain, (5) carry the resolved company
  number through the rest of the investigation. Directly targets the
  observed failure: the agent treated "East Midlands" as a stronger
  identifier than "TAS" and never tried the literal business name.
