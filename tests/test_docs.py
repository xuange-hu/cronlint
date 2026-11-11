"""docs/rules.md is generated; this test is the diff CI would run."""

from __future__ import annotations

from pathlib import Path

from cronlint.rulebook import brief_markdown, full_markdown
from cronlint.rules import REGISTRY

ROOT = Path(__file__).resolve().parents[1]
RULES_MD = ROOT / "docs" / "rules.md"


def test_rules_md_matches_the_generator():
    assert RULES_MD.exists(), "docs/rules.md is missing"
    on_disk = RULES_MD.read_text(encoding="utf-8")
    assert on_disk == full_markdown(), (
        "docs/rules.md is stale; regenerate with: "
        "python -m cronlint rules --markdown > docs/rules.md"
    )


def test_every_rule_appears_in_the_doc():
    doc = RULES_MD.read_text(encoding="utf-8")
    for item in REGISTRY:
        assert item.id in doc, f"{item.id} missing from docs/rules.md"
        assert item.title in doc, f"{item.title!r} missing from docs/rules.md"


def test_doc_has_all_three_passes():
    doc = RULES_MD.read_text(encoding="utf-8")
    assert "## Pass 1: expression" in doc
    assert "## Pass 2: task" in doc
    assert "## Pass 3: file" in doc


def test_brief_markdown_is_a_table():
    brief = brief_markdown()
    lines = brief.splitlines()
    assert lines[0].startswith("| Id |")
    assert lines[1].startswith("|---|")
    assert len(lines) == 2 + len(REGISTRY)


def test_markdown_is_deterministic():
    assert full_markdown() == full_markdown()


def test_markdown_carries_no_host_paths():
    doc = full_markdown()
    assert "C:\\" not in doc
    assert "/Users/" not in doc
    assert "/home/" not in doc
