---
status: landed
repo: uk-due-diligence-mcp
date: 2026-11-18
supersedes: none
sessions: one Claude Code session, 2026-11-17 → 2026-11-18 (validation audit → 1.7.0 → wrap-up)
---

# Handoff: validation audit fixes (1.7.0)

## Purpose and scope

Fix the uk-due-diligence-mcp findings (D1–D4) of the 2026-11-17 validation
audit plus the residual unstructured errors in #37, and ship them. Template:
[TEMPLATE-handoff.md](TEMPLATE-handoff.md), shape A. Changes:
[CHANGELOG 1.7.0](../../CHANGELOG.md), PRs #40 (fixes) and #42 (release).

## What shipped

- **1.7.0, tag `v1.7.0` → `772164e`**, released 2026-11-18 via `release.yml`.
- Verified: prod `/health` = `1.7.0` / `git_sha 772164e…`;
  `scripts/check-deploy-drift.sh` OK; `tests/live/validation_audit_calls.py`
  8/8 on staging-from-main, on prod, and through uk-business-mcp
  (`upstream_audit_via_proxy.py dd`).
- Since: CLAUDE.md contracts (#44), no hardcoded tool counts (#45), generated
  [tool reference](../tool-reference.md) (#46), `/release` skill with the gates (#47).

## Decisions

1. **Search filters are applied server-side, capped at 500 upstream results
   per call.** Companies House `/search/companies` accepts `status`/`type` and
   ignores them; `/advanced-search` filters but matches names differently. The
   cap bounds latency; hitting it is reported as `is_partial`, never as a
   complete answer. Descriptions state "500", not the constant name.
2. **A missing upstream flag is `None`, never `False`** (`overdue`, as
   `has_charges` already was): unknown is not an all-clear.
3. **Malformed company number = `validation`, distinct from `not_found`**: in
   due diligence "not found" is a finding.

## Gotchas

- Companies House `/search/companies` silently ignores filters — it returns
  200 with unfiltered results.
- FastMCP reports **its own** version in the handshake unless
  `FastMCP(version=...)` is passed; the version comes from package metadata, so
  run `uv sync` after `uv lock` or `test_server_version_is_the_project_version`
  fails on a stale `.venv`.
- A branch staging deploy needs `--ref <branch>` (see the `/release` skill).

## Open issues

#43 · #41 · #26 · #28 · #29

## Recommended next step

#43: PR CI modelled on uk-legal-mcp's `ci.yml` (suite, invariants,
`gen_reference.py --check`). Every PR in this work was merged on local runs
only. Then #41 (port legal's `StructuredErrorMiddleware` so schema rejections
are structured).

## Re-verify

```bash
curl -s https://uk-due-diligence-mcp.fly.dev/health
scripts/check-deploy-drift.sh
uv run --no-sync python tests/live/validation_audit_calls.py --url https://uk-due-diligence-mcp.fly.dev/mcp
# through the proxy, from ../uk-business-mcp:
uv run --no-sync python tests/live/upstream_audit_via_proxy.py dd
```

For a fresh session: everything above is merged on `main`; start from the
`/release` skill and CLAUDE.md, and #43.
