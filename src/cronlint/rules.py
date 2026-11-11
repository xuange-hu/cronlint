"""The rule table, in three passes.

* Pass 1 reads the expression itself: how many fields, whether each value is
  legal, whether the schedule is one that standard cron can even represent.
* Pass 2 reads one task end to end: what the command can be made to do, and
  what the schedule implies about it.
* Pass 3 reads the file as a whole: two tasks that collide, a schedule that
  runs into itself, an environment line that changes every task under it.

Every rule yields an Issue carrying the line and byte offset it is talking
about. A rule that cannot name its own evidence does not ship.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from dataclasses import field as dc_field

from .expr import overlaps
from .model import App, Task


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    severity: str
    score: int
    stage: int  # 1 expression, 2 task, 3 file
    why: str
    fix: str
    check: Callable[[Context], Iterator[Issue]]


@dataclass
class Issue:
    """One rule's complaint, with the bytes that caused it."""

    message: str
    line: int
    offset: int
    length: int = 0
    saw: str = ""
    fix: str = ""
    where: str = ""


@dataclass
class Context:
    """Everything a rule may look at."""

    app: App
    by_script: dict[str, list[Task]] = dc_field(default_factory=dict)

    def tasks(self) -> list[Task]:
        return self.app.tasks


REGISTRY: list[Rule] = []


def rule(
    rule_id: str,
    title: str,
    severity: str,
    score: int,
    stage: int,
    why: str,
    fix: str,
) -> Callable:
    def decorate(fn: Callable) -> Callable:
        REGISTRY.append(
            Rule(
                id=rule_id,
                title=title,
                severity=severity,
                score=score,
                stage=stage,
                why=why,
                fix=fix,
                check=fn,
            )
        )
        return fn

    return decorate


def _executable() -> list[Rule]:
    return sorted(REGISTRY, key=lambda item: item.id)


# ---------------------------------------------------------------- pass 1

@rule(
    "CRN001",
    "Schedule does not parse",
    "high",
    4,
    1,
    why=(
        "A schedule cron cannot parse is not a schedule: depending on the "
        "implementation the line is rejected outright or silently dropped, so "
        "the job never runs and nothing says so."
    ),
    fix="Fix the field count and the values, then confirm with `crontab -l` or `run-parts --test`.",
)
def schedule_does_not_parse(ctx: Context) -> Iterator[Issue]:
    for task in ctx.tasks():
        expression = task.expression
        if expression.kind == "malformed" or not expression.is_valid:
            details = "; ".join(expression.errors) or "; ".join(
                error for field in expression.fields for error in field.errors
            )
            yield Issue(
                message=f"schedule {expression.raw!r} does not parse: {details}",
                line=task.lineno,
                offset=task.expr_start,
                length=task.expr_end - task.expr_start,
                saw=expression.raw,
                where=task.label,
            )


@rule(
    "CRN002",
    "Six-field schedule (leading seconds)",
    "high",
    4,
    1,
    why=(
        "Standard cron and cronie take five fields. With six, the fifth is read "
        "as the start of the command and the sixth as its first argument: the "
        "job still installs, runs something unintended at the wrong time, or "
        "fails on every tick."
    ),
    fix="Drop the seconds field, or run it on a scheduler that documents six fields (quartz, systemd timer).",
)
def six_field_schedule(ctx: Context) -> Iterator[Issue]:
    for task in ctx.tasks():
        if task.expression.kind not in {"seconds", "seconds-forced"}:
            continue
        yield Issue(
            message="schedule has 6 fields (leading seconds); standard cron expects 5",
            line=task.lineno,
            offset=task.expr_start,
            length=task.expr_end - task.expr_start,
            saw=task.expression.raw,
            where=task.label,
        )


@rule(
    "CRN003",
    "Seven-field schedule (trailing year)",
    "medium",
    3,
    1,
    why=(
        "A trailing year is a quartz convention. cronie and vixie read only the "
        "first five fields and treat the rest as the command, so a job written "
        "to run in one year runs every minute of every year instead."
    ),
    fix="Remove the year field and gate it in the command, or move the job to a scheduler that accepts it.",
)
def seven_field_schedule(ctx: Context) -> Iterator[Issue]:
    for task in ctx.tasks():
        if task.expression.kind != "year":
            continue
        yield Issue(
            message="schedule has 7 fields (trailing year), which standard cron ignores",
            line=task.lineno,
            offset=task.expr_start,
            length=task.expr_end - task.expr_start,
            saw=task.expression.raw,
            where=task.label,
        )


