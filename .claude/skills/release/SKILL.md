---
name: release
description: Cut and ship a uk-due-diligence-mcp release. Use when asked to release, cut a release, ship, or deploy this project.
disable-model-invocation: true
---

The procedure around the release machinery, not the machinery itself.
`.github/workflows/release.yml` (invariants + tag check → tool-reference check
→ build → PyPI → Fly deploy) and `.claude/hooks/check_invariants.py` are the
deterministic parts; read them and `CLAUDE.md` → Releasing rather than trusting
a paraphrase here. A hand-run `fly deploy` is refused by
`.claude/hooks/pre_bash_deploy.py`.

## Gates (shared with uk-legal-mcp and uk-business-mcp)

- **One approval gate: `gh release create`.** Feature and release-prep PRs
  may be merged once the `Non-live tests` check (`ci.yml`) is green; the
  "main: pull request + CI" ruleset requires it. Running
  `uv run --no-sync pytest -m "not live" -q`,
  `uv run --no-sync python .claude/hooks/check_invariants.py --standalone` and
  `uv run --no-sync python scripts/gen_reference.py --check` locally first is
  optional, a quicker check rather than a requirement.
- **One repo at a time.** Release, verify prod, then start the next repo. If
  any prod check fails, stop, give the rollback, and do not release the next.
- **Staging from `main` before tagging.** After the prep PR merges, staging
  redeploys from `main`: `/health` `git_sha` must be the merge commit and
  `tests/live/validation_audit_calls.py` must pass.
- **State the rollback before asking for the gate** (see Rollback below).
- **Verify prod and the proxy after** (step 9).
- CHANGELOG names breaking changes tersely under `### Breaking`; check every
  line against the code, including text the user supplies.

## Steps

1. Verify `main` is clean and up to date with `origin/main`.
2. Read the PRs merged since the last tag (`gh pr view`) to write the
   CHANGELOG entry.
3. Branch `chore/prepare-X.Y.Z-release` off `main`.
4. Bump the version triad by hand — `pyproject.toml`, `server.py`
   (`smithery_server_card`), `server.json` (×2) — then `uv lock` and `uv sync`
   (the handshake version comes from installed package metadata; a stale
   `.venv` fails `test_server_version_is_the_project_version`). Confirm with
   `check_invariants.py --standalone --expect vX.Y.Z`.
5. Add a `## [X.Y.Z] — YYYY-MM-DD` entry to `CHANGELOG.md`.
6. Open the PR, wait for `Non-live tests` to pass, then merge it with a merge
   commit (`gh pr merge --merge`, this repo's convention; precedent: #42).
7. Watch `deploy-staging.yml` for the merge commit, then check staging
   `/health` (version and `git_sha` == merge commit) and run
   `uv run --no-sync python tests/live/validation_audit_calls.py`.
8. Tag the merge commit `vX.Y.Z` and push the tag separately. State the
   rollback, then ask for the gate. On approval,
   `gh release create vX.Y.Z --verify-tag` with the CHANGELOG entry as notes;
   watch `release.yml` with `gh run watch --exit-status`.
9. Verify what actually shipped:
   - prod `/health` shows the version and the tag's SHA;
   - `scripts/check-deploy-drift.sh`;
   - `tests/live/validation_audit_calls.py --url https://uk-due-diligence-mcp.fly.dev/mcp`;
   - through the proxy, from uk-business-mcp:
     `uv run --no-sync python tests/live/upstream_audit_via_proxy.py dd`
     (a live mount with no PyPI pin — it serves the new release immediately).

## Rollback

State all three before asking for the gate:

- Previous image: `fly releases -a uk-due-diligence-mcp --image` (the current
  top row is what a rollback restores).
- Command: `FLY_DEPLOY_ACK=1 fly deploy -a uk-due-diligence-mcp --image <previous image>`
  — the deploy hook still asks for confirmation. An image older than 1.7.0
  reports no `version`/`git_sha` on `/health`.
- PyPI: yanking is web-only —
  `https://pypi.org/manage/project/uk-due-diligence-mcp/release/X.Y.Z/` → Yank.

## Branch staging deploys

`gh workflow run deploy-staging.yml --ref <branch> -f ref=<branch>` runs the
branch's own workflow file; `--ref main` runs main's, which may lack the
branch's changes.
