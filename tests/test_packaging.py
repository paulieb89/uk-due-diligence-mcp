"""Guards against the exact packaging drift this repo has hit before
(437924f: "fix packaging manifest" after prompts.py was removed but the
wheel manifest wasn't updated). pyproject.toml's [tool.hatch.build.targets.wheel]
only-include list is a manually-maintained module list — nothing enforces
that a new module actually gets added to it, so a real build+install+import
is the only thing that proves a module ships, not just that it exists on disk.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent

# Every first-party module this server imports at startup (server.py's own
# `import companies_house, companies_house_documents, ...` line) — if any
# of these were missing from the wheel, importing server would fail exactly
# as it would for a real `uvx`/PyPI install.
EXPECTED_MODULES = [
    "server",
    "companies_house",
    "companies_house_documents",
    "charity",
    "disqualified",
    "land_registry",
    "gazette",
    "hmrc_vat",
    "sanctions",
    "search_fetch",
    "models",
    "http_client",
]


def test_wheel_contains_and_can_import_every_first_party_module(tmp_path):
    dist_dir = tmp_path / "dist"
    install_dir = tmp_path / "install"

    build = subprocess.run(
        ["uv", "build", "--wheel", "-o", str(dist_dir)],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=60,
    )
    assert build.returncode == 0, f"uv build failed:\n{build.stdout}\n{build.stderr}"

    wheels = list(dist_dir.glob("*.whl"))
    assert len(wheels) == 1, f"expected exactly one wheel, got {wheels}"
    wheel_path = wheels[0]

    venv_python = REPO_ROOT / ".venv" / "bin" / "python3"
    assert venv_python.exists(), f"expected a venv python at {venv_python}"

    install = subprocess.run(
        [
            "uv", "pip", "install",
            "--python", str(venv_python),
            "--target", str(install_dir),
            "--no-deps",
            str(wheel_path),
        ],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=60,
    )
    assert install.returncode == 0, f"wheel install failed:\n{install.stdout}\n{install.stderr}"

    for module_name in EXPECTED_MODULES:
        shipped_file = install_dir / f"{module_name}.py"
        assert shipped_file.is_file(), (
            f"{module_name}.py was not installed from the built wheel — "
            f"check [tool.hatch.build.targets.wheel] only-include in pyproject.toml"
        )

    # Import from the installed wheel's own files (not this repo's source
    # tree — cwd is deliberately outside REPO_ROOT, with only install_dir on
    # sys.path) using the repo's real dependency set (fastmcp/mcp/pydantic
    # etc, already present in .venv) via PYTHONPATH rather than editable
    # install shadowing. This is the actual proof: importable end-to-end,
    # not just "the file exists in the zip."
    import_check = subprocess.run(
        [str(venv_python), "-c", "import server; print('OK')"],
        cwd=str(tmp_path),
        env={"PYTHONPATH": str(install_dir), "PATH": "/usr/bin:/bin"},
        capture_output=True, text=True, timeout=30,
    )
    assert import_check.returncode == 0, (
        f"importing server from the built wheel failed:\n"
        f"{import_check.stdout}\n{import_check.stderr}"
    )
    assert "OK" in import_check.stdout


def test_pyproject_wheel_manifest_lists_every_first_party_module():
    """Faster, no-subprocess companion check — pinpoints exactly which
    module is missing from the manifest if the build-based test above ever
    fails, without needing to re-run a build to see why."""
    pyproject_text = (REPO_ROOT / "pyproject.toml").read_text()
    for module_name in EXPECTED_MODULES:
        assert f'"{module_name}.py"' in pyproject_text, (
            f"{module_name}.py is missing from "
            f"[tool.hatch.build.targets.wheel] only-include in pyproject.toml"
        )
