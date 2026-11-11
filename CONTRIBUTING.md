# Contributing

**cronlint** — Crontab security auditor with byte offsets — is small on
purpose: hand-written parsers, deterministic rules, no dependencies.
Contributions that keep it that way are welcome.

## Setup

```bash
git clone https://github.com/xuange-hu/cronlint
cd cronlint
pip install -e ".[mcp]" pytest ruff
pytest                 # 189 tests
ruff check src tests
```

No `pip install` is needed to run the tests after a clone — the root
`conftest.py` puts `src/` on `sys.path`.

## Three passes and where they live

| Pass | Module | Rules |
|---|---|---|
| 1 — expression | `src/cronlint/expr.py` + `parser.py` | CRN001–009 |
| 2 — task line | `src/cronlint/rules.py` | CRN010–026 |
| 3 — whole file | `src/cronlint/rules.py` | CRN050–058 |

The engine (`engine.py`) runs the three passes in order and sorts findings
by a total order, so two runs over the same input are byte-identical.

## Adding a rule

1. **The check** in `src/cronlint/rules.py` as an `executable()` entry with a
   stable id (`CRN0NN`), severity, stage and score.
2. **A trigger sample** in the `_SAMPLES` table of `tests/test_rules.py`.
   `test_every_rule_has_a_sample` fails if the registry and the table
   disagree, so a rule without a sample cannot land.
3. **Regenerate the rulebook**: `python -m cronlint.cli rules > docs/rules.md`
   — `tests/test_docs.py` fails when the file is stale.

Then update the rule counts and the test-count badge in `README.md` and
`README_CN.md`. CI warns when the badge and pytest disagree.

## Shape handling (the part that breaks silently)

`parser.py` decides per line whether a schedule is 5-field standard,
6-field seconds, 7-field year, `@special`, or the `/etc/crontab` user
column. Two predicates guard this:

- `_is_field_token()` — strict; used for shape decisions. Must reject
  `root`, `backup`, any username that happens to be lowercase letters.
- `_looks_like_schedule_text()` — loose; used only in the five-field
  rejection path so that legal steps like `mon/2` are not thrown away.

If you touch shape detection, run `pytest tests/test_demo.py tests/test_cli.py`
— the demo corpora cover all five shapes on fixed lines.

## Rules of the road

- **Zero dependencies in `src/` outside `mcp_server.py`.** `tests/test_packaging.py`
  fails on an optional import leaking into a required path.
- **No network access.** Not in the scanner, not in the tests. Tests that need
  a file write it to `tmp_path`.
- **Every finding carries a location and its source text.** A report that says
  "something is wrong" without saying where gets closed and forgotten.
- **Determinism is a feature.** No timestamps, no dict iteration order, no
  absolute host paths in findings.
- **Python 3.10 floor.** `tomllib` is 3.11+; `tests/_pytoml.py` covers 3.10
  and `tests/test_pytoml_parity.py` keeps it honest against the real parser.
