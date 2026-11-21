"""Tests for the hand-deploy acknowledge gate.

This file exists because of four measured defects: 1 and 2 on 2026-09-08, 3 and 4
on 2026-09-09.

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

3. **Coverage.** `_segments` merged multi-line commands into one segment, so
   `cd app\\nfly deploy` — and subshell and command-substitution forms — passed in
   silence and the deploy ran. `"\\n"` was already in OPERATORS, so the case was
   intended; the token was simply never emitted, making it dead code. Nothing
   failed and nothing warned. 69% of the session that found it used multi-line
   Bash, so this was the majority shape. The fix reintroduces one narrow, known
   false positive: a heredoc body line *opening* with the deployer. That is
   asserted below rather than left to be rediscovered.

4. **Crash posture.** Only `json.load` was guarded; everything after it was bare,
   and an uncaught exception exits 1 — non-blocking — so a crash on the deploy
   path failed open. Now scoped fail-closed: silent for an unrelated crash, exit
   2 when the raw payload mentions a deploy.

The ACK path returns `"ask"`, deliberately, and the two ACK cases below assert
that exact string rather than "not deny". `"allow"` would skip the permission
prompt entirely (HOOKS-REF.md § PreToolUse decision control), and since a
`"deny"` reason is shown to *Claude* (HOOKS-REF.md § PreToolUse decision
control), an agent that read the reason could set the variable and deploy with
no human involved. `"ask"` is the only decision documented to force a prompt
even in auto mode (HOOKS-REF.md § PreToolUse decision control). A hook
returning `"allow"` would satisfy a weaker assertion while carrying the whole
defect.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_HOOK = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "pre_bash_deploy.py"


@pytest.fixture(autouse=True)
def project_dir(tmp_path, monkeypatch):
    """Point every hook run at a throwaway project root.

    The hook now appends deny/ask records under
    `$CLAUDE_PROJECT_DIR/.claude/metrics/`. Without this, running the suite
    would write synthetic deploy records into the real checkout's metrics.
    Every subprocess below inherits os.environ, so setting it here covers them.
    """
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    return tmp_path


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


# --- defect 3: multi-line and subshell forms bypassed the gate entirely ---


@pytest.mark.parametrize(
    "command",
    [
        "cd app\nfly deploy",
        "uv run pytest\nfly deploy --ha=false\necho done",
        "(fly deploy)",
        "echo $(fly deploy)",
    ],
)
def test_multiline_and_subshell_deploys_are_denied(command):
    """Measured 2026-09-09: all four returned silence, and the deploy ran.

    `shlex` treats `\\n` as whitespace, so `_segments` merged every line into a
    single segment whose first token was `cd`; the deployer was never in command
    position. `"\\n"` was already in OPERATORS, so the case was intended — it was
    dead code, because a newline token was never emitted. Nothing failed and
    nothing warned, the same shape as a check that silently reads no file.

    Reachable, not theoretical: 69% of that session's Bash calls contained a
    newline. `cd`-then-run is the ordinary shape, not a contrived one.
    """
    assert decide(command) == "deny"


def test_acked_multiline_asks():
    """The escape must survive the fix in the shape people actually type it."""
    assert decide("cd app\nFLY_DEPLOY_ACK=1 fly deploy") == "ask"


@pytest.mark.parametrize(
    "command",
    [
        "cat > d.md <<'EOF'\nNever run fly deploy by hand.\nEOF",
        'echo "line one\nfly deploy inside quotes"',
        "python3 - <<'PY'\nprint('fly deploy')\nPY",
    ],
)
def test_multiline_prose_still_does_not_fire(command):
    """The zero-false-positive record must survive the multi-line fix.

    A newline inside quotes stays within its token, so prose keeps passing. This
    is the half of the fix most at risk from a future tokeniser change.
    """
    assert decide(command) is None


def test_heredoc_body_opening_with_the_deployer_is_denied():
    """A KNOWN, ACCEPTED false positive — asserted so it stays visible.

    A heredoc body line that *begins* with the deployer is indistinguishable
    from a command at this layer, so writing a runbook whose body opens with a
    bare `fly deploy` line is denied. It errs closed, which is the right
    direction for this gate, but it is still a false positive and this test
    exists so it is a known one rather than a surprise during doc work.

    The boundary is narrow and measured: the deployer must be the first token on
    the line. Prose mentioning it mid-line stays silent — pinned directly above.
    If this ever needs to change, the fix is to strip heredoc bodies before
    classification, not to loosen the tokeniser.
    """
    assert decide("cat > RUNBOOK.md <<'EOF'\nfly deploy --ha=false\nEOF") == "deny"


# --- defect 4: a crash on the deploy path failed open ---


def test_malformed_payload_mentioning_a_deploy_blocks():
    """Only `json.load` was guarded; everything after it was bare.

    `json.loads('"fly deploy"')` yields a str, `.get` raises AttributeError, and
    an uncaught exception exits 1 — a *non-blocking* error, so the deploy
    proceeded. The guard is scoped to the ambiguous-and-deploy-shaped case: a
    blanket `except: return 2` on a hook that runs for every Bash call would
    wedge the terminal on any bug in it.
    """
    proc = subprocess.run(
        [sys.executable, str(_HOOK)],
        input='"fly deploy"',
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 2
    assert "crashed classifying a possible deploy" in proc.stderr


def test_malformed_payload_without_a_deploy_stays_silent():
    """The other half of the scope: an unrelated crash must not block."""
    proc = subprocess.run(
        [sys.executable, str(_HOOK)],
        input="null",
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


# --- issue #62: a hook-issued deny or ask was never logged ---
#
# PermissionDenied fires only in auto mode and not when a PreToolUse hook blocks
# a call, so the guard has to record its own decisions. Measured 2026-09-09: a
# refused deploy reached the debug log and left nothing in .claude/metrics/.


def _run(payload: dict, *, ack_in_env: bool = False) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("FLY_DEPLOY_ACK", None)
    if ack_in_env:
        env["FLY_DEPLOY_ACK"] = "1"
    return subprocess.run(
        [sys.executable, str(_HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


def _payload(command: str) -> dict:
    return {
        "session_id": "s-test",
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
    }


def _records(root: Path) -> list[dict]:
    metrics = root / ".claude" / "metrics"
    if not metrics.exists():
        return []
    lines: list[str] = []
    for f in sorted(metrics.glob("*.jsonl")):
        lines.extend(f.read_text(encoding="utf-8").splitlines())
    return [json.loads(line) for line in lines]


def test_denied_deploy_writes_one_record(project_dir):
    payload = _payload("fly deploy")
    proc = _run(payload)
    assert proc.returncode == 0

    records = _records(project_dir)
    assert len(records) == 1
    rec = records[0]
    # Same shape as log_event.py: a ts, then the hook input at top level.
    assert set(rec) == {"ts", *payload, "hookSpecificOutput"}
    assert rec["ts"].endswith("+00:00")
    for key, value in payload.items():
        assert rec[key] == value
    # The logged decision is exactly what the hook printed.
    assert rec["hookSpecificOutput"] == json.loads(proc.stdout)["hookSpecificOutput"]
    assert rec["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize(
    ("command", "ack_in_env"),
    [("FLY_DEPLOY_ACK=1 fly deploy", False), ("fly deploy", True)],
    ids=["command-prefix", "environment"],
)
def test_acked_deploy_writes_one_ask_record(project_dir, command, ack_in_env):
    proc = _run(_payload(command), ack_in_env=ack_in_env)
    assert proc.returncode == 0

    records = _records(project_dir)
    assert len(records) == 1
    out = records[0]["hookSpecificOutput"]
    assert out["permissionDecision"] == "ask"
    assert out == json.loads(proc.stdout)["hookSpecificOutput"]


def test_each_decision_appends_exactly_one_line(project_dir):
    _run(_payload("fly deploy"))
    _run(_payload("FLY_DEPLOY_ACK=1 fly deploy"))
    _run(_payload("git status"))

    decisions = [r["hookSpecificOutput"]["permissionDecision"] for r in _records(project_dir)]
    assert decisions == ["deny", "ask"]


def test_incoming_hook_specific_output_is_overridden(project_dir):
    """The record must carry the decision taken, not a field smuggled in."""
    payload = {**_payload("fly deploy"), "hookSpecificOutput": {"permissionDecision": "allow"}}
    _run(payload)
    (rec,) = _records(project_dir)
    assert rec["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize("command", ["git status", 'echo "run fly deploy to ship"', "fly logs"])
def test_non_deploy_bash_writes_nothing(project_dir, command):
    proc = _run(_payload(command))
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""
    assert not (project_dir / ".claude" / "metrics").exists()


def test_non_bash_tool_writes_nothing(project_dir):
    _run({**_payload("fly deploy"), "tool_name": "Write"})
    assert not (project_dir / ".claude" / "metrics").exists()


@pytest.mark.parametrize(
    ("command", "ack_in_env"),
    [("fly deploy", False), ("FLY_DEPLOY_ACK=1 fly deploy", False), ("fly deploy", True)],
    ids=["deny", "ask-prefix", "ask-env"],
)
def test_logging_failure_never_changes_the_decision(
    tmp_path_factory, monkeypatch, project_dir, command, ack_in_env
):
    """A broken metrics dir must not fail the deploy open, or block anything else.

    Deterministic failure: `.claude/metrics` is a regular file, so mkdir raises.
    The output and exit status must match a run whose logging succeeded.
    """
    good_root = tmp_path_factory.mktemp("good")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(good_root))
    good = _run(_payload(command), ack_in_env=ack_in_env)
    assert len(_records(good_root)) == 1
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))

    (project_dir / ".claude").mkdir()
    blocker = project_dir / ".claude" / "metrics"
    blocker.write_text("not a directory", encoding="utf-8")

    bad = _run(_payload(command), ack_in_env=ack_in_env)
    assert bad.returncode == good.returncode == 0
    assert bad.stdout == good.stdout
    assert bad.stderr == ""
    assert blocker.read_text(encoding="utf-8") == "not a directory"