@rule(
    "CRN004",
    "Unknown @special",
    "high",
    4,
    1,
    why=(
        "`@every 5m` and friends belong to other schedulers. cron leaves the "
        "line unrunnable, so the task is installed and never fires."
    ),
    fix="Use a standard special (@reboot, @hourly, @daily, @weekly, @monthly, @yearly) or write the fields out.",
)
def unknown_special(ctx: Context) -> Iterator[Issue]:
    for task in ctx.tasks():
        if task.expression.kind != "malformed" or not task.expression.special:
            continue
        yield Issue(
            message=f"unknown special @{task.expression.special}",
            line=task.lineno,
            offset=task.expr_start,
            length=task.expr_end - task.expr_start,
            saw=task.expression.raw,
            where=task.label,
        )


@rule(
    "CRN005",
    "Schedule with no command",
    "high",
    4,
    1,
    why=(
        "A schedule whose command column is empty is either a truncated line "
        "or a typo; cron accepts it and does nothing, which reads as a job "
        "that 'used to work'."
    ),
    fix="Restore the command, or delete the line.",
)
def schedule_without_command(ctx: Context) -> Iterator[Issue]:
    for task in ctx.tasks():
        if task.command.strip():
            continue
        yield Issue(
            message="schedule has no command",
            line=task.lineno,
            offset=task.expr_start,
            length=task.expr_end - task.expr_start,
            saw=task.raw.strip(),
            where=task.label,
        )


@rule(
    "CRN006",
    "Day-of-month and day-of-week are both restricted",
    "low",
    1,
    1,
    why=(
        "When both fields are restricted cron ORs them, it does not AND them: "
        "'0 0 1 * mon' fires on the 1st AND on every Monday, which surprises "
        "everyone who wrote it expecting the intersection."
    ),
    fix="Restrict one field to * if you meant the intersection, and check the run log for the days you did not expect.",
)
def dom_dow_or_semantics(ctx: Context) -> Iterator[Issue]:
    for task in ctx.tasks():
        dom, dow = task.expression.day_of_month, task.expression.day_of_week
        if dom is None or dow is None:
            continue
        if dom.is_wildcard or dow.is_wildcard:
            continue
        yield Issue(
            message=(
                "both day-of-month and day-of-week are restricted: cron ORs them, "
                "so this runs on the 1st of the month and on every matching weekday"
            ),
            line=task.lineno,
            offset=task.expr_start,
            length=task.expr_end - task.expr_start,
            saw=task.expression.raw,
            where=task.label,
        )


@rule(
    "CRN007",
    "Weekday written as 7",
    "note",
    1,
    1,
    why=(
        "0 and 7 both mean Sunday in vixie cron, but not every implementation "
        "accepts 7. A file that works on one host and is rejected on the next "
        "is a portability trap."
    ),
    fix="Write 0 for Sunday.",
)
def weekday_seven(ctx: Context) -> Iterator[Issue]:
    for task in ctx.tasks():
        dow = task.expression.day_of_week
        if dow is None:
            continue
        if "7" not in dow.raw:
            continue
        yield Issue(
            message="day-of-week uses 7 for Sunday; 0 is the portable spelling",
            line=task.lineno,
            offset=task.expr_start,
            length=task.expr_end - task.expr_start,
            saw=task.expression.raw,
            where=task.label,
        )


@rule(
    "CRN008",
    "Field value out of range",
    "high",
    4,
    1,
    why=(
        "Minute 60, hour 24 or month 13 make the whole line unparsable on most "
        "crons. Where a lenient parser accepts it the job simply never matches "
        "any date."
    ),
    fix="Bring every value inside its field's range.",
)
def value_out_of_range(ctx: Context) -> Iterator[Issue]:
    for task in ctx.tasks():
        for field in task.expression.fields:
            for error in field.errors:
                if "outside" not in error:
                    continue
                yield Issue(
                    message=error,
                    line=task.lineno,
                    offset=task.expr_start,
                    length=task.expr_end - task.expr_start,
                    saw=field.raw,
                    where=task.label,
                )


