"""Tests for the deploy-drift guard's remote-URL normalisation.

This file exists because of one concrete bug. The repo guard reduced a remote URL
to `owner/repo` with `s#^.*[:/]([^/]+/[^/]+?)(\\.git)?$#\\1#`, but POSIX ERE has no
lazy quantifier: `+?` is greedy, so the `.git` suffix was never stripped. Any
clone using the `.git` remote form — GitHub's own default — got a false
`FAIL ... built from owner/repo but this working tree is owner/repo.git` and
exit 3. It stayed invisible because the repo it was written in happens to use the
bare HTTPS form, so the two strings matched anyway.

`test_git_suffix_is_stripped` replays that exactly.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check-deploy-drift.sh"
REPO = "paulieb89/uk-due-diligence-mcp"


def normalise(url: str) -> str:
    """Run the real shell function by sourcing the script.

    Sourcing returns before the executable body (the BASH_SOURCE guard), so this
    exercises the shipped code rather than a Python restatement of it.
    """
    proc = subprocess.run(
        ["bash", "-c", f'source "{_SCRIPT}"; normalise_repo_url "$1"', "_", url],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"sourcing failed: {proc.stderr}"
    return proc.stdout.strip()


# --- the incident ---


@pytest.mark.parametrize(
    "url",
    [
        f"https://github.com/{REPO}.git",
        f"git@github.com:{REPO}.git",
        f"ssh://git@github.com/{REPO}.git",
    ],
)
def test_git_suffix_is_stripped(url):
    """The greedy `[^/]+?` left `.git` on, so the guard failed every such clone."""
    assert normalise(url) == REPO


# --- the forms that already worked, kept so a fix cannot regress them ---


@pytest.mark.parametrize(
    "url",
    [
        f"https://github.com/{REPO}",
        f"git@github.com:{REPO}",
        f"ssh://git@github.com/{REPO}",
        f"https://github.com/{REPO}/",
        f"https://github.com/{REPO}.git/",
    ],
)
def test_bare_and_trailing_slash_forms(url):
    assert normalise(url) == REPO


def test_sourcing_does_not_run_the_checks():
    """The guard must return before the body, or every test would hit flyctl."""
    proc = subprocess.run(
        ["bash", "-c", f'source "{_SCRIPT}"; echo SOURCED_CLEAN'],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "SOURCED_CLEAN"
