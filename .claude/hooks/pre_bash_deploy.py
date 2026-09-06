#!/usr/bin/env python3
"""PreToolUse: warn before a hand-run Fly deploy. Asks — never blocks.

A manual `fly deploy` ships prod but skips the PyPI publish, so the git tag
and the PyPI release silently stop describing what is running. release.yml
fires on `release: published`, not on a pushed tag, so "I tagged it" does not
mean "it published".

There are legitimate reasons to deploy by hand (rollback, a wedged machine).
This surfaces the cost and lets a human decide.
"""
from __future__ import annotations

import json
import re
import sys

DEPLOY = re.compile(r"\bfly(?:ctl)?\s+deploy\b")

REASON = """`fly deploy` by hand ships prod but skips the PyPI publish.

The release pipeline is: GitHub release published -> release.yml -> PyPI -> flyctl deploy.
It does NOT fire on a pushed tag alone. Deploying by hand leaves PyPI and the git
tags describing a version that is not what is running.

Legitimate for a rollback or a wedged machine. If this is a release, cut a GitHub
release instead. Afterwards, scripts/check-deploy-drift.sh will tell you whether
prod matches origin/main."""


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0

    if data.get("tool_name") != "Bash":
        return 0
    command = (data.get("tool_input") or {}).get("command") or ""
    if not DEPLOY.search(command):
        return 0

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": REASON,
        }
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
