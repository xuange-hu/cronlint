"""What a parsed crontab is made of.

Everything carries the line number and the byte offset it came from. A finding
that cannot point at the characters it is talking about is an opinion, so the
offsets are recorded at parse time, before any rule runs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from dataclasses import field as dc_field

from .expr import Expression


@dataclass
class Task:
    """One scheduled command."""

    lineno: int
    offset: int  # byte offset of the line start inside the file
    raw: str
    expression: Expression
    expr_start: int  # absolute byte offset of the expression
    expr_end: int
    command: str
    command_start: int  # absolute byte offset of the command
    command_end: int
    user: str | None = None
    source: str = "<text>"
    user_inferred: bool = False  # the user column was guessed, not declared

    @property
    def schedule(self) -> str:
        return self.expression.raw

    @property
    def span(self) -> tuple[int, int]:
        return (self.expr_start, self.expr_end)

    @property
    def command_span(self) -> tuple[int, int]:
        return (self.command_start, self.command_end)

    @property
    def label(self) -> str:
        short = self.command.strip()
        if len(short) > 48:
            short = short[:45] + "..."
        return f"line {self.lineno}: {short}"

    @property
    def script_path(self) -> str | None:
        """The first absolute path in the command, when there is one.

        POSIX paths start with ``/``; on a Windows box the tests run on, the
        same rule has to recognise ``C:\\...`` or the shebang rule would see
        nothing to read and silently skip every script.
        """
        for token in self.command.split():
            if token.startswith("/") and not token.startswith("//"):
                return token
            if re.match(r"^[A-Za-z]:[\\/]", token):
                return token
        return None


@dataclass
class EnvVar:
    """A ``NAME=value`` line in a user crontab."""

    lineno: int
    offset: int
    raw: str
    name: str
    value: str
    value_start: int
    value_end: int


@dataclass
class Comment:
    lineno: int
    offset: int
    raw: str
    text: str


@dataclass
class App:
    """A whole crontab file: tasks, environment, comments and parse notes."""

    source: str = "<text>"
    lines: list[str] = dc_field(default_factory=list)
    tasks: list[Task] = dc_field(default_factory=list)
    env: list[EnvVar] = dc_field(default_factory=list)
    comments: list[Comment] = dc_field(default_factory=list)
    errors: list[str] = dc_field(default_factory=list)
    notes: list[str] = dc_field(default_factory=list)
    assume_user: bool = False
    assume_seconds: bool = False
    size: int = 0

    @property
    def line_count(self) -> int:
        return len(self.lines)

    def tasks_for(self, script: str) -> list[Task]:
        return [task for task in self.tasks if task.script_path == script]


@dataclass
class Finding:
    """One rule's opinion about one place in the file."""

    rule: str
    severity: str  # critical | high | medium | low | note
    message: str
    line: int
    offset: int
    length: int = 0
    saw: str = ""
    fix: str = ""
    where: str = ""
    score: int = 1

    @property
    def span(self) -> tuple[int, int]:
        return (self.offset, self.offset + max(self.length, 0))

    @property
    def end(self) -> int:
        return self.offset + max(self.length, 0)

    def sort_key(self) -> tuple:
        order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "note": 4}
        return (order.get(self.severity, 5), self.line, self.offset, self.rule)


@dataclass
class Report:
    """The result of running every rule over one file."""

    source: str
    app: App
    findings: list[Finding] = dc_field(default_factory=list)
    rules_run: int = 0
    rule_ids: list[str] = dc_field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.findings)

    @property
    def risk(self) -> int:
        """0-100. Criticals saturate quickly: three of them is already a bad day."""
        total = sum(f.score for f in self.findings)
        return min(100, total)

    def by_severity(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for finding in self.findings:
            out[finding.severity] = out.get(finding.severity, 0) + 1
        return out

    def by_rule(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for finding in self.findings:
            out[finding.rule] = out.get(finding.rule, 0) + 1
        return out

    @property
    def fired(self) -> set[str]:
        return {f.rule for f in self.findings}
