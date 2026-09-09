"""Tests for the hand-deploy acknowledge gate.

This file exists because of two measured defects, both on 2026-09-08.

1. **Precision.** The matcher was `\\bfly(?:ctl)?\\s+deploy\\b` — unanchored, so it
   matched the phrase anywhere: inside an `echo`, a commit message, a heredoc, a
   file being written. Measured 4 firings on inert strings and none on a real
   deploy. Recall was never the problem; the guard fired correctly on every real
   deploy form. It simply also fired on prose, which is how a gate trains you to
   ignore it.

2. **Strength.** The gate returned `"ask"` for everything, which is a prompt a
   human can click through. For an irreversible prod action the doctrine is deny
   plus a typed escape (`FLY_DEPLOY_ACK=1`), so the bypass leaves a trace in
   shell history that a clicked prompt does not.

The ACK path returns `"ask"`, deliberately, and the two ACK cases below assert
that exact string rather than "not deny". `"allow"` would skip the permission
prompt entirely (HOOKS-REF.md:1744), and since a `"deny"` reason is shown to
*Claude* (:1745), an agent that read the reason could set the variable and
deploy with no human involved. `"ask"` is the only decision documented to force
a prompt even in auto mode (:1755). A hook returning `"allow"` would satisfy a
weaker assertion while carrying the whole defect.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_HOOK = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "pre_bash_deploy.py"


def decide(command: str, *, ack_in_env: bool = False, tool: str = "Bash") -> str | None:
    """Run the real hook on a synthetic PreToolUse payload.

    Returns the permissionDecision, or None when the hook stayed silent. Uses
    sys.executable rather than a bare `python3`, which resolves against PATH and
    so varies by machine — the same reason check_invariants.py avoids it.
    """
    env = dict(os.environ)
    env.pop("FLY_DEPLOY_ACK", None)
    if ack_in_env:
        env["FLY_DEPLOY_ACK"] = "1"

    payload = json.dumps({"tool_name": tool, "tool_input": {"command": command}})
    proc = subprocess.run(
        [sys.executable, str(_HOOK)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert proc.returncode == 0, f"hook exited {proc.returncode}: {proc.stderr}"

    out = proc.stdout.strip()
    if not out:
        return None
    return json.loads(out)["hookSpecificOutput"]["permissionDecision"]


# --- defect 2: a real deploy must be denied, not merely prompted ---


@pytest.mark.parametrize(
    "command",
    [
        "fly deploy",
        "flyctl deploy",
        "fly deploy --ha=false --remote-only",
        "git push && fly deploy",
        "git push && flyctl deploy",
        "git push; fly deploy",
        "  fly deploy",
    ],
)
def test_real_deploy_is_denied(command):
    assert decide(command) == "deny"


# --- defect 1: inert text must not fire at all ---


@pytest.mark.parametrize(
    "command",
    [
        'echo "run fly deploy to ship"',
        'git commit -m "note: fly deploy is manual"',
        "grep -r 'fly deploy' docs/",
        "fly logs",
        "fly status",
    ],
)
def test_inert_text_does_not_fire(command):
    assert decide(command) is None


# --- the escape, in both forms a person actually types it ---


def test_ack_as_command_prefix_asks():
    """A `VAR=val cmd` prefix is part of the command text, not the hook's env."""
    assert decide("FLY_DEPLOY_ACK=1 fly deploy") == "ask"


def test_ack_as_command_prefix_flyctl_asks():
    """`flyctl` is the canonical binary; `fly` is commonly a symlink to it.

    Added 2026-09-09 as a regression pin, not a fix: both the `if` dispatch
    filter and `DEPLOYERS` already named `flyctl`, and this case passed on
    first run. It exists so a future edit narrowing either one fails here.
    """
    assert decide("FLY_DEPLOY_ACK=1 flyctl deploy") == "ask"


def test_ack_exported_in_environment_asks():
    """The other legitimate form: exported by the caller before claude launched."""
    assert decide("fly deploy", ack_in_env=True) == "ask"


# --- fail-open posture ---


def test_non_bash_tool_is_ignored():
    assert decide("fly deploy", tool="Write") is None


def test_unparseable_payload_exits_clean():
    """Anything it cannot read is not an opinion — it must not block the session."""
    proc = subprocess.run(
        [sys.executable, str(_HOOK)],
        input="not json",
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""
