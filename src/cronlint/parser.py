"""Turn a crontab file into tasks, environment lines and comments.

Three shapes live in the wild and they are not interchangeable:

* a user crontab is ``minute hour dom mon dow command``;
* ``/etc/crontab`` and everything under ``/etc/cron.d/`` add a user column
  between the schedule and the command;
* cronie and quartz accept a leading seconds field, and quartz a trailing year.

Guessing wrong shifts the whole line: the command is read as a field, or a
field is read as the command. So the shape is decided from the file's location
first, then from the tokens, and whatever the decision was is recorded in
``App.notes`` so a report can say why it read the line the way it did.
"""

from __future__ import annotations

import re
from pathlib import Path

from .expr import DAY_NAMES, MONTH_NAMES, parse_expression
from .model import App, Comment, EnvVar, Task

# A NAME=value line at the start of a user crontab line. Schedules never start
# with a name, so this cannot be confused with a task.
_ENV_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")

# Two predicates, because one regex cannot do both jobs.
#
# _is_field_token is the strict shape test used to decide structure: is this
# token the user column, a seconds field, a year? It must say NO to `root` --
# a user name that is also three letters -- or a cron.d user column reads as
# a schedule field and every column after it shifts.
#
# _looks_like_schedule_text is the looser test, used only to reject paths in
# the five-field zone: `mon/2` and `jan-mar/2` are valid schedule text that
# the strict test rejects, and a line containing one must not be dropped.
_FIELD_TOKEN_RE = re.compile(
    r"^(?:"
    r"[0-9*,\-/]+"  # 0, *, 1-5, */15, 1,15
    r"|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)"  # months
    r"|(?:sun|mon|tue|wed|thu|fri|sat)"  # weekdays
    r")$",
    re.IGNORECASE,
)
_ENDPOINT = r"(?:[0-9]+|[a-z]{3})"
_ITEM = rf"(?:\*|{_ENDPOINT}(?:-{_ENDPOINT})?)(?:/[0-9]+)?"
_SCHEDULE_TOKEN_RE = re.compile(rf"^{_ITEM}(?:,{_ITEM})*$", re.IGNORECASE)

_USER_COLUMN_SOURCES = ("/etc/crontab",)


def _is_field_token(token: str) -> bool:
    return bool(_FIELD_TOKEN_RE.match(token)) and token not in {"-", "/", ","}


def _looks_like_schedule_text(token: str) -> bool:
    """Loose shape test: numbers, wildcards, ranges, steps, named ranges."""
    return bool(_SCHEDULE_TOKEN_RE.match(token))


def _looks_like_user(token: str) -> bool:
    """True when a sixth token can be the cron.d user column.

    A user column holds a name; a ``/`` there means the column is missing
    entirely, not that someone named their user ``tmp``. Such a line is read
    as five fields plus a command instead, so the command stays a command.
    """
    return not _is_field_token(token) and "/" not in token


def _byte_offset(line: str, char_index: int) -> int:
    """Byte offset of a character index inside one line (UTF-8 aware)."""
    return len(line[:char_index].encode("utf-8"))


def _split_with_offsets(text: str) -> list[tuple[str, int]]:
    """Split on whitespace, remembering each token's start index."""
    out: list[tuple[str, int]] = []
    index = 0
    while index < len(text):
        if text[index].isspace():
            index += 1
            continue
        start = index
        while index < len(text) and not text[index].isspace():
            index += 1
        out.append((text[start:index], start))
    return out


def _needs_user_column(source: str, assume_user: bool | None) -> bool:
    if assume_user is not None:
        return assume_user
    path = source.replace("\\", "/")
    # Only the two system locations that document a user column get one. A
    # file merely *named* crontab (a backup, an export, a spool copy) is a
    # user crontab: reading a user column into it shifts the command into the
    # user slot and the real user into nowhere.
    return path in _USER_COLUMN_SOURCES or "/etc/cron.d/" in path


