---
status: landed (dd); uk-legal harvest deferred
repo: uk-due-diligence-mcp
date: 2026-09-08
supersedes: none — implements the ratified+investigated hook-mechanism addendum (amends handoff-2026-09-08-v2 §2 and §5)
sessions: claude.ai project chat ("Claude Code Hooks/CLI/Plugins reference") → local Claude Code session, 2026-09-08 (investigation, then implementation)
---

# Hook channel corrections

Two hooks spoke to Claude on channels that don't carry. Measured, then fixed.

## What shipped

**`pre_bash_deploy.py` — acknowledge gate.** Three outcomes: `deny` for a hand
deploy, `ask` when `FLY_DEPLOY_ACK` is set, silence otherwise. Matcher rewritten
to tokenise with `shlex` (quote-aware, `punctuation_chars=True`) and anchor to
command position, so `git push; fly deploy` is caught and `echo "run fly deploy
to ship"` is not. Both escape forms honoured: command-string env prefix and the
hook's own environment.

**`settings.json` — spawn filter**, as two handlers: `Bash(fly *)` and
`Bash(flyctl *)`. Both `$CLAUDE_PROJECT_DIR` anchors preserved (§7).

**`post_edit_python.py` — `additionalContext`.** Success and both fail-open
branches now reach the model; failures keep `exit 2`. Counts only, no durations.

**`tests/test_pre_bash_deploy.py`** — 15 cases, a standing assertion.

**`CLAUDE.md`** — one line under Releasing naming `FLY_DEPLOY_ACK=1`.

**`FIELD-NOTES.md`** — §4 DONE with its table amended, §5 RESOLVED, §7 sweep
marked run, §2 corrected, §1 marked fixed, new §8 with the measurements.

## The one design change from the ratified spec

The addendum rejected `"allow"` on the ACK path and prescribed pass-through
(`exit 0`, no JSON). Pass-through is not equivalent to "a human answers a
prompt": in auto mode the classifier approves Bash calls silently. `"ask"` is
the only decision documented to force a prompt anyway (`HOOKS-REF.md:1755`), and
its reason is shown to the user rather than to Claude — the right audience for a
confirmation. Shipped as `"ask"`.

Second, smaller: the filed `"if": "Bash(fly deploy *)"` is the noisier choice. A
pattern specifying more than the command name spawns the hook on any command
containing `$()` or `$VAR` (`:438`); a command-name pattern does not. And `if`
holds exactly one rule with no OR syntax (`:432`), hence two handlers.

## Evidence

The gate's first run caught a real pre-existing defect, not a staged one — the
test was written against the old hook and failed `assert 'ask' == 'deny'`. The
`additionalContext` change was verified by transcript probe: `content: ""`
before, `content: ["ruff reports no issues and the test suite passes: 152
passed."]` after, same session. Full record in FIELD-NOTES §8.

## Deferred

uk-legal's `post_edit_check.py` harvest (dead `startswith("src/")` test gate,
`cwd=`, containment check, unguarded `timeout=60`, exit-code shape), the
two-file settings anchoring that rides with it, ruff adoption there, and the
stale baseline-failure-filter question — all parked with that repo per the
2026-09-08 scope decision. Global `~/.claude/CLAUDE.md` additions remain the
user's call.
