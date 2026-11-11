"""Shared construction helpers.

Every test builds its input from text, so nothing in the repository depends on
a file that exists only on one machine.
"""

from __future__ import annotations

from cronlint import parse, run
from cronlint.model import App, Finding, Report

CRON_D = """# /etc/cron.d/app
SHELL=/bin/sh
PATH=/usr/local/sbin:/usr/local/bin

*/15 * * * * root /usr/local/bin/collect >> /var/log/m.log 2>&1
0 3 * * * root /opt/backup/nightly.sh
"""

USER_CRON = """# user crontab
MAILTO=ops@example.com

0 12 * * mon /opt/jobs/weekly-report
30 4 * * 1-5 /opt/reports/weekly --full
"""


def app_from(text: str, source: str = "crontab", **kwargs) -> App:
    return parse(text, source=source, **kwargs)


def report_from(text: str, source: str = "crontab", **kwargs) -> Report:
    return run(app_from(text, source, **kwargs), target=source)


def findings_for(report: Report, rule_id: str) -> list[Finding]:
    return [finding for finding in report.findings if finding.rule == rule_id]


def fired_rules(report: Report) -> set[str]:
    return {finding.rule for finding in report.findings}
