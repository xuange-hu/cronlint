# cronlint

[![CI](https://github.com/xuange-hu/cronlint/actions/workflows/ci.yml/badge.svg)](https://github.com/xuange-hu/cronlint/actions/workflows/ci.yml)
![tests-188%20passing](https://img.shields.io/badge/tests-188%20passing-brightgreen)
![dependencies-0](https://img.shields.io/badge/dependencies-0-blue)
![license-MIT](https://img.shields.io/badge/license-MIT-lightgrey)

**带字节偏移的 crontab 安全审计。** 三遍扫描（表达式 / 任务行 / 文件级）
共 35 条规则，零依赖，SARIF 输出可直接进 CI，附 MCP 服务端。
支持 5 字段标准 cron、6 字段 cronie（前置秒）、7 字段 Quartz（尾部年）、
`@special` 宏，以及 `/etc/crontab` 的 user 列。

English: [README.md](README.md)

```bash
uvx --from "git+https://github.com/xuange-hu/cronlint" cronlint demo
```

## 目录

- [为什么需要一个 cron linter](#为什么需要一个-cron-linter)
- [三步上手](#三步上手)
- [它认识的形状](#它认识的形状)
- [退出码即 CI 卡点](#退出码即-ci-卡点)
- [MCP 服务端](#mcp-服务端)
- [从克隆安装](#从克隆安装)
- [许可](#许可)

## 为什么需要一个 cron linter

crontab 配错不会报错，只会静默地在错误的时间执行。四类 shell 不会提醒你的问题：

1. **六字段有三种读法。** cronie 读成前置秒字段，Quartz 读成尾部年份，
   `/etc/crontab` 在 schedule 和命令之间读出一个 user 列。形状判错，
   命令会在错误的时间跑——或者以错误的用户身份跑。cronlint 对每一行
   单独判定形状，告诉你它选了哪种，并把有歧义的行标出来
   （CRN002 / CRN003）。
2. **DOM 和 DOW 同时限制是「或」不是「与」。** `0 0 1 * mon` 会在
   1 号**和**每个周一都触发，而几乎所有人都读成「1 号那个周一」（CRN006）。
3. **命令行就是凭据展示板。** `--password=hunter2` 在任务运行期间
   对机器上所有用户在 `ps` 里可见（CRN014）。
4. **两个任务在同一分钟写同一个日志、每分钟任务不加锁导致副本堆积**
   ——这些没有任何人会警告你（CRN050–CRN058）。

| 遍 | 读什么 | 抓什么 |
|---|---|---|
| 1 | 一条 schedule | 解析失败、秒/年形状、未知 `@special`、越界值、DOM+DOW 或语义、星期写成 7、缺命令 |
| 2 | 一条任务行 | 相对路径、全局可写目录、未加引号的变量展开、命令替换、明文凭据、`curl \| sh`、重定向到设备、`rm -rf`、缺 shebang、需要交互终端的工具 |
| 3 | 整个文件 | schedule 重复、同一脚本排两次、两个任务同分钟触发、共写同一日志、每分钟任务没加 `flock`、文件里没有任务 |

每条 finding 都带 `file:line`、字节偏移、命中的原文切片和修复建议。

## 三步上手

```bash
pip install "cronlint @ git+https://github.com/xuange-hu/cronlint"

# 1. 内置坏语料 —— 5 秒内证明工具能干活
cronlint demo

# 2. 审计仓库里所有 crontab
cronlint scan .

# 3. 接入 CI 卡点
cronlint scan /etc/cron.d --sarif > cronlint.sarif --fail-on high
```

`demo` 预期输出（185 条 finding，35 条规则全部命中）：

```
=== cron.d ===
cron.d
  170 finding(s)  critical=3  high=27  medium=112  low=28  note=0  risk=100/100

[critical] CRN014  Credential on the command line
           credential is passed as an argument and is visible in the process list
           line 10, offset 0x10a+18  (line 10: /opt/backup/nightly.sh --password=hunter2)
           saw:   --password=hunter2
           fix:   Read the secret from a root-only file or a keyring; never put it in the command.

corpora: 3, findings: 185
rules fired: 35/35
```

结尾的 `rules fired: 35/35` 意味着 [docs/rules.md](docs/rules.md) 里
每条规则在语料中都有能触发它的样例行——demo 同时就是规则的回归测试。

## 它认识的形状

| 行 | 形状 | 判定方式 |
|---|---|---|
| `*/5 * * * * /usr/bin/job` | standard | 五个字段 |
| `*/5 * * * * * /usr/bin/job` | 六字段 | 第六个 token 不是 schedule 字段 → 是命令 |
| `0 0 12 * * ? 2026 /job` | year | 五字段后还有两个字段形状的 token |
| `@daily /job` | special | `@` 名（允许 `@reboot`） |
| `* * * * * root /job` | user 列 | 路径是 `/etc/crontab` 或 `/etc/cron.d/` |

拿不准六字段该按哪种读，直接指定：

```bash
cronlint scan file --assume-seconds   # 强制按前置秒读
cronlint scan file --standard         # 强制按 5 字段 + 命令读
cronlint scan file --assume-user      # 强制按 /etc/crontab user 列读
```

## 退出码即 CI 卡点

| 码 | 含义 |
|---|---|
| 0 | 没有达到 `--fail-on`（默认 `high`）的 finding |
| 1 | 有 |
| 2 | 输入读不了 |

```bash
cronlint scan . --fail-on critical --json | jq '.findings[].rule_id'
```

## MCP 服务端

```bash
pip install "cronlint[mcp] @ git+https://github.com/xuange-hu/cronlint"
cronlint-mcp
```

六个工具：`cronlint_scan`、`cronlint_text`、`cronlint_rules`、
`cronlint_explain`、`cronlint_sarif`、`cronlint_demo`。`mcp` 包只在
`build_server()` 内部导入，CLI 永远不会加载它
（`tests/test_packaging.py` 静态断言这一点）。

## 从克隆安装

```bash
git clone https://github.com/xuange-hu/cronlint
cd cronlint
pip install -e ".[mcp]" pytest ruff
pytest          # 188 tests
ruff check src tests
```

克隆后无需安装即可跑测试——根目录 `conftest.py` 已把 `src/` 放进 `sys.path`。

## 许可

MIT，见 [LICENSE](LICENSE)。
