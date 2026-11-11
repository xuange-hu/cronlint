"""Parsing a crontab file: the three shapes, and the offsets they must keep."""

from __future__ import annotations

from helpers import app_from


def test_tasks_comments_and_environment_are_all_kept():
    app = app_from(
        "# a comment\n"
        "SHELL=/bin/sh\n"
        "\n"
        "0 12 * * mon /opt/jobs/weekly\n",
        source="crontab",
    )
    assert len(app.tasks) == 1
    assert len(app.comments) == 1
    assert len(app.env) == 1
    assert app.env[0].name == "SHELL"
    assert app.line_count == 4


def test_user_crontab_has_no_user_column():
    app = app_from("0 12 * * mon /opt/jobs/weekly\n", source="crontab")
    task = app.tasks[0]
    assert task.user is None
    assert task.command == "/opt/jobs/weekly"
    assert task.expression.raw == "0 12 * * mon"


def test_cron_d_reads_the_user_column():
    app = app_from("0 2 * * * appuser /opt/app/migrate.sh\n", source="/etc/cron.d/app")
    assert app.assume_user
    task = app.tasks[0]
    assert task.user == "appuser"
    assert task.command == "/opt/app/migrate.sh"


def test_etc_crontab_also_takes_the_user_column():
    app = app_from("0 2 * * * root /opt/app/migrate.sh\n", source="/etc/crontab")
    assert app.assume_user
    assert app.tasks[0].user == "root"


def test_special_line_in_cron_d_has_a_user_too():
    app = app_from("@reboot root /opt/app/boot.sh\n", source="/etc/cron.d/app")
    task = app.tasks[0]
    assert task.expression.special == "reboot"
    assert task.user == "root"
    assert task.command == "/opt/app/boot.sh"


def test_special_line_in_a_user_crontab_has_no_user():
    app = app_from("@reboot /opt/app/boot.sh\n", source="crontab")
    task = app.tasks[0]
    assert task.user is None
    assert task.command == "/opt/app/boot.sh"


def test_a_star_in_the_sixth_position_is_not_a_user_name():
    # `* * * * * * cmd` is a six-field schedule, not a cron.d line whose user
    # happens to be "*". Reading it as a user shifts every column after it.
    app = app_from("* * * * * * /opt/jobs/seconds\n", source="/etc/cron.d/app")
    task = app.tasks[0]
    assert task.expression.kind == "seconds"
    assert task.user is None
    assert task.command == "/opt/jobs/seconds"


def test_six_field_reading_is_recorded_as_a_note():
    app = app_from("* * * * * * /opt/jobs/seconds\n", source="crontab")
    assert any("6-field" in note for note in app.notes)


def test_standard_reading_can_be_forced_over_the_six_field_guess():
    app = app_from(
        "* * * * * * /opt/jobs/seconds\n",
        source="crontab",
        assume_seconds=True,
    )
    assert app.assume_seconds
    assert app.tasks[0].expression.kind == "seconds-forced"


def test_seven_field_schedule_is_detected_and_noted():
    app = app_from("0 0 0 1 1 * 2030 /opt/yearly.sh\n", source="crontab")
    assert app.tasks[0].expression.kind == "year"
    assert any("7-field" in note for note in app.notes)


def test_a_schedule_with_no_command_is_kept_so_a_rule_can_report_it():
    app = app_from("0 12 * * mon\n", source="crontab")
    assert len(app.tasks) == 1
    assert app.tasks[0].command == ""
    assert any("no command" in error for error in app.errors)


def test_a_line_that_is_not_a_schedule_is_an_error():
    app = app_from("not-a-schedule foo\n", source="crontab")
    assert app.tasks == []
    assert any("does not start with a schedule" in error for error in app.errors)


def test_too_few_fields_is_an_error():
    app = app_from("0 12 * * /opt/x\n", source="crontab")
    assert app.tasks == []
    assert any("needs 5" in error for error in app.errors)


def test_a_comment_line_never_becomes_a_task():
    app = app_from("# 0 12 * * * /opt/commented.sh\n", source="crontab")
    assert app.tasks == []
    assert app.comments[0].text.startswith("0 12")


def test_the_expression_offset_slices_the_line_back_to_the_expression():
    line = "  0 12 * * mon /opt/jobs/weekly-report\n"
    app = app_from(line, source="crontab")
    task = app.tasks[0]
    assert task.raw.strip().startswith("0 12")
    relative = task.expr_start - task.offset
    assert line[relative : task.expr_end - task.offset] == "0 12 * * mon"


def test_the_command_offset_slices_the_line_back_to_the_command():
    line = "30 4 * * 1-5 /opt/reports/weekly --full\n"
    app = app_from(line, source="crontab")
    task = app.tasks[0]
    relative = task.command_start - task.offset
    assert line[relative : task.command_end - task.offset] == "/opt/reports/weekly --full"


def test_offsets_survive_a_multibyte_comment_above_the_task():
    # A UTF-8 comment is longer in bytes than in characters; the task below it
    # must still slice correctly from its byte offset.
    text = "# 中文注释，用作字节偏移的检验\n0 12 * * mon /opt/jobs/weekly\n"
    app = app_from(text, source="crontab")
    task = app.tasks[0]
    line = text.splitlines()[1]
    relative = task.expr_start - task.offset
    assert line[relative : task.expr_end - task.offset] == "0 12 * * mon"
    assert line[relative + task.expr_end - task.expr_start + 1 :].startswith("/opt/jobs")


def test_script_path_is_the_first_absolute_path():
    app = app_from("0 12 * * mon /opt/jobs/weekly --with /var/lib/x\n", source="crontab")
    assert app.tasks[0].script_path == "/opt/jobs/weekly"


def test_script_path_is_none_for_a_bare_name():
    app = app_from("0 12 * * mon backup --full\n", source="crontab")
    assert app.tasks[0].script_path is None


def test_parse_file_reads_a_written_file(tmp_path):
    path = tmp_path / "crontab"
    path.write_text("0 12 * * mon /opt/jobs/weekly\n", encoding="utf-8", newline="\n")
    from cronlint.parser import parse_file

    app = parse_file(path)
    assert len(app.tasks) == 1
    assert app.source.endswith("crontab")
    assert app.size > 0


def test_parse_file_decodes_a_damaged_byte_without_losing_the_schedule(tmp_path):
    path = tmp_path / "crontab"
    path.write_bytes(b"# \xff\xfe broken comment\n0 12 * * mon /opt/jobs/weekly\n")
    from cronlint.parser import parse_file

    app = parse_file(path)
    assert len(app.tasks) == 1
    assert app.tasks[0].command == "/opt/jobs/weekly"


def test_assume_user_can_be_forced_from_the_path_logic():
    from cronlint.parser import parse

    app = parse("0 2 * * * root /opt/x\n", source="anything.txt", assume_user=True)
    assert app.assume_user
    assert app.tasks[0].user == "root"


def test_environment_line_with_spaces_is_kept_verbatim():
    app = app_from("OPTS=a b c\n0 12 * * mon /opt/x\n", source="crontab")
    assert app.env[0].value == "a b c"


def test_crontab_suffix_detection():
    from cronlint.parser import looks_like_crontab

    assert looks_like_crontab("/etc/cron.d/app")
    assert looks_like_crontab("myjobs.cron")
    assert looks_like_crontab("crontab")
    assert not looks_like_crontab("notes.txt")
