#!/usr/bin/env python3
"""Append one JSONL line per hook event. Never blocks, never fails loudly."""
import json
import os
import stat
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
    # Opened non-blocking and checked to be a regular file before writing: a
    # plain open("a") on a FIFO with no reader blocks forever, and a hook that
    # hangs is killed on timeout with its output discarded. With O_NONBLOCK a
    # reader-less FIFO raises ENXIO at once; any other non-regular sink (a FIFO
    # with a reader, a device) is rejected via fstat before a byte is written.
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(out, flags, 0o666)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(f"metrics destination is not a regular file: {out}")
        line = json.dumps(rec, separators=(",", ":")) + "\n"
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


def main() -> None:
    append_event(json.loads(sys.stdin.read()))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
