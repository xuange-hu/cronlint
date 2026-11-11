"""The CLI: exit codes are the contract CI gates on."""

from __future__ import annotations

import json

import pytest
from helpers import CRON_D, USER_CRON

from cronlint.cli import main


@pytest.fixture
def clean_file(tmp_path):
    path = tmp_path / "clean.cron"
    path.write_text(
        "0 3 * * * /usr/local/bin/nightly >> /var/log/nightly.log 2>&1\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


@pytest.fixture
def dirty_file(tmp_path):
    path = tmp_path / "dirty.cron"
    path.write_text(
        "PATH=/tmp:/usr/bin\n* * * * * /tmp/refresh.sh\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


def test_scan_clean_returns_zero(clean_file, capsys):
    assert main(["scan", str(clean_file), "--no-color"]) == 0
    assert "no findings" in capsys.readouterr().out


def test_scan_dirty_returns_one(dirty_file):
    assert main(["scan", str(dirty_file), "--no-color"]) == 1


def test_fail_on_none_never_fails(dirty_file):
    assert main(["scan", str(dirty_file), "--fail-on", "none"]) == 0


def test_fail_on_critical_ignores_high(dirty_file):
    # dirty.cron has highs but no criticals.
    assert main(["scan", str(dirty_file), "--fail-on", "critical", "--quiet"]) == 0
    assert main(["scan", str(dirty_file), "--fail-on", "high", "--quiet"]) == 1


def test_missing_path_returns_two():
    assert main(["scan", "/no/such/crontab-anywhere", "--quiet"]) == 2


def test_directory_scan_picks_cron_shaped_files(tmp_path, capsys):
    (tmp_path / "notes.txt").write_text("not a crontab\n", encoding="utf-8")
    (tmp_path / "jobs.cron").write_text(USER_CRON, encoding="utf-8", newline="\n")
    d = tmp_path / "cron.d"
    d.mkdir()
    (d / "app").write_text(CRON_D, encoding="utf-8", newline="\n")
    assert main(["scan", str(tmp_path), "--no-color"]) in {0, 1}
    out = capsys.readouterr().out
    assert "jobs.cron" in out
    assert "app" in out
    assert "notes.txt" not in out


def test_json_output_is_parseable(dirty_file, capsys):
    main(["scan", str(dirty_file), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["findings"] >= 1


def test_sarif_output_is_parseable(dirty_file, capsys):
    main(["scan", str(dirty_file), "--sarif"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["version"] == "2.1.0"


def test_standard_flag_reads_six_fields_as_five(tmp_path, capsys):
    path = tmp_path / "amb.cron"
    path.write_text("* * * * * * /opt/jobs/seconds\n", encoding="utf-8", newline="\n")
    assert main(["scan", str(path), "--standard", "--json"]) in {0, 1}
    payload = json.loads(capsys.readouterr().out)
    # The six-field guess is suppressed: CRN002 must not fire under --standard.
    rules = {f["rule"] for f in payload["findings"]}
    assert "CRN002" not in rules


def test_assume_seconds_forces_the_six_field_reading(tmp_path, capsys):
    path = tmp_path / "amb.cron"
    path.write_text("* * * * * * /opt/jobs/seconds\n", encoding="utf-8", newline="\n")
    main(["scan", str(path), "--assume-seconds", "--json"])
    payload = json.loads(capsys.readouterr().out)
    rules = {f["rule"] for f in payload["findings"]}
    assert "CRN002" in rules


def test_assume_user_reads_the_crontab_shape(tmp_path, capsys):
    path = tmp_path / "plain.txt"
    path.write_text("0 2 * * * root /opt/app/migrate.sh\n", encoding="utf-8", newline="\n")
    main(["scan", str(path), "--assume-user", "--json"])
    payload = json.loads(capsys.readouterr().out)
    # With the user column read correctly there is no relative-path finding
    # for `root` being mistaken for the command.
    messages = [f["message"] for f in payload["findings"]]
    assert not any("command 'root'" in m for m in messages)


def test_rules_command_lists_the_table(capsys):
    assert main(["rules"]) == 0
    out = capsys.readouterr().out
    assert "CRN001" in out
    assert "CRN058" in out
    assert "35 rules" in out


def test_rules_markdown_is_deterministic(capsys):
    assert main(["rules", "--markdown"]) == 0
    first = capsys.readouterr().out
    assert main(["rules", "--markdown"]) == 0
    assert capsys.readouterr().out == first
    assert first.startswith("# Rule reference")


def test_explain_known_rule(capsys):
    assert main(["explain", "crn014"]) == 0
    out = capsys.readouterr().out
    assert "CRN014" in out
    assert "why it matters" in out
    assert "fix" in out


def test_explain_unknown_rule_is_two(capsys):
    assert main(["explain", "CRN999"]) == 2
    assert "no rule CRN999" in capsys.readouterr().err


def test_demo_reports_full_coverage(capsys):
    assert main(["demo", "--no-color"]) == 0
    out = capsys.readouterr().out
    assert "rules fired: 35/35" in out
    assert "every rule in the table" in out


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert "cronlint" in capsys.readouterr().out


def test_quiet_scan_prints_nothing_on_success(clean_file, capsys):
    assert main(["scan", str(clean_file), "--quiet"]) == 0
    assert capsys.readouterr().out == ""
