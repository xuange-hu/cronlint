"""Parse a cron expression, keeping a byte offset for every token.

Standard cron is five fields: minute, hour, day-of-month, month, day-of-week.
cronie accepts a leading seconds field and some quartz-flavoured schedulers
append a year. Both are accepted here rather than rejected, because a file that
means "every minute" on one host and "every second" on another deserves a
finding, not a parse error.

Every field and every comma item keeps the span it came from, so a finding can
point at the exact characters instead of "somewhere in the expression".
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dc_field

MONTH_NAMES = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

DAY_NAMES = {
    "sun": 0,
    "mon": 1,
    "tue": 2,
    "wed": 3,
    "thu": 4,
    "fri": 5,
    "sat": 6,
    # Vixie cron and cronie both accept 7 as Sunday; normalising it here keeps
    # later comparisons from treating 0 and 7 as two different weekdays.
    "7": 0,
}


@dataclass(frozen=True)
class FieldSpec:
    """What one position of the expression means and what it may contain."""

    name: str
    lo: int
    hi: int
    names: dict[str, int] = dc_field(default_factory=dict)
    alias: dict[int, int] = dc_field(default_factory=dict)


SPECS: tuple[FieldSpec, ...] = (
    FieldSpec("minute", 0, 59),
    FieldSpec("hour", 0, 23),
    FieldSpec("day-of-month", 1, 31),
    FieldSpec("month", 1, 12, MONTH_NAMES),
    FieldSpec("day-of-week", 0, 7, DAY_NAMES, {7: 0}),
)

SECOND_SPEC = FieldSpec("second", 0, 59)
YEAR_SPEC = FieldSpec("year", 1970, 2099)

SPECIALS: dict[str, str | None] = {
    "reboot": None,  # no schedule at all: it runs once at boot
    "yearly": "0 0 1 1 *",
    "annually": "0 0 1 1 *",
    "monthly": "0 0 1 * *",
    "weekly": "0 0 * * 0",
    "daily": "0 0 * * *",
    "midnight": "0 0 * * *",
    "hourly": "0 * * * *",
}


@dataclass
class Item:
    """One comma-separated item of a field, with the span it occupied."""

    text: str
    start: int
    end: int
    values: frozenset[int] = frozenset()
    error: str | None = None

    @property
    def span(self) -> tuple[int, int]:
        return (self.start, self.end)


@dataclass
class Field:
    """One whitespace-separated position of the expression."""

    spec: FieldSpec
    raw: str
    start: int
    end: int
    items: list[Item] = dc_field(default_factory=list)
    errors: list[str] = dc_field(default_factory=list)

    @property
    def span(self) -> tuple[int, int]:
        return (self.start, self.end)

    @property
    def values(self) -> frozenset[int]:
        out: set[int] = set()
        for item in self.items:
            out |= set(item.values)
        return frozenset(out)

    @property
    def is_wildcard(self) -> bool:
        """True when the field matches everything it is allowed to match."""
        if len(self.items) != 1:
            return False
        item = self.items[0]
        return item.text.strip() == "*" and not item.error

    @property
    def is_single_value(self) -> bool:
        return len(self.values) == 1 and not self.errors

    @property
    def display(self) -> str:
        return self.raw or "*"


@dataclass
class Expression:
    """A parsed schedule: five fields, six with seconds, seven with a year, or a special."""

    raw: str
    offset: int = 0  # where the expression starts inside its line
    fields: list[Field] = dc_field(default_factory=list)
    special: str | None = None
    expands: str | None = None  # what a @special stands for
    kind: str = "standard"  # standard | seconds | seconds-forced | year | special | malformed
    errors: list[str] = dc_field(default_factory=list)

    @property
    def has_seconds(self) -> bool:
        # seconds-forced is the same six-field reading chosen by a flag rather
        # than guessed from the tokens; the field offsets below must shift for
        # it exactly as they do for a guessed seconds field.
        return self.kind in {"seconds", "seconds-forced", "year"}

    @property
    def has_year(self) -> bool:
        return self.kind == "year"

    @property
    def minute(self) -> Field | None:
        return self._at(1 if self.has_seconds else 0)

    @property
    def hour(self) -> Field | None:
        return self._at(2 if self.has_seconds else 1)

    @property
    def day_of_month(self) -> Field | None:
        return self._at(3 if self.has_seconds else 2)

    @property
    def month(self) -> Field | None:
        return self._at(4 if self.has_seconds else 3)

    @property
    def day_of_week(self) -> Field | None:
        return self._at(5 if self.has_seconds else 4)

    @property
    def second(self) -> Field | None:
        return self._at(0) if self.has_seconds else None

    @property
    def year(self) -> Field | None:
        return self._at(6) if self.has_year else None

    @property
    def is_valid(self) -> bool:
        return not self.errors and all(not f.errors for f in self.fields)

    @property
    def runs_every_minute(self) -> bool:
        minute, hour = self.minute, self.hour
        return bool(
            minute
            and hour
            and minute.is_wildcard
            and (hour.is_wildcard or not hour.is_single_value or 0 in hour.values)
        )

    @property
    def runs_every_day(self) -> bool:
        dom, dow = self.day_of_month, self.day_of_week
        return bool(dom and dow and dom.is_wildcard and dow.is_wildcard)

    @property
    def runs_monthly(self) -> bool:
        dom, dow = self.day_of_month, self.day_of_week
        return bool(dom and dow and (dom.is_single_value or dow.is_single_value))

    def _at(self, index: int) -> Field | None:
        return self.fields[index] if 0 <= index < len(self.fields) else None


def _split_commas(text: str) -> list[tuple[str, int]]:
    """Split a field on commas, remembering where each piece started."""
    out: list[tuple[str, int]] = []
    start = 0
    for index, char in enumerate(text):
        if char == ",":
            out.append((text[start:index], start))
            start = index + 1
    out.append((text[start:], start))
    return out


def _to_int(text: str) -> int | None:
    text = text.strip()
    if not text:
        return None
    # cron never uses signs; rejecting them keeps "+5" and " 5" apart.
    if not text.isdigit():
        return None
    return int(text)


def _resolve(name: str, spec: FieldSpec) -> int | None:
    lowered = name.strip().lower()
    if lowered in spec.names:
        return spec.names[lowered]
    return None


def parse_item(text: str, base: int, spec: FieldSpec) -> Item:
    """Parse one comma item: ``*``, ``5``, ``1-10``, ``*/15``, ``jan-mar``, ``5/2``."""
    stripped = text.strip()
    lead = len(text) - len(text.lstrip())
    start = base + lead
    end = base + len(text.rstrip())
    item = Item(text=stripped, start=start, end=end)

    if not stripped:
        item.error = "empty list item"
        return item

    body, slash, step_text = stripped.partition("/")
    step = 1
    if slash:
        parsed = _to_int(step_text)
        if parsed is None or parsed < 1:
            item.error = f"step must be a positive integer, got {step_text.strip()!r}"
            return item
        step = parsed

    body = body.strip()
    low: int | None = None
    high: int | None = None

    if body == "*":
        low, high = spec.lo, spec.hi
    elif "-" in body:
        left, _, right = body.partition("-")
        low = _resolve(left, spec) if not left.strip().isdigit() else _to_int(left)
        high = _resolve(right, spec) if not right.strip().isdigit() else _to_int(right)
        if low is None or high is None:
            item.error = f"range {body!r} is not two numbers or month/day names"
            return item
        if low > high:
            item.error = f"range {body!r} runs backwards ({low} > {high})"
            return item
    else:
        value = _resolve(body, spec) if not body.isdigit() else _to_int(body)
        if value is None:
            item.error = f"{body!r} is not a number, a name, a range or *"
            return item
        low = value
        # "5/2" means "from 5 to the top of the field, every 2" in Vixie cron
        # and cronie; treating it as a single value would silently change the
        # schedule when the file moves between implementations.
        high = spec.hi if slash else value

    if low < spec.lo or high > spec.hi:
        item.error = f"{body!r} is outside {spec.lo}-{spec.hi} for {spec.name}"
        return item

    values = {value for value in range(low, high + 1) if (value - low) % step == 0}
    if spec.alias:
        values = {spec.alias.get(value, value) for value in values}
    item.values = frozenset(values)
    return item


def parse_field(text: str, offset: int, spec: FieldSpec) -> Field:
    """Parse one whitespace-separated position of the expression."""
    field = Field(spec=spec, raw=text, start=offset, end=offset + len(text))
    for piece, relative in _split_commas(text):
        item = parse_item(piece, offset + relative, spec)
        field.items.append(item)
        if item.error:
            field.errors.append(f"{spec.name} offset {item.start}: {item.error}")
    if not field.items:
        field.errors.append(f"{spec.name} is empty")
    return field


def _split_tokens(text: str) -> list[tuple[str, int]]:
    """Split on whitespace, remembering the offset of each token."""
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


def parse_expression(text: str, offset: int = 0) -> Expression:
    """Parse ``text`` as a cron expression.

    ``offset`` is where the expression starts inside the line it came from, so
    every token span can be reported as an absolute position in the file.
    """
    raw = text
    expression = Expression(raw=raw, offset=offset)
    stripped = text.strip()
    if not stripped:
        expression.errors.append("empty expression")
        expression.kind = "malformed"
        return expression

    if stripped.startswith("@"):
        name = stripped[1:].split()[0].lower() if len(stripped) > 1 else ""
        # The name is recorded even when unknown, so the rule that reports it
        # can quote `@every` instead of "an unknown special".
        expression.special = name
        if name not in SPECIALS:
            expression.errors.append(f"unknown special @{name}")
            expression.kind = "malformed"
            return expression
        expression.expands = SPECIALS[name]
        expression.kind = "special"
        if expression.expands is None:
            return expression  # @reboot has no fields to parse
        # Analyse the expansion as if it had been written out, but keep the
        # user's own text as the span to report.
        inner = parse_expression(expression.expands, offset)
        expression.fields = inner.fields
        expression.errors.extend(inner.errors)
        return expression

    tokens = _split_tokens(stripped)
    if len(tokens) not in {5, 6, 7}:
        expression.errors.append(
            f"{len(tokens)} fields, expected 5 (minute hour dom mon dow), "
            "6 with seconds or 7 with a year"
        )
        expression.kind = "malformed"
        # Keep parsing the leading fields so a report can still show what did
        # parse; the error above is what the finding will quote.
        specs = list(SPECS)
    elif len(tokens) == 5:
        specs = list(SPECS)
        expression.kind = "standard"
    elif len(tokens) == 6:
        specs = [SECOND_SPEC, *SPECS]
        expression.kind = "seconds"
    else:
        specs = [SECOND_SPEC, *SPECS, YEAR_SPEC]
        expression.kind = "year"

    for index, (token, relative) in enumerate(tokens):
        if index >= len(specs):
            break
        expression.fields.append(parse_field(token, offset + relative, specs[index]))

    return expression


def overlaps(left: Expression, right: Expression) -> bool:
    """Conservative check: could two expressions fire in the same minute?

    This compares the field value sets only. Month and hour must intersect, and
    the day must be possible for both: a wildcard on either side of the day-of-
    month / day-of-week pair counts as a match, which mirrors cron's own OR
    rule for those two fields. It is deliberately generous -- the rules that
    use it are reporting a collision risk, not proving a collision.
    """
    if not left.is_valid or not right.is_valid:
        return False
    if left.has_seconds != right.has_seconds:
        return False
    for one, other in (
        (left.minute, right.minute),
        (left.hour, right.hour),
        (left.month, right.month),
    ):
        if one is None or other is None:
            return False
        if not (one.values & other.values):
            return False

    def day_possible(expr: Expression) -> frozenset[int] | None:
        dom, dow = expr.day_of_month, expr.day_of_week
        if dom is None or dow is None:
            return None
        if dom.is_wildcard and dow.is_wildcard:
            return None  # every day
        return dom.values

    left_day, right_day = day_possible(left), day_possible(right)
    if left_day is None or right_day is None:
        return True
    return bool(left_day & right_day)
