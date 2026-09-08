#!/usr/bin/env python3
"""PostToolUse: lint the edited Python file, then run the suite.

Exits immediately for non-Python edits, so the cost lands only where it buys
something. pytest runs fail-fast: ~18s green, ~2-3s to first failure.

Two channels, chosen per the runtime rather than by habit:

  a real failure  -> exit 2, reason on stderr. The tool already ran, but the
                     model does see the text (HOOKS-REF.md:865).
  everything else -> exit 0 with hookSpecificOutput.additionalContext on stdout
                     (:1969), which lands next to the tool result (:989).

Not stderr at exit 0. That was the original shape here and it reached nobody:
measured 2026-09-08, the summary text sat verbatim in the transcript's
hook_success record while the model was handed "" (FIELD-NOTES §1, §8).

Fails OPEN if the tooling itself cannot run (missing uv, timeout) — a broken
toolchain should surface as a broken toolchain, not as a phantom test failure.
Those branches say so through additionalContext, because a gate that has
silently stopped running is worth more to know about than a passing one.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TAIL = 20
TIMEOUT_S = 180

# Counts only, never the duration. On --continue/--resume the saved text is
# replayed rather than re-run (HOOKS-REF.md:1005), so an elapsed time would be
# restated later as though it were fresh.
COUNTS = re.compile(r"\b(\d+)\s+(passed|failed|errors?|skipped|xfailed)\b")


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


def _context(text: str) -> int:
    """Hand text to the model as context. Declarative statements only.

    Phrasing matters: text framed as an out-of-band instruction can trip
    prompt-injection defenses and be surfaced to the user instead of absorbed
    as context (HOOKS-REF.md:1003).
    """
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": text,
        }
    }))
    return 0


def _summarise(stdout: str) -> str:
    found = COUNTS.findall(stdout)
    return ", ".join(f"{n} {word}" for n, word in found) if found else "no counts reported"


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
        return _context(
            "The ruff lint check did not run in this repo: ruff could not be "
            "started. Python edits are not being lint-checked in this session."
        )
    if lint.returncode != 0:
        print(f"\nruff failed on {file_path}:\n", file=sys.stderr)
        print(_tail(lint), file=sys.stderr)
        return 2

    tests = _run(["uv", "run", "--no-sync", "pytest", "-x", "-q"])
    if tests is None:
        return _context(
            "The test suite did not run in this repo: pytest could not be "
            "started. Python edits are not being test-checked in this session."
        )
    if tests.returncode != 0:
        print(f"\nTests fail after editing {file_path}:\n", file=sys.stderr)
        print(_tail(tests), file=sys.stderr)
        return 2

    return _context(
        f"ruff reports no issues and the test suite passes: {_summarise(tests.stdout)}."
    )


if __name__ == "__main__":
    raise SystemExit(main())
