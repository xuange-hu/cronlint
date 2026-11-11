"""The engine: deterministic order, stable reports, honest risk."""

from __future__ import annotations

from helpers import app_from, report_from

from cronlint import run
from cronlint.rules import REGISTRY, executable


def test_every_rule_runs_on_every_report():
    report = report_from("0 3 * * * /usr/local/bin/x >> /var/log/x.log 2>&1\n")
    assert report.rules_run == len(REGISTRY) == 35
    assert report.rule_ids == [item.id for item in executable()]


def test_two_runs_over_one_file_are_identical():
    text = (
        "PATH=/tmp:/usr/bin\n"
        "* * * * * /tmp/refresh.sh\n"
        "0 3 * * * root /opt/backup/nightly.sh --password=hunter2\n"
    )
    first = report_from(text, source="/etc/cron.d/app")
    second = report_from(text, source="/etc/cron.d/app")
    assert first.findings == second.findings, "report is not deterministic"
    assert first.risk == second.risk


def test_findings_sort_by_severity_then_position():
    report = report_from(
        "PATH=/tmp:/usr/bin\n"
        "* * * * * /tmp/a.sh\n"
        "0 3 * * * /opt/b --password=x\n",
        source="/etc/cron.d/app",
    )
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "note": 4}
    keys = [
        (order[f.severity], f.line, f.offset, f.rule) for f in report.findings
    ]
    assert keys == sorted(keys)


def test_risk_saturates_at_100():
    """Seventeen criticals sum past 100; the gauge must cap, not overflow."""
    text = "".join(
        f"0 {hour} * * * /opt/j{i} --password=x\n" for i, hour in enumerate(range(1, 18))
    )
    report = report_from(text, source="crontab")
    assert sum(f.score for f in report.findings) > 100
    assert report.risk == 100


def test_clean_report_has_zero_risk(clean_cron):
    assert clean_cron.count == 0
    assert clean_cron.risk == 0
    assert clean_cron.by_severity() == {}
    assert clean_cron.fired == set()


def test_by_rule_counts_match_findings(bad_cron):
    counts = bad_cron.by_rule()
    assert sum(counts.values()) == bad_cron.count
    for rule_id in counts:
        assert rule_id in {item.id for item in REGISTRY}


def test_run_accepts_an_app_directly():
    app = app_from("0 3 * * * /opt/x\n", source="crontab")
    report = run(app, target="custom-name")
    assert report.source == "custom-name"
    assert report.app is app


def test_empty_file_still_produces_a_report():
    report = report_from("# nothing\n", source="empty.cron")
    assert report.count >= 1  # CRN056: no scheduled tasks
    assert "CRN056" in report.fired
