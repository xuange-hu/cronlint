"""Render: text for humans, JSON for machines, SARIF for code scanning."""

from __future__ import annotations

import json

from helpers import report_from

from cronlint import render


def test_text_report_has_summary_and_evidence(bad_cron):
    text = render.text(bad_cron, color=False)
    assert bad_cron.source in text
    assert "finding(s)" in text
    assert "risk=" in text
    assert "saw:" in text
    assert "fix:" in text
    assert "CRN" in text


def test_text_report_without_findings_says_so(clean_cron):
    text = render.text(clean_cron, color=False)
    assert "no findings" in text
    assert clean_cron.source in text


def test_text_report_never_emits_ansi_when_color_is_off(bad_cron):
    assert "\033[" not in render.text(bad_cron, color=False)


def test_text_report_carries_offsets_in_hex(bad_cron):
    text = render.text(bad_cron, color=False)
    assert "offset 0x" in text


def test_json_round_trips(bad_cron):
    payload = json.loads(render.to_json(bad_cron))
    assert payload["source"] == bad_cron.source
    assert payload["summary"]["findings"] == bad_cron.count
    assert payload["summary"]["risk"] == bad_cron.risk
    assert payload["summary"]["rules_run"] == 35
    assert len(payload["findings"]) == bad_cron.count
    first = payload["findings"][0]
    for key in ("rule", "severity", "message", "line", "offset", "saw", "fix"):
        assert key in first


def test_json_by_rule_counts_sum_to_findings(bad_cron):
    payload = json.loads(render.to_json(bad_cron))
    assert sum(payload["summary"]["by_rule"].values()) == bad_cron.count


def test_sarif_is_2_1_with_rule_metadata(bad_cron):
    payload = json.loads(render.to_sarif(bad_cron))
    assert payload["version"] == "2.1.0"
    run = payload["runs"][0]
    driver = run["tool"]["driver"]
    assert driver["name"] == "cronlint"
    assert driver["informationUri"].endswith("/cronlint")
    ids = {rule["id"] for rule in driver["rules"]}
    assert ids == {finding.rule for finding in bad_cron.findings}
    for result in run["results"]:
        assert result["ruleId"]
        assert result["level"] in {"error", "warning", "note"}
        region = result["locations"][0]["physicalLocation"]["region"]
        assert region["startLine"] >= 1


def test_sarif_byte_offsets_match_findings(bad_cron):
    payload = json.loads(render.to_sarif(bad_cron))
    results = payload["runs"][0]["results"]
    assert len(results) == bad_cron.count
    for result, finding in zip(results, bad_cron.findings, strict=True):
        region = result["locations"][0]["physicalLocation"]["region"]
        assert region["byteOffset"] == finding.offset


def test_sarif_source_is_forward_slashed(bad_cron):
    payload = json.loads(render.to_sarif(bad_cron))
    uri = payload["runs"][0]["results"][0]["locations"][0]["physicalLocation"][
        "artifactLocation"
    ]["uri"]
    assert "\\" not in uri


def test_brief_is_one_line(bad_cron):
    line = render.brief(bad_cron)
    assert "\n" not in line
    assert f"{bad_cron.count} finding(s)" in line
    assert "worst=" in line


def test_severity_levels_map_to_sarif():
    assert render._sarif_level("critical") == "error"
    assert render._sarif_level("high") == "error"
    assert render._sarif_level("medium") == "warning"
    assert render._sarif_level("low") == "note"
    assert render._sarif_level("note") == "note"


def test_empty_report_json_is_valid():
    report = report_from("# nothing\n", source="empty.cron")
    payload = json.loads(render.to_json(report))
    assert payload["summary"]["findings"] >= 1
    assert isinstance(payload["findings"], list)
