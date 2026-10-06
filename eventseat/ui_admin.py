from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import flet as ft

from eventseat.config import import_cover
from eventseat.domain import AppError
from eventseat.ui import (
    CATEGORIES,
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
        if tab == "Статистика":
            tab = "Бронирования"
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
        if tab == "Бронирования" and search:
            for key in ("admin:booking_filters", "admin:booking_applied"):
                self.drafts.setdefault(key, {})["search"] = search
        render = {
            "Мероприятия": self.events,
            "Сеансы": lambda: self.sessions(focus_session_id),
            "Залы": self.halls,
            "Бронирования": self.bookings,
        }[tab]
        controls = render()
        self.app.show(
            *(
                [self.app.back_button("К мероприятиям", self.show)]
                if self.app._route.get("return_to")
                else []
            ),
            self.app.heading(
                "Администрирование", "Управляйте событиями, пространствами и бронированиями"
            ),
            self.tabs(tab),
            *controls,
        )
        if focus_session_id is not None:
            self.focus_session(focus_session_id)

    def focus_session(self, session_id):
        controls = self.app.content.controls
        card = next(
            (control for control in controls if str(control.key) == f"session-{session_id}"),
            None,
        )
        if card is None:
            return
        card.bgcolor = "#E4F3F1"
        card.border = ft.Border.all(2, TEAL)
        hoverable(card, background="#F1F9F8")
        self.page.update()

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
                for label in ["Мероприятия", "Сеансы", "Залы", "Бронирования"]
            ],
            wrap=True,
            spacing=10,
        )

    def events(self):
        events = self.service.list_events(admin=True)
        controls = [
            self.app.button(
                "Создать мероприятие",
                lambda _: self.app.open_related(self.event_form),
                icon=ft.Icons.ADD,
            )
        ]
        for event in events:
            card = panel(
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
                            "Создать сеанс",
                            lambda _, eid=event["id"]: self.app.open_related(
                                lambda: self.session_form(event_id=eid)
                            ),
                            secondary=True,
                        ),
                        self.app.button(
                            "Сеансы",
                            lambda _, eid=event["id"]: self.app.open_related(
                                lambda: self.show("Сеансы", event_id=eid)
                            ),
                            secondary=True,
                        ),
                        self.app.button(
                            "Изменить",
                            lambda _, eid=event["id"]: self.app.open_related(
                                lambda: self.event_form(eid)
                            ),
                            secondary=True,
                        ),
                    ]
                ),
                on_click=self.app.safe(
                    lambda _, eid=event["id"]: self.app.open_related(
                        lambda: self.show("Сеансы", event_id=eid)
                    )
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
            self.app.go_back(self.show)
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
            self.app.back_button("К мероприятиям", self.show),
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
                                lambda _: self.app.open_related(
                                    lambda: self.show("Сеансы", event_id=event_id)
                                ),
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
            date_from.read()
            date_to.read()
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
                        lambda _: self.app.open_related(
                            lambda: self.session_form(
                                event_id=int(event.value) if event.value else None
                            )
                        ),
                        icon=ft.Icons.ADD,
                    ),
                    text("Нажмите на сеанс, чтобы открыть его редактор.", color=MUTED),
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
                    lambda _, sid=session["id"]: self.app.open_related(
                        lambda: self.app.seats(sid, True)
                    ),
                    secondary=True,
                ),
                self.app.button(
                    "К залу",
                    lambda _, hid=session["hall_id"]: self.app.open_related(
                        lambda: self.hall_form(hid)
                    ),
                    secondary=True,
                ),
            ]
            if not is_cancelled and is_future:
                actions += [
                    self.app.button(
                        "Изменить",
                        lambda _, sid=session["id"]: self.app.open_related(
                            lambda: self.session_form(session_id=sid)
                        ),
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
                        text(session["title"], 21, bold=True, expand=True),
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
                on_click=self.app.safe(
                    lambda _, sid=session["id"]: self.app.open_related(
                        lambda: self.session_form(sid)
                    )
                ),
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
        self.session_form(session_id)

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
        editable = not session_id or (
            session["status"] == "available" and session["start"] > datetime.now()
        )
        if not editable:
            saved = {}
        current_event = int(
            saved.get("event_id") or session.get("event_id", event_id or events[0]["id"])
        )
        current_hall = int(saved.get("hall_id") or session.get("hall_id", halls[0]["id"]))
        event_select = select(
            "Мероприятие", [(e["id"], e["title"]) for e in events], str(current_event)
        )
        hall_select = select("Зал", [(h["id"], h["name"]) for h in halls], str(current_hall))
        start = session.get(
            "start", (datetime.now() + timedelta(days=1)).replace(hour=19, minute=0)
        )
        date = self.app.date_field(
            "Дата · ДД.ММ.ГГГГ",
            saved.get("date", start.strftime("%d.%m.%Y")),
            width=245,
            required=True,
        )
        time = field("Время · ЧЧ:ММ", saved.get("time", start.strftime("%H:%M")), width=245)
        for control in (event_select, hall_select, date, time):
            control.disabled = not editable

        def capture():
            self.drafts[draft_key] = {
                "event_id": event_select.value,
                "hall_id": hall_select.value,
                "date": date.value,
                "time": time.value,
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

        def save(_):
            starts = datetime.combine(
                date.read(), datetime.strptime(time.value.strip(), "%H:%M").time()
            )
            sid = self.service.save_session(
                int(event_select.value),
                int(hall_select.value),
                starts,
                session_id=session_id,
            )
            self.app.clear_capture()
            self.drafts.pop(draft_key, None)
            returning = bool(self.app._route.get("return_to"))
            self.app.go_back(lambda: self.show("Сеансы", focus_session_id=sid))
            if (
                returning
                and self.app._route.get("page") == "list"
                and self.app._route.get("tab") == "Сеансы"
            ):
                self.focus_session(sid)
            self.app.notice("Сеанс сохранён.")
            return sid

        self.app.set_view(
            {
                "section": "Администрирование",
                "page": "session_form",
                "session_id": session_id,
                "event_id": event_id,
            },
            capture=capture if editable else None,
        )
        self.app.show(
            self.app.back_button("К сеансам", lambda: self.show("Сеансы")),
            self.app.heading(
                "Редактировать сеанс"
                if session_id and editable
                else "Сеанс"
                if session_id
                else "Новый сеанс",
                f"Сеанс №{session_id} · {session['title']} · {date_text(session['start'])} · {session['hall_name']}"
                if session_id
                else "Выберите мероприятие, зал и время",
            ),
            panel(
                event_select,
                hall_select,
                ft.Row([date, time], wrap=True),
                text(
                    "Цены категорий задаются в зале. Новый сеанс получает цены выбранного зала; существующие сеансы сохраняют цены на момент создания.",
                    12,
                    MUTED,
                ),
                text(
                    "Сеансы в одном зале не могут пересекаться. При активных бронях менять время и зал нельзя.",
                    12,
                    MUTED,
                ),
                *(
                    [self.app.button("Сохранить сеанс", save)]
                    if editable
                    else [
                        tag("Отменён" if session["status"] == "cancelled" else "Завершён", RED),
                        text("Этот сеанс доступен только для просмотра.", color=MUTED),
                        *(
                            [text("Причина отмены: " + session["cancel_reason"], color=RED)]
                            if session["cancel_reason"]
                            else []
                        ),
                    ]
                ),
                ft.Row(
                    [
                        self.app.button(
                            "К мероприятию",
                            lambda _: self.app.open_related(
                                lambda: self.event_form(int(event_select.value))
                            ),
                            secondary=True,
                        ),
                        self.app.button(
                            "К залу",
                            lambda _: self.app.open_related(
                                lambda: self.hall_form(int(hall_select.value))
                            ),
                            secondary=True,
                        ),
                        *(
                            [
                                self.app.button(
                                    "Заполненность и места",
                                    lambda _: self.app.open_related(
                                        lambda: self.app.seats(session_id, True)
                                    ),
                                    secondary=True,
                                )
                            ]
                            if session_id
                            else []
                        ),
                    ],
                    wrap=True,
                ),
                width=740,
            ),
        )

    def halls(self):
        halls = self.service.list_halls()
        controls = [
            self.app.button(
                "Создать зал",
                lambda _: self.app.open_related(self.hall_form),
                icon=ft.Icons.ADD,
            )
        ]
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
                                lambda _, hid=hall["id"]: self.app.open_related(
                                    lambda: self.show("Сеансы", hall_id=hid)
                                ),
                                secondary=True,
                            ),
                            self.app.button(
                                "Редактировать схему",
                                lambda _, hid=hall["id"]: self.app.open_related(
                                    lambda: self.hall_form(hid)
                                ),
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
            self.app.open_related(lambda: self.hall_form(hall_id))

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

    def bookings(self):
        saved = self.drafts.get("admin:booking_filters", {})
        sessions = self.service.list_sessions(admin=True)
        search = field("Поиск по номеру, имени или логину", saved.get("search", ""), width=300)
        event = select(
            "Мероприятие",
            [
                ("", "Все мероприятия"),
                *[(e["id"], e["title"]) for e in self.service.list_events(admin=True)],
            ],
            saved.get("event_id", ""),
            width=290,
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
            saved.get("session_status", "all"),
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
            saved.get("booking_status", "all"),
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
            width=430,
        )
        result = ft.Column(spacing=18)
        listing = ft.Column(spacing=18)

        def capture():
            self.drafts["admin:booking_filters"] = {
                "search": search.value.strip(),
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
            if current:
                date_from.read()
                date_to.read()
            filters = (
                self.drafts["admin:booking_filters"]
                if current
                else self.drafts.get("admin:booking_applied", {})
            )
            query = self.booking_query(filters)
            data = self.service.statistics(**query)
            bookings = self.service.list_bookings(admin=True, **query)
            if current:
                self.drafts["admin:booking_applied"] = dict(filters)
            result.controls = [
                ft.Row(
                    [
                        panel(
                            text(label, 13, MUTED),
                            text(value, 28, bold=True),
                            width=220,
                            padding=16,
                        )
                        for label, value in [
                            ("Бронирований по фильтру", str(data["booking_count"])),
                            ("Билетов по фильтру", str(data["ticket_count"])),
                            ("Заполненность сеансов", f"{data['occupancy_percent']:.1f}%"),
                            ("Сумма по фильтру", money(data["amount"])),
                        ]
                    ],
                    wrap=True,
                    spacing=16,
                ),
                text(
                    f"Сеансов: {data['session_count']} · мест: {data['total_seats']} · занято: {data['active_tickets']}",
                    color=MUTED,
                ),
                text(
                    "Заполненность — по выбранным сеансам без отменённых билетов; поиск и статус брони меняют список, количество и сумму. Сумма бронирований не означает оплату.",
                    12,
                    MUTED,
                ),
            ]
            listing.controls = [
                text(f"Найдено бронирований: {len(bookings)}", 20, bold=True),
                *[self.booking_card(booking) for booking in bookings],
                *(
                    []
                    if bookings
                    else [
                        self.app.empty(
                            "Бронирования не найдены",
                            "Измените фильтры или дождитесь новых бронирований.",
                        )
                    ]
                ),
            ]
            self.page.update()

        def reset(_):
            self.app.clear_capture()
            self.drafts.pop("admin:booking_filters", None)
            self.drafts.pop("admin:booking_applied", None)
            self.show("Бронирования")

        self.app.set_view(
            {"section": "Администрирование", "page": "list", "tab": "Бронирования"},
            capture=capture,
        )
        search.on_submit = self.app.safe(refresh)
        refresh(current=False)
        return [
            result,
            panel(
                ft.Row([search, event, hall], wrap=True),
                ft.Row([selector, date_from, date_to], wrap=True),
                ft.Row(
                    [
                        session_status,
                        booking_status,
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
            listing,
        ]

    def booking_query(self, filters):
        return {
            "search": filters.get("search", ""),
            "session_id": int(filters["session_id"]) if filters.get("session_id") else None,
            "event_id": int(filters["event_id"]) if filters.get("event_id") else None,
            "hall_id": int(filters["hall_id"]) if filters.get("hall_id") else None,
            "date_from": self.parse_date(filters.get("date_from", "")),
            "date_to": self.parse_date(filters.get("date_to", "")),
            "session_status": filters.get("session_status", "all"),
            "booking_status": filters.get("booking_status", "all"),
        }

    def booking_card(self, booking):
        def return_to_list():
            self.show("Бронирования")

        def cancel(reason):
            self.service.cancel_booking(booking["id"], reason)
            self.show("Бронирования")

        actions = [
            self.app.button(
                "Электронный билет", lambda _: self.app.ticket(booking["id"]), secondary=True
            ),
            self.app.button(
                "Места на схеме",
                lambda _: self.app.open_related(lambda: self.app.booking_map(booking["id"])),
                secondary=True,
            ),
            self.app.button(
                "К сеансу",
                lambda _: self.app.open_related(lambda: self.session_form(booking["session_id"])),
                secondary=True,
            ),
            self.app.button(
                "К мероприятию",
                lambda _: self.app.open_related(
                    lambda: self.app.event_detail(
                        booking["event_id"], related=True, back=("К бронированиям", return_to_list)
                    )
                ),
                secondary=True,
            ),
        ]
        if booking["status"] == "active" and booking["start"] > datetime.now():
            actions.append(
                self.app.button(
                    "Отменить",
                    lambda _: self.app.confirm(
                        "Отменить бронирование?",
                        f"Бронирование {booking['number']}. Все места этого заказа будут освобождены.",
                        cancel,
                        reason=True,
                    ),
                    secondary=True,
                )
            )
        return panel(
            ft.Row(
                [
                    tag(
                        {"active": "Активно", "cancelled": "Отменено", "completed": "Завершено"}[
                            booking["status"]
                        ],
                        RED if booking["status"] == "cancelled" else TEAL,
                        "#F8E9E9" if booking["status"] == "cancelled" else "#E7F3F2",
                    ),
                    text(booking["number"], 13, MUTED, selectable=True),
                    text(money(booking["total"]), 20, bold=True),
                ],
                wrap=True,
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                run_spacing=8,
            ),
            text(booking["title"], 22, bold=True),
            text(
                f"{date_text(booking['start'])} · {booking['hall_name']} · сеанс №{booking['session_id']}",
                color=MUTED,
            ),
            text(f"Владелец: {booking['user_name']} · @{booking['user_login']}", color=MUTED),
            text(" · ".join(f"ряд {t['row']}, место {t['number']}" for t in booking["tickets"])),
            *(
                [text("Причина отмены: " + booking["cancel_reason"], color=RED)]
                if booking["cancel_reason"]
                else []
            ),
            ft.Row(actions, wrap=True),
            data={"booking_id": booking["id"]},
        )
