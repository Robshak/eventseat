import asyncio
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import flet as ft
import pytest

from eventseat.date_input import DateInput, calendar_date, format_date_edit, parse_date
from eventseat.domain import AppError


@pytest.mark.parametrize("hours", [3, 14, -5, -12])
def test_datepicker_utc_wire_value_is_converted_to_selected_local_day(hours):
    zone = timezone(timedelta(hours=hours))
    chosen = datetime(2026, 10, 8, tzinfo=zone)
    decoded_flet_value = chosen.astimezone(timezone.utc)
    assert calendar_date(decoded_flet_value, zone) == date(2026, 10, 8)
    assert calendar_date(date(2026, 10, 8), zone) == date(2026, 10, 8)
    assert calendar_date(datetime(2026, 10, 8), zone) == date(2026, 10, 8)


@pytest.mark.parametrize("text", ["08102026", "08.10.2026", "08/10/2026", "2026-10-08"])
def test_paste_common_date_formats(text):
    result = format_date_edit("", text)
    assert result.value == "08.10.2026"
    assert result.base == result.extent == 10


def test_typing_inserts_dots_and_keeps_caret_after_inserted_separator():
    value = ""
    for digit, expected in zip(
        "08102026", ["0", "08.", "08.1", "08.10.", "08.10.2", "08.10.20", "08.10.202", "08.10.2026"]
    ):
        result = format_date_edit(value, value + digit, (len(value) + 1,) * 2, (len(value),) * 2)
        assert result.value == expected
        assert result.base == result.extent == len(expected)
        value = result.value


def test_backspace_at_terminal_separator_does_not_trap_the_caret():
    assert format_date_edit("08.", "08", (2, 2), (3, 3)).value == "0"
    result = format_date_edit("08.10.", "08.10", (5, 5), (6, 6))
    assert (result.value, result.extent) == ("08.1", 4)


def test_manually_typed_dots_do_not_duplicate_automatic_separators():
    value = ""
    for character in "08.10.2026":
        result = format_date_edit(
            value, value + character, (len(value) + 1,) * 2, (len(value),) * 2
        )
        value = result.value
    assert value == "08.10.2026"


def test_backspace_and_delete_at_internal_separator_preserve_other_components():
    backspace = format_date_edit("08.10.2026", "08.102026", (5, 5), (6, 6))
    assert (backspace.value, backspace.extent) == ("08.1.2026", 4)
    delete = format_date_edit("08.10.2026", "0810.2026", (2, 2), (2, 2))
    assert (delete.value, delete.extent) == ("08.0.2026", 3)


def test_middle_edit_and_selection_replacement_do_not_shift_month_or_year():
    removed = format_date_edit("08.10.2026", "0.10.2026", (1, 1), (2, 2))
    assert (removed.value, removed.extent) == ("0.10.2026", 1)
    replaced = format_date_edit("08.10.2026", "9.10.2026", (1, 1), (0, 2))
    assert replaced.value == "9.10.2026"
    filled = format_date_edit("9.10.2026", "19.10.2026", (1, 1), (0, 0))
    assert filled.value == "19.10.2026"
    assert filled.extent == 1
    selection = format_date_edit("", "08102026", (2, 4))
    assert (selection.base, selection.extent) == (3, 6)


def test_partial_components_and_overflow_do_not_silently_replace_a_valid_year():
    result = format_date_edit("08.10.2026", "08..2026", (3, 3), (3, 5))
    assert result.value == "08..2026"
    rejected = format_date_edit("08.10.2026", "018.10.2026", (2, 2), (1, 1))
    assert (rejected.value, rejected.extent) == ("08.10.2026", 1)
    assert format_date_edit("08.10.2026", "", (0, 0), (0, 10)).value == ""


@pytest.mark.parametrize(
    "value", ["29.02.2025", "31.04.2026", "00.10.2026", "08.13.2026", "08.10.20", "08..2026"]
)
def test_invalid_or_incomplete_dates_are_rejected(value):
    with pytest.raises(AppError):
        parse_date(value)


def test_leap_date_optional_empty_and_required_validation():
    assert parse_date("29.02.2024") == date(2024, 2, 29)
    assert parse_date("8.1.2026") == date(2026, 1, 8)
    assert parse_date("") is None
    with pytest.raises(AppError):
        parse_date("", required=True)


class DateApp:
    def __init__(self):
        self.page = SimpleNamespace(
            update=lambda: None,
            show_dialog=lambda dialog: setattr(self, "dialog", dialog),
            pop_dialog=lambda: setattr(self, "dialog", None),
        )

    def safe(self, callback):
        return callback


def walk(control):
    yield control
    content = getattr(control, "content", None)
    if isinstance(content, ft.BaseControl):
        yield from walk(content)
    for child in [*getattr(control, "controls", []), *getattr(control, "actions", [])]:
        yield from walk(child)


def test_calendar_selects_date_only_without_flet_datetime_serialization():
    app = DateApp()
    changes = []
    field = DateInput(
        app, "С · ДД.ММ.ГГГГ", "07.10.2026", on_change=lambda e: changes.append(e.control.value)
    )
    field.open_calendar()
    assert app.dialog.data == "date-picker"
    assert app.dialog.content.width >= 400
    controls = list(walk(app.dialog))
    chosen = next(control for control in controls if control.data == {"date": "2026-10-08"})
    chosen.on_click(None)
    confirm = next(
        control
        for control in controls
        if isinstance(control, ft.Button) and control.content == "Выбрать"
    )
    asyncio.run(confirm.on_click(None))
    assert field.value == "08.10.2026"
    assert field.read() == date(2026, 10, 8)
    assert changes == ["08.10.2026"]


def test_field_uses_latest_flet_selection_event_and_shows_validation_errors():
    app = DateApp()
    field = DateInput(app, "Дата", "08.10.2026", required=True)
    field._selection_changed(SimpleNamespace(selection=ft.TextSelection(6, 6)))
    field._selection_changed(SimpleNamespace(selection=ft.TextSelection(5, 5)))
    field.selection = ft.TextSelection(5, 5)
    field.value = "08.102026"
    asyncio.run(field.on_change(SimpleNamespace(data=field.value, control=field)))
    assert field.value == "08.1.2026"
    assert field.selection.extent_offset == 4
    field.value = "31.02.2026"
    asyncio.run(field.on_blur(SimpleNamespace(control=field)))
    assert field.error_text and "Такой даты нет" in field.error_text
    field.value = "29.02.2024"
    assert field.read() == date(2024, 2, 29)
    assert field.error_text is None


def test_calendar_month_navigation_and_year_dropdown_are_explicit():
    app = DateApp()
    field = DateInput(app, "Дата", "08.12.2026")
    field.open_calendar()
    next_month = next(
        control
        for control in walk(app.dialog)
        if getattr(control, "tooltip", None) == "Следующий месяц"
    )
    next_month.on_click(None)
    dropdowns = {
        control.label: control for control in walk(app.dialog) if isinstance(control, ft.Dropdown)
    }
    assert dropdowns["Месяц"].value == "1"
    assert dropdowns["Год"].value == "2027"
