"""check_version_triad fails closed on a file it can read but not parse (#63).

Measured 2026-09-09: `"version" : "9.9.9"` in server.py (a space before the
colon) exited 0 while `"version": "9.9.9"` exited 2 — the regex miss dropped
server.py out of the comparison instead of reporting it.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_HOOK = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "check_invariants.py"
OTHER_CHECKS = ("check_port_invariant", "check_routes", "check_absolute_paths")


@pytest.fixture
def hook():
    spec = importlib.util.spec_from_file_location("check_invariants_triad", _HOOK)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tree(root: Path, *, pyproject="1.7.0", server_py='"version": "1.7.0"', server_json=None):
    (root / "pyproject.toml").write_text(f'[project]\nname = "x"\nversion = "{pyproject}"\n')
    (root / "server.py").write_text(f"card = {{{server_py}}}\n")
    sj = server_json if server_json is not None else {
        "version": "1.7.0", "packages": [{"version": "1.7.0"}]
    }
    (root / "server.json").write_text(json.dumps(sj))


def test_current_repo_tree_passes(hook):
    assert hook.check_version_triad() == []


def test_consistent_tree_passes(hook, tmp_path, monkeypatch):
    _tree(tmp_path)
    monkeypatch.setattr(hook, "ROOT", tmp_path)
    assert hook.check_version_triad() == []


def test_space_before_colon_is_drift_and_standalone_exits_2(hook, tmp_path, monkeypatch, capsys):
    _tree(tmp_path, server_py='"version" : "9.9.9"')
    monkeypatch.setattr(hook, "ROOT", tmp_path)
    for name in OTHER_CHECKS:
        monkeypatch.setattr(hook, name, lambda: [])
    monkeypatch.setattr(sys, "argv", ["check_invariants.py", "--standalone"])

    assert hook.main() == 2
    err = capsys.readouterr().err
    assert "VERSION TRIAD DRIFT" in err
    assert "9.9.9" in err


def test_unparseable_server_card_is_reported(hook, tmp_path, monkeypatch):
    _tree(tmp_path, server_py='"ver": "1.7.0"')
    monkeypatch.setattr(hook, "ROOT", tmp_path)
    problems = hook.check_version_triad()
    assert len(problems) == 1
    assert "UNPARSEABLE" in problems[0] and "server.py" in problems[0]


@pytest.mark.parametrize(
    "server_json",
    [
        {"packages": [{"version": "1.7.0"}]},  # no top-level version
        {"version": "1.7.0", "packages": [{"name": "x"}]},  # package without version
        {"version": "1.7.0"},  # no packages at all
    ],
)
def test_server_json_missing_version_is_reported(hook, tmp_path, monkeypatch, server_json):
    _tree(tmp_path, server_json=server_json)
    monkeypatch.setattr(hook, "ROOT", tmp_path)
    problems = hook.check_version_triad()
    assert any("UNPARSEABLE" in p and "server.json" in p for p in problems)


def test_every_package_version_is_compared(hook, tmp_path, monkeypatch):
    _tree(tmp_path, server_json={
        "version": "1.7.0", "packages": [{"version": "9.9.9"}, {"version": "1.7.0"}]
    })
    monkeypatch.setattr(hook, "ROOT", tmp_path)
    problems = hook.check_version_triad()
    assert any("DRIFT" in p and "9.9.9" in p for p in problems)


def test_unparseable_pyproject_still_checks_the_rest(hook, tmp_path, monkeypatch):
    # tomllib raises a plain ValueError (not TOMLDecodeError) for an oversized int.
    _tree(tmp_path, server_py='"version": "9.9.9"')
    (tmp_path / "pyproject.toml").write_text("[project]\nversion = " + "9" * 5000 + "\n")
    monkeypatch.setattr(hook, "ROOT", tmp_path)
    problems = hook.check_version_triad()
    assert any("UNPARSEABLE" in p and "pyproject.toml" in p for p in problems)
    assert any("DRIFT" in p and "9.9.9" in p for p in problems)


def test_unreadable_files_fail_open(hook, tmp_path, monkeypatch):
    monkeypatch.setattr(hook, "ROOT", tmp_path)  # empty dir: nothing to read
    assert hook.check_version_triad() == []
