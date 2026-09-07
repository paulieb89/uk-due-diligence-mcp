#!/usr/bin/env python3
"""Assert this repo's four structural invariants.

Two entry points. As a PostToolUse hook (Write|Edit|MultiEdit) it runs after an
agent edit. With --standalone it runs with no payload, for CI and /verify — the
standing assertion, because the hook only sees edits made through those tools:
a `sed -i` from Bash, a human in vim, or a merge commit all bypass it entirely.

Each of these was prose in a file that did not load (AGENTS.md), which is
how server.json drifted to 1.2.0 while the v1.3.0 release shipped. Prose an
agent may not read is not a control; this is.

Fails OPEN on anything it cannot parse. A gate that fires when it cannot tell
is worse than no gate — it trains you to ignore it.

Exit 0 clean, exit 2 with the reason on stderr (the mechanism the runtime
actually honours for feeding text back to the model).

Invoked as `uv run --no-sync python .claude/hooks/check_invariants.py`, matching
post_edit_python.py, CI and the smoke test — one invocation convention per
workbench. Bare `python3` is deliberately avoided: it resolves against PATH, so
the interpreter (and whether it has tomllib, 3.11+) varies by machine while CI
stays green. Where uv is unavailable, the documented fallback is the venv
interpreter directly: "$CLAUDE_PROJECT_DIR"/.venv/bin/python.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Narrowed to the actual leak pattern: "/home/" alone matches URL path segments
# and documented example paths, which would wedge every edit at exit 2 with no
# way to satisfy the gate. Built by concatenation so this file does not match
# its own scan.
HOME_PREFIX = ("/" + "home" + "/bch").encode()


def normalise_tag(tag: str) -> str:
    """Strip a single leading v.

    Lifted verbatim from property-shared/scripts/verify_release.py — same fleet,
    same problem, and a second implementation is a second thing to get wrong.

    Deliberately not `lstrip("v")`, which strips every leading v and turns the
    nonsense tag `vv1.0.0` into a plausible `1.0.0`.
    """
    tag = tag.strip()
    return tag[1:] if tag[:1] in ("v", "V") else tag


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


def check_release_tag(expected_tag: str) -> list[str]:
    """The triad agreeing with itself is worthless if the release tag disagrees.

    Without this, publishing v1.4.0 with every file still at 1.3.0 passes the
    triad check, builds a 1.3.0 wheel, uploads nothing (skip_existing: true) and
    deploys prod anyway — a green run producing the exact tag/PyPI drift the gate
    exists to prevent. Only release.yml passes --expect; it is the only context
    that knows the tag.

    Fails open on an unreadable pyproject for the same reason the rest of this
    file does: `uv build` fails on it one step later regardless.
    """
    try:
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
        declared = pyproject["project"]["version"]
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        return []

    want = normalise_tag(expected_tag)
    if declared == want:
        return []
    return [
        f"RELEASE TAG MISMATCH — tag {expected_tag!r} normalises to {want}, but "
        f"pyproject.toml declares {declared}.\n"
        "    The version triad is self-consistent and anchored to the wrong "
        "version. Bump the three files, or retag."
    ]


def check_port_invariant() -> list[str]:
    """PORT, internal_port and [metrics] port must all be 8080; no FASTMCP_PORT."""
    problems: list[str] = []
    # Both fly configs, matching the FASTMCP_PORT list below: a wrong
    # internal_port in fly.staging.toml passed this gate and surfaced only as a
    # failed staging health-check loop.
    for cfg in ("fly.toml", "fly.staging.toml"):
        fly = _read(cfg)
        if fly is None:
            continue
        ports = {
            "PORT": re.search(r'^\s*PORT\s*=\s*"?(\d+)"?', fly, re.M),
            "internal_port": re.search(r"^\s*internal_port\s*=\s*(\d+)", fly, re.M),
            "[metrics] port": re.search(r"\[metrics\][^\[]*?^\s*port\s*=\s*(\d+)", fly, re.M | re.S),
        }
        wrong = {k: m.group(1) for k, m in ports.items() if m and m.group(1) != "8080"}
        if wrong:
            problems.append(
                f"PORT INVARIANT — {cfg} must use 8080 throughout, found: "
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
    """No machine-local absolute paths in tracked files.

    Enumerates with `git ls-files -z` and matches on bytes: splitting whitespace
    mangled any tracked path containing a space, and decoding every file to text
    is both slower and able to manufacture a match across a decode boundary.
    """
    try:
        proc = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=ROOT, capture_output=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []

    hits: list[str] = []
    for raw in proc.stdout.split(b"\0"):
        if not raw:
            continue
        rel = raw.decode("utf-8", "surrogateescape")
        try:
            blob = (ROOT / rel).read_bytes()
        except OSError:
            continue
        if b"\0" in blob[:8192]:
            continue  # binary: not a path leak we can act on
        if HOME_PREFIX in blob:
            hits.append(rel)
    if not hits:
        return []
    return [
        "ABSOLUTE PATH in tracked files: "
        + ", ".join(hits)
        + "\n    These break for every other clone. Name the repo, not the path."
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Assert this repo's invariants.")
    # --standalone is load-bearing, not a convenience: as a hook this reads stdin
    # and fails open on anything unparseable, and CI supplies no payload at all —
    # so without the flag every CI run would exit 0 having checked nothing, which
    # is worse than no gate. The PostToolUse path below is unchanged.
    parser.add_argument(
        "--standalone", action="store_true",
        help="run with no hook payload (CI, /verify)",
    )
    parser.add_argument(
        "--expect", metavar="TAG",
        help="release tag the version triad must match (release.yml only)",
    )
    # argparse rejects a bare trailing --expect rather than silently reading the
    # next flag as its value: a malformed invocation must not become "no check".
    args = parser.parse_args()

    if not args.standalone:
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

    if args.expect:
        try:
            problems.extend(check_release_tag(args.expect))
        except Exception:
            pass

    if not problems:
        return 0

    print("\nRepo invariant violation:\n", file=sys.stderr)
    for p in problems:
        print(f"  - {p}\n", file=sys.stderr)
    print("Fix before continuing. These are enforced, not advisory.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
