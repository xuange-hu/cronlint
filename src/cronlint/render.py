"""Render a report as text, JSON or SARIF.

The text form is what a person reads in a terminal: severity first, then the
rule id, then the line and the bytes that caused it. The SARIF form is what a
code-scanning upload consumes, and it carries the same byte offset, so a
finding in the UI and a finding in the file point at the same place.
"""

from __future__ import annotations

import json
from typing import Any

from .__about__ import __version__
from .model import Report
from .rules import REGISTRY

_SEVERITY_ORDER = ("critical", "high", "medium", "low", "note")

_ANSI = {
    "critical": "\033[1;31m",
    "high": "\033[31m",
    "medium": "\033[33m",
    "low": "\033[36m",
    "note": "\033[2m",
}
_RESET = "\033[0m"
_DIM = "\033[2m"

_RULES = {item.id: item for item in REGISTRY}


def _paint(text: str, severity: str, color: bool) -> str:
    if not color:
        return text
    return f"{_ANSI.get(severity, '')}{text}{_RESET}"


def _summary(report: Report) -> str:
    counts = report.by_severity()
    parts = [f"{name}={counts.get(name, 0)}" for name in _SEVERITY_ORDER]
    return f"{report.count} finding(s)  " + "  ".join(parts) + f"  risk={report.risk}/100"


def text(report: Report, color: bool = False) -> str:
    """The terminal report."""
    lines = [report.source, f"  {_summary(report)}", ""]

    if not report.findings:
        lines.append("  no findings")
        lines.append("")
        return "\n".join(lines)

    for finding in report.findings:
        rule = _RULES.get(finding.rule)
        title = rule.title if rule else finding.rule
        head = f"[{finding.severity:<8s}] {finding.rule}  {title}"
        lines.append(_paint(head, finding.severity, color))
        lines.append(f"           {finding.message}")
        where = f"line {finding.line}"
        if finding.length:
            where += f", offset 0x{finding.offset:x}+{finding.length}"
        else:
            where += f", offset 0x{finding.offset:x}"
        if finding.where:
            where += f"  ({finding.where})"
        lines.append(f"           {where}" if color else f"           {where}")
        if finding.saw:
            lines.append(f"           saw:   {finding.saw}")
        if finding.fix:
            lines.append(f"           fix:   {finding.fix}")
        lines.append("")

    return "\n".join(lines)


def json_dict(report: Report) -> dict[str, Any]:
    """The machine-readable report."""
    counts = report.by_severity()
    return {
        "source": report.source,
        "summary": {
            "findings": report.count,
            "risk": report.risk,
            "by_severity": {name: counts.get(name, 0) for name in _SEVERITY_ORDER},
            "by_rule": report.by_rule(),
            "rules_run": report.rules_run,
        },
        "findings": [
            {
                "rule": finding.rule,
                "severity": finding.severity,
                "message": finding.message,
                "line": finding.line,
                "offset": finding.offset,
                "length": finding.length,
                "saw": finding.saw,
                "fix": finding.fix,
                "where": finding.where,
                "score": finding.score,
            }
            for finding in report.findings
        ],
    }


def to_json(report: Report, indent: int = 2) -> str:
    return json.dumps(json_dict(report), indent=indent, ensure_ascii=False)


def _sarif_level(severity: str) -> str:
    if severity in {"critical", "high"}:
        return "error"
    if severity == "medium":
        return "warning"
    return "note"


def _rule_descriptor(rule_id: str) -> dict[str, Any] | None:
    rule = _RULES.get(rule_id)
    if rule is None:
        return None
    return {
        "id": rule.id,
        "name": rule.title.replace(" ", ""),
        "shortDescription": {"text": rule.title},
        "fullDescription": {"text": rule.why},
        "help": {"text": f"{rule.why}\n\nFix: {rule.fix}"},
        "defaultConfiguration": {"level": _sarif_level(rule.severity)},
        "properties": {"security-severity": str(rule.score), "stage": rule.stage},
    }


def sarif(report: Report) -> dict[str, Any]:
    """SARIF 2.1.0, ready for a GitHub code-scanning upload."""
    descriptors = []
    seen: set[str] = set()
    for finding in report.findings:
        if finding.rule in seen:
            continue
        seen.add(finding.rule)
        descriptor = _rule_descriptor(finding.rule)
        if descriptor:
            descriptors.append(descriptor)
    descriptors.sort(key=lambda item: item["id"])

    results: list[dict[str, Any]] = []
    for finding in report.findings:
        region: dict[str, Any] = {"startLine": max(finding.line, 1)}
        if finding.length:
            region["byteOffset"] = finding.offset
            region["byteLength"] = finding.length
        results.append(
            {
                "ruleId": finding.rule,
                "level": _sarif_level(finding.severity),
                "message": {"text": finding.message},
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": report.source.replace("\\", "/")},
                            "region": region,
                        }
                    }
                ],
                "properties": {
                    "saw": finding.saw,
                    "fix": finding.fix,
                    "score": finding.score,
                },
            }
        )

    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "cronlint",
                        "version": __version__,
                        "informationUri": "https://github.com/xuange-hu/cronlint",
                        "rules": descriptors,
                    }
                },
                "results": results,
            }
        ],
    }


def to_sarif(report: Report) -> str:
    return json.dumps(sarif(report), indent=2, ensure_ascii=False)


def brief(report: Report) -> str:
    """One line per report, for the demo and for a directory scan."""
    counts = report.by_severity()
    worst = next((name for name in _SEVERITY_ORDER if counts.get(name)), "-")
    return (
        f"{report.source}: {report.count} finding(s), worst={worst}, risk={report.risk}/100"
    )


__all__ = ["brief", "json_dict", "sarif", "text", "to_json", "to_sarif"]
