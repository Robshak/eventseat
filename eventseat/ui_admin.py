from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timedelta

import flet as ft

from eventseat.config import import_cover
from eventseat.domain import AppError
from eventseat.ui import (
    BG,
    CATEGORIES,
    CATEGORY_COLORS,
    MUTED,
    RED,
    SEAT_CATEGORIES,
    TEAL,
    date_text,
    field,
    hoverable,
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

    @property
    def drafts(self):
        return self.app.view_state.drafts

    def show(
        self, tab="Мероприятия", search="", *, event_id=None, hall_id=None, focus_session_id=None
    ):
        self.app.capture_view()
        self.app.clear_capture()
        if self.service.current_user["role"] != "admin":
            raise AppError("Это действие доступно только администратору.")
        if tab == "Сеансы" and (event_id is not None or hall_id is not None):
            self.drafts["admin:session_filters"] = {
                "event_id": str(event_id or ""),
                "hall_id": str(hall_id or ""),
                "date_from": "",
                "date_to": "",
                "status": "all",
            }
        if focus_session_id is not None:
            session = self.service.get_session(focus_session_id)
            self.drafts["admin:session_filters"] = {
                "event_id": str(session["event_id"]),
                "hall_id": "",
                "date_from": "",
                "date_to": "",
                "status": "all",
            }
        if tab == "Сеансы" and (
            event_id is not None or hall_id is not None or focus_session_id is not None
        ):
            self.drafts["admin:session_applied"] = dict(self.drafts["admin:session_filters"])
        self.app.set_view(
            {"section": "Администрирование", "page": "list", "tab": tab, "search": search}
        )
        if tab == "Бронирования":
            self.app.bookings(True, search)
            self.app.content.controls.insert(0, self.tabs(tab))
            self.page.update()
            return
        render = {
            "Мероприятия": self.events,
            "Сеансы": lambda: self.sessions(focus_session_id),
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
        if focus_session_id is not None:
            self.focus_session(focus_session_id)

    def focus_session(self, session_id):
        controls = self.app.content.controls

        async def reveal():
            await asyncio.sleep(0.12)
            if self.app.service is self.service and self.app.content.controls is controls:
                await self.app.content.scroll_to(scroll_key=f"session-{session_id}", duration=300)

        self.page.run_task(reveal)

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
            card = panel(
                ft.Row(
                    [
                        self.app.cover(event.get("cover_path", ""), 130, 90),
                        ft.Column(
                            [
                                ft.TextButton(
                                    content=text(event["title"], 20, TEAL, bold=True),
                                    tooltip="Сеансы мероприятия",
                                    on_click=self.app.safe(
                                        lambda _, eid=event["id"]: self.show("Сеансы", event_id=eid)
                                    ),
                                ),
                                text(
                                    f"{event['category']} · {event['duration']} мин",
                                    color=MUTED,
                                ),
                                tag("Опубликовано" if event["published"] else "Черновик"),
                            ],
                            expand=True,
                        ),
                        self.app.button(
                            "Создать сеанс",
                            lambda _, eid=event["id"]: self.session_form(event_id=eid),
                            secondary=True,
                        ),
                        self.app.button(
                            "Сеансы",
                            lambda _, eid=event["id"]: self.show("Сеансы", event_id=eid),
                            secondary=True,
                        ),
                        self.app.button(
                            "Изменить",
                            lambda _, eid=event["id"]: self.event_form(eid),
                            secondary=True,
                        ),
                    ]
                ),
                on_click=self.app.safe(
                    lambda _, eid=event["id"]: self.show("Сеансы", event_id=eid)
                ),
            )
            controls.append(hoverable(card, background="#F1F9F8"))
        if not events:
            controls.append(
                self.app.empty(
                    "Мероприятий пока нет", "Создайте событие, добавьте сеанс и опубликуйте его."
                )
            )
        return controls

    def event_form(self, event_id=None):
        self.app.capture_view()
        draft_key = f"event:{event_id or 'new'}"
        event = self.service.get_event(event_id) if event_id else {}
        event = {**event, **self.drafts.get(draft_key, {})}
        title = field("Название", event.get("title", ""), max_length=160)
        description = field(
            "Описание", event.get("description", ""), multiline=True, min_lines=4, max_lines=7
        )
        category = select("Категория", CATEGORIES, event.get("category", "кино"), width=245)
        duration = field("Продолжительность, мин", event.get("duration", 90), width=245)
        cover = {"path": event.get("cover_path", "")}
        cover_text = text("Обложка выбрана" if cover["path"] else "Обложка не выбрана", 13, MUTED)
        preview = ft.Container(self.app.cover(cover["path"], 240, 140))

        def capture():
            self.drafts[draft_key] = {
                "title": title.value,
                "description": description.value,
                "category": category.value,
                "duration": duration.value,
                "cover_path": cover["path"],
            }

        async def upload(_):
            generation = self.app._view_generation
            files = await self.app.picker.pick_files(
                dialog_title="Выберите обложку",
                allow_multiple=False,
                file_type=ft.FilePickerFileType.CUSTOM,
                allowed_extensions=["png", "jpg", "jpeg", "webp"],
            )
            if files and files[0].path:
                if self.app.service is not self.service or self.app._view_generation != generation:
                    return
                cover["path"] = import_cover(files[0].path)
                cover_text.value = files[0].name
                preview.content = self.app.cover(cover["path"], 240, 140)
                self.page.update()

        def save(published):
            saved_id = self.service.save_event(
                title.value,
                description.value,
                category.value,
                int(duration.value),
                published,
                cover["path"],
                event_id,
            )
            self.app.clear_capture()
            self.drafts.pop(draft_key, None)
            self.show()
            self.app.notice("Мероприятие опубликовано." if published else "Черновик сохранён.")
            return saved_id

        self.app.set_view(
            {
                "section": "Администрирование",
                "page": "event_form",
                "event_id": event_id,
            },
            capture=capture,
        )
        self.app.show(
            self.app.button(
                "К мероприятиям", lambda _: self.show(), icon=ft.Icons.ARROW_BACK, secondary=True
            ),
            self.app.heading(
                "Редактировать мероприятие" if event_id else "Новое мероприятие",
                "Введённые данные сохраняются при переходах между разделами",
            ),
            panel(
                title,
                ft.Row([category, duration], wrap=True),
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
                    ],
                    wrap=True,
                ),
                text(
                    "В афише видны только опубликованные мероприятия с будущими доступными сеансами.",
                    12,
                    MUTED,
                ),
                width=740,
            ),
            ft.Row(
                [
                    self.app.button("Сохранить черновик", lambda _: save(False), secondary=True),
                    self.app.button("Опубликовать", lambda _: save(True)),
                    *(
                        [
                            self.app.button(
                                "Сеансы мероприятия",
                                lambda _: self.show("Сеансы", event_id=event_id),
                                secondary=True,
                            )
                        ]
                        if event_id
                        else []
                    ),
                ],
                wrap=True,
            ),
        )

    def sessions(self, focus_session_id=None):
        saved = self.drafts.get("admin:session_filters", {})
        event = select(
            "Мероприятие",
            [
                ("", "Все мероприятия"),
                *[(e["id"], e["title"]) for e in self.service.list_events(admin=True)],
            ],
            saved.get("event_id", ""),
            width=310,
        )
        hall = select(
            "Зал",
            [("", "Все залы"), *[(h["id"], h["name"]) for h in self.service.list_halls()]],
            saved.get("hall_id", ""),
            width=260,
        )
        date_from = self.app.date_field("С · ДД.ММ.ГГГГ", saved.get("date_from", ""))
        date_to = self.app.date_field("По · ДД.ММ.ГГГГ", saved.get("date_to", ""))
        status = select(
            "Состояние сеанса",
            [
                ("all", "Все состояния"),
                ("upcoming", "Предстоящие"),
                ("completed", "Завершённые"),
                ("cancelled", "Отменённые"),
            ],
            saved.get("status", "all"),
            width=245,
        )

        def capture():
            self.drafts["admin:session_filters"] = {
                "event_id": event.value or "",
                "hall_id": hall.value or "",
                "date_from": date_from.value.strip(),
                "date_to": date_to.value.strip(),
                "status": status.value or "all",
            }

        def refresh(_):
            capture()
            filters = self.drafts["admin:session_filters"]
            self.service.list_sessions(**self.session_query(filters))
            self.drafts["admin:session_applied"] = dict(filters)
            self.show("Сеансы")

        def reset(_):
            self.app.clear_capture()
            self.drafts.pop("admin:session_filters", None)
            self.drafts.pop("admin:session_applied", None)
            self.show("Сеансы")

        self.app.set_view(
            {"section": "Администрирование", "page": "list", "tab": "Сеансы"}, capture=capture
        )
        controls = [
            ft.Row(
                [
                    self.app.button(
                        "Создать сеанс",
                        lambda _: self.session_form(
                            event_id=int(event.value) if event.value else None
                        ),
                        icon=ft.Icons.ADD,
                    ),
                    text("Нажмите на сеанс, чтобы открыть его сведения и управление.", color=MUTED),
                ],
                wrap=True,
            ),
            panel(
                ft.Row([event, hall, status], wrap=True),
                ft.Row(
                    [
                        date_from,
                        date_to,
                        self.app.button("Применить", refresh),
                        self.app.button("Сбросить", reset, secondary=True),
                    ],
                    wrap=True,
                ),
            ),
        ]
        sessions = self.service.list_sessions(
            **self.session_query(self.drafts.get("admin:session_applied", {}))
        )
        controls.append(text(f"Сеансов: {len(sessions)}", 15, bold=True))
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
            card = panel(
                ft.Row(
                    [
                        ft.TextButton(
                            content=text(session["title"], 21, TEAL, bold=True),
                            tooltip="Открыть сеанс",
                            on_click=self.app.safe(
                                lambda _, sid=session["id"]: self.session_detail(sid)
                            ),
                            expand=True,
                        ),
                        tag(
                            state,
                            RED if is_cancelled else TEAL,
                            "#F8E9E9" if is_cancelled else "#E7F3F2",
                        ),
                    ]
                ),
                text(
                    f"{date_text(session['start'])} · {session['hall_name']} · {session['duration']} мин · №{session['id']}",
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
                key=ft.ScrollKey(f"session-{session['id']}"),
                on_click=self.app.safe(lambda _, sid=session["id"]: self.session_detail(sid)),
            )
            if session["id"] == focus_session_id:
                card.bgcolor = "#E4F3F1"
                card.border = ft.Border.all(2, TEAL)
            controls.append(hoverable(card, background="#F1F9F8"))
        if not sessions:
            controls.append(
                self.app.empty(
                    "Сеансы не найдены",
                    "Измените фильтры или создайте новый сеанс.",
                )
            )
        return controls

    @staticmethod
    def parse_date(value):
        return datetime.strptime(value.strip(), "%d.%m.%Y").date() if value.strip() else None

    def session_query(self, filters):
        return {
            "event_id": int(filters["event_id"]) if filters.get("event_id") else None,
            "admin": True,
            "hall_id": int(filters["hall_id"]) if filters.get("hall_id") else None,
            "date_from": self.parse_date(filters.get("date_from", "")),
            "date_to": self.parse_date(filters.get("date_to", "")),
            "status": filters.get("status", "all"),
        }

    def session_detail(self, session_id):
        self.app.capture_view()
        session = self.service.get_session(session_id)
        self.app.set_view(
            {
                "section": "Администрирование",
                "page": "session_detail",
                "session_id": session_id,
            }
        )
        editable = session["status"] != "cancelled" and session["start"] > datetime.now()
        state = (
            "Отменён"
            if session["status"] == "cancelled"
            else "Предстоящий"
            if editable
            else "Завершён"
        )
        actions = [
            self.app.button("Заполненность и места", lambda _: self.app.seats(session_id, True)),
            self.app.button(
                "Мероприятие", lambda _: self.event_form(session["event_id"]), secondary=True
            ),
            self.app.button(
                "Редактировать зал", lambda _: self.hall_form(session["hall_id"]), secondary=True
            ),
            self.app.button(
                "Сеансы этого зала",
                lambda _: self.show("Сеансы", hall_id=session["hall_id"]),
                secondary=True,
            ),
        ]
        if editable:
            actions.extend(
                [
                    self.app.button(
                        "Изменить сеанс", lambda _: self.session_form(session_id), secondary=True
                    ),
                    self.app.button(
                        "Цены мест", lambda _: self.session_prices(session_id), secondary=True
                    ),
                    self.app.button(
                        "Отменить сеанс", lambda _: self.cancel_session(session), secondary=True
                    ),
                ]
            )
        self.app.show(
            self.app.button(
                "К сеансам",
                lambda _: self.show("Сеансы", focus_session_id=session_id),
                icon=ft.Icons.ARROW_BACK,
                secondary=True,
            ),
            self.app.heading(
                session["title"], f"Сеанс №{session_id} · {date_text(session['start'])}"
            ),
            panel(
                tag(state, RED if session["status"] == "cancelled" else TEAL),
                text(session["hall_name"], 23, bold=True),
                text(f"Продолжительность: {session['duration']} минут", color=MUTED),
                text(
                    f"Свободно: {session['free_count']} из {session['total_count']} мест",
                    18,
                    bold=True,
                ),
                *(
                    [text("Причина отмены: " + session["cancel_reason"], color=RED)]
                    if session["cancel_reason"]
                    else []
                ),
                ft.Row(actions, wrap=True),
            ),
        )

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
        self.app.capture_view()
        draft_key = f"session:{session_id or 'new'}:{event_id or ''}"
        saved = self.drafts.get(draft_key, {})
        events = self.service.list_events(admin=True)
        halls = self.service.list_halls()
        if not events or not halls:
            raise AppError("Для создания сеанса сначала создайте мероприятие и зал.")
        session = self.service.get_session(session_id) if session_id else {}
        current_event = int(
            saved.get("event_id") or session.get("event_id", event_id or events[0]["id"])
        )
        current_hall = int(saved.get("hall_id") or session.get("hall_id", halls[0]["id"]))
        hall = self.service.get_hall(current_hall)
        event_select = select(
            "Мероприятие", [(e["id"], e["title"]) for e in events], str(current_event)
        )
        hall_select = select("Зал", [(h["id"], h["name"]) for h in halls], str(current_hall))
        start = session.get(
            "start", (datetime.now() + timedelta(days=1)).replace(hour=19, minute=0)
        )
        date = self.app.date_field(
            "Дата · ДД.ММ.ГГГГ", saved.get("date", start.strftime("%d.%m.%Y")), width=245
        )
        time = field("Время · ЧЧ:ММ", saved.get("time", start.strftime("%H:%M")), width=245)
        prices = self.price_fields(session.get("category_prices", hall["category_prices"]))
        for category, value in saved.get("prices", {}).items():
            prices[category].value = value
        change_prices = ft.Checkbox(
            label="Изменить цены категорий в этом сеансе",
            value=saved.get("change_prices", not bool(session_id)),
        )
        category_row = ft.Row(list(prices.values()), wrap=True, visible=change_prices.value)

        def capture():
            self.drafts[draft_key] = {
                "event_id": event_select.value,
                "hall_id": hall_select.value,
                "date": date.value,
                "time": time.value,
                "change_prices": change_prices.value,
                "prices": {category: control.value for category, control in prices.items()},
            }

        def pick_time(_):
            try:
                selected = datetime.strptime(time.value.strip(), "%H:%M").time()
            except ValueError:
                selected = start.time()

            def changed(event):
                if event.control.value:
                    time.value = event.control.value.strftime("%H:%M")
                    time.update()

            self.page.show_dialog(
                ft.TimePicker(
                    value=selected,
                    help_text="Выбрать время сеанса",
                    cancel_text="Отмена",
                    confirm_text="Выбрать",
                    hour_label_text="Часы",
                    minute_label_text="Минуты",
                    hour_format=ft.TimePickerHourFormat.H24,
                    on_change=self.app.safe(changed),
                )
            )

        time.suffix = ft.IconButton(
            icon=ft.Icons.ACCESS_TIME,
            tooltip="Выбрать время сеанса",
            on_click=self.app.safe(pick_time),
        )

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
            self.app.clear_capture()
            self.drafts.pop(draft_key, None)
            self.show("Сеансы", focus_session_id=sid)
            self.app.notice(
                "Сеанс сохранён. Индивидуальные цены можно настроить кнопкой «Цены мест»."
            )
            return sid

        self.app.set_view(
            {
                "section": "Администрирование",
                "page": "session_form",
                "session_id": session_id,
                "event_id": event_id,
            },
            capture=capture,
        )
        self.app.show(
            self.app.button(
                "К сеансам", lambda _: self.show("Сеансы"), icon=ft.Icons.ARROW_BACK, secondary=True
            ),
            self.app.heading(
                "Редактировать сеанс" if session_id else "Новый сеанс",
                f"Сеанс №{session_id}" if session_id else "Выберите мероприятие, зал и время",
            ),
            panel(
                event_select,
                hall_select,
                ft.Row([date, time], wrap=True),
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
                self.app.button("Сохранить сеанс", save),
                width=740,
            ),
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
                            hoverable(
                                ft.Container(
                                    text(f"{s['number']}\n{money(s['price'])}", 10, bold=True),
                                    width=68,
                                    height=48,
                                    alignment=ft.Alignment.CENTER,
                                    bgcolor=CATEGORY_COLORS[s["category"]],
                                    border_radius=8,
                                    on_click=self.app.safe(lambda _, seat=s: edit(seat)),
                                )
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
                                "Сеансы зала",
                                lambda _, hid=hall["id"]: self.show("Сеансы", hall_id=hid),
                                secondary=True,
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
        from eventseat.ui_hall_editor import HallEditor

        HallEditor(self, hall_id).show()

    def statistics(self):
        saved = self.drafts.get("admin:statistics_filters", {})
        sessions = self.service.list_sessions(admin=True)
        event = select(
            "Мероприятие",
            [
                ("", "Все мероприятия"),
                *[(e["id"], e["title"]) for e in self.service.list_events(admin=True)],
            ],
            saved.get("event_id", ""),
            width=310,
        )
        hall = select(
            "Зал",
            [("", "Все залы"), *[(h["id"], h["name"]) for h in self.service.list_halls()]],
            saved.get("hall_id", ""),
            width=260,
        )
        date_from = self.app.date_field("С · ДД.ММ.ГГГГ", saved.get("date_from", ""))
        date_to = self.app.date_field("По · ДД.ММ.ГГГГ", saved.get("date_to", ""))
        session_status = select(
            "Состояние сеанса",
            [
                ("all", "Все состояния"),
                ("upcoming", "Предстоящие"),
                ("completed", "Завершённые"),
                ("cancelled", "Отменённые"),
            ],
            saved.get("session_status", "upcoming"),
            width=245,
        )
        booking_status = select(
            "Состояние брони",
            [
                ("valid", "Без отменённых"),
                ("all", "Все бронирования"),
                ("active", "Активные"),
                ("completed", "Завершённые"),
                ("cancelled", "Отменённые"),
            ],
            saved.get("booking_status", "valid"),
            width=245,
        )
        selector = select(
            "Сеанс",
            [
                ("", "Все сеансы"),
                *[
                    (
                        str(s["id"]),
                        f"№{s['id']} · {s['title']} · {date_text(s['start'])} · {s['hall_name']}",
                    )
                    for s in sessions
                ],
            ],
            saved.get("session_id", ""),
            width=650,
        )
        result = ft.Column(spacing=18)

        def capture():
            self.drafts["admin:statistics_filters"] = {
                "event_id": event.value or "",
                "hall_id": hall.value or "",
                "date_from": date_from.value.strip(),
                "date_to": date_to.value.strip(),
                "session_id": selector.value or "",
                "session_status": session_status.value,
                "booking_status": booking_status.value,
            }

        def refresh(_=None, *, current=True):
            capture()
            filters = (
                self.drafts["admin:statistics_filters"]
                if current
                else self.drafts.get("admin:statistics_applied", {})
            )
            data = self.service.statistics(
                int(filters["session_id"]) if filters.get("session_id") else None,
                event_id=int(filters["event_id"]) if filters.get("event_id") else None,
                hall_id=int(filters["hall_id"]) if filters.get("hall_id") else None,
                date_from=self.parse_date(filters.get("date_from", "")),
                date_to=self.parse_date(filters.get("date_to", "")),
                session_status=filters.get("session_status", "upcoming"),
                booking_status=filters.get("booking_status", "valid"),
            )
            if current:
                self.drafts["admin:statistics_applied"] = dict(filters)
            result.controls = [
                ft.Row(
                    [
                        panel(text(label, 13, MUTED), text(value, 30, bold=True), width=235)
                        for label, value in [
                            ("Бронирований по фильтру", str(data["booking_count"])),
                            ("Билетов по фильтру", str(data["ticket_count"])),
                            ("Заполненность", f"{data['occupancy_percent']:.1f}%"),
                            ("Сумма по фильтру", money(data["amount"])),
                        ]
                    ],
                    wrap=True,
                    spacing=16,
                ),
                panel(
                    text("Заполненность сеансов", 20, bold=True),
                    text(
                        f"Сеансов: {data['session_count']} · мест: {data['total_seats']} · занято: {data['active_tickets']}",
                        color=MUTED,
                    ),
                    ft.ProgressBar(
                        value=min(1, data["occupancy_percent"] / 100),
                        color=TEAL,
                        bgcolor=BG,
                        height=10,
                    ),
                    text(
                        "Заполненность отражает занятые места без отменённых билетов. Фильтр состояния брони влияет на количество и сумму выше.",
                        13,
                        MUTED,
                    ),
                    text(
                        "Сумма бронирований не является выручкой: приложение не принимает и не учитывает оплату.",
                        13,
                        MUTED,
                    ),
                ),
            ]
            self.page.update()

        def reset(_):
            self.app.clear_capture()
            self.drafts.pop("admin:statistics_filters", None)
            self.drafts.pop("admin:statistics_applied", None)
            self.show("Статистика")

        self.app.set_view(
            {"section": "Администрирование", "page": "list", "tab": "Статистика"},
            capture=capture,
        )
        refresh(current=False)
        return [
            panel(
                ft.Row([event, hall], wrap=True),
                selector,
                ft.Row([date_from, date_to], wrap=True),
                ft.Row([session_status, booking_status], wrap=True),
                ft.Row(
                    [
                        self.app.button("Применить фильтры", refresh),
                        self.app.button("Сбросить", reset, secondary=True),
                    ],
                    wrap=True,
                ),
                text(
                    "Фильтры применяются вместе. Период включает обе даты начала сеансов.",
                    12,
                    MUTED,
                ),
            ),
            result,
        ]
