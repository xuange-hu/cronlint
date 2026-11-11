"""The expression parser: values, spans and the errors that matter."""

from __future__ import annotations

import pytest

from cronlint.expr import SPECIALS, SPECS, overlaps, parse_expression, parse_field


def values(expression_text: str, index: int = 0) -> set[int]:
    expression = parse_expression(expression_text)
    field = expression.fields[index]
    assert not field.errors, field.errors
    return set(field.values)


def test_five_fields_parse_with_the_right_kinds():
    expression = parse_expression("0 12 1 jan mon")
    assert expression.kind == "standard"
    assert not expression.errors
    assert [field.spec.name for field in expression.fields] == [
        "minute",
        "hour",
        "day-of-month",
        "month",
        "day-of-week",
    ]


def test_wildcard_covers_the_whole_range():
    assert values("* * * * *", 0) == set(range(60))
    assert values("* * * * *", 1) == set(range(24))


def test_step_from_wildcard_starts_at_the_field_floor():
    assert values("*/15 * * * *") == {0, 15, 30, 45}
    assert values("0 */6 * * *", 1) == {0, 6, 12, 18}


def test_step_from_a_value_runs_to_the_top_of_the_field():
    # Vixie and cronie both read "5/15" as "from 5 upward, every 15".
    assert values("5/15 * * * *") == {5, 20, 35, 50}


def test_range_is_inclusive_at_both_ends():
    assert values("10-13 * * * *") == {10, 11, 12, 13}


def test_list_is_a_union_of_its_items():
    assert values("1,15,30 * * * *") == {1, 15, 30}


def test_month_and_weekday_names_resolve():
    assert values("0 0 1 jan-mar *", 3) == {1, 2, 3}
    assert values("0 0 * * mon,fri", 4) == {1, 5}


def test_names_are_case_insensitive():
    assert values("0 0 * * MON", 4) == {1}


def test_seven_means_sunday_and_normalises_to_zero():
    assert values("0 0 * * 7", 4) == {0}
    assert values("0 0 * * 0", 4) == {0}


def test_out_of_range_value_is_reported_with_its_position():
    expression = parse_expression("60 * * * *")
    assert not expression.is_valid
    assert "outside 0-59" in expression.fields[0].errors[0]


def test_backwards_range_is_reported():
    expression = parse_expression("10-5 * * * *")
    assert "runs backwards" in expression.fields[0].errors[0]


def test_zero_step_is_reported():
    expression = parse_expression("*/0 * * * *")
    assert "positive integer" in expression.fields[0].errors[0]


def test_unknown_name_is_reported():
    expression = parse_expression("0 0 * * xyz")
    assert "not a number" in expression.fields[4].errors[0]


def test_wrong_field_count_is_an_error_not_a_guess():
    expression = parse_expression("* * * *")
    assert expression.kind == "malformed"
    assert "4 fields" in expression.errors[0]


def test_six_fields_means_leading_seconds():
    expression = parse_expression("*/5 * * * * *")
    assert expression.kind == "seconds"
    assert expression.has_seconds
    assert expression.second is not None
    assert expression.minute is not None


def test_seven_fields_means_trailing_year():
    # sec min hour dom mon dow year -- seven tokens, the last one the year.
    expression = parse_expression("0 0 0 1 1 * 2030")
    assert expression.kind == "year"
    assert expression.has_year
    assert expression.year is not None


def test_specials_expand_to_a_schedule():
    for name, expansion in SPECIALS.items():
        expression = parse_expression(f"@{name}")
        if expansion is None:
            assert expression.kind == "special"
            assert expression.special == "reboot"
            assert expression.fields == []
        else:
            assert expression.kind == "special"
            assert expression.expands == expansion
            assert expression.fields


def test_unknown_special_keeps_its_name_so_a_rule_can_quote_it():
    expression = parse_expression("@every 5m")
    assert expression.kind == "malformed"
    assert expression.special == "every"
    assert "unknown special" in expression.errors[0]


def test_empty_expression_is_an_error():
    expression = parse_expression("   ")
    assert expression.kind == "malformed"
    assert "empty expression" in expression.errors[0]


def test_token_spans_point_at_the_right_characters():
    text = "*/15 3 * jan mon"
    expression = parse_expression(text, offset=100)
    minute, hour, _, month, weekday = expression.fields
    assert text[minute.start - 100 : minute.end - 100] == "*/15"
    assert text[hour.start - 100 : hour.end - 100] == "3"
    assert text[month.start - 100 : month.end - 100] == "jan"
    assert text[weekday.start - 100 : weekday.end - 100] == "mon"


def test_span_of_a_list_item_covers_just_that_item():
    field = parse_field("1,15,30", 40, SPECS[0])
    first, second, third = field.items
    assert (first.start, first.end) == (40, 41)
    assert (second.start, second.end) == (42, 44)
    assert (third.start, third.end) == (45, 47)


def test_runs_every_minute_detection():
    assert parse_expression("* * * * *").runs_every_minute
    assert parse_expression("0 0 * * *").runs_every_minute is False
    assert parse_expression("*/5 * * * *").runs_every_minute is False


def test_runs_every_day_detection():
    assert parse_expression("0 0 * * *").runs_every_day
    assert parse_expression("0 0 * * mon").runs_every_day is False


def test_overlaps_requires_intersecting_minutes():
    # */5 covers 0,5,...,55 and 10 is one of them, so the two share a minute.
    assert overlaps(parse_expression("*/5 * * * *"), parse_expression("10 3 * * *"))
    # 7 is not a multiple of 5, so */5 and :07 never coincide.
    assert not overlaps(parse_expression("*/5 * * * *"), parse_expression("7 3 * * *"))
    # Minute 0 and minute 30 are disjoint, even though the hours match.
    assert not overlaps(parse_expression("0 * * * *"), parse_expression("30 * * * *"))


def test_overlaps_is_false_when_months_disagree():
    left = parse_expression("0 0 1 1 *")
    right = parse_expression("0 0 1 6 *")
    assert not overlaps(left, right)


def test_overlaps_returns_false_for_an_invalid_expression():
    assert not overlaps(parse_expression("60 * * * *"), parse_expression("* * * * *"))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0 0 1 * *", {1}),
        ("0 0 15 * *", {15}),
        ("0 0 1,15 * *", {1, 15}),
        ("0 0 1-3 * *", {1, 2, 3}),
    ],
)
def test_day_of_month_values(text, expected):
    assert values(text, 2) == expected
