---
allowed-tools: Bash(uv run --no-sync python scripts/mcp_smoke_test.py:*), Bash(uv run --no-sync python .claude/hooks/check_invariants.py:*)
description: Assert the repo invariants at rest, then smoke-test the deployed server against a known-good Companies House result
---

# /verify

Two checks, local first — it is instant and catches the thing that has actually
shipped broken twice.

## 1. Repo invariants at rest

```
uv run --no-sync python .claude/hooks/check_invariants.py --standalone
```

Same script as the `PostToolUse` hook, same four checks, no payload. The hook
path only sees agent edits through `Write|Edit|MultiEdit`; a `sed -i` from Bash,
a human in vim, or a merge commit bypasses it, so this is the only thing that
asserts the version triad *at rest*. Exit 0 clean, exit 2 with the reason on
stderr — report that reason verbatim, do not paraphrase it.

The triad (`pyproject.toml`, `server.py` server card, `server.json` x2) is the
one that drifted at `daaa9dc` and again at v1.3.0. The same line runs in CI, in
`deploy-staging.yml` on every push to main and in `release.yml` before publish.

It fails **open** by design: anything it cannot parse is not an opinion. A clean
exit therefore means "found no disagreement", not "verified every file".

## 2. Deployed smoke test

Run the deployed smoke test:

```
uv run --no-sync python scripts/mcp_smoke_test.py --deployed
```

It calls `company_search(query="Carillion", items_per_page=2)` against
`https://uk-due-diligence-mcp.fly.dev/mcp` and asserts `total_results > 0`
and that company `03782379` (CARILLION PLC) is present.

Report the result verbatim — the target, timing, `total_results`, the company
numbers returned, and PASS/FAIL. Exit 0 is pass, exit 1 is fail.

If it fails, do not guess at the cause. Check in this order and say which one broke:

1. `curl https://uk-due-diligence-mcp.fly.dev/health` — is the app up at all?
2. `scripts/check-deploy-drift.sh` — is prod running the latest release tag?
3. A `CH_API_KEY` problem shows up as a tool error, not an empty result set.

An empty result set is a real regression, not a flaky test: Carillion is in
liquidation, so its record is finished changing.
