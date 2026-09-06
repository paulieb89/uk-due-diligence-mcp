# uk-due-diligence-mcp

FastMCP server over 6 UK registers — Companies House, Charity Commission,
The Gazette, HMLR Land Registry, HMRC VAT, and OFSI/OFAC/EU/UN sanctions.
19 tools. Flat layout: `server.py` registers 9 domain modules.

- **GitHub:** `paulieb89/uk-due-diligence-mcp`
- **Prod:** `https://uk-due-diligence-mcp.fly.dev/mcp`
- **Staging:** `https://uk-due-diligence-mcp-staging.fly.dev` (auto-deploys on push to main)

## Not to be confused with

`uk-business-mcp` — a sibling repo in the same fleet directory, deployed at
`uk-business-mcp.fly.dev/mcp` — is the **Ledgerhall proxy**. It mounts this server
plus `govuk-mcp`, `uk-legal-mcp` and `property-shared` behind one URL, and has no
domain tools of its own. An unqualified "uk-business-mcp" almost always means that
proxy, not this server.

## Releasing

Releases are **GitHub-release-triggered**, not tag-triggered: `release.yml` runs
`on: release: published` → PyPI → `flyctl deploy`. Pushing a tag alone publishes
nothing, and a hand-run `fly deploy` ships prod while silently skipping the PyPI
publish. Push to main deploys staging only.

Bumping a version means editing **three** files or the registries drift (this has
happened — see `daaa9dc`, and again at v1.3.0):
`pyproject.toml`, `server.py` (`smithery_server_card`), `server.json` (×2).

Run `scripts/check-deploy-drift.sh` to confirm what is actually running in prod
was built by CI from a commit on `origin/main`.

## Invariants

- The server reads `PORT` only — `FASTMCP_PORT` is not wired up.
- `PORT`, `fly.toml` `internal_port`, and `[metrics] port` must all stay 8080.
- These three routes are external contracts (Smithery/Glama/Fly) — never drop:
  `/.well-known/mcp/server-card.json`, `/.well-known/glama.json`, `/health`.

These are enforced by hooks in `.claude/settings.json`, not just documented here.

## Verify

`/verify` (or `uv run --no-sync python scripts/mcp_smoke_test.py --deployed`)
calls `company_search` against prod and asserts a known-good result.
