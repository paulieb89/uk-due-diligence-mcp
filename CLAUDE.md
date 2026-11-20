# uk-due-diligence-mcp

FastMCP server over 5 UK registers — Companies House, Charity Commission,
The Gazette, HMLR Land Registry, and OFSI/OFAC/EU/UN sanctions.
Flat layout: `server.py` registers 8 domain modules. For the live tool list don't
trust a count in prose — run `uv run --no-sync fastmcp inspect server.py:mcp`
(add `--format fastmcp -o manifest.json` for the full JSON).

`docs/tool-reference.md` is generated: `uv run --no-sync python scripts/gen_reference.py`
captures the surface with `fastmcp inspect` and rewrites only the tables between
`GENERATED` markers (title annotation + first sentence of the description). A new
tool namespace needs its own marker block. `--check` (run in `ci.yml` on every PR and
push to main, and in `release.yml`) fails on drift or on a tool missing from every
table. The script is shared verbatim with uk-legal-mcp and uk-business-mcp.

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
Then `uv lock`, and add a `## [X.Y.Z] — YYYY-MM-DD` entry to `CHANGELOG.md`
(started at 1.7.0; name breaking changes under `### Breaking`). After `uv lock`,
`uv sync` locally too: the handshake version comes from installed package
metadata, and a stale `.venv` fails `test_server_version_is_the_project_version`.

Release gates — one approval gate at `gh release create`, one repo at a time,
rollback stated before the gate, staging re-verified from `main` before tagging,
prod and proxy checks after — and the step-by-step procedure live in the
`/release` skill (`.claude/skills/release/SKILL.md`).

The version triad is asserted mechanically — see Invariants below — and
`release.yml` additionally checks it against the release tag, so neither a bump
that misses a file nor a tag that outruns the files can reach PyPI.

Run `scripts/check-deploy-drift.sh` to confirm what is actually running in prod
was built by CI from the **latest release tag**. Prod is release-gated, so main
running ahead between releases is normal — the script reports that distance as
information and still exits 0.

## Foreman

Foreman, the software factory behind `eve-sf-app`, also works on this repo.

- **Starting it.** The `factory` label on an issue starts an unattended run that
  ends in a draft PR on a `factory/<issue>-...` branch, or in clarifying
  questions if the issue is unclear. Mentioning `@eve-sf-app` on an issue or PR
  starts an attended run: use it for a narrowly scoped follow-up, or to save a
  note to Foreman's memory for this repo. Only the owner and collaborators can
  start either.
- **The issue is the spec.** Put the decision and the scope in the issue before
  labelling it. If it leaves a behaviour decision open (e.g. #26, #29), state the
  chosen behaviour first.
- **Who labels.** Label `factory` only for single-repo, testable work; a
  maintainer applies it after review.
- **GitHub only.** Each run clones `origin/main`, so push work before labelling
  an issue that depends on it. Don't push to `factory/*` branches by hand.
- **It never merges.** Its PRs are drafts. `Non-live tests` must pass (the
  ruleset requires it); merge with `gh pr merge --merge`. Each merge deploys
  staging only.
- **Dependencies.** Commit `uv.lock` with any dependency change. The sandbox runs
  `uv sync --group dev --frozen` on Python 3.14 before every run, so a stale lock,
  or a dependency without Python 3.14 wheels, fails every run at setup even when
  CI (3.12) passes.
- **Following a run.** Progress and the result are posted as issue comments.
- **Not in this repo.** Changes to how Foreman behaves go in
  `paulieb89/factory-agent`, not here.

## Invariants

- `/health` returns `status`, `version` and `git_sha`. `GIT_SHA` is a Docker
  build arg passed by `deploy-staging.yml` and `release.yml`; a hand-built image
  reports `unknown`. The MCP handshake reports `uk-due-diligence-mcp` and the
  package version — FastMCP reports its own version unless `version=` is passed,
  so `FastMCP(version=SERVER_VERSION)` must stay.

- The server reads `PORT` only — `FASTMCP_PORT` is not wired up.
- `PORT`, `fly.toml` `internal_port`, and `[metrics] port` must all stay 8080.
- These three routes are external contracts (Smithery/Glama/Fly) — never drop:
  `/.well-known/mcp/server-card.json`, `/.well-known/glama.json`, `/health`.

These are enforced by `.claude/hooks/check_invariants.py`, not just documented
here. It runs two ways: as a `PostToolUse` hook (wired in `.claude/settings.json`)
after an agent edit, and with `--standalone` as a standing assertion in `/verify`,
in `ci.yml` on every PR and push to main, in `deploy-staging.yml` on every push to
main, and in `release.yml` before the PyPI publish (there with `--expect <tag>`,
which anchors the triad to the release tag — self-consistent files pinned to the
wrong version is still drift). The hook path only sees `Write|Edit|MultiEdit`, so
a `sed`, a vim edit or a merge bypasses it — the standalone runs are what cover
the tree at rest.

It fails **open**: anything it cannot parse is not an opinion, so a clean exit
means "found no disagreement", not "checked everything".

## Verify

`/verify` (or `uv run --no-sync python scripts/mcp_smoke_test.py --deployed`)
calls `company_search` against prod and asserts a known-good result.

`tests/live/validation_audit_calls.py` re-runs the 2026-11-17 validation audit's
calls against a deployed server and prints `/health` version + git_sha, the
recorded before, the expected after and the actual result; exits 1 on any
failure. Default URL is staging; `--url https://uk-due-diligence-mcp.fly.dev/mcp`
for prod. Not collected by pytest.

## Error and input contracts

- Every failure is a structured `ToolError` (`raise_tool_error` /
  `raise_http_tool_error` from mcpfleet-obs). Parse upstream JSON with
  `http_client._json_or_error`, never bare `resp.json()` (#37).
  `tests/test_every_tool_structured_errors.py` forces a timeout and an HTML body
  under every tool and fails when a new tool has no entry. Schema rejections are
  still plain text (#41).
- Unknown is not negative: a missing upstream flag is `None`, never `False`
  (`has_charges`, `accounts.overdue`, `confirmation_statement.overdue`).
- Company numbers go through `_normalise_company_number`: malformed is
  `validation`, distinct from `not_found`.
- Companies House `/search/companies` ignores `status`/`type`, so
  `company_search` filters server-side, scanning at most 500 upstream results per
  call (`FILTER_SCAN_CAP`) and reporting `is_partial` when it stops at the cap.
  Tool descriptions state the number, not the constant name.