def parse(
    text: str,
    source: str = "<text>",
    assume_user: bool | None = None,
    assume_seconds: bool = False,
    standard: bool = False,
) -> App:
    """Parse crontab ``text``.

    ``assume_user`` forces the ``/etc/crontab`` shape (a user column); ``None``
    decides from ``source``. ``assume_seconds`` forces the six-field reading of
    an ambiguous line, which is the only way to settle ``* * * * * *`` vs
    ``* * * * * <command starting with *>`` from the text alone.
    ``standard`` is the opposite lever: read every ambiguous six-field line as
    five fields plus a command, the way vanilla cron does.
    """
    user_column = _needs_user_column(source, assume_user)
    app = App(
        source=source,
        assume_user=user_column,
        assume_seconds=assume_seconds,
        size=len(text.encode("utf-8")),
    )
    app.lines = text.splitlines()

    offset = 0
    for lineno, raw in enumerate(text.splitlines(keepends=True), 1):
        line = raw.rstrip("\r\n")
        line_bytes = len(raw.encode("utf-8"))
        stripped = line.strip()

        if not stripped:
            offset += line_bytes
            continue

        if stripped.startswith("#"):
            app.comments.append(
                Comment(
                    lineno=lineno,
                    offset=offset + _byte_offset(line, line.index("#")),
                    raw=line,
                    text=stripped.lstrip("#").strip(),
                )
            )
            offset += line_bytes
            continue

        env = _ENV_RE.match(stripped)
        if env and not stripped.startswith("@"):
            lead = line.index(stripped)
            name, _, value = env.group(1), "=", env.group(2)
            value_char = lead + env.start(2)
            app.env.append(
                EnvVar(
                    lineno=lineno,
                    offset=offset,
                    raw=line,
                    name=name,
                    value=value.strip(),
                    value_start=offset + _byte_offset(line, value_char),
                    value_end=offset + _byte_offset(line, value_char + len(env.group(2))),
                )
            )
            offset += line_bytes
            continue

        task = _parse_task_line(
            line=line,
            lineno=lineno,
            offset=offset,
            source=source,
            user_column=user_column,
            assume_seconds=assume_seconds,
            standard=standard,
            app=app,
        )
        if task is not None:
            app.tasks.append(task)

        offset += line_bytes

    return app


def _parse_task_line(
    line: str,
    lineno: int,
    offset: int,
    source: str,
    user_column: bool,
    assume_seconds: bool,
    standard: bool,
    app: App,
) -> Task | None:
    stripped = line.strip()
    lead = line.index(stripped)
    tokens = _split_with_offsets(stripped)

    if not tokens:
        return None

    if stripped.startswith("@"):
        name = tokens[0][0]
        # /etc/cron.d writes "@reboot root /path/to/job": the user column sits
        # between the special and the command there, and reading it as part of
        # the command hides both the privilege and the real path.
        command_index = 1
        user: str | None = None
        if user_column and len(tokens) >= 3 and "/" not in tokens[1][0]:
            user = tokens[1][0]
            command_index = 2
        if command_index >= len(tokens):
            app.errors.append(f"{source}:{lineno}: {name} has no command")
            return None
        _, command_char = tokens[command_index]
        expression = parse_expression(name, offset + _byte_offset(line, lead + tokens[0][1]))
        return Task(
            lineno=lineno,
            offset=offset,
            raw=line,
            expression=expression,
            expr_start=offset + _byte_offset(line, lead + tokens[0][1]),
            expr_end=offset + _byte_offset(line, lead + tokens[0][1] + len(name)),
            command=stripped[command_char:].strip(),
            command_start=offset + _byte_offset(line, lead + command_char),
            command_end=offset + _byte_offset(line, len(stripped)),
            user=user,
            source=source,
        )

    # Not a special: it has to start with a schedule.
    if not _is_field_token(tokens[0][0]):
        app.errors.append(
            f"{source}:{lineno}: line does not start with a schedule or @special: {stripped[:40]!r}"
        )
        return None

    # Five schedule fields come before any user column or command. A path in
    # that zone means the line has fewer than five fields -- `0 12 * * /opt/x`
    # is four fields plus a command -- and reading past it would shift the
    # user, the command, or both. Schedule text the strict test rejects
    # (`mon/2`, an unknown name) still flows through to the expression
    # parser, which reports it with a span as CRN001.
    for index in range(min(5, len(tokens))):
        token = tokens[index][0]
        if "/" in token and not _looks_like_schedule_text(token):
            app.errors.append(
                f"{source}:{lineno}: schedule field {index + 1} {token!r} "
                "is not a schedule field; a schedule needs 5"
            )
            return None

    count = len(tokens)
    expression_tokens: list[tuple[str, int]]
    user: str | None = None
    user_inferred = False
    command_index: int
    shape = "standard"

    # The user column only appears when token 6 cannot itself be a schedule
    # token and is not a path: in `/etc/cron.d` it is a name like `root`,
    # never a `*`, a number, or `/some/command`. A `*` there means someone
    # wrote a six-field schedule into a file that does not take one; a path
    # there means the user column is missing. Both are worth parsing as the
    # schedule rather than reading the token as a user name.
    if user_column and count >= 6 and _looks_like_user(tokens[5][0]):
        expression_tokens = tokens[:5]
        user = tokens[5][0]
        command_index = 6
        shape = "user-column"
    elif count >= 7 and _is_field_token(tokens[5][0]) and _is_field_token(tokens[6][0]):
        expression_tokens = tokens[:7]
        command_index = 7
        shape = "year"
        # cron.d puts the user after the schedule; with a trailing year that
        # user sits at index 7, not 5.
        if user_column and count >= 8 and _looks_like_user(tokens[7][0]):
            user = tokens[7][0]
            command_index = 8
    elif assume_seconds and count >= 6:
        expression_tokens = tokens[:6]
        command_index = 6
        shape = "seconds-forced"
        # Same for a forced six-field reading in a user-column file: after
        # six schedule fields the next name is the user, not the command.
        if user_column and count >= 7 and _looks_like_user(tokens[6][0]):
            user = tokens[6][0]
            command_index = 7
    elif not standard and count >= 7 and _is_field_token(tokens[5][0]) and not _is_field_token(tokens[6][0]):
        expression_tokens = tokens[:6]
        command_index = 6
        shape = "seconds"
        # `* * * * * * root /opt/x` in /etc/cron.d: six fields, then the user
        # column, then the command. Reading `root` as part of the command
        # hides both the privilege and the real path.
        if user_column and _looks_like_user(tokens[6][0]):
            user = tokens[6][0]
            command_index = 7
    elif count >= 5:
        expression_tokens = tokens[:5]
        command_index = 5
        shape = "standard"
    else:
        app.errors.append(f"{source}:{lineno}: only {count} fields, a schedule needs 5")
        return None

    # A schedule with no command is still a schedule: dropping the line here
    # would hide it from every rule, and "installed, does nothing" is exactly
    # the failure CRN005 exists to report.
    has_command = command_index < count
    if not has_command:
        app.errors.append(f"{source}:{lineno}: schedule with no command")

    expr_char = lead + expression_tokens[0][1]
    expr_end_char = lead + expression_tokens[-1][1] + len(expression_tokens[-1][0])
    schedule_text = stripped[
        expression_tokens[0][1] : expression_tokens[-1][1] + len(expression_tokens[-1][0])
    ]
    expression = parse_expression(schedule_text, offset + _byte_offset(line, expr_char))
    # parse_expression sees six tokens and calls that "seconds"; the forced
    # reading is a distinct decision the report should be able to name, and
    # CRN002 treats both the same.
    if shape == "seconds-forced":
        expression.kind = "seconds-forced"
    # Expression and field errors stay on the expression: a rule turns them into
    # a finding with a span, which is more useful than a flat string in
    # App.errors. Only whole-line problems land there.

    if shape == "seconds":
        app.notes.append(
            f"{source}:{lineno}: read as a 6-field schedule (leading seconds) because "
            f"field 6 {tokens[5][0]!r} is a schedule token; pass --standard to read it "
            "as a 5-field schedule with a command instead"
        )
    elif shape == "seconds-forced":
        app.notes.append(
            f"{source}:{lineno}: read as a 6-field schedule (leading seconds) because "
            "--assume-seconds was passed; pass --standard to read it as a 5-field "
            "schedule with a command instead"
        )
    if shape == "year":
        app.notes.append(
            f"{source}:{lineno}: read as a 7-field schedule (trailing year), which "
            "standard cron and cronie do not support"
        )

    if has_command:
        command_char = lead + tokens[command_index][1]
        command_text = stripped[tokens[command_index][1] :].strip()
    else:
        command_char = lead + len(stripped)  # empty: point at the end of the line
        command_text = ""
    return Task(
        lineno=lineno,
        offset=offset,
        raw=line,
        expression=expression,
        expr_start=offset + _byte_offset(line, expr_char),
        expr_end=offset + _byte_offset(line, expr_end_char),
        command=command_text,
        command_start=offset + _byte_offset(line, command_char),
        command_end=offset + _byte_offset(line, len(stripped)),
        user=user,
        source=source,
        user_inferred=user_inferred,
    )


