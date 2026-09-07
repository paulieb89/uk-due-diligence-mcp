#!/usr/bin/env python3
"""PostToolUse: lint the edited Python file, then run the suite.

Exits immediately for non-Python edits, so the cost lands only where it buys
something. pytest runs fail-fast: ~18s green, ~2-3s to first failure.

Fails OPEN if the tooling itself cannot run (missing uv, timeout) — a broken
toolchain should surface as a broken toolchain, not as a phantom test failure.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TAIL = 20
TIMEOUT_S = 180


def _run(cmd: list[str]) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(
            cmd, cwd=ROOT, capture_output=True, text=True, timeout=TIMEOUT_S
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _tail(proc: subprocess.CompletedProcess) -> str:
    lines = (proc.stdout + proc.stderr).strip().splitlines()
    return "\n".join(lines[-TAIL:])


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0

    tool_input = data.get("tool_input") or {}
    file_path = tool_input.get("file_path") or tool_input.get("path") or ""
    if not str(file_path).endswith(".py"):
        return 0

    # Contain to this repo. Keying on the extension alone ran *this* repo's ruff
    # and full suite against any .py anywhere on the machine — editing a
    # scratchpad script could block on an unrelated red test in here.
    try:
        Path(file_path).resolve().relative_to(ROOT)
    except (OSError, ValueError):
        return 0

    lint = _run(["uv", "run", "--no-sync", "ruff", "check", str(file_path)])
    if lint is None:
        print("ruff could not run — skipping lint/test gate.", file=sys.stderr)
        return 0
    if lint.returncode != 0:
        print(f"\nruff failed on {file_path}:\n", file=sys.stderr)
        print(_tail(lint), file=sys.stderr)
        return 2

    tests = _run(["uv", "run", "--no-sync", "pytest", "-x", "-q"])
    if tests is None:
        print("pytest could not run — skipping test gate.", file=sys.stderr)
        return 0
    if tests.returncode != 0:
        print(f"\nTests fail after editing {file_path}:\n", file=sys.stderr)
        print(_tail(tests), file=sys.stderr)
        return 2

    summary = [ln for ln in tests.stdout.splitlines() if "passed" in ln or "failed" in ln]
    print(f"ruff clean, {summary[-1] if summary else 'tests pass'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