@rule(
    "CRN009",
    "Step or range is malformed",
    "high",
    4,
    1,
    why=(
        "`*/0` divides by zero in the reader, `10-5` never matches, and an "
        "unknown name like `xyz` is not a weekday. Each one takes the whole "
        "line down with it."
    ),
    fix="Use a positive step, a low-to-high range, and a real month/weekday abbreviation.",
)
def malformed_step_or_range(ctx: Context) -> Iterator[Issue]:
    for task in ctx.tasks():
        for field in task.expression.fields:
            for error in field.errors:
                if "outside" in error:
                    continue
                yield Issue(
                    message=error,
                    line=task.lineno,
                    offset=task.expr_start,
                    length=task.expr_end - task.expr_start,
                    saw=field.raw,
                    where=task.label,
                )


# ---------------------------------------------------------------- pass 2

_ABSOLUTE = re.compile(r"^/")
_ENV_PREFIX = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_VARIABLE = re.compile(r"\$\{?[A-Za-z_][A-Za-z0-9_]*\}?|\$\{[^}]*\}")
_COMMAND_SUB = re.compile(r"\$\(|`")
_CREDENTIAL = re.compile(
    r"(?i)(--?(?:password|passwd|pwd|token|secret|api[-_]?key|auth)"
    r"|(?:password|passwd|token|secret|api[-_]?key|pgpassword)\s*[=:])\s*\S+"
)
_PIPE_TO_SHELL = re.compile(r"\b(?:curl|wget)\b[^|;&]*\|\s*(?:sh|bash|zsh|python\d?)\b")
_RM_RF = re.compile(r"\brm\s+(?:-{1,2}\S*\s+)*-[a-zA-Z]*[rf][a-zA-Z]*\b")
_DEVICE_REDIRECT = re.compile(r"(?:>+|of=)\s*/dev/(?:sd|hd|vd|nvme|xvd|dm-)\S*")
_SENSITIVE_REDIRECT = re.compile(r">+\s*(?:/etc/|/var/spool/|/boot/|/root/)")
_WORLD_WRITABLE = re.compile(r"(?:^|[\s'\"])(?:/tmp/|/var/tmp/|/dev/shm/)")
_TTY_COMMAND = re.compile(r"\b(?:xterm|gnome-terminal|ssh-agent|sudo -i|su -)\b")


def _leading_command(task: Task) -> str:
    """The command with any ``NAME=value`` prefixes stripped off."""
    command = task.command.strip()
    while True:
        match = _ENV_PREFIX.match(command)
        if not match:
            return command
        command = command[match.end() :].lstrip()


def _first_token(command: str) -> str:
    parts = command.split()
    return parts[0] if parts else ""


@rule(
    "CRN010",
    "Command is not an absolute path",
    "high",
    3,
    2,
    why=(
        "cron runs with a minimal PATH (usually /usr/bin:/bin). A bare name "
        "resolves against whatever the cron daemon inherited, which is often "
        "empty -- the job fails silently -- or, on a box where someone edited "
        "root's crontab PATH, resolves to a different program entirely."
    ),
    fix="Write the full path: /usr/local/bin/job, not job.",
)
def relative_command_path(ctx: Context) -> Iterator[Issue]:
    for task in ctx.tasks():
        command = _leading_command(task)
        if not command:
            continue
        first = _first_token(command)
        if _ABSOLUTE.match(first) or first.startswith("./") or first.startswith("../"):
            continue
        yield Issue(
            message=f"command {first!r} is resolved against cron's PATH, not a known directory",
            line=task.lineno,
            offset=task.command_start,
            length=len(first),
            saw=first,
            where=task.label,
        )


@rule(
    "CRN011",
    "World-writable directory in the command",
    "high",
    4,
    2,
    why=(
        "/tmp and /var/tmp are sticky but still attacker-writable: a symlink "
        "placed between the schedule and the run redirects the write, and a "
        "program dropped there first is the one that executes."
    ),
    fix="Keep the program and its data under a directory only root can write.",
)
def world_writable_path(ctx: Context) -> Iterator[Issue]:
    for task in ctx.tasks():
        for match in _WORLD_WRITABLE.finditer(task.command):
            yield Issue(
                message=f"command touches {match.group(0).strip()!r}, which any local user can write",
                line=task.lineno,
                offset=task.command_start + match.start(),
                length=match.end() - match.start(),
                saw=task.command[match.start() : match.end()].strip(),
                where=task.label,
            )


