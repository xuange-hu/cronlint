"""The MCP facade: six tools, same engine as the CLI, mcp imported lazily."""

from __future__ import annotations

import json

import pytest
from helpers import USER_CRON

from cronlint import mcp_server


def test_manifest_lists_six_tools():
    manifest = mcp_server.tool_manifest()
    names = [tool["name"] for tool in manifest]
    assert names == [
        "cronlint_scan",
        "cronlint_text",
        "cronlint_rules",
        "cronlint_explain",
        "cronlint_sarif",
        "cronlint_demo",
    ]


def test_manifest_descriptions_are_non_empty():
    for tool in mcp_server.tool_manifest():
        assert tool["description"].strip(), tool["name"]


def test_text_tool_audits_a_string():
    out = mcp_server.cronlint_text(
        "* * * * * /tmp/refresh.sh\n", source="<pr>"
    )
    assert "<pr>" in out
    assert "CRN" in out


def test_text_tool_assume_user():
    out = mcp_server.cronlint_text(
        "0 2 * * * root /opt/x\n", source="<etc>", assume_user=True
    )
    assert "command 'root'" not in out


def test_rules_tool_lists_and_filters():
    all_rules = mcp_server.cronlint_rules()
    assert "35 rules" in all_rules
    stage1 = mcp_server.cronlint_rules(stage=1)
    assert "CRN001" in stage1
    assert "CRN010" not in stage1
    critical = mcp_server.cronlint_rules(severity="critical")
    assert "CRN014" in critical
    assert mcp_server.cronlint_rules(stage=99).startswith("cronlint: no rules")


def test_explain_tool_known_and_unknown():
    out = mcp_server.cronlint_explain("crn002")
    assert "CRN002" in out
    assert "why it matters" in out
    missing = mcp_server.cronlint_explain("CRN999")
    assert missing.startswith("cronlint: no rule CRN999")
    assert "CRN001" in missing  # lists the known ids


def test_scan_tool_on_missing_path():
    out = mcp_server.cronlint_scan("/no/such/crontab-anywhere")
    assert out.startswith("cronlint: no such file or directory")


def test_scan_tool_on_a_real_file(tmp_path):
    path = tmp_path / "jobs.cron"
    path.write_text(USER_CRON, encoding="utf-8", newline="\n")
    out = mcp_server.cronlint_scan(str(path))
    assert out.startswith(("PASS", "FAIL"))
    assert "fail_on=high" in out


def test_scan_tool_fail_on_none_passes(tmp_path):
    path = tmp_path / "bad.cron"
    path.write_text("* * * * * /tmp/x.sh\n", encoding="utf-8", newline="\n")
    out = mcp_server.cronlint_scan(str(path), fail_on="none")
    assert out.startswith("PASS")


def test_scan_tool_on_empty_directory(tmp_path):
    out = mcp_server.cronlint_scan(str(tmp_path))
    assert out.startswith("cronlint: no crontab files under")


def test_sarif_tool_needs_a_file(tmp_path):
    assert mcp_server.cronlint_sarif(str(tmp_path)).startswith("cronlint: not a file")
    path = tmp_path / "jobs.cron"
    path.write_text(USER_CRON, encoding="utf-8", newline="\n")
    payload = json.loads(mcp_server.cronlint_sarif(str(path)))
    assert payload["version"] == "2.1.0"


def test_build_server_needs_the_optional_extra():
    try:
        import mcp  # noqa: F401
    except ImportError:
        with pytest.raises(ImportError):
            mcp_server.build_server()
    else:
        server = mcp_server.build_server()
        assert server is not None


def test_module_scope_does_not_import_mcp():
    import ast
    from pathlib import Path

    src = Path(mcp_server.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    top_level: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            top_level.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            top_level.add(node.module.split(".")[0])
    assert "mcp" not in top_level


def test_served_tools_match_manifest_with_descriptions():
    """The names an agent calls must be the names the manifest promises, and
    FastMCP serves an empty description when the registered function has no
    docstring - an unidentified tool is an uncallable tool."""
    pytest.importorskip("mcp")
    import asyncio

    server = mcp_server.build_server()
    tools = asyncio.run(server.list_tools())
    served = {t.name for t in tools}
    manifest = {t["name"] for t in mcp_server.tool_manifest()}
    assert served == manifest, f"only served: {served - manifest}; only manifest: {manifest - served}"
    for t in tools:
        assert (t.description or "").strip(), f"{t.name} has no description"
