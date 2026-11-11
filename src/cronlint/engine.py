"""Run every rule over a parsed crontab.

The three passes are a reading order, not a filter: all rules see the same
context, and the report is sorted by severity so the top of the output is the
thing to fix first. Rules are run in id order, which keeps a report stable
between runs -- two runs over one file must produce byte-identical output or a
CI diff of that output means nothing.
"""

from __future__ import annotations

from .model import App, Finding, Report, Task
from .rules import Context, executable


def _context(app: App) -> Context:
    by_script: dict[str, list[Task]] = {}
    for task in app.tasks:
        path = task.script_path
        if path:
            by_script.setdefault(path, []).append(task)
    return Context(app=app, by_script=by_script)


def run(app: App, target: str | None = None) -> Report:
    """Evaluate every rule against ``app`` and return the sorted report."""
    ctx = _context(app)
    table = executable()
    findings: list[Finding] = []
    for item in table:
        for issue in item.check(ctx) or ():
            findings.append(
                Finding(
                    rule=item.id,
                    severity=item.severity,
                    message=issue.message,
                    line=issue.line,
                    offset=issue.offset,
                    length=issue.length,
                    saw=issue.saw,
                    fix=issue.fix or item.fix,
                    where=issue.where,
                    score=item.score,
                )
            )
    findings.sort(key=lambda finding: finding.sort_key())
    return Report(
        source=target or app.source,
        app=app,
        findings=findings,
        rules_run=len(table),
        rule_ids=[item.id for item in table],
    )


__all__ = ["run"]