@rule(
    "CRN012",
    "Unquoted variable expansion",
    "medium",
    2,
    2,
    why=(
        "cron expands nothing itself, but the shell the command runs under "
        "does. An unquoted $NAME containing spaces or glob characters is split "
        "into words and pathname-expanded before the program sees it, which is "
        "how a backup path becomes two arguments and a wildcard becomes every "
        "file in the directory."
    ),
    fix='Quote it: "$NAME", and quote the path it expands into.',
)
def unquoted_variable(ctx: Context) -> Iterator[Issue]:
    for task in ctx.tasks():
        for match in _VARIABLE.finditer(task.command):
            start = match.start()
            before = task.command[:start]
            # Inside double quotes it is safe; single quotes prevent expansion
            # altogether. Counting quotes backwards is enough for a line that
            # is one command.
            if before.count('"') % 2 == 1:
                continue
            if before.count("'") % 2 == 1:
                continue
            yield Issue(
                message=f"{match.group(0)} is expanded unquoted",
                line=task.lineno,
                offset=task.command_start + start,
                length=match.end() - start,
                saw=match.group(0),
                where=task.label,
            )


@rule(
    "CRN013",
    "Command substitution in a scheduled command",
    "medium",
    2,
    2,
    why=(
        "Whatever the substitution returns becomes words in the command line. "
        "If it reads a file an unprivileged process can write, that content is "
        "executed as arguments at the next tick."
    ),
    fix="Resolve the value once, outside the schedule, and pass it in explicitly.",
)
def command_substitution(ctx: Context) -> Iterator[Issue]:
    for task in ctx.tasks():
        for match in _COMMAND_SUB.finditer(task.command):
            yield Issue(
                message=f"command substitution {task.command[match.start() : match.start() + 12]!r} runs at every tick",
                line=task.lineno,
                offset=task.command_start + match.start(),
                length=1,
                saw=task.command[match.start() : match.start() + 24],
                where=task.label,
            )


@rule(
    "CRN014",
    "Credential on the command line",
    "critical",
    6,
    2,
    why=(
        "cron's children inherit the terminal-less environment, but the "
        "argument vector is world-readable in /proc while the job runs: any "
        "local user can `ps` it, and it lands in shell history files and in "
        "any process accounting the box keeps."
    ),
    fix="Read the secret from a root-only file or a keyring; never put it in the command.",
)
def credential_in_command(ctx: Context) -> Iterator[Issue]:
    for task in ctx.tasks():
        for match in _CREDENTIAL.finditer(task.command):
            yield Issue(
                message="credential is passed as an argument and is visible in the process list",
                line=task.lineno,
                offset=task.command_start + match.start(),
                length=match.end() - match.start(),
                saw=task.command[match.start() : match.start() + 40],
                where=task.label,
            )


@rule(
    "CRN015",
    "Download piped into a shell",
    "critical",
    6,
    2,
    why=(
        "The script that runs is whatever the server returns at that moment. "
        "Scheduled, unattended and usually root: it is the most reliable way "
        "to turn someone else's outage into your compromise."
    ),
    fix="Fetch to a file, verify a signature or a pinned hash, then execute the verified copy.",
)
def pipe_to_shell(ctx: Context) -> Iterator[Issue]:
    for task in ctx.tasks():
        for match in _PIPE_TO_SHELL.finditer(task.command):
            yield Issue(
                message="remote content is executed without being checked first",
                line=task.lineno,
                offset=task.command_start + match.start(),
                length=match.end() - match.start(),
                saw=task.command[match.start() : match.end()],
                where=task.label,
            )


@rule(
    "CRN016",
    "Redirect onto a block device",
    "critical",
    6,
    2,
    why=(
        "Writing to /dev/sda destroys the partition table rather than a file. "
        "A typo in a path here is unrecoverable and runs unattended."
    ),
    fix="Redirect to a log file under /var/log, never to a device node.",
)
def device_redirect(ctx: Context) -> Iterator[Issue]:
    for task in ctx.tasks():
        for match in _DEVICE_REDIRECT.finditer(task.command):
            yield Issue(
                message="output is redirected onto a block device",
                line=task.lineno,
                offset=task.command_start + match.start(),
                length=match.end() - match.start(),
                saw=task.command[match.start() : match.end()],
                where=task.label,
            )


@rule(
    "CRN017",
    "Overwrite redirect onto a system path",
    "high",
    4,
    2,
    why=(
        "`> /etc/passwd` truncates before the command runs: even a failing job "
        "has already emptied the file. Append (`>>`) does not have this "
        "failure mode."
    ),
    fix="Use >> for logs, and write generated config to a temp file you rename.",
)
def sensitive_redirect(ctx: Context) -> Iterator[Issue]:
    for task in ctx.tasks():
        for match in _SENSITIVE_REDIRECT.finditer(task.command):
            yield Issue(
                message="truncate-redirect targets a system path; the file is emptied before the command runs",
                line=task.lineno,
                offset=task.command_start + match.start(),
                length=match.end() - match.start(),
                saw=task.command[match.start() : match.end()],
                where=task.label,
            )


