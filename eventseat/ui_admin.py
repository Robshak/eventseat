from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

import flet as ft

from eventseat.config import import_cover
from eventseat.domain import AppError
from eventseat.ui import (
    BG,
    CATEGORIES,
    CATEGORY_COLORS,
    INK,
    LINE,
    MUTED,
    RED,
    SEAT_CATEGORIES,
    TEAL,
    date_text,
    field,
    money,
    panel,
    rubles,
    select,
    tag,
    text,
)


class AdminUI:
    def __init__(self, app):
        self.app = app
        self.service = app.service
        self.page = app.page

    def show(self, tab="Мероприятия"):
        if self.service.current_user["role"] != "admin":
            raise AppError("Это действие доступно только администратору.")
        if tab == "Бронирования":
            self.app.bookings(True)
            self.app.content.controls.insert(0, self.tabs(tab))
            self.page.update()
            return
        render = {
            "Мероприятия": self.events,
            "Сеансы": self.sessions,
            "Залы": self.halls,
            "Статистика": self.statistics,
        }[tab]
        self.app.show(
            self.app.heading(
                "Администрирование", "Управляйте событиями, пространствами и бронированиями"
            ),
            self.tabs(tab),
            *render(),
        )

    def tabs(self, selected):
        return ft.Row(
            [
                self.app.button(
                    label, lambda _, name=label: self.show(name), secondary=label != selected
                )
                for label in ["Мероприятия", "Сеансы", "Залы", "Бронирования", "Статистика"]
            ],
            wrap=True,
            spacing=10,
        )

    def events(self):
        events = self.service.list_events(admin=True)
        controls = [
            self.app.button("Создать мероприятие", lambda _: self.event_form(), icon=ft.Icons.ADD)
        ]
        for event in events:
            controls.append(
                panel(
                    ft.Row(
                        [
                            self.app.cover(event.get("cover_path", ""), 130, 90),
                            ft.Column(
                                [
                                    text(event["title"], 20, bold=True),
                                    text(
                                        f"{event['category']} · {event['duration']} мин",
                                        color=MUTED,
                                    ),
                                    tag("Опубликовано" if event["published"] else "Черновик"),
                                ],
                                expand=True,
                            ),
                            self.app.button(
                                "Сеанс",
                                lambda _, eid=event["id"]: self.session_form(event_id=eid),
                                secondary=True,
                            ),
                            self.app.button(
                                "Изменить",
                                lambda _, eid=event["id"]: self.event_form(eid),
                                secondary=True,
                            ),
                        ]
                    )
                )
            )
        if not events:
            controls.append(
                self.app.empty(
                    "Мероприятий пока нет", "Создайте событие, добавьте сеанс и опубликуйте его."
                )
            )
        return controls

    def event_form(self, event_id=None):
        event = self.service.get_event(event_id) if event_id else {}
        title = field("Название", event.get("title", ""), max_length=160)
        description = field(
            "Описание", event.get("description", ""), multiline=True, min_lines=4, max_lines=7
        )
        category = select("Категория", CATEGORIES, event.get("category", "кино"), width=245)
        duration = field("Продолжительность, мин", event.get("duration", 90), width=245)
        cover = {"path": event.get("cover_path", "")}
        cover_text = text("Обложка выбрана" if cover["path"] else "Обложка не выбрана", 13, MUTED)
        preview = ft.Container(self.app.cover(cover["path"], 240, 140))

        async def upload(_):
            files = await self.app.picker.pick_files(
                dialog_title="Выберите обложку",
                allow_multiple=False,
                file_type=ft.FilePickerFileType.CUSTOM,
                allowed_extensions=["png", "jpg", "jpeg", "webp"],
            )
            if files and files[0].path:
                cover["path"] = import_cover(files[0].path)
                cover_text.value = files[0].name
                preview.content = self.app.cover(cover["path"], 240, 140)
                self.page.update()

        def save(published):
            self.service.save_event(
                title.value,
                description.value,
                category.value,
                int(duration.value),
                published,
                cover["path"],
                event_id,
            )
            self.page.pop_dialog()
            self.show()
            self.app.notice("Мероприятие опубликовано." if published else "Черновик сохранён.")

        self.app.dialog(
            "Редактировать мероприятие" if event_id else "Новое мероприятие",
            [
                title,
                ft.Row([category, duration]),
                description,
                ft.Row(
                    [
                        preview,
                        ft.Column(
                            [
                                cover_text,
                                self.app.button("Загрузить обложку", upload, secondary=True),
                                text("PNG, JPEG или WebP · до 15 МБ", 12, MUTED),
                            ]
                        ),
                    ]
                ),
                text(
                    "В афише видны только опубликованные мероприятия с будущими доступными сеансами.",
                    12,
                    MUTED,
                ),
            ],
            [
                self.app.button("Назад", lambda _: self.page.pop_dialog(), secondary=True),
                self.app.button("Сохранить черновик", lambda _: save(False), secondary=True),
                self.app.button("Опубликовать", lambda _: save(True)),
            ],
            width=610,
        )

    def sessions(self):
        controls = [
            self.app.button("Создать сеанс", lambda _: self.session_form(), icon=ft.Icons.ADD)
        ]
        sessions = self.service.list_sessions(admin=True)
        for session in sessions:
            is_cancelled = session["status"] == "cancelled"
            is_future = session["start"] > datetime.now()
            state = "Отменён" if is_cancelled else "Открыт" if is_future else "Завершён"
            actions = [
                self.app.button(
                    "Заполненность и места",
                    lambda _, sid=session["id"]: self.app.seats(sid, True),
                    secondary=True,
                )
            ]
            if not is_cancelled and is_future:
                actions += [
                    self.app.button(
                        "Изменить",
                        lambda _, sid=session["id"]: self.session_form(session_id=sid),
                        secondary=True,
                    ),
                    self.app.button(
                        "Цены мест",
                        lambda _, sid=session["id"]: self.session_prices(sid),
                        secondary=True,
                    ),
                    self.app.button(
                        "Отменить сеанс",
                        lambda _, s=session: self.cancel_session(s),
                        secondary=True,
                    ),
                ]
            controls.append(
                panel(
                    ft.Row(
                        [
                            text(session["title"], 21, bold=True, expand=True),
                            tag(
                                state,
                                RED if is_cancelled else TEAL,
                                "#F8E9E9" if is_cancelled else "#E7F3F2",
                            ),
                        ]
                    ),
                    text(
                        f"{date_text(session['start'])} · {session['hall_name']} · {session['duration']} мин",
                        color=MUTED,
                    ),
                    text(
                        f"Свободно {session['free_count']} из {session['total_count']} мест",
                        15,
                        bold=True,
                    ),
                    *(
                        [text("Причина: " + session["cancel_reason"], color=RED)]
                        if session.get("cancel_reason")
                        else []
                    ),
                    ft.Row(actions, wrap=True),
                )
            )
        if not sessions:
            controls.append(
                self.app.empty(
                    "Расписание ещё не создано",
                    "Добавьте мероприятие и зал, затем назначьте дату и цены.",
                )
            )
        return controls

    def cancel_session(self, session):
        def cancel(reason):
            self.service.cancel_session(session["id"], reason)
            self.show("Сеансы")

        self.app.confirm(
            "Отменить сеанс?",
            f"{session['title']} · {date_text(session['start'])}. "
            "Все активные бронирования будут отменены, история сохранится.",
            cancel,
            reason=True,
        )

    def price_fields(self, prices):
        return {
            category: field(
                f"{category.capitalize()}, ₽", f"{prices.get(category, 0) / 100:.2f}", width=165
            )
            for category in SEAT_CATEGORIES
        }

    @staticmethod
    def read_prices(fields):
        return {key: rubles(control.value) for key, control in fields.items()}

    def session_form(self, session_id=None, event_id=None):
        events = self.service.list_events(admin=True)
        halls = self.service.list_halls()
        if not events or not halls:
            raise AppError("Для создания сеанса сначала создайте мероприятие и зал.")
        session = self.service.get_session(session_id) if session_id else {}
        current_event = session.get("event_id", event_id or events[0]["id"])
        current_hall = session.get("hall_id", halls[0]["id"])
        hall = self.service.get_hall(current_hall)
        event_select = select(
            "Мероприятие", [(e["id"], e["title"]) for e in events], str(current_event)
        )
        hall_select = select("Зал", [(h["id"], h["name"]) for h in halls], str(current_hall))
        start = session.get(
            "start", (datetime.now() + timedelta(days=1)).replace(hour=19, minute=0)
        )
        date = field("Дата · ДД.ММ.ГГГГ", start.strftime("%d.%m.%Y"), width=245)
        time = field("Время · ЧЧ:ММ", start.strftime("%H:%M"), width=245)
        prices = self.price_fields(session.get("category_prices", hall["category_prices"]))
        change_prices = ft.Checkbox(
            label="Изменить цены категорий в этом сеансе", value=not bool(session_id)
        )
        category_row = ft.Row(list(prices.values()), wrap=True, visible=not bool(session_id))

        def change_hall(_):
            selected_hall = self.service.get_hall(int(hall_select.value))
            for category, control in prices.items():
                control.value = f"{selected_hall['category_prices'][category] / 100:.2f}"
            self.page.update()

        def toggle_prices(_):
            category_row.visible = bool(change_prices.value)
            self.page.update()

        hall_select.on_select = self.app.safe(change_hall)
        change_prices.on_change = self.app.safe(toggle_prices)

        def save(_):
            starts = datetime.strptime(
                date.value.strip() + " " + time.value.strip(), "%d.%m.%Y %H:%M"
            )
            sid = self.service.save_session(
                int(event_select.value),
                int(hall_select.value),
                starts,
                self.read_prices(prices) if change_prices.value else None,
                session_id=session_id,
            )
            self.page.pop_dialog()
            self.show("Сеансы")
            self.app.notice(
                "Сеанс сохранён. Индивидуальные цены можно настроить кнопкой «Цены мест»."
            )
            return sid

        self.app.dialog(
            "Редактировать сеанс" if session_id else "Новый сеанс",
            [
                event_select,
                hall_select,
                ft.Row([date, time]),
                change_prices,
                category_row,
                text(
                    "При создании копируются цены и индивидуальные переопределения зала. Изменения шаблона зала не меняют цены сеанса.",
                    12,
                    MUTED,
                ),
                text(
                    "Сеансы в одном зале не могут пересекаться. При активных бронях менять время и зал нельзя.",
                    12,
                    MUTED,
                ),
            ],
            [
                self.app.button("Назад", lambda _: self.page.pop_dialog(), secondary=True),
                self.app.button("Сохранить сеанс", save),
            ],
            width=550,
        )

    def session_prices(self, session_id):
        session = self.service.get_session(session_id)
        seats = self.service.seat_map(session_id)
        fields = self.price_fields(session["category_prices"])
        override = {}
        summary = text("Индивидуальные изменения: 0", color=MUTED)
        grid = ft.Column(spacing=8)
        rows = defaultdict(list)
        for seat in seats:
            rows[seat["row"]].append(seat)

        def edit(seat):
            current = override.get(seat["id"], seat.get("price_override"))
            value = field(
                "Цена места, ₽",
                "" if current is None else f"{current / 100:.2f}",
                helper="Оставьте пустым, чтобы применить цену категории",
            )

            def apply(_):
                override[seat["id"]] = rubles(value.value) if value.value.strip() else None
                summary.value = f"Индивидуальные изменения: {len(override)}"
                self.page.pop_dialog()
                self.page.update()

            self.app.dialog(
                f"Ряд {seat['row']} · место {seat['number']}",
                [
                    value,
                    text(
                        "Новая цена действует для будущих бронирований. Стоимость уже подтверждённых билетов сохраняется.",
                        12,
                        MUTED,
                    ),
                ],
                [
                    self.app.button("Назад", lambda _: self.page.pop_dialog(), secondary=True),
                    self.app.button("Применить", apply),
                ],
                width=380,
            )

        for row, items in sorted(rows.items()):
            grid.controls.append(
                ft.Row(
                    [
                        text(f"Ряд {row}", 12, MUTED, width=50),
                        *[
                            ft.Container(
                                text(f"{s['number']}\n{money(s['price'])}", 10, bold=True),
                                width=68,
                                height=48,
                                alignment=ft.Alignment.CENTER,
                                bgcolor=CATEGORY_COLORS[s["category"]],
                                border_radius=8,
                                on_click=self.app.safe(lambda _, seat=s: edit(seat)),
                            )
                            for s in items
                        ],
                    ],
                    spacing=6,
                )
            )
        apply_categories = ft.Checkbox(
            label="Изменить цены категорий (индивидуальные цены сохранятся)", value=False
        )

        def save(_):
            if not apply_categories.value and not override:
                raise AppError(
                    "Выберите место для изменения цены или включите пересчёт по категориям."
                )
            category_prices = (
                self.read_prices(fields) if apply_categories.value else session["category_prices"]
            )
            self.service.set_session_prices(session_id, category_prices, override)
            self.page.pop_dialog()
            self.show("Сеансы")
            self.app.notice("Цены обновлены. Подтверждённые билеты сохраняют прежнюю стоимость.")

        self.app.dialog(
            "Цены сеанса · " + session["title"],
            [
                text("Нажмите место, чтобы задать индивидуальную цену.", color=MUTED),
                ft.Row([grid], scroll=ft.ScrollMode.ALWAYS),
                summary,
                apply_categories,
                ft.Row(list(fields.values()), wrap=True),
                text(
                    "Индивидуальная цена всегда имеет приоритет. Чтобы убрать её, очистите поле цены конкретного места.",
                    12,
                    MUTED,
                ),
            ],
            [
                self.app.button("Назад", lambda _: self.page.pop_dialog(), secondary=True),
                self.app.button("Сохранить цены", save),
            ],
            width=760,
        )

    def halls(self):
        halls = self.service.list_halls()
        controls = [self.app.button("Создать зал", lambda _: self.hall_form(), icon=ft.Icons.ADD)]
        for hall in halls:
            controls.append(
                panel(
                    ft.Row(
                        [
                            ft.Icon(ft.Icons.EVENT_SEAT_OUTLINED, color=TEAL, size=32),
                            ft.Column(
                                [
                                    text(hall["name"], 21, bold=True),
                                    text(
                                        f"{hall['rows']} рядов · сетка {hall['rows']} × {hall['columns']}",
                                        color=MUTED,
                                    ),
                                ],
                                expand=True,
                            ),
                            self.app.button(
                                "Скопировать", lambda _, h=hall: self.copy_hall(h), secondary=True
                            ),
                            self.app.button(
                                "Редактировать схему",
                                lambda _, hid=hall["id"]: self.hall_form(hid),
                                secondary=True,
                            ),
                        ]
                    )
                )
            )
        if not halls:
            controls.append(
                self.app.empty(
                    "Залов пока нет",
                    "Создайте сетку кресел, добавьте проходы и назначьте категории.",
                )
            )
        return controls

    def copy_hall(self, hall):
        name = field("Название копии", hall["name"] + " — копия")

        def save(_):
            hall_id = self.service.copy_hall(hall["id"], name.value)
            self.page.pop_dialog()
            self.hall_form(hall_id)

        self.app.dialog(
            "Скопировать зал",
            [
                name,
                text(
                    "Копия получает новую схему, которую можно редактировать независимо от существующих билетов.",
                    color=MUTED,
                ),
            ],
            [
                self.app.button("Назад", lambda _: self.page.pop_dialog(), secondary=True),
                self.app.button("Создать копию", save),
            ],
        )

    def hall_form(self, hall_id=None):
        hall = self.service.get_hall(hall_id) if hall_id else {}
        name = field("Название зала", hall.get("name", ""), width=310)
        stage = field("Сцена или экран", hall.get("stage", "ЭКРАН"), width=230)
        row_count = field("Рядов", hall.get("rows", 6), width=105)
        col_count = field("Мест в ряду", hall.get("columns", 10), width=140)
        first_row = field(
            "Первый ряд", min((s["row"] for s in hall.get("seats", [])), default=1), width=130
        )
        first_seat = field(
            "Первое место", min((s["number"] for s in hall.get("seats", [])), default=1), width=130
        )
        prices = self.price_fields(
            hall.get("category_prices", {"эконом": 50000, "стандарт": 80000, "VIP": 140000})
        )
        tool = select(
            "Действие при нажатии",
            [
                ("edit", "Параметры кресла"),
                ("aisle", "Кресло / проход"),
                *[(c, "Категория: " + c) for c in SEAT_CATEGORIES],
            ],
            "edit",
            width=260,
        )
        draft = [dict(s) for s in hall.get("seats", [])]
        dimensions = {"rows": hall.get("rows", 6), "columns": hall.get("columns", 10)}
        grid = ft.Column(spacing=8)
        summary = text("", color=MUTED)

        def draw():
            grid.controls = []
            grouped = defaultdict(list)
            for seat in draft:
                grouped[seat["row"]].append(seat)
            for row, items in sorted(grouped.items()):
                grid.controls.append(
                    ft.Row(
                        [
                            text(f"Ряд {row}", 12, MUTED, width=60),
                            *[
                                ft.Container(
                                    text(s["number"] if s["enabled"] else "·", 12, bold=True),
                                    width=39,
                                    height=36,
                                    alignment=ft.Alignment.CENTER,
                                    border_radius=8,
                                    bgcolor=CATEGORY_COLORS[s["category"]] if s["enabled"] else BG,
                                    border=ft.Border.all(1, LINE),
                                    tooltip=f"Ряд {s['row']}, место {s['number']} · {s['category']}"
                                    + (
                                        f" · {money(s['price_override'])}"
                                        if s.get("price_override") is not None
                                        else ""
                                    ),
                                    on_click=self.app.safe(lambda _, seat=s: click(seat)),
                                )
                                for s in sorted(items, key=lambda seat: seat["number"])
                            ],
                        ],
                        spacing=6,
                    )
                )
            summary.value = f"Кресел: {sum(bool(s['enabled']) for s in draft)} · проходов: {sum(not s['enabled'] for s in draft)}"

        def generate(_=None):
            rows, columns = int(row_count.value), int(col_count.value)
            row_start, seat_start = int(first_row.value), int(first_seat.value)
            if not 1 <= rows <= 50 or not 1 <= columns <= 50:
                raise AppError("Размер сетки: от 1 до 50 рядов и от 1 до 50 мест в ряду.")
            if not 1 <= row_start <= 999 or not 1 <= seat_start <= 999:
                raise AppError("Первый номер ряда и места должен быть от 1 до 999.")
            draft[:] = [
                {
                    "row": row_start + r,
                    "number": seat_start + c,
                    "category": "стандарт",
                    "price_override": None,
                    "enabled": True,
                }
                for r in range(rows)
                for c in range(columns)
            ]
            dimensions.update(rows=rows, columns=columns)
            draw()
            self.page.update()

        def click(seat):
            if tool.value == "aisle":
                seat["enabled"] = not seat["enabled"]
            elif tool.value in SEAT_CATEGORIES:
                seat["category"] = tool.value
                seat["enabled"] = True
            else:
                seat_number = field("Номер кресла", seat["number"])
                seat_category = select("Категория", SEAT_CATEGORIES, seat["category"])
                override = field(
                    "Индивидуальная цена, ₽",
                    ""
                    if seat.get("price_override") is None
                    else f"{seat['price_override'] / 100:.2f}",
                    helper="Оставьте пустым, чтобы использовать цену категории",
                )
                enabled = ft.Checkbox(
                    label="Кресло доступно (снимите для прохода)", value=seat["enabled"]
                )

                def save_seat(_):
                    number = int(seat_number.value)
                    if number <= 0 or number > 999:
                        raise AppError("Номер места должен быть от 1 до 999.")
                    if any(
                        s is not seat and s["row"] == seat["row"] and s["number"] == number
                        for s in draft
                    ):
                        raise AppError("Такой номер уже есть в этом ряду.")
                    seat.update(
                        number=number,
                        category=seat_category.value,
                        price_override=rubles(override.value) if override.value.strip() else None,
                        enabled=bool(enabled.value),
                    )
                    self.page.pop_dialog()
                    draw()
                    self.page.update()

                self.app.dialog(
                    f"Ряд {seat['row']} · место {seat['number']}",
                    [seat_number, seat_category, override, enabled],
                    [
                        self.app.button("Назад", lambda _: self.page.pop_dialog(), secondary=True),
                        self.app.button("Применить", save_seat),
                    ],
                    width=450,
                )
                return
            draw()
            self.page.update()

        def preview(_):
            self.read_prices(prices)
            preview_rows = []
            grouped = defaultdict(list)
            for seat in draft:
                grouped[seat["row"]].append(seat)
            for row, items in sorted(grouped.items()):
                preview_rows.append(
                    ft.Row(
                        [
                            text(f"Ряд {row}", 11, MUTED, width=55),
                            *[
                                ft.Container(
                                    text(s["number"], 11, bold=True) if s["enabled"] else None,
                                    width=36,
                                    height=32,
                                    border_radius=8,
                                    alignment=ft.Alignment.CENTER,
                                    bgcolor=CATEGORY_COLORS[s["category"]]
                                    if s["enabled"]
                                    else None,
                                )
                                for s in sorted(items, key=lambda item: item["number"])
                            ],
                        ],
                        spacing=6,
                    )
                )
            self.app.dialog(
                "Предпросмотр · " + (name.value or "Новый зал"),
                [
                    ft.Container(
                        text(stage.value, 13, MUTED, True),
                        padding=15,
                        bgcolor=BG,
                        alignment=ft.Alignment.CENTER,
                    ),
                    ft.Row([ft.Column(preview_rows)], scroll=ft.ScrollMode.ALWAYS),
                    ft.Row([tag(c, INK, CATEGORY_COLORS[c]) for c in SEAT_CATEGORIES], wrap=True),
                    text(summary.value),
                ],
                width=780,
            )

        def save(_):
            if (
                int(row_count.value) != dimensions["rows"]
                or int(col_count.value) != dimensions["columns"]
            ):
                raise AppError(
                    "После изменения размеров нажмите «Построить сетку», затем сохраните зал."
                )
            self.service.save_hall(
                name.value,
                dimensions["rows"],
                dimensions["columns"],
                stage.value,
                self.read_prices(prices),
                draft,
                hall_id,
            )
            self.show("Залы")
            self.app.notice("Зал сохранён.")

        if not draft:
            generate()
        else:
            draw()
        self.app.show(
            self.app.button(
                "К залам", lambda _: self.show("Залы"), icon=ft.Icons.ARROW_BACK, secondary=True
            ),
            self.app.heading("Конструктор зала", "Создайте пространство для ваших событий"),
            panel(
                ft.Row([name, stage], wrap=True),
                ft.Row(
                    [
                        row_count,
                        col_count,
                        first_row,
                        first_seat,
                        self.app.button(
                            "Построить сетку",
                            lambda _: self.app.confirm(
                                "Перестроить схему?",
                                "Категории, проходы и индивидуальные цены в текущем редакторе будут сброшены.",
                                lambda _: generate(),
                            ),
                            secondary=True,
                        ),
                    ],
                    wrap=True,
                ),
                text(
                    "Нумерация применяется при построении сетки. Параметры отдельного кресла меняются нажатием.",
                    12,
                    MUTED,
                ),
                ft.Row(list(prices.values()), wrap=True),
            ),
            panel(
                ft.Row([tool, summary], wrap=True),
                ft.Container(
                    text(stage.value, 13, MUTED, True),
                    padding=15,
                    bgcolor=BG,
                    alignment=ft.Alignment.CENTER,
                ),
                ft.Row([grid], scroll=ft.ScrollMode.ALWAYS),
                ft.Row([tag(c, INK, CATEGORY_COLORS[c]) for c in SEAT_CATEGORIES], wrap=True),
            ),
            ft.Row(
                [
                    self.app.button("Предпросмотр", preview, secondary=True),
                    self.app.button("Сохранить зал", save),
                ],
                wrap=True,
            ),
            text(
                "Если зал уже используется сеансами, для новой структуры создайте копию. Изменение цен шаблона не меняет существующие сеансы.",
                12,
                MUTED,
            ),
        )

    def statistics(self):
        sessions = self.service.list_sessions(admin=True)
        selector = select(
            "Сеанс",
            [
                ("", "Все сеансы"),
                *[(str(s["id"]), s["title"] + " · " + date_text(s["start"])) for s in sessions],
            ],
            "",
            width=590,
        )
        result = ft.Column(spacing=18)

        def refresh(_=None):
            data = self.service.statistics(int(selector.value) if selector.value else None)
            result.controls = [
                ft.Row(
                    [
                        panel(text(label, 13, MUTED), text(value, 30, bold=True), width=235)
                        for label, value in [
                            ("Активных билетов", str(data["active_tickets"])),
                            ("Заполненность", f"{data['occupancy_percent']:.1f}%"),
                            ("Сумма активных бронирований", money(data["active_amount"])),
                        ]
                    ],
                    wrap=True,
                    spacing=16,
                ),
                panel(
                    text("Заполненность сеансов", 20, bold=True),
                    text(f"Всего мест в выбранном расписании: {data['total_seats']}", color=MUTED),
                    ft.ProgressBar(
                        value=min(1, data["occupancy_percent"] / 100),
                        color=TEAL,
                        bgcolor=BG,
                        height=10,
                    ),
                    text(
                        "Сумма бронирований не является выручкой: приложение не принимает и не учитывает оплату.",
                        13,
                        MUTED,
                    ),
                ),
            ]
            self.page.update()

        selector.on_select = self.app.safe(refresh)
        refresh()
        return [selector, result]
