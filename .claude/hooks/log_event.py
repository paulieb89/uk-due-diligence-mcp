#!/usr/bin/env python3
"""Append one JSONL line per hook event. Never blocks, never fails loudly."""
import json, os, sys
from datetime import datetime, timezone
from pathlib import Path


def main() -> None:
    data = json.loads(sys.stdin.read())
    now = datetime.now(timezone.utc)
    rec = {"ts": now.isoformat(timespec="seconds"), **data}
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR", "."))
    out = root / ".claude" / "metrics" / f"{now:%Y-%m-%d}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
