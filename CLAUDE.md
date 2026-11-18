# uk-due-diligence-mcp

FastMCP server over 5 UK registers — Companies House, Charity Commission,
The Gazette, HMLR Land Registry, and OFSI/OFAC/EU/UN sanctions.
Flat layout: `server.py` registers 8 domain modules. For the live tool list don't
trust a count in prose — run `uv run --no-sync fastmcp inspect server.py:mcp`
(add `--format fastmcp -o manifest.json` for the full JSON).

`docs/tool-reference.md` is generated: `uv run --no-sync python scripts/gen_reference.py`
captures the surface with `fastmcp inspect` and rewrites only the tables between
`GENERATED` markers (title annotation + first sentence of the description). A new
tool namespace needs its own marker block. `--check` (run in `release.yml`; and in PR
CI once #43 lands) fails on drift or on a tool missing from every table. The script
is shared verbatim with uk-legal-mcp and uk-business-mcp.

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

Release gates, one repo at a time:
- The only approval gate is `gh release create`. Feature and release-prep PRs
  may be merged once the suite passes; there is no PR CI yet (#43), so run
  `uv run pytest -m "not live" -q` and `check_invariants.py --standalone` locally.
- After merging, before tagging: staging redeploys from `main`; confirm
  `/health` `git_sha` is the merge commit and `tests/live/validation_audit_calls.py`
  passes.
- Before asking for the gate, state the rollback: the previous Fly image
  (`fly releases -a uk-due-diligence-mcp --image`), the command
  (`FLY_DEPLOY_ACK=1 fly deploy -a uk-due-diligence-mcp --image <previous>`),
  and the PyPI yank page (`https://pypi.org/manage/project/uk-due-diligence-mcp/release/<version>/`
  — yanking is web-only).
- After release: prod `/health` shows the version and the tag's SHA,
  `scripts/check-deploy-drift.sh` passes, and the live script passes against
  prod and through uk-business-mcp (a live proxy, no PyPI pin; tools prefixed
  `dd_`). If any check fails, stop and give the rollback.
- A branch staging deploy must run the branch's workflow:
  `gh workflow run deploy-staging.yml --ref <branch> -f ref=<branch>`
  (`--ref main` runs main's workflow file).

The version triad is asserted mechanically — see Invariants below — and
`release.yml` additionally checks it against the release tag, so neither a bump
that misses a file nor a tag that outruns the files can reach PyPI.

Run `scripts/check-deploy-drift.sh` to confirm what is actually running in prod
was built by CI from the **latest release tag**. Prod is release-gated, so main
running ahead between releases is normal — the script reports that distance as
information and still exits 0.

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
in `deploy-staging.yml` on every push to main, and in `release.yml` before the
PyPI publish (there with `--expect <tag>`, which anchors the triad to the release
tag — self-consistent files pinned to the wrong version is still drift). The hook path only sees `Write|Edit|MultiEdit`, so a `sed`, a vim
edit or a merge bypasses it — the standalone runs are what cover the tree at rest.

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