@rule(
    "CRN018",
    "Recursive delete with a variable or a broad root",
    "high",
    4,
    2,
    why=(
        "`rm -rf $TARGET` with an unset TARGET becomes `rm -rf /`'s argument "
        "list with an empty word dropped -- but with a set-but-wrong one it is "
        "the whole tree, unattended, as root."
    ),
    fix="Guard it: refuse to run when the variable is empty, and delete a fixed, narrow path.",
)
def recursive_delete(ctx: Context) -> Iterator[Issue]:
    for task in ctx.tasks():
        match = _RM_RF.search(task.command)
        if not match:
            continue
        rest = task.command[match.end() :]
        # Only a variable target is ambiguous enough to report. A literal path
        # is visible in the line and the reader can judge it for themselves;
        # a variable is whatever the environment makes it at run time.
        if not _VARIABLE.search(rest[:80]):
            continue
        yield Issue(
            message="recursive delete whose target comes from a variable",
            line=task.lineno,
            offset=task.command_start + match.start(),
            length=match.end() - match.start(),
            saw=task.command[match.start() : match.start() + 48],
            where=task.label,
        )


@rule(
    "CRN019",
    "Job runs with no log destination",
    "low",
    1,
    2,
    why=(
        "cron mails stdout to the user when there is an address and discards "
        "it otherwise. A job that fails at 03:00 with no redirect leaves no "
        "trace, which is how a broken backup stays broken for a year."
    ),
    fix="Append to a log: `>> /var/log/job.log 2>&1`, or pipe to logger.",
)
def no_log_destination(ctx: Context) -> Iterator[Issue]:
    for task in ctx.tasks():
        command = task.command
        if ">>" in command or "2>&1" in command or "logger" in command:
            continue
        if "|" in command and "grep" not in command:
            continue  # a pipeline has somewhere for output to go
        yield Issue(
            message="stdout and stderr go nowhere, so failures leave no record",
            line=task.lineno,
            offset=task.command_start,
            length=min(len(command), 60),
            saw=command[:60],
            where=task.label,
        )


@rule(
    "CRN020",
    "Command needs an interactive terminal",
    "medium",
    2,
    2,
    why=(
        "cron has no tty and no DISPLAY. Anything that needs one fails on the "
        "first tick, usually with an error that only appears if you were "
        "watching."
    ),
    fix="Use the non-interactive form of the tool, or move the job to a session that has a display.",
)
def needs_tty(ctx: Context) -> Iterator[Issue]:
    for task in ctx.tasks():
        for match in _TTY_COMMAND.finditer(task.command):
            yield Issue(
                message=f"{match.group(0)!r} expects an interactive session, which cron does not provide",
                line=task.lineno,
                offset=task.command_start + match.start(),
                length=match.end() - match.start(),
                saw=match.group(0),
                where=task.label,
            )


@rule(
    "CRN021",
    "Script without a shebang",
    "low",
    1,
    2,
    why=(
        "Without `#!` the kernel falls back to the shell that execs it, which "
        "is /bin/sh under cron even when the file uses bash-only syntax. The "
        "same script works from your terminal and fails at 03:00."
    ),
    fix="Put a shebang on line 1 and make it executable.",
)
def missing_shebang(ctx: Context) -> Iterator[Issue]:
    for task in ctx.tasks():
        path = task.script_path
        if path is None:
            continue  # a shell one-liner needs no shebang
        if path.startswith(("/bin/", "/usr/bin/", "/usr/local/bin/")) and not path.endswith(".sh"):
            continue
        try:
            with open(path, "rb") as handle:
                first = handle.readline(4)
        except OSError:
            continue  # not readable here; the path rule already spoke
        if first.startswith(b"#!"):
            continue
        yield Issue(
            message=f"{path} has no shebang, so cron runs it with /bin/sh",
            line=task.lineno,
            offset=task.command_start,
            length=len(task.command),
            saw=path,
            where=task.label,
        )


