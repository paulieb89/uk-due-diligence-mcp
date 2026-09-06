---
allowed-tools: Bash(uv run --no-sync python scripts/mcp_smoke_test.py:*)
description: Smoke-test the deployed server against a known-good Companies House result
---

# /verify

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
2. `scripts/check-deploy-drift.sh` — is prod running what `origin/main` says?
3. A `CH_API_KEY` problem shows up as a tool error, not an empty result set.

An empty result set is a real regression, not a flaky test: Carillion is in
liquidation, so its record is finished changing.
