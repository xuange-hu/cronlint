# cronlint

[![CI](https://github.com/xuange-hu/cronlint/actions/workflows/ci.yml/badge.svg)](https://github.com/xuange-hu/cronlint/actions/workflows/ci.yml)
![tests-188%20passing](https://img.shields.io/badge/tests-188%20passing-brightgreen)
![dependencies-0](https://img.shields.io/badge/dependencies-0-blue)
![license-MIT](https://img.shields.io/badge/license-MIT-lightgrey)

**Crontab security audit with the byte offsets that prove each finding.**
35 rules over three passes — expression, task, file — zero dependencies,
SARIF for CI, MCP server. Reads the 5-field standard, 6-field cronie
(leading seconds), 7-field Quartz (trailing year), `@special` names,
and the `/etc/crontab` user column.

中文文档：[README_CN.md](README_CN.md)

```bash
uvx --from "git+https://github.com/xuange-hu/cronlint" cronlint demo
```

## Why a cron linter

Crontab entries fail quietly. Four failure modes a shell never warns you about:

1. **A six-field schedule is read three different ways.** cronie reads a
   leading seconds field, Quartz reads a trailing year, and `/etc/crontab`
   reads a user column between the schedule and the command. Get the shape
   wrong and the command you think you scheduled runs at a different time —
   or under a different user. cronlint decides the shape per line, tells you
   which one it chose, and flags the ambiguous ones (CRN002/CRN003).
2. **DOM and DOW together are an OR, not an AND.** `0 0 1 * mon` fires on
   the 1st *and* every Monday. Almost everyone reads it as "the Monday that
   is the 1st" (CRN006).
3. **The command line is a credential display.** `--password=hunter2` is
   visible in `ps` to every user on the box for the whole run (CRN014).
4. **Nothing warns you that two jobs write the same log in the same
   minute**, or that an every-minute job has no lock and stacks copies of
   itself (CRN050–CRN058).

| Pass | Reads | Catches |
|---|---|---|
| 1 | one schedule | unparseable fields, seconds/year shapes, unknown `@special`, out-of-range values, DOM+DOW OR, weekday `7`, no command |
| 2 | one task line | relative paths, world-writable dirs, unquoted expansion, command substitution, credentials, `curl \| sh`, device redirects, `rm -rf`, no shebang, interactive-only tools |
| 3 | the whole file | duplicate schedules, same script twice, two jobs in the same minute, shared log files, every-minute jobs without `flock`, file with no tasks |

Every finding carries `file:line`, a byte offset, the offending slice, and a fix.

## 3-step start

```bash
pip install "cronlint @ git+https://github.com/xuange-hu/cronlint"

# 1. the built-in broken corpus — proves the tool works in 5 seconds
cronlint demo

# 2. audit every crontab in your repo
cronlint scan .

# 3. gate CI on it
cronlint scan /etc/cron.d --sarif > cronlint.sarif --fail-on high
```

Expected `demo` output (185 findings, all 35 rules exercised):

```
=== cron.d ===
cron.d
  170 finding(s)  critical=3  high=27  medium=112  low=28  note=0  risk=100/100

[critical] CRN014  Credential on the command line
           credential is passed as an argument and is visible in the process list
           line 10, offset 0x10a+18  (line 10: /opt/backup/nightly.sh --password=hunter2)
           saw:   --password=hunter2
           fix:   Read the secret from a root-only file or a keyring; never put it in the command.

[high    ] CRN054  Every-minute job with no lock
           fires every minute with nothing preventing a second copy
           line 9, offset 0xc5+9  (line 9: /tmp/refresh.sh)
           saw:   * * * * *
           fix:   Wrap it: `flock -n /run/job.lock /path/to/job`.

corpora: 3, findings: 185
rules fired: 35/35
```

`demo` ends with `rules fired: 35/35` — every rule in
[docs/rules.md](docs/rules.md) has a sample line in the corpus that
triggers it, so the demo doubles as the rule regression suite.

## Shapes it understands

| Line | Shape | How cronlint decides |
|---|---|---|
| `*/5 * * * * /usr/bin/job` | standard | five fields |
| `*/5 * * * * * /usr/bin/job` | six fields | sixth token not a schedule field → command |
| `0 0 12 * * ? 2026 /job` | year | two field-shaped tokens after five |
| `@daily /job` | special | `@` name, `@reboot` allowed |
| `* * * * * root /job` | user column | path is `/etc/crontab` or `/etc/cron.d/` |

Unsure which shape your six fields are? Say so:

```bash
cronlint scan file --assume-seconds   # force leading-seconds reading
cronlint scan file --standard         # force 5 fields + command
cronlint scan file --assume-user      # force the /etc/crontab user column
```

## Exit codes gate CI

| Code | Meaning |
|---|---|
| 0 | nothing at or above `--fail-on` (default `high`) |
| 1 | something was |
| 2 | input could not be read |

```bash
cronlint scan . --fail-on critical --json | jq '.findings[].rule_id'
```

## MCP server

```bash
pip install "cronlint[mcp] @ git+https://github.com/xuange-hu/cronlint"
cronlint-mcp
```

Six tools: `cronlint_scan`, `cronlint_text`, `cronlint_rules`,
`cronlint_explain`, `cronlint_sarif`, `cronlint_demo`. The `mcp` package is
imported inside `build_server()` only — the CLI never loads it
(`tests/test_packaging.py` asserts this statically).

## Install from a clone

```bash
git clone https://github.com/xuange-hu/cronlint
cd cronlint
pip install -e ".[mcp]" pytest ruff
pytest          # 188 tests
ruff check src tests
```

No install is needed to run the tests after a clone — the root
`conftest.py` puts `src/` on `sys.path`.

## License

MIT. See [LICENSE](LICENSE).