@rule(
    "CRN022",
    "Task runs as root",
    "medium",
    2,
    2,
    why=(
        "Every bug in the command is then a root bug. Most scheduled jobs "
        "need one directory's worth of access, not the whole box."
    ),
    fix="Name a dedicated user in the user column, and give that user only what the job touches.",
)
def runs_as_root(ctx: Context) -> Iterator[Issue]:
    app = ctx.app
    for task in app.tasks:
        if task.user and task.user != "root":
            continue
        if task.user is None and not app.assume_user:
            continue  # a user crontab already runs as its owner
        if task.user is None:
            continue
        yield Issue(
            message="scheduled as root",
            line=task.lineno,
            offset=task.expr_start,
            length=task.expr_end - task.expr_start,
            saw=task.raw.strip()[:70],
            where=task.label,
        )


@rule(
    "CRN023",
    "Every minute",
    "medium",
    2,
    2,
    why=(
        "Once a minute is the densest schedule cron offers. Any job longer "
        "than 60 seconds then runs into itself, and the failure mode is two "
        "copies of a backup writing over each other."
    ),
    fix="Widen the interval, or make the job take a lock so a second copy exits immediately.",
)
def every_minute(ctx: Context) -> Iterator[Issue]:
    for task in ctx.tasks():
        if task.expression.special == "reboot":
            continue
        if not task.expression.runs_every_minute:
            continue
        yield Issue(
            message="runs every minute",
            line=task.lineno,
            offset=task.expr_start,
            length=task.expr_end - task.expr_start,
            saw=task.expression.raw,
            where=task.label,
        )


@rule(
    "CRN024",
    "Environment line is not quoted",
    "low",
    1,
    2,
    why=(
        "cron splits environment lines on whitespace after the `=`. A value "
        "with spaces is truncated at the first one, so PATH or BACKUP_DIR is "
        "silently shorter than it looks."
    ),
    fix='Quote the value: DIR="/var/backups".',
)
def unquoted_env_value(ctx: Context) -> Iterator[Issue]:
    for entry in ctx.app.env:
        if " " not in entry.value:
            continue
        if entry.raw.split("=", 1)[1].strip().startswith(('"', "'")):
            continue
        yield Issue(
            message=f"{entry.name} contains spaces and is not quoted; cron stops the value at the first space",
            line=entry.lineno,
            offset=entry.value_start,
            length=entry.value_end - entry.value_start,
            saw=entry.value[:50],
            where=entry.name,
        )


@rule(
    "CRN025",
    "PATH points at a world-writable directory",
    "high",
    4,
    2,
    why=(
        "cron resolves every bare command against this PATH. With /tmp in it, "
        "any local user who drops /tmp/ls first wins the race for every "
        "scheduled job on the box."
    ),
    fix="Keep PATH to system directories: /usr/local/bin:/usr/bin:/bin.",
)
def unsafe_path(ctx: Context) -> Iterator[Issue]:
    for entry in ctx.app.env:
        if entry.name != "PATH":
            continue
        for part in entry.value.split(":"):
            if part in {"/tmp", "/var/tmp", "/dev/shm", "."} or part.endswith("/."):
                yield Issue(
                    message=f"PATH contains {part!r}, which another user can write",
                    line=entry.lineno,
                    offset=entry.value_start,
                    length=entry.value_end - entry.value_start,
                    saw=entry.value[:60],
                    where="PATH",
                )


@rule(
    "CRN026",
    "Two timezone settings in one file",
    "medium",
    2,
    2,
    why=(
        "TZ and CRON_TZ are honoured by different implementations. Setting "
        "both, or setting one and relying on the other, means the file means "
        "different hours on different hosts."
    ),
    fix="Set exactly one of them, and say which in a comment.",
)
def conflicting_timezones(ctx: Context) -> Iterator[Issue]:
    names = {entry.name for entry in ctx.app.env}
    both = {"TZ", "CRON_TZ"} & names
    if len(both) < 2:
        return
    entries = [entry for entry in ctx.app.env if entry.name in both]
    first = min(entries, key=lambda item: item.lineno)
    yield Issue(
        message="both TZ and CRON_TZ are set; different crons honour different ones",
        line=first.lineno,
        offset=first.offset,
        length=len(first.raw),
        saw=first.raw.strip(),
        where="environment",
    )


# ---------------------------------------------------------------- pass 3


@rule(
    "CRN050",
    "Duplicate schedule",
    "medium",
    2,
    3,
    why=(
        "The same schedule twice means the command runs twice at the same "
        "instant. That is two writers on one output file, or a job that takes "
        "twice as long as it looks."
    ),
    fix="Delete the duplicate, or give it a different time if you meant two runs.",
)
def duplicate_schedule(ctx: Context) -> Iterator[Issue]:
    seen: dict[str, Task] = {}
    for task in ctx.tasks():
        key = task.expression.raw
        if key in seen:
            first = seen[key]
            yield Issue(
                message=f"same schedule as line {first.lineno}",
                line=task.lineno,
                offset=task.expr_start,
                length=task.expr_end - task.expr_start,
                saw=task.expression.raw,
                where=task.label,
            )
        else:
            seen[key] = task


