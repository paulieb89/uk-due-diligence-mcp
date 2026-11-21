#!/usr/bin/env python3
"""Append one JSONL line per hook event. Never fails loudly.

It does not hang on a reader-less FIFO, but mkdir and the write itself can still
stall on a hung filesystem; callers that must answer in time (pre_bash_deploy.py)
bound the wait themselves.
"""
import json
import os
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path


def append_event(data: dict) -> None:
    """Append one record for `data` to today's metrics file. May raise.

    Callers decide their own failure posture: run as a script, the __main__
    block swallows every exception; pre_bash_deploy.py runs this in a thread
    with a bounded wait so neither a failure nor a stall changes its decision.
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
        line = (json.dumps(rec, separators=(",", ":")) + "\n").encode("utf-8")
        # One record, one write in the normal case, so O_APPEND keeps concurrent
        # hooks' lines whole. os.write may still return short (signal, full
        # disk); loop so the remainder lands rather than leaving a truncated line.
        while line:
            written = os.write(fd, line)
            if written <= 0:
                raise OSError(f"metrics write made no progress: {out}")
            line = line[written:]
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
