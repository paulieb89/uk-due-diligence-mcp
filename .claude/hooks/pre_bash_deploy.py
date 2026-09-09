#!/usr/bin/env python3
"""PreToolUse: an acknowledge gate on hand-run Fly deploys.

A manual `fly deploy` ships prod but skips the PyPI publish, so the git tag and
the PyPI release silently stop describing what is running.

Four outcomes, and the choice of each is load-bearing:

  deploy, no ack -> "deny". The reason is shown to Claude (HOOKS-REF.md:1745),
                    so it names the escape rather than dead-ending.
  deploy, acked  -> "ask". NOT "allow": allow skips the permission prompt
                    (:1744), and since Claude reads the deny reason it could set
                    the variable itself and deploy with nobody watching. Plain
                    pass-through is not sufficient either — in auto mode the
                    classifier approves Bash calls silently. "ask" is the only
                    decision documented to force a prompt anyway (:1755), and
                    its reason is shown to the user, which is the right audience
                    for a confirmation.
  anything else  -> silent.
  crash          -> silent for an unrelated command, exit 2 when the raw payload
                    mentions a deploy. See main(); an uncaught exception exits 1,
                    which is non-blocking, so the deploy would otherwise proceed.

There are legitimate reasons to deploy by hand (rollback, a wedged machine).
FLY_DEPLOY_ACK=1 is the escape, honoured both as a command-string env prefix and
in this process's own environment — a `VAR=val cmd` prefix is part of the
command text the hook is handed, not of the hook's environment, so reading only
os.environ would miss the form people actually type.

Deliberately not a warning printed to stderr: at exit 0 that reaches nobody
(FIELD-NOTES §1). The decision JSON is the whole message.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys

ACK_VAR = "FLY_DEPLOY_ACK"
DEPLOYERS = {"fly", "flyctl"}
OPERATORS = {"&&", "||", ";", "|", "&", "\n", "(", ")"}
ASSIGNMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", re.S)

# Fallback only, for a command shlex cannot tokenise. Unanchored and so prone to
# matching prose — which is the old defect — but an unparseable command naming a
# deploy is exactly when a human should look, so this one errs closed.
FALLBACK = re.compile(r"\bfly(?:ctl)?\s+deploy\b")

DENY_REASON = (
    "Hand deploys bypass the release pipeline (PyPI publish + CI provenance).\n"
    "See CLAUDE.md > Releasing. Deliberate rollback: re-run with "
    f"{ACK_VAR}=1."
)

ASK_REASON = (
    f"{ACK_VAR} is set, so this hand deploy is acknowledged. It ships prod "
    "without publishing to PyPI, leaving the tags and PyPI describing a version "
    "that is not what is running. Confirm only if this is a rollback or a wedged "
    "machine."
)


def _segments(command: str) -> list[list[str]] | None:
    """Split into subcommands, respecting quotes. None if it cannot be parsed.

    Quote-aware so that `echo "a; fly deploy"` stays one token and never looks
    like a second subcommand — splitting the raw string on operators is what let
    prose match in the first place.

    Newline is *declared as punctuation*, not merely dropped from `whitespace`.
    Dropping it alone glues it into the adjacent word (`'app\\nfly'`); it has to
    be punctuation to be emitted as its own token. Without that, `cd app\\nfly
    deploy` lexed to one merged segment whose first token was `cd`, the deployer
    was never in command position, and a routine multi-line command walked past
    the gate in silence. `"\\n"` was already in OPERATORS, so the case was
    intended all along — it was dead code, because the token was never emitted.
    Nothing failed and nothing warned. Measured 2026-09-09: 69% of one session's
    Bash calls contained a newline, so this was the majority shape, not an edge.
    """
    lexer = shlex.shlex(command, posix=True, punctuation_chars="();<>|&\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return None

    segments: list[list[str]] = [[]]
    for token in tokens:
        if token in OPERATORS:
            segments.append([])
        else:
            segments[-1].append(token)
    return [s for s in segments if s]


def _acknowledged(value: str | None) -> bool:
    return bool(value) and value.strip().lower() not in {"0", "false", "no"}


def _classify(command: str) -> tuple[bool, bool]:
    """Return (is_deploy, acknowledged_in_command)."""
    segments = _segments(command)
    if segments is None:
        return bool(FALLBACK.search(command)), False

    for segment in segments:
        env_prefix: dict[str, str] = {}
        rest = list(segment)
        while rest:
            m = ASSIGNMENT.match(rest[0])
            if not m:
                break
            env_prefix[m.group(1)] = m.group(2)
            rest.pop(0)

        # Anchored to command position: the deployer must be the command being
        # run, not a word inside an argument. `fly -a app deploy` still counts.
        if rest and rest[0] in DEPLOYERS and "deploy" in rest[1:]:
            return True, _acknowledged(env_prefix.get(ACK_VAR))

    return False, False


def main() -> int:
    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
        if data.get("tool_name") != "Bash":
            return 0
        command = (data.get("tool_input") or {}).get("command") or ""
        is_deploy, acked_in_command = _classify(command)
    except Exception as exc:
        # Scoped fail-closed, deliberately not blanket. This hook runs on every
        # Bash call, so `except Exception: return 2` would wedge the terminal on
        # any bug in it. Only `json.load` was guarded before, and everything
        # after it was bare: `json.loads("null")` yields None, `.get` raises,
        # and an uncaught exception exits 1 — a non-blocking error, so the
        # deploy proceeded. Low reachability today; the real exposure is a
        # future edit introducing an exception on the deploy path and failing
        # open in silence. Same principle as FALLBACK: err closed only where the
        # command is ambiguous *and* a deploy is in play.
        if FALLBACK.search(raw):
            print(
                f"pre_bash_deploy crashed classifying a possible deploy: {exc}",
                file=sys.stderr,
            )
            return 2
        return 0

    if not is_deploy:
        return 0

    acked = acked_in_command or _acknowledged(os.environ.get(ACK_VAR))
    decision, reason = ("ask", ASK_REASON) if acked else ("deny", DENY_REASON)

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
