from __future__ import annotations

from collections import defaultdict
from copy import deepcopy

import flet as ft

from eventseat.domain import AppError
from eventseat.hall_selection import SeatSelection, apply_properties
from eventseat.ui import (
    BG,
    CATEGORY_COLORS,
    INK,
    LINE,
    MUTED,
    SEAT_CATEGORIES,
    TEAL,
    field,
    hoverable,
    money,
    panel,
    select,
    tag,
    text,
)


class HallEditor:
    """A hall draft and its inspector; only plain values survive navigation."""

    def __init__(self, admin, hall_id=None):
        self.admin = admin
        self.app = admin.app
        self.service = admin.service
        self.page = admin.page
        self.hall_id = hall_id
        self.draft_key = f"hall:{hall_id or 'new'}"
        self.pressed = set()
        self.property_fields = {}

    def clear_modifiers(self, _=None):
        self.pressed.clear()

    def key_down(self, event):
        self.pressed.add(event.key.lower())

    def key_up(self, event):
        self.pressed.discard(event.key.lower())

    def capture(self):
        draft = {
            "fields": {key: control.value for key, control in self.fields.items()},
            "prices": {key: control.value for key, control in self.prices.items()},
            "dimensions": dict(self.dimensions),
            "seats": deepcopy(self.seats),
            "selected": sorted(self.selection.selected),
            "anchor": self.selection.anchor,
            "properties": {key: control.value for key, control in self.property_fields.items()},
        }
        self.app.view_state.drafts[self.draft_key] = draft
        return draft

    def changed(self, _=None):
        self.capture()

    def show(self):
        self.app.capture_view()
        saved = deepcopy(self.app.view_state.drafts.get(self.draft_key, {}))
        hall = self.service.get_hall(self.hall_id) if self.hall_id else {}
        values = saved.get("fields", {})
        defaults = {
            "name": hall.get("name", ""),
            "stage": hall.get("stage", "ЭКРАН"),
            "rows": hall.get("rows", 6),
            "columns": hall.get("columns", 10),
            "first_row": min((s["row"] for s in hall.get("seats", [])), default=1),
            "first_seat": min((s["number"] for s in hall.get("seats", [])), default=1),
        }
        self.fields = {
            key: field(label, values.get(key, defaults[key]), width=width)
            for key, label, width in [
                ("name", "Название зала", 310),
                ("stage", "Сцена или экран", 230),
                ("rows", "Рядов", 105),
                ("columns", "Мест в ряду", 140),
                ("first_row", "Первый ряд", 130),
                ("first_seat", "Первое место", 130),
            ]
        }
        self.prices = self.admin.price_fields(
            hall.get("category_prices", {"эконом": 50000, "стандарт": 80000, "VIP": 140000})
        )
        for category, value in saved.get("prices", {}).items():
            self.prices[category].value = value
        for control in [*self.fields.values(), *self.prices.values()]:
            control.on_change = self.app.safe(self.changed)
            control.on_focus = self.app.safe(self.clear_modifiers)
        self.seats = saved.get("seats", deepcopy(hall.get("seats", [])))
        for seat in self.seats:
            seat.pop("price_override", None)
        self.dimensions = saved.get(
            "dimensions", {"rows": hall.get("rows", 6), "columns": hall.get("columns", 10)}
        )
        self.selection = SeatSelection(
            {index for index in saved.get("selected", []) if 0 <= index < len(self.seats)},
            saved.get("anchor"),
        )
        self.grid = ft.Column(spacing=8)
        self.summary = text("", color=MUTED)
        self.selection_summary = text("", size=13, color=MUTED)
        self.inspector = ft.Container(width=280, visible=bool(self.selection.selected))
        self.stage_label = text(self.fields["stage"].value, 13, MUTED, True)

        def change_stage(_):
            self.stage_label.value = self.fields["stage"].value
            self.capture()
            self.page.update()

        self.fields["stage"].on_change = self.app.safe(change_stage)
        if not self.seats:
            self.generate(update=False)
        self.draw()
        self.draw_inspector(saved.get("properties"))
        editor = ft.Column(
            [
                self.app.button(
                    "К залам",
                    lambda _: self.admin.show("Залы"),
                    icon=ft.Icons.ARROW_BACK,
                    secondary=True,
                ),
                self.app.heading("Конструктор зала", "Настройте кресла и проходы на схеме"),
                panel(
                    ft.Row([self.fields["name"], self.fields["stage"]], wrap=True),
                    ft.Row(
                        [
                            *[
                                self.fields[key]
                                for key in ("rows", "columns", "first_row", "first_seat")
                            ],
                            self.app.button(
                                "Построить сетку", self.confirm_generate, secondary=True
                            ),
                        ],
                        wrap=True,
                    ),
                    text("Нумерация рядов и мест применяется при построении сетки.", 12, MUTED),
                    ft.Row(list(self.prices.values()), wrap=True),
                ),
                ft.Row(
                    [
                        panel(
                            self.summary,
                            text(
                                "Клик — одно место · Shift + клик — прямоугольник между местами · Ctrl + клик — добавить или убрать место",
                                12,
                                MUTED,
                            ),
                            ft.Container(
                                self.stage_label,
                                padding=15,
                                bgcolor=BG,
                                alignment=ft.Alignment.CENTER,
                            ),
                            ft.Row([self.grid], scroll=ft.ScrollMode.ALWAYS),
                            ft.Row(
                                [tag(c, INK, CATEGORY_COLORS[c]) for c in SEAT_CATEGORIES],
                                wrap=True,
                            ),
                            ft.Row(
                                [
                                    self.app.button(
                                        "Выделить всё", self.select_all, secondary=True
                                    ),
                                    self.app.button(
                                        "Снять выделение", self.clear_selection, secondary=True
                                    ),
                                ],
                                wrap=True,
                            ),
                            self.selection_summary,
                            expand=True,
                        ),
                        self.inspector,
                    ],
                    alignment=ft.MainAxisAlignment.START,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                    spacing=16,
                ),
                ft.Row(
                    [
                        self.app.button("Предпросмотр", self.preview, secondary=True),
                        self.app.button("Сохранить зал", self.save),
                    ],
                    wrap=True,
                ),
                text(
                    "Черновик сохраняется при переходе между страницами до закрытия приложения. "
                    "Если зал уже используется сеансами, для новой структуры создайте копию. "
                    "Цены шаблона не меняют существующие сеансы.",
                    12,
                    MUTED,
                ),
            ],
            spacing=20,
        )
        self.listener = ft.KeyboardListener(
            editor,
            autofocus=True,
            on_key_down=self.app.safe(self.key_down),
            on_key_up=self.app.safe(self.key_up),
        )
        self.app.set_view(
            {"section": "Администрирование", "page": "hall", "hall_id": self.hall_id},
            capture=self.capture,
        )
        self.app.on_view_blur = self.clear_modifiers
        self.capture()
        self.app.show(self.listener)

    def draw(self):
        grouped = defaultdict(list)
        for index, seat in enumerate(self.seats):
            grouped[seat["row"]].append((index, seat))
        self.grid.controls = []
        for row, items in sorted(grouped.items()):
            cells = []
            for index, seat in sorted(items, key=lambda item: item[1]["number"]):
                selected = index in self.selection.selected
                cells.append(
                    hoverable(
                        ft.Container(
                            text(
                                seat["number"] if seat["enabled"] else "·",
                                12,
                                "#FFFFFF" if selected else INK,
                                bold=True,
                            ),
                            width=39,
                            height=36,
                            alignment=ft.Alignment.CENTER,
                            border_radius=8,
                            bgcolor=TEAL
                            if selected
                            else CATEGORY_COLORS[seat["category"]]
                            if seat["enabled"]
                            else BG,
                            border=ft.Border.all(2, TEAL if selected else LINE),
                            tooltip=f"Ряд {seat['row']}, место {seat['number']} · "
                            + (seat["category"] if seat["enabled"] else "проход")
                            + (" · выделено" if selected else ""),
                            on_click=self.app.safe(lambda _, item=index: self.click(item)),
                            data={"hall_seat_index": index, "selected": selected},
                        )
                    )
                )
            self.grid.controls.append(
                ft.Row([text(f"Ряд {row}", 12, MUTED, width=60), *cells], spacing=6)
            )
        enabled = sum(bool(seat["enabled"]) for seat in self.seats)
        self.summary.value = f"Кресел: {enabled} · проходов: {len(self.seats) - enabled}"
        self.selection_summary.value = f"Выделено мест: {len(self.selection.selected)}"

    async def click(self, index):
        self.selection.click(
            self.seats,
            index,
            shift=any(key.startswith("shift") for key in self.pressed),
            ctrl=any(key.startswith(("control", "ctrl", "meta")) for key in self.pressed),
        )
        self.draw()
        self.draw_inspector()
        self.capture()
        self.page.update()
        await self.listener.focus()

    def select_all(self, _=None):
        self.selection.selected = set(range(len(self.seats)))
        self.selection.anchor = 0 if self.seats else None
        self.refresh_selection()

    def clear_selection(self, _=None):
        self.selection.clear()
        self.refresh_selection()

    def refresh_selection(self):
        self.draw()
        self.draw_inspector()
        self.capture()
        self.page.update()

    def draw_inspector(self, restored=None):
        self.property_fields = {}
        self.inspector.visible = bool(self.selection.selected)
        if not self.selection.selected:
            self.inspector.content = None
            return
        selected = [self.seats[index] for index in sorted(self.selection.selected)]
        single = len(selected) == 1
        seat = selected[0]
        controls = [
            text("Параметры кресла" if single else "Параметры группы", 18, bold=True),
            text(
                f"Ряд {seat['row']} · место {seat['number']}"
                if single
                else f"Выделено: {len(selected)} мест",
                13,
                MUTED,
            ),
        ]
        if single:
            self.property_fields["number"] = field("Номер кресла", seat["number"])
            controls.append(self.property_fields["number"])
        category = select(
            "Категория мест",
            [("unchanged", "Не менять"), *[(value, value) for value in SEAT_CATEGORIES]],
            seat["category"] if single else "unchanged",
        )
        kind = select(
            "Тип места",
            [("unchanged", "Не менять"), ("seat", "Кресло"), ("aisle", "Проход")],
            ("seat" if seat["enabled"] else "aisle") if single else "unchanged",
        )
        self.property_fields.update(category=category, kind=kind)
        if restored:
            for key, value in restored.items():
                if key in self.property_fields:
                    self.property_fields[key].value = value
        for control in self.property_fields.values():
            if isinstance(control, ft.Dropdown):
                control.on_select = self.app.safe(self.changed)
            else:
                control.on_change = self.app.safe(self.changed)
            control.on_focus = self.app.safe(self.clear_modifiers)
        controls += [
            category,
            kind,
            self.app.button("Применить к выделенным", self.apply),
            text("Изменения применяются к черновику. Затем нажмите «Сохранить зал».", 12, MUTED),
        ]
        self.inspector.content = panel(*controls)

    def apply(self, _=None):
        values = {key: control.value for key, control in self.property_fields.items()}
        apply_properties(
            self.seats,
            self.selection.selected,
            category=None if values["category"] == "unchanged" else values["category"],
            enabled=None if values["kind"] == "unchanged" else values["kind"] == "seat",
            number=int(values["number"]) if "number" in values else None,
        )
        self.refresh_selection()
        self.app.notice(f"Параметры применены: {len(self.selection.selected)} мест.")

    def confirm_generate(self, _=None):
        self.clear_modifiers()
        self.app.confirm(
            "Перестроить схему?",
            "Категории, номера кресел и проходы в текущем редакторе будут сброшены.",
            lambda _: self.generate(),
        )

    def generate(self, _=None, *, update=True):
        rows, columns = int(self.fields["rows"].value), int(self.fields["columns"].value)
        first_row, first_seat = (
            int(self.fields["first_row"].value),
            int(self.fields["first_seat"].value),
        )
        if not 1 <= rows <= 50 or not 1 <= columns <= 50:
            raise AppError("Размер сетки: от 1 до 50 рядов и от 1 до 50 мест в ряду.")
        if not 1 <= first_row <= 999 or not 1 <= first_seat <= 999:
            raise AppError("Первый номер ряда и места должен быть от 1 до 999.")
        if first_row + rows - 1 > 999 or first_seat + columns - 1 > 999:
            raise AppError("Номера рядов и мест после построения не должны превышать 999.")
        self.seats = [
            {
                "row": first_row + row,
                "number": first_seat + column,
                "category": "стандарт",
                "enabled": True,
            }
            for row in range(rows)
            for column in range(columns)
        ]
        self.dimensions = {"rows": rows, "columns": columns}
        self.selection.clear()
        if update:
            self.refresh_selection()

    def preview(self, _=None):
        self.clear_modifiers()
        prices = self.admin.read_prices(self.prices)
        grouped = defaultdict(list)
        for seat in self.seats:
            grouped[seat["row"]].append(seat)
        rows = [
            ft.Row(
                [
                    text(f"Ряд {row}", 11, MUTED, width=55),
                    *[
                        ft.Container(
                            text(seat["number"], 11, bold=True) if seat["enabled"] else None,
                            width=36,
                            height=32,
                            border_radius=8,
                            alignment=ft.Alignment.CENTER,
                            bgcolor=CATEGORY_COLORS[seat["category"]] if seat["enabled"] else None,
                            tooltip=money(prices[seat["category"]])
                            if seat["enabled"]
                            else "Проход",
                        )
                        for seat in sorted(items, key=lambda item: item["number"])
                    ],
                ],
                spacing=6,
            )
            for row, items in sorted(grouped.items())
        ]
        self.app.dialog(
            "Предпросмотр · " + (self.fields["name"].value or "Новый зал"),
            [
                ft.Container(
                    text(self.fields["stage"].value, 13, MUTED, True),
                    padding=15,
                    bgcolor=BG,
                    alignment=ft.Alignment.CENTER,
                ),
                ft.Row([ft.Column(rows)], scroll=ft.ScrollMode.ALWAYS),
                ft.Row([tag(c, INK, CATEGORY_COLORS[c]) for c in SEAT_CATEGORIES], wrap=True),
                text(self.summary.value),
            ],
            width=780,
        )

    def save(self, _=None):
        if (
            int(self.fields["rows"].value) != self.dimensions["rows"]
            or int(self.fields["columns"].value) != self.dimensions["columns"]
        ):
            raise AppError(
                "После изменения размеров нажмите «Построить сетку», затем сохраните зал."
            )
        self.service.save_hall(
            self.fields["name"].value,
            self.dimensions["rows"],
            self.dimensions["columns"],
            self.fields["stage"].value,
            self.admin.read_prices(self.prices),
            self.seats,
            self.hall_id,
        )
        self.app.clear_capture()
        self.app.view_state.drafts.pop(self.draft_key, None)
        self.admin.show("Залы")
        self.app.notice("Зал сохранён.")
