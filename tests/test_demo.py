"""The demo corpora: three files that between them fire every rule."""

from __future__ import annotations

from cronlint import demo
from cronlint.rules import REGISTRY


def test_three_corpora():
    corpora = demo.build()
    assert len(corpora) == 3
    names = [name for name, _, _ in corpora]
    assert names == ["cron.d", "user-crontab", "empty"]
    for _, source, text in corpora:
        assert text.strip(), f"{source} corpus is empty"


def test_every_rule_fires():
    fired, total = demo.coverage()
    missing = sorted(item.id for item in REGISTRY if item.id not in fired)
    assert total == 35
    assert not missing, f"rules with no sample line: {missing}"


def test_corpora_parse_without_errors():
    for name, app in demo.apps():
        assert app.tasks or name == "empty", f"{name} produced no tasks"
        if name == "user-crontab":
            # Line 9 is `0 12 * * mon` with no command: CRN005's sample is
            # deliberately broken, and that is the only accepted error.
            assert app.errors == ["crontab:9: schedule with no command"], app.errors
        else:
            assert not app.errors, f"{name} parse errors: {app.errors}"


def test_each_corpus_produces_findings():
    for name, report in demo.reports():
        assert report.count > 0, f"{name} produced no findings"


def test_demo_is_deterministic(tmp_path):
    # Same workdir both times: the shebang rule reads real paths, and those
    # legitimately differ between fresh tempdirs. Within one input the report
    # must be byte-identical.
    first = [(name, r.findings) for name, r in demo.reports(tmp_path)]
    second = [(name, r.findings) for name, r in demo.reports(tmp_path)]
    assert first == second


def test_cron_d_corpus_reads_the_user_column():
    apps = dict(demo.apps())
    for task in apps["cron.d"].tasks:
        if task.expression.special:
            continue
        assert task.user, f"cron.d line without a user: {task.raw!r}"


def test_user_corpus_has_no_user_column():
    apps = dict(demo.apps())
    for task in apps["user-crontab"].tasks:
        assert task.user is None


def test_demo_text_mentions_coverage():
    from cronlint import mcp_server

    out = mcp_server.cronlint_demo()
    assert "rules fired: 35/35" in out
    assert "every rule in the table" in out
