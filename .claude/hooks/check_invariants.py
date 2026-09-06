#!/usr/bin/env python3
"""PostToolUse: assert this repo's four structural invariants after any edit.

Each of these was prose in a file that did not load (AGENTS.md), which is
how server.json drifted to 1.2.0 while the v1.3.0 release shipped. Prose an
agent may not read is not a control; this is.

Fails OPEN on anything it cannot parse. A gate that fires when it cannot tell
is worse than no gate — it trains you to ignore it.

Exit 0 clean, exit 2 with the reason on stderr (the mechanism the runtime
actually honours for feeding text back to the model).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Built by concatenation so this file does not match its own scan.
HOME_PREFIX = "/" + "home" + "/"


def _read(rel: str) -> str | None:
    try:
        return (ROOT / rel).read_text()
    except OSError:
        return None


def check_version_triad() -> list[str]:
    """pyproject == server.py server card == server.json (x2).

    The v1.3.0 release commit (8dd311f) touched pyproject.toml and server.py
    and never server.json, exactly as daaa9dc had already had to fix once.
    """
    try:
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
        declared = pyproject["project"]["version"]
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        return []

    found: dict[str, str] = {"pyproject.toml": declared}

    server_py = _read("server.py")
    if server_py:
        m = re.search(r'"version":\s*"([0-9]+\.[0-9]+\.[0-9]+)"', server_py)
        if m:
            found["server.py (server card)"] = m.group(1)

    server_json_raw = _read("server.json")
    if server_json_raw:
        try:
            sj = json.loads(server_json_raw)
            if "version" in sj:
                found["server.json (top level)"] = sj["version"]
            for pkg in sj.get("packages") or []:
                if "version" in pkg:
                    found["server.json (packages[])"] = pkg["version"]
        except ValueError:
            pass

    if len(set(found.values())) <= 1:
        return []
    lines = [f"    {name:28} {value}" for name, value in found.items()]
    return [
        "VERSION TRIAD DRIFT — these must all match:\n"
        + "\n".join(lines)
        + "\n    A release is not done until all four agree. See CLAUDE.md > Releasing."
    ]


def check_port_invariant() -> list[str]:
    """PORT, internal_port and [metrics] port must all be 8080; no FASTMCP_PORT."""
    problems: list[str] = []
    fly = _read("fly.toml")
    if fly is not None:
        ports = {
            "PORT": re.search(r'^\s*PORT\s*=\s*"?(\d+)"?', fly, re.M),
            "internal_port": re.search(r"^\s*internal_port\s*=\s*(\d+)", fly, re.M),
            "[metrics] port": re.search(r"\[metrics\][^\[]*?^\s*port\s*=\s*(\d+)", fly, re.M | re.S),
        }
        wrong = {k: m.group(1) for k, m in ports.items() if m and m.group(1) != "8080"}
        if wrong:
            problems.append(
                "PORT INVARIANT — fly.toml must use 8080 throughout, found: "
                + ", ".join(f"{k}={v}" for k, v in wrong.items())
            )

    for rel in ("fly.toml", "fly.staging.toml", "server.py", "Dockerfile"):
        text = _read(rel)
        if text and "FASTMCP_PORT" in text:
            problems.append(
                f"FASTMCP_PORT in {rel} — the server reads PORT only "
                f"(server.py: os.environ.get('PORT', '8080')). FASTMCP_PORT is not wired up."
            )
    return problems


def check_routes() -> list[str]:
    """The three routes external services contract against."""
    required = (
        "/.well-known/mcp/server-card.json",  # Smithery
        "/.well-known/glama.json",  # Glama maintainer claim
        "/health",  # Fly health check
    )
    server_py = _read("server.py")
    if server_py is None:
        return []
    missing = [r for r in required if r not in server_py]
    if not missing:
        return []
    return [
        "ROUTE GUARD — server.py no longer registers: "
        + ", ".join(missing)
        + "\n    These are external contracts (Smithery / Glama / Fly health check)."
    ]


def check_absolute_paths() -> list[str]:
    """No machine-local absolute paths in tracked files."""
    try:
        proc = subprocess.run(
            ["git", "grep", "-l", "-F", HOME_PREFIX, "--", "."],
            cwd=ROOT, capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode not in (0, 1):
        return []
    hits = [f for f in proc.stdout.split() if f]
    if not hits:
        return []
    return [
        "ABSOLUTE PATH in tracked files: "
        + ", ".join(hits)
        + "\n    These break for every other clone. Name the repo, not the path."
    ]


def main() -> int:
    try:
        json.load(sys.stdin)
    except Exception:
        return 0  # unparseable input: no opinion

    problems: list[str] = []
    for check in (
        check_version_triad,
        check_port_invariant,
        check_routes,
        check_absolute_paths,
    ):
        try:
            problems.extend(check())
        except Exception:
            continue  # a broken check must not block the session

    if not problems:
        return 0

    print("\nRepo invariant violation:\n", file=sys.stderr)
    for p in problems:
        print(f"  - {p}\n", file=sys.stderr)
    print("Fix before continuing. These are enforced, not advisory.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
