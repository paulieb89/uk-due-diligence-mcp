#!/usr/bin/env python3
"""Append one JSONL line per hook event. Never blocks, never fails loudly."""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def append_event(data: dict) -> None:
    """Append one record for `data` to today's metrics file. May raise.

    Callers decide their own failure posture: main() swallows everything, and
    pre_bash_deploy.py wraps this so a logging failure never changes a decision.
    """
    now = datetime.now(timezone.utc)
    rec = {"ts": now.isoformat(timespec="seconds"), **data}
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR", "."))
    out = root / ".claude" / "metrics" / f"{now:%Y-%m-%d}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, separators=(",", ":")) + "\n")


def main() -> None:
    append_event(json.loads(sys.stdin.read()))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
