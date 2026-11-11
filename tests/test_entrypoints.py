"""Entry points: `python -m cronlint` and the package surface."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import cronlint

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "cronlint", *args],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        cwd=str(ROOT),
        env={"PYTHONPATH": str(SRC), "PATH": "/usr/bin:/bin", "SYSTEMROOT": "C:\\Windows"},
    )


def test_package_exports():
    assert cronlint.__all__ == ["Report", "__version__", "parse", "parse_file", "run"]
    assert callable(cronlint.parse)
    assert callable(cronlint.parse_file)
    assert callable(cronlint.run)
    assert cronlint.__version__ == "0.1.0"


def test_module_runs_version():
    proc = _run("--version")
    assert proc.returncode == 0, proc.stderr
    assert "cronlint 0.1.0" in proc.stdout


def test_module_runs_rules():
    proc = _run("rules")
    assert proc.returncode == 0, proc.stderr
    assert "35 rules" in proc.stdout


def test_module_runs_demo():
    proc = _run("demo", "--no-color")
    assert proc.returncode == 0, proc.stderr
    assert "rules fired: 35/35" in proc.stdout


def test_module_requires_a_subcommand():
    proc = _run()
    assert proc.returncode == 2
    assert "usage" in (proc.stderr + proc.stdout).lower()


def test_parse_and_run_from_the_package_api():
    report = cronlint.run(
        cronlint.parse("0 3 * * * /opt/x >> /var/log/x.log 2>&1\n", source="t"),
        target="t",
    )
    assert report.count == 0
    assert report.risk == 0