@rule(
    "CRN051",
    "Same script scheduled more than once",
    "medium",
    2,
    3,
    why=(
        "Two schedules for one program is either a leftover or an overlap: if "
        "one run has not finished when the next starts, they contend for the "
        "same files and the last one wins."
    ),
    fix="Keep one schedule, or make the program lock so a second instance exits.",
)
def same_script_twice(ctx: Context) -> Iterator[Issue]:
    by_path: dict[str, list[Task]] = {}
    for task in ctx.tasks():
        path = task.script_path
        if path:
            by_path.setdefault(path, []).append(task)
    for path, tasks in by_path.items():
        if len(tasks) < 2:
            continue
        first = tasks[0]
        for task in tasks[1:]:
            yield Issue(
                message=f"{path} is also scheduled on line {first.lineno} ({first.expression.raw})",
                line=task.lineno,
                offset=task.expr_start,
                length=task.expr_end - task.expr_start,
                saw=task.expression.raw,
                where=task.label,
            )


@rule(
    "CRN052",
    "Two tasks fire in the same minute",
    "medium",
    2,
    3,
    why=(
        "Tasks that share a minute share a machine: two backups, a backup and "
        "a rotation, anything with a lock file. On a small box the second one "
        "waits, times out, or collides with the first."
    ),
    fix="Stagger the minutes, or make both jobs lock-aware.",
)
def same_minute_collision(ctx: Context) -> Iterator[Issue]:
    tasks = ctx.tasks()
    reported: set[tuple[int, int]] = set()
    for index, left in enumerate(tasks):
        for right in tasks[index + 1 :]:
            if not overlaps(left.expression, right.expression):
                continue
            key = (left.lineno, right.lineno)
            if key in reported:
                continue
            reported.add(key)
            yield Issue(
                message=f"can fire in the same minute as line {right.lineno} ({right.expression.raw})",
                line=left.lineno,
                offset=left.expr_start,
                length=left.expr_end - left.expr_start,
                saw=left.expression.raw,
                where=left.label,
            )


@rule(
    "CRN053",
    "Two tasks write the same log file",
    "low",
    1,
    3,
    why=(
        "Two appends into one file interleave line by line. The log still "
        "looks fine and is useless for telling which job said what."
    ),
    fix="One log per job, or a logger that tags each line with its source.",
)
def shared_output_file(ctx: Context) -> Iterator[Issue]:
    writers: dict[str, list[Task]] = {}
    pattern = re.compile(r"(>>|>)\s*([^\s;&|]+)")
    for task in ctx.tasks():
        for match in pattern.finditer(task.command):
            target = match.group(2)
            if target.startswith("/dev/"):
                continue
            writers.setdefault(target, []).append(task)
    for target, tasks in writers.items():
        unique = {task.lineno: task for task in tasks}
        if len(unique) < 2:
            continue
        ordered = sorted(unique.values(), key=lambda task: task.lineno)
        first = ordered[0]
        for task in ordered[1:]:
            yield Issue(
                message=f"also writes {target}, first written on line {first.lineno}",
                line=task.lineno,
                offset=task.command_start,
                length=min(len(task.command), 70),
                saw=task.command[:70],
                where=task.label,
            )


@rule(
    "CRN054",
    "Every-minute job with no lock",
    "high",
    3,
    3,
    why=(
        "A job that fires every minute and has no lock, no `flock -n` and no "
        "pidfile check will overlap itself as soon as it takes longer than a "
        "minute -- and the overlap is silent."
    ),
    fix="Wrap it: `flock -n /run/job.lock /path/to/job`.",
)
def frequent_without_lock(ctx: Context) -> Iterator[Issue]:
    lock_pattern = re.compile(r"\bflock\b|\bpidfile\b|lockfile|\s-n\s+/run/")
    for task in ctx.tasks():
        if not task.expression.runs_every_minute:
            continue
        if lock_pattern.search(task.command):
            continue
        yield Issue(
            message="fires every minute with nothing preventing a second copy",
            line=task.lineno,
            offset=task.expr_start,
            length=task.expr_end - task.expr_start,
            saw=task.expression.raw,
            where=task.label,
        )


