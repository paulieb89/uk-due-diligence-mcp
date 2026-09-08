---
allowed-tools: Bash(git status:*), Bash(git log:*), Bash(git diff:*), Bash(ls:*), Bash(grep:*), Bash(uv run:*), Read
argument-hint: [focus-area]
description: Load current repo state - git activity, work in flight, verification status
---

# Prime — current state

## Git
- Status: !`git status -sb`
- Recent: !`git log --oneline -10`
- Hotspots (30 commits): !`git log --pretty=format: --name-only -30 | sort | uniq -c | sort -rg | head -8`

## Work in flight
- Repo plans/specs: !`find docs -maxdepth 3 -name "*.md" \( -path "*/plans/*" -o -path "*/specs/*" \) 2>/dev/null | xargs -r ls -t 2>/dev/null | head -5`
- Backlog (top 3 live): !`grep -m 3 "^- [^~]" docs/BACKLOG.md 2>/dev/null || echo none`
- Open TODOs: !`grep -rn "TODO\|FIXME" --include="*.py" --exclude-dir=.venv . 2>/dev/null | head -8 || echo none`
- Uncommitted diff shape: !`git diff HEAD --stat | tail -5`

## Verification
- Smoke (deployed): !`uv run --no-sync python scripts/mcp_smoke_test.py --deployed 2>&1 | tail -3`

## Task
Read CLAUDE.md if not already in context. If a focus area was given ($ARGUMENTS),
grep for it across *.py and *.md and read the 2-3 most relevant hits.
Then summarize in 6 lines max: branch state, what changed recently, anything
red in verification, and open threads — no headers, no emoji.