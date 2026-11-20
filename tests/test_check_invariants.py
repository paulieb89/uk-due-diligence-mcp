"""Tests for the check_invariants hook's path scoping.

Measured 2026-09-09: the PostToolUse `Write|Edit|MultiEdit` matcher has no path
predicate, and the hook read then discarded its payload, so editing a file in
the sibling `mcpfleet-obs` repo re-asserted *this* repo's triad and exited 0 —
a green light from a repo the edit never touched.

The check loop swallows exceptions from individual checks, so "no checks ran"
cannot be inferred from the exit code. Every check is replaced with a fake that
records its call, and the assertions are on that record.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
from pathlib import Path

import pytest

_HOOK = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "check_invariants.py"

CHECKS = (
    "check_version_triad",
    "check_port_invariant",
    "check_routes",
    "check_absolute_paths",
)


def _load():
    spec = importlib.util.spec_from_file_location("check_invariants_under_test", _HOOK)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def hook(monkeypatch):
    """The hook module with every check replaced by a call-recording fake."""
    module = _load()
    module.calls = []

    def fake(name, result=()):
        def _check(*args):
            module.calls.append(name)
            return list(result)

        return _check

    for name in CHECKS:
        monkeypatch.setattr(module, name, fake(name))
    monkeypatch.setattr(module, "check_release_tag", fake("check_release_tag"))
    module.fake = fake
    return module


def _run(hook, monkeypatch, argv, stdin):
    monkeypatch.setattr(sys, "argv", ["check_invariants.py", *argv])
    monkeypatch.setattr(sys, "stdin", stdin)
    return hook.main()


def _payload(file_path) -> io.StringIO:
    return io.StringIO(
        json.dumps({"tool_name": "Edit", "tool_input": {"file_path": file_path}})
    )


class _ExplodingStdin:
    """Stdin that fails the test if --standalone ever reads it."""

    def read(self, *args):
        raise AssertionError("--standalone must not read stdin")

    readline = read

    def __iter__(self):
        raise AssertionError("--standalone must not read stdin")


# --- outside the repo: no checks, exit 0 -------------------------------------


def test_sibling_repo_path_runs_no_checks(hook, monkeypatch, tmp_path):
    outside = tmp_path / "mcpfleet-obs" / "PROBE.md"
    assert _run(hook, monkeypatch, [], _payload(str(outside))) == 0
    assert hook.calls == []


def test_sibling_with_same_name_prefix_is_outside(hook, monkeypatch):
    # A string-prefix test would accept this; component-aware containment must not.
    sibling = hook.ROOT.parent / (hook.ROOT.name + "-other") / "server.py"
    assert _run(hook, monkeypatch, [], _payload(str(sibling))) == 0
    assert hook.calls == []


def test_dotdot_escape_is_outside(hook, monkeypatch):
    escape = str(hook.ROOT / "tests" / ".." / ".." / "elsewhere" / "file.py")
    assert _run(hook, monkeypatch, [], _payload(escape)) == 0
    assert hook.calls == []


def test_symlink_inside_repo_pointing_outside_is_outside(hook, monkeypatch, tmp_path):
    # A throwaway ROOT keeps the symlink out of the real checkout.
    root = tmp_path / "repo"
    root.mkdir()
    target = tmp_path / "outside"
    target.mkdir()
    monkeypatch.setattr(hook, "ROOT", root.resolve())
    link = root / "escape"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    assert _run(hook, monkeypatch, [], _payload(str(link / "file.py"))) == 0
    assert hook.calls == []

    # Control: the same throwaway ROOT does accept a real path inside it.
    assert _run(hook, monkeypatch, [], _payload(str(root / "file.py"))) == 0
    assert hook.calls == list(CHECKS)


@pytest.mark.parametrize(
    "raw",
    [
        "{}",
        "[]",
        '"just a string"',
        json.dumps({"tool_input": None}),
        json.dumps({"tool_input": "server.py"}),
        json.dumps({"tool_input": {}}),
        json.dumps({"tool_input": {"file_path": ""}}),
        json.dumps({"tool_input": {"file_path": None}}),
        json.dumps({"tool_input": {"file_path": 42}}),
        json.dumps({"tool_input": {"file_path": "bad\u0000path"}}),
    ],
)
def test_missing_or_unusable_path_fails_open(hook, monkeypatch, raw):
    assert _run(hook, monkeypatch, [], io.StringIO(raw)) == 0
    assert hook.calls == []


def test_malformed_json_still_fails_open(hook, monkeypatch):
    assert _run(hook, monkeypatch, [], io.StringIO("{not json")) == 0
    assert hook.calls == []


# --- inside the repo: checks run as today -------------------------------------


def test_inside_path_runs_all_checks(hook, monkeypatch):
    inside = hook.ROOT / "server.py"
    assert _run(hook, monkeypatch, [], _payload(str(inside))) == 0
    assert hook.calls == list(CHECKS)


def test_relative_inside_path_resolves_against_cwd(hook, monkeypatch):
    monkeypatch.chdir(hook.ROOT)
    assert _run(hook, monkeypatch, [], _payload("server.json")) == 0
    assert hook.calls == list(CHECKS)


def test_inside_path_violation_exits_2(hook, monkeypatch, capsys):
    monkeypatch.setattr(
        hook, "check_routes", hook.fake("check_routes", ["SYNTHETIC VIOLATION"])
    )
    inside = hook.ROOT / "fly.toml"
    assert _run(hook, monkeypatch, [], _payload(str(inside))) == 2
    assert hook.calls == list(CHECKS)
    assert "SYNTHETIC VIOLATION" in capsys.readouterr().err


# --- --standalone unchanged ----------------------------------------------------


def test_standalone_runs_all_checks_without_reading_stdin(hook, monkeypatch):
    assert _run(hook, monkeypatch, ["--standalone"], _ExplodingStdin()) == 0
    assert hook.calls == list(CHECKS)


def test_standalone_ignores_outside_path_payload(hook, monkeypatch, tmp_path):
    # Even with an outside-path payload waiting on stdin, --standalone never reads
    # it and asserts the repo regardless.
    outside = _payload(str(tmp_path / "elsewhere.py"))
    assert _run(hook, monkeypatch, ["--standalone"], outside) == 0
    assert hook.calls == list(CHECKS)


def test_standalone_expect_runs_release_tag_check(hook, monkeypatch):
    assert _run(hook, monkeypatch, ["--standalone", "--expect", "v1.0.0"], _ExplodingStdin()) == 0
    assert hook.calls == [*CHECKS, "check_release_tag"]