@rule(
    "CRN055",
    "Many tasks share one minute",
    "low",
    1,
    3,
    why=(
        "Everything scheduled at the same minute starts at once. On a small "
        "host that is a load spike, and any job with a timeout loses."
    ),
    fix="Spread them across minutes; cron has 60 of them.",
)
def crowded_minute(ctx: Context) -> Iterator[Issue]:
    counts: Counter[tuple[int, ...]] = Counter()
    owners: dict[tuple[int, ...], list[Task]] = {}
    for task in ctx.tasks():
        minute, hour = task.expression.minute, task.expression.hour
        if minute is None or hour is None or not minute.is_single_value:
            continue
        key = (next(iter(minute.values)), next(iter(hour.values)))
        counts[key] += 1
        owners.setdefault(key, []).append(task)
    for key, count in counts.items():
        if count < 4:
            continue
        tasks = owners[key]
        first = tasks[0]
        yield Issue(
            message=f"{count} tasks all fire at {key[1]:02d}:{key[0]:02d}",
            line=first.lineno,
            offset=first.expr_start,
            length=first.expr_end - first.expr_start,
            saw=first.expression.raw,
            where=first.label,
        )


@rule(
    "CRN056",
    "File has no scheduled tasks",
    "note",
    1,
    3,
    why=(
        "A crontab with only comments and environment lines schedules "
        "nothing. If that was not intended, the jobs are missing; if it was, "
        "the file is dead weight."
    ),
    fix="Add the schedules, or delete the file so it stops looking like a source of jobs.",
)
def empty_schedule(ctx: Context) -> Iterator[Issue]:
    if ctx.app.tasks:
        return
    yield Issue(
        message="no scheduled tasks in this file",
        line=1,
        offset=0,
        saw=f"{len(ctx.app.comments)} comment(s), {len(ctx.app.env)} environment line(s)",
        where=ctx.app.source,
    )


@rule(
    "CRN057",
    "Schedule line is very long",
    "low",
    1,
    3,
    why=(
        "Implementations cap a line at a few hundred to a few thousand "
        "characters. Past the cap the command is cut mid-word, and the job "
        "runs something that is not what you wrote."
    ),
    fix="Move the logic into a script and schedule that.",
)
def overlong_line(ctx: Context) -> Iterator[Issue]:
    for task in ctx.tasks():
        if len(task.raw) <= 512:
            continue
        yield Issue(
            message=f"line is {len(task.raw)} characters; many crons truncate well before that",
            line=task.lineno,
            offset=task.offset,
            length=len(task.raw),
            saw=task.raw[:60] + "...",
            where=task.label,
        )


@rule(
    "CRN058",
    "Task reads a file another task writes in the same minute",
    "medium",
    2,
    3,
    why=(
        "Two tasks that share a minute and touch the same path have an "
        "undefined order: the reader can see a half-written file, and which "
        "one wins depends on the scheduler, not on you."
    ),
    fix="Give the writer an earlier minute, or have the reader wait for a sentinel.",
)
def read_write_race(ctx: Context) -> Iterator[Issue]:
    write_pattern = re.compile(r"(?<!>)>>?\s*([^\s;&|]+)")
    read_pattern = re.compile(r"(?:^|\s)([^\s;&|]*\.(?:txt|csv|json|log|dat|db))\b")
    writers: list[tuple[Task, str]] = []
    readers: list[tuple[Task, str]] = []
    for task in ctx.tasks():
        for match in write_pattern.finditer(task.command):
            if not match.group(1).startswith("/dev/"):
                writers.append((task, match.group(1)))
        for match in read_pattern.finditer(task.command):
            readers.append((task, match.group(1)))
    reported: set[tuple[int, int]] = set()
    for writer, path in writers:
        for reader, other in readers:
            if writer.lineno == reader.lineno or path != other:
                continue
            if not overlaps(writer.expression, reader.expression):
                continue
            key = (reader.lineno, writer.lineno)
            if key in reported:
                continue
            reported.add(key)
            yield Issue(
                message=f"reads {path} in the same minute that line {writer.lineno} writes it",
                line=reader.lineno,
                offset=reader.command_start,
                length=min(len(reader.command), 70),
                saw=reader.command[:70],
                where=reader.label,
            )


def executable() -> list[Rule]:
    """The table in id order, which is also the report order within a stage."""
    return sorted(REGISTRY, key=lambda item: item.id)
