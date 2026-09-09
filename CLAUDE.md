# uk-due-diligence-mcp

FastMCP server over 5 UK registers — Companies House, Charity Commission,
The Gazette, HMLR Land Registry, and OFSI/OFAC/EU/UN sanctions.
18 tools. Flat layout: `server.py` registers 8 domain modules.

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

A hand-run `fly deploy` is **refused** by `.claude/hooks/pre_bash_deploy.py`. A
deliberate rollback re-runs it with `FLY_DEPLOY_ACK=1`, which downgrades the
refusal to a confirmation prompt — it does not wave the command through, because
an agent reads the refusal and could otherwise set the variable itself.

Bumping a version means editing **three** files or the registries drift (this has
happened — see `daaa9dc`, and again at v1.3.0):
`pyproject.toml`, `server.py` (`smithery_server_card`), `server.json` (×2).

The version triad is asserted mechanically — see Invariants below — and
`release.yml` additionally checks it against the release tag, so neither a bump
that misses a file nor a tag that outruns the files can reach PyPI.

Run `scripts/check-deploy-drift.sh` to confirm what is actually running in prod
was built by CI from the **latest release tag**. Prod is release-gated, so main
running ahead between releases is normal — the script reports that distance as
information and still exits 0.

## Invariants

- The server reads `PORT` only — `FASTMCP_PORT` is not wired up.
- `PORT`, `fly.toml` `internal_port`, and `[metrics] port` must all stay 8080.
- These three routes are external contracts (Smithery/Glama/Fly) — never drop:
  `/.well-known/mcp/server-card.json`, `/.well-known/glama.json`, `/health`.

These are enforced by `.claude/hooks/check_invariants.py`, not just documented
here. It runs two ways: as a `PostToolUse` hook (wired in `.claude/settings.json`)
after an agent edit, and with `--standalone` as a standing assertion in `/verify`,
in `deploy-staging.yml` on every push to main, and in `release.yml` before the
PyPI publish (there with `--expect <tag>`, which anchors the triad to the release
tag — self-consistent files pinned to the wrong version is still drift). The hook path only sees `Write|Edit|MultiEdit`, so a `sed`, a vim
edit or a merge bypasses it — the standalone runs are what cover the tree at rest.

It fails **open**: anything it cannot parse is not an opinion, so a clean exit
means "found no disagreement", not "checked everything".

## Verify

`/verify` (or `uv run --no-sync python scripts/mcp_smoke_test.py --deployed`)
calls `company_search` against prod and asserts a known-good result.

## Citing the vendored docs

Cite `.claude/hooks/HOOKS-REF.md` and any other vendored reference **by heading,
never by line number** — line numbers drift silently across doc updates, so a
stale `:295` still resolves, just to the wrong sentence. A heading survives edits
above it and fails loudly if renamed.

> **Promote to global.** This is a fleet-wide rule, not a dd one; move it to
> `~/.claude/CLAUDE.md` when the workbench extends past this repo. Existing
> line-number citations in `.claude/hooks/*.py` and `FIELD-NOTES.md` predate it
> and are not yet converted.
