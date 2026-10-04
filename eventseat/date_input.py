"""Date-only input and a spacious calendar, independent of Flet's UTC DatePicker wire value."""

from __future__ import annotations

import calendar
import inspect
import re
from dataclasses import dataclass
from datetime import date, datetime, tzinfo
from types import SimpleNamespace

import flet as ft

from eventseat.domain import AppError

TEAL = "#087F8C"
INK = "#182638"
MUTED = "#6D7B8C"
MONTHS = (
    "Январь",
    "Февраль",
    "Март",
    "Апрель",
    "Май",
    "Июнь",
    "Июль",
    "Август",
    "Сентябрь",
    "Октябрь",
    "Ноябрь",
    "Декабрь",
)


def calendar_date(value: date | datetime, zone: tzinfo | None = None) -> date:
    """Recover the local calendar day of an aware instant; never shift a plain date.

    Flet 1.0.3's Dart MsgPack encoder converts DateTime to UTC, and Python's
    protocol decoder preserves that UTC offset. Calling date()/strftime() on the
    decoded UTC value therefore loses the selected local day in positive offsets.
    Our calendar sends only the ISO day key on a button, avoiding that conversion.
    """
    if isinstance(value, datetime):
        return value.astimezone(zone).date() if value.tzinfo is not None else value.date()
    return value


