---
status: landed
repo: uk-due-diligence-mcp
date: 2026-11-20
as_of: f01cbb3 (main, clean, in sync with origin)
supersedes: none (amends handoff-2026-11-18-validation-audit.md's "Recommended next step")
issue: none (one thread; its follow-ups are #61–#73)
sessions: one Claude Code session, 2026-11-20
---

# Handoff: moving this repo onto the Foreman workflow

## Purpose and scope

Bring uk-dd's own tooling in line with the Foreman operating model. In that
model, Paul plus Claude/Codex plan and judge, Foreman (`eve-sf-app`) builds
draft PRs, and Paul plus Claude review and ship. Single repo except the new
`paulieb89/.github`. Canonical rules: `CLAUDE.md` § Foreman, the `/release`
skill.

## What landed (all on `main`)

- **#59:** `CLAUDE.md` § Foreman gains a "Who labels" bullet: `factory` goes
  only on single-repo, testable work, and a maintainer applies it after review.
- **#60:** `/prime` lists open issues and PRs (Foreman drafts are on
  `factory/*`). The `/release` skill now has Foreman drafts reviewed and
  marked ready with `gh pr ready` before the merge gate.
- **#75** (replaces #74, see Gotchas):
  - `docs/BACKLOG.md` is archived to `docs/archive/BACKLOG-2026-09.md`, frozen,
    with each line's outcome noted.
  - The never-built transcript-review plan is set to `status: superseded` and
    archived.
  - `/prime` now lists only `status: active` plans.
  - `TEMPLATE-handoff.md` gains an `issue:` field.
  - FIELD-NOTES routing now points to issues.
- **`paulieb89/.github`** (new, public): `.github/ISSUE_TEMPLATE/factory.yml`,
  "Factory task (maintainers)". Fields: problem, scope, acceptance criteria,
  out of scope. **No `labels:` key**, so filing an issue never starts a run.
- **This handoff's PR** also updates `CLAUDE.md`: a new "Work queue and
  handoffs" section, and the Foreman bullet now says Foreman never marks ready,
  merges or releases. § Releasing was checked line by line and needed no change
  (`check-deploy-drift.sh`: prod on v1.7.0; main 28 commits ahead, none of
  which change behaviour, so no release is due).
- **Issues #61–#73:** live backlog entries, each checked against the
  code on 2026-11-20. None is labelled `factory`. #61–#70 are code work;
  #71–#73 are decisions (`question`, never factory).
- Comments on #49 and #51 link back to the 2026-11-18 handoff.

## Decisions in force

1. **GitHub issues are the only work queue.** `docs/plans/` holds only long
   handoffs, each linked from its issue.
2. **Labelling stays human.** The shared form cannot apply `factory`.
3. **No cross-repo issues from this thread.** The property-shared logger port,
   the global cite-by-heading rule and the mcpfleet-obs trusted publisher are
   recorded only in the archive, as "not migrated".
4. **Backlog L26 (explore subagents) is closed:** Foreman overtook it.

## Facts and how they were observed

| Fact | How observed |
|---|---|
| uk-dd meets the "Before a repo gets Foreman" list | Ruleset `main: pull request + CI` read via `gh api` (requires `Non-live tests`, `non_fast_forward`, no bypass actors) |
| The factory form appears on uk-dd and AI-Whiteboard | **Paul observed it in a browser.** The API cannot show it: GraphQL `issueTemplates` returns `[]` for inherited templates |
| Foreman app access: uk-dd, scratch, AI-Whiteboard | Paul stated it; not queryable with this token |
| Reviewer stage: GPT-6.2, at most 2 revision rounds, no PR if the work still fails | Paul stated it, from `factory-agent`; not read here |
| `tests/fixtures/transcripts/*.jsonl` is unreferenced | `grep` over `*.py`, 2026-11-20 |

## Approvals and actions

| Date | Action | Approval | Evidence |
|---|---|---|---|
| 2026-11-20 | Created public repo `paulieb89/.github` and pushed the form | Paul: "Put it in a new public repo…" | repo `main` |
| 2026-11-20 | Merged #59 | Paul: "merge it once CI passes" | merge `94d9b1e` |
| 2026-11-20 | Merged #60, then #75 | Paul: "yes, merge both once CI passes" | `d0b58ed`, `f01cbb3` |
| 2026-11-20 | Created #61–#73 | Paul answered Q2–Q4 of the migration plan; **an explicit OK on Q1 was not recorded**, so the go-ahead was inferred | issues |

No paid or production actions were taken. Each merge redeployed **staging**
only; all changes were docs or `.claude/`, so server behaviour is unchanged.

## Gotchas

- **Stacked PRs:** `gh pr merge --delete-branch` on the lower PR **closes** the
  upper PR rather than retargeting it. Retarget the upper PR first
  (`gh pr edit N --base main`).
- **Stale CI result:** `gh pr checks --watch` can report an earlier PR's run
  for the same commit. Go by `mergeStateStatus` (`CLEAN` vs `BLOCKED`).
- **Deploy guard false positive:** `pre_bash_deploy.py` denies a Bash command
  whose text contains an apostrophe plus the phrase "fly deploy" (for example
  a heredoc of issue prose). `shlex` fails on the apostrophe, and the fallback
  regex then errs closed by design. Reproduced with a minimal payload. Write
  such text with the Write tool; never set `FLY_DEPLOY_ACK` to get around it.

## Open items

1. **Behaviour decisions** must be made before #62, #66 or #69 can be labelled
   (each issue says "confirm before labelling").
2. **Deploy-guard false positive:** file an issue or accept it. Paul has not
   decided.
3. **AI-Whiteboard:** check it against the "Before a repo gets Foreman" list
   (ruleset, required CI, setup command). Only the template and app access are
   known.
4. **The uk-legal harvest** deferred in `plan-2026-09-08-hook-channel-corrections.md`
   remains frontmatter only. Cross-repo, so out of scope per decision 3.
5. **Next build work** (unchanged from 2026-11-18): #49 FastMCP 4.1.8, then #51
   stdio.

## For a fresh session

Start with `/prime`, which now shows the real queue. Treat this file as a
claim as of `f01cbb3`: run `git log f01cbb3..HEAD -- . ':!docs/plans/'`, and
if that shows changes, re-check before relying on it. Preserve the archive
files unedited. Do not apply `factory` yourself; propose it. Report back on
which of the open items moved.
