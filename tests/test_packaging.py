"""Packaging contract: console scripts must not import an optional extra.

CI installs `.[mcp]`, so a missing base dependency is invisible there and
only shows up as ModuleNotFoundError for the user who runs `cronlint`. These
tests assert the contract statically instead.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # Python <= 3.10: tomllib landed in 3.11
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import _pytoml as tomllib  # type: ignore[no-redef]

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
SRC = ROOT / "src" / "cronlint"
SRCROOT = ROOT / "src"


def _pyproject() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def _scripts() -> dict[str, str]:
    return _pyproject()["project"]["scripts"]


def _target_module(script: str) -> str:
    return script.split(":", 1)[0]


def _collect_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            out.add(node.module.split(".")[0])
    return out


def test_console_scripts_exist():
    scripts = _scripts()
    assert "cronlint" in scripts
    assert "cronlint-mcp" in scripts


def _target_path(script: str) -> Path:
    """`cronlint.cli:main` -> src/cronlint/cli.py."""
    return SRCROOT / Path(_target_module(script).replace(".", "/") + ".py")


def test_every_console_script_target_module_resolves():
    for name, target in _scripts().items():
        path = _target_path(target)
        assert path.exists(), f"{name} -> {path} is missing"


def test_stdlib_only_imports_in_cli_entrypoint():
    """`cronlint` must run with zero third-party packages installed."""
    stdlib = set(sys.stdlib_module_names) | {"__future__"}
    imports = _collect_imports(SRC / "cli.py")
    third_party = {i for i in imports if i not in stdlib and i != "cronlint"}
    assert third_party == set(), f"cli.py pulls in {third_party}"


def test_core_package_imports_without_extras():
    """Importing cronlint must not drag in the mcp extra."""
    stdlib = set(sys.stdlib_module_names) | {"__future__"}
    for module in ("__init__.py", "engine.py", "parser.py", "rules.py", "render.py"):
        imports = _collect_imports(SRC / module)
        third_party = {i for i in imports if i not in stdlib and i != "cronlint"}
        assert third_party == set(), f"{module} pulls in {third_party}"


def test_mcp_server_imports_mcp_only_inside_build():
    """The optional dependency must be imported lazily, not at module scope."""
    src = (SRC / "mcp_server.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    top_level: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            top_level.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            top_level.add(node.module.split(".")[0])
    assert "mcp" not in top_level
    assert "def build_server" in src


def test_mcp_extra_requires_mcp_package():
    extras = _pyproject()["project"].get("optional-dependencies", {})
    assert "mcp" in extras
    assert any(
        re.split(r"[<>=!~;\[\s]", d)[0] == "mcp" for d in extras["mcp"]
    ), extras["mcp"]


def test_mcp_version_pinned_below_2():
    """mcp 2.x renamed FastMCP; the server is written for the 1.x line."""
    extras = _pyproject()["project"]["optional-dependencies"]["mcp"]
    assert any("mcp>=1" in d and "<2" in d for d in extras), extras


def test_base_dependencies_are_empty():
    deps = _pyproject()["project"].get("dependencies", [])
    assert deps == [], f"core must stay zero-dependency, got {deps}"


def test_version_matches_about():
    project = _pyproject()["project"]
    about = (SRC / "__about__.py").read_text(encoding="utf-8")
    assert f'__version__ = "{project["version"]}"' in about


def test_requires_python_covers_310():
    assert _pyproject()["project"]["requires-python"] == ">=3.10"


def test_src_layout_is_used():
    cfg = _pyproject()
    assert cfg["build-system"]["requires"], "build backend must be declared"
    wheel = cfg["tool"].get("setuptools", {}).get("package-dir", {})
    assert wheel.get("") == "src", cfg["tool"].get("setuptools")


def test_py_typed_marker_exists():
    assert (SRC / "py.typed").exists(), "PEP 561 marker missing"


def test_readme_exists_and_declares_itself():
    project = _pyproject()["project"]
    readme = ROOT / project["readme"]
    assert readme.exists(), f"{project['readme']} missing but declared"
    assert (SRC / "__init__.py").exists()


def _every_test_module_can_run() -> bool:
    """The badge number is only meaningful where nothing skips itself.

    tests/test_pytoml_parity.py skips wholesale without tomllib (3.11+), so a
    3.10 leg collects 183 items while a 3.11+ leg collects 188. Comparing the
    badge there would fail on every 3.10 run with no actual drift. Enforcement
    lives in the CI badge job, which runs on 3.12.
    """
    import importlib.util

    return importlib.util.find_spec("tomllib") is not None


@pytest.mark.skipif(
    not _every_test_module_can_run(),
    reason="badge count is validated on 3.11+, where the parity suite runs",
)
def test_readme_test_count_matches_collection():
    """Reads the badge in README.md; keeps the headline number honest."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    m = re.search(r"tests-(\d+)%20passing", readme)
    assert m, "README is missing the tests badge"
    declared = int(m.group(1))
    collected = _collected_tests()
    assert declared == collected, f"README says {declared}, pytest collects {collected}"


def _collected_tests() -> int:
    """Ask pytest how many items exist, without running them."""
    import subprocess

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-o", "addopts=", str(ROOT / "tests")],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    # -o addopts= protects against a project-level addopts="-q": -qq drops the
    # "N tests collected" summary and this silently fell back to skipping.
    m = re.search(r"(\d+) tests? collected", proc.stdout)
    if m is None:
        import pytest

        pytest.skip(f"could not collect tests: {proc.stdout[-200:]} {proc.stderr[-200:]}")
    return int(m.group(1))