def parse_date(value: str, *, required=False) -> date | None:
    value = value.strip()
    if not value:
        if required:
            raise AppError("Укажите дату в формате ДД.ММ.ГГГГ.")
        return None
    match = re.fullmatch(r"([0-9]{1,2})\.([0-9]{1,2})\.([0-9]{4})", value)
    if not match:
        raise AppError("Введите дату полностью: ДД.ММ.ГГГГ.")
    day, month, year = map(int, match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        raise AppError("Такой даты нет. Проверьте день, месяц и год.") from None


@dataclass(frozen=True)
class DateEdit:
    value: str
    base: int
    extent: int


def format_date_edit(
    previous: str,
    entered: str,
    selection: tuple[int, int] | None = None,
    previous_selection: tuple[int, int] | None = None,
) -> DateEdit:
    """Format a text edit while retaining partial components and the caret/range.

    A deleted digit inside a component does not pull digits from the next one.
    Backspace/Delete at an automatic dot removes the adjacent digit, so the user
    cannot get stuck repeatedly deleting a separator that is immediately restored.
    """
    base, extent = selection or (len(entered), len(entered))
    base, extent = max(0, base), max(0, extent)
    old_base, old_extent = previous_selection or (base, extent)
    prefix = 0
    while prefix < min(len(previous), len(entered)) and previous[prefix] == entered[prefix]:
        prefix += 1
    suffix = 0
    while (
        suffix < min(len(previous), len(entered)) - prefix
        and previous[-suffix - 1] == entered[-suffix - 1]
    ):
        suffix += 1
    removed = previous[prefix : len(previous) - suffix if suffix else len(previous)]
    inserted = entered[prefix : len(entered) - suffix if suffix else len(entered)]
    if inserted in {".", "/", "-"} and not removed:
        # Typing the punctuation manually after an automatic dot must not create
        # an empty component. Empty components caused by deleting digits remain.
        adjacent = previous[max(0, prefix - 1) : prefix + 1]
        if "." in adjacent:
            caret = min(
                len(previous), prefix + (prefix < len(previous) and previous[prefix] == ".")
            )
            return DateEdit(previous, caret, caret)
    if removed == "." and not inserted and old_base == old_extent:
        if old_extent == prefix + 1 and prefix > 0:
            entered = previous[: prefix - 1] + (
                previous[prefix:] if prefix < len(previous) - 1 else ""
            )
            base = extent = prefix - 1
        elif old_extent == prefix and prefix + 1 < len(previous):
            entered = previous[: prefix + 1] + previous[prefix + 2 :]
            base = extent = prefix + 1
    iso = re.fullmatch(r"\s*([0-9]{4})-([0-9]{2})-([0-9]{2})\s*", entered)
    if iso:
        year, month, day = iso.groups()
        result = f"{day}.{month}.{year}"
        return DateEdit(result, len(result), len(result))
    filtered = []
    offsets = [0]
    for character in entered:
        if character in "0123456789":
            filtered.append(character)
        elif character in ".-/":
            filtered.append(".")
        offsets.append(len(filtered))
    raw = "".join(filtered)
    base = offsets[min(base, len(entered))]
    extent = offsets[min(extent, len(entered))]
    if "." not in raw:
        digits = raw[:8]
        result = digits[:2]
        if len(digits) > 2:
            result += "." + digits[2:4]
        if len(digits) > 4:
            result += "." + digits[4:]
        # Insert the next separator as the user finishes a day/month.
        if inserted and len(digits) in (2, 4):
            result += "."

        def position(offset):
            offset = min(offset, len(digits))
            return min(
                len(result),
                offset + (offset >= 2 and len(result) > 2) + (offset >= 4 and len(result) > 5),
            )

        return DateEdit(result, position(base), position(extent))
    parts = raw.split(".")
    if len(parts) > 3 or any(len(part) > limit for part, limit in zip(parts, (2, 2, 4))):
        return DateEdit(previous, max(0, old_base), max(0, old_extent))
    result = raw
    if inserted and len(parts) == 2 and len(parts[-1]) == 2 and extent == len(raw):
        result += "."
        base += base == len(raw)
        extent += 1
    return DateEdit(result, min(base, len(result)), min(extent, len(result)))


class DateInput(ft.TextField):
    """TextField-compatible date input. Use on_value_change instead of replacing on_change."""

    def __init__(
        self,
        app,
        label,
        value="",
        *,
        required=False,
        width=230,
        on_change=None,
        first_date=date(2000, 1, 1),
        last_date=date(2100, 12, 31),
        tooltip=None,
        **kwargs,
    ):
        if isinstance(value, date):
            value = calendar_date(value).strftime("%d.%m.%Y")
        super().__init__(
            label=label,
            value=str(value),
            width=width,
            filled=True,
            fill_color="#FFFFFF",
            text_size=14,
            hover_color="#F0F8F7",
            focused_border_color=TEAL,
            focused_border_width=1,
            border_width=1,
            error_max_lines=3,
            hint_text="ДД.ММ.ГГГГ",
            keyboard_type=ft.KeyboardType.DATETIME,
            input_filter=ft.InputFilter(regex_string=r"[0-9./\-\s]", allow=True),
            **kwargs,
        )
        self._app = app
        self._required = required
        self._first_date = calendar_date(first_date)
        self._last_date = calendar_date(last_date)
        self._previous_value = self.value
        self._last_selection = (len(self.value), len(self.value))
        self._previous_selection = self._last_selection
        self.on_value_change = on_change
        self.on_selection_change = app.safe(self._selection_changed)
        self.on_change = app.safe(self._changed)
        self.on_blur = app.safe(self._validate_event)
        self.on_submit = app.safe(self._validate_event)
        self.suffix_icon = ft.IconButton(
            icon=ft.Icons.CALENDAR_MONTH_OUTLINED,
            tooltip=tooltip or "Выбрать дату: " + label,
            on_click=app.safe(self.open_calendar),
        )

    def _selection_changed(self, event):
        selected = event.selection
        self._previous_selection = self._last_selection
        self._last_selection = (selected.base_offset, selected.extent_offset)

    async def _notify_value_change(self, event):
        if self.on_value_change:
            result = self.on_value_change(event)
            if inspect.isawaitable(result):
                await result

    async def _changed(self, event):
        raw = event.data if isinstance(event.data, str) else self.value
        selected = self.selection
        cursor = (selected.base_offset, selected.extent_offset) if selected else None
        edited = format_date_edit(self._previous_value, raw, cursor, self._previous_selection)
        self.value = self._previous_value = edited.value
        self.selection = ft.TextSelection(edited.base, edited.extent)
        self._last_selection = (edited.base, edited.extent)
        self.error_text = None
        self._app.page.update()
        await self._notify_value_change(event)

    def read(self) -> date | None:
        try:
            result = parse_date(self.value, required=self._required)
            if result and not self._first_date <= result <= self._last_date:
                raise AppError(
                    f"Дата должна быть с {self._first_date:%d.%m.%Y} по {self._last_date:%d.%m.%Y}."
                )
        except AppError as error:
            self.error_text = str(error)
            self._app.page.update()
            raise
        self.error_text = None
        return result

    async def _validate_event(self, event):
        try:
            selected = self.read()
        except AppError:
            return
        normalized = selected.strftime("%d.%m.%Y") if selected else ""
        changed = normalized != self.value
        self.value = self._previous_value = normalized
        self._app.page.update()
        if changed:
            await self._notify_value_change(event)

    def open_calendar(self, _=None):
        try:
            selected = parse_date(self.value)
        except AppError:
            selected = None
        today = date.today()
        selected = min(self._last_date, max(self._first_date, selected or today))
        current = {"selected": selected, "year": selected.year, "month": selected.month}
        header = ft.Text(
            f"Выбрано: {selected:%d.%m.%Y}", size=21, color=INK, weight=ft.FontWeight.W_600
        )
        grid = ft.Column(spacing=6, tight=True)
        month = ft.Dropdown(
            value=str(selected.month),
            width=180,
            text_size=15,
            options=[ft.DropdownOption(str(index), name) for index, name in enumerate(MONTHS, 1)],
            label="Месяц",
            filled=True,
            fill_color="#FFFFFF",
        )
        year = ft.Dropdown(
            value=str(selected.year),
            width=115,
            text_size=15,
            options=[
                ft.DropdownOption(str(number), str(number))
                for number in range(self._first_date.year, self._last_date.year + 1)
            ],
            label="Год",
            filled=True,
            fill_color="#FFFFFF",
            menu_height=300,
        )

        def choose(day):
            current["selected"] = day
            draw()
            self._app.page.update()

        def draw():
            month.value, year.value = str(current["month"]), str(current["year"])
            header.value = f"Выбрано: {current['selected']:%d.%m.%Y}"
            grid.controls = []
            weeks = calendar.Calendar(firstweekday=0).monthdayscalendar(
                current["year"], current["month"]
            )
            for week in weeks:
                controls = []
                for number in week:
                    if not number:
                        controls.append(ft.Container(width=44, height=42))
                        continue
                    day = date(current["year"], current["month"], number)
                    chosen = day == current["selected"]
                    controls.append(
                        ft.TextButton(
                            str(number),
                            width=44,
                            height=42,
                            data={"date": day.isoformat()},
                            disabled=not self._first_date <= day <= self._last_date,
                            tooltip=f"{day:%d.%m.%Y}" + (" · сегодня" if day == today else ""),
                            style=ft.ButtonStyle(
                                color="#FFFFFF" if chosen else INK,
                                bgcolor=TEAL
                                if chosen
                                else "#E8F3F3"
                                if day == today
                                else "#FFFFFF",
                                padding=0,
                                shape=ft.RoundedRectangleBorder(radius=10),
                                side=ft.BorderSide(1, TEAL if day == today else "#DDE4EB"),
                            ),
                            on_click=self._app.safe(lambda _, day=day: choose(day)),
                        )
                    )
                grid.controls.append(ft.Row(controls, spacing=8, tight=True))
            previous.disabled = (current["year"], current["month"]) <= (
                self._first_date.year,
                self._first_date.month,
            )
            following.disabled = (current["year"], current["month"]) >= (
                self._last_date.year,
                self._last_date.month,
            )

        def shift_month(step):
            index = current["year"] * 12 + current["month"] - 1 + step
            current["year"], remainder = divmod(index, 12)
            current["month"] = remainder + 1
            draw()
            self._app.page.update()

        def switch(_):
            current.update(year=int(year.value), month=int(month.value))
            draw()
            self._app.page.update()

        async def confirm(_):
            self.value = self._previous_value = current["selected"].strftime("%d.%m.%Y")
            self.selection = ft.TextSelection(len(self.value), len(self.value))
            self.error_text = None
            self._app.page.pop_dialog()
            self._app.page.update()
            await self._notify_value_change(SimpleNamespace(control=self, data=self.value))

        month.on_select = self._app.safe(switch)
        year.on_select = self._app.safe(switch)
        previous = ft.IconButton(
            ft.Icons.CHEVRON_LEFT,
            tooltip="Предыдущий месяц",
            on_click=self._app.safe(lambda _: shift_month(-1)),
        )
        following = ft.IconButton(
            ft.Icons.CHEVRON_RIGHT,
            tooltip="Следующий месяц",
            on_click=self._app.safe(lambda _: shift_month(1)),
        )
        draw()
        dialog = ft.AlertDialog(
            data="date-picker",
            modal=True,
            scrollable=True,
            title=ft.Text("Выберите дату", size=23, color=INK, weight=ft.FontWeight.W_600),
            bgcolor="#FFFFFF",
            content_padding=24,
            inset_padding=24,
            content=ft.Column(
                [
                    ft.Text(self.label, size=13, color=MUTED),
                    header,
                    ft.Row([previous, month, year, following], spacing=4, tight=True),
                    ft.Row(
                        [
                            ft.Container(
                                ft.Text(day, color=MUTED, size=13),
                                width=44,
                                alignment=ft.Alignment.CENTER,
                            )
                            for day in ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
                        ],
                        spacing=8,
                    ),
                    grid,
                ],
                width=400,
                spacing=16,
                tight=True,
            ),
            actions=[
                ft.TextButton(
                    "Отмена", on_click=self._app.safe(lambda _: self._app.page.pop_dialog())
                ),
                ft.Button("Выбрать", on_click=self._app.safe(confirm)),
            ],
            actions_padding=24,
        )
        self._app.page.show_dialog(dialog)