def parse_file(
    path: str | Path,
    assume_user: bool | None = None,
    assume_seconds: bool = False,
    standard: bool = False,
) -> App:
    """Read and parse a crontab file.

    The bytes are decoded as UTF-8 with replacement rather than raising: a
    crontab with one bad byte in a comment should still have its schedules
    audited, and the damaged comment shows up as itself.
    """
    file_path = Path(path)
    data = file_path.read_bytes()
    text = data.decode("utf-8", errors="replace")
    return parse(
        text,
        source=str(file_path),
        assume_user=assume_user,
        assume_seconds=assume_seconds,
        standard=standard,
    )


def looks_like_crontab(path: str | Path) -> bool:
    """Heuristic used by the CLI when a directory is scanned.

    Files under a ``cron.d`` directory count whatever they are called: those
    are named ``app``, ``sysstat`` and ``apt-compat``, not ``crontab``.
    """
    normalized = str(path).replace("\\", "/")
    if "/cron.d/" in normalized:
        return True
    candidate = Path(path)
    name = candidate.name
    if name in {"crontab", "crontab.txt"}:
        return True
    if name.startswith("cron") and candidate.suffix in {"", ".txt", ".cron"}:
        return True
    return candidate.suffix == ".cron"


__all__ = ["looks_like_crontab", "parse", "parse_file", "DAY_NAMES", "MONTH_NAMES"]
