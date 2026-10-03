from __future__ import annotations

import inspect
import logging
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4

import flet as ft

from eventseat.config import asset_path
from eventseat.domain import AppError, PriceChanged

BG = "#F4F6F8"
WHITE = "#FFFFFF"
INK = "#182638"
MUTED = "#6D7B8C"
TEAL = "#087F8C"
LINE = "#DDE4EB"
NAVY = "#142C43"
RED = "#B4424A"
CATEGORIES = ["кино", "концерт", "спектакль", "лекция", "другое"]
SEAT_CATEGORIES = ["эконом", "стандарт", "VIP"]
CATEGORY_COLORS = {"эконом": "#D6E9E6", "стандарт": "#B4D5EC", "VIP": "#E5D4FA"}


def money(value):
    return f"{int(value) / 100:,.2f}".replace(",", " ").replace(".", ",") + " ₽"


def rubles(value):
    try:
        number = Decimal(str(value).replace(",", ".").replace(" ", ""))
        if not number.is_finite() or number < 0 or number != number.quantize(Decimal("0.01")):
            raise ValueError
        return int(number * 100)
    except (InvalidOperation, ValueError):
        raise AppError(
            "Укажите неотрицательную цену в рублях, не более двух знаков после запятой."
        ) from None


def date_text(value):
    return value.strftime("%d.%m.%Y · %H:%M") if value else "Сеансы не назначены"


def text(value, size=14, color=INK, bold=False, **kwargs):
    return ft.Text(
        str(value),
        size=size,
        color=color,
        weight=ft.FontWeight.W_600 if bold else ft.FontWeight.W_400,
        **kwargs,
    )


def field(label, value="", **kwargs):
    return ft.TextField(
        label=label, value=str(value), filled=True, fill_color=WHITE, text_size=14, **kwargs
    )


def select(label, options, value=None, **kwargs):
    return ft.Dropdown(
        label=label,
        value=value,
        options=[
            ft.DropdownOption(key=str(k), text=str(v))
            if isinstance(item, tuple)
            else ft.DropdownOption(key=str(item), text=str(item))
            for item in options
            for k, v in [item if isinstance(item, tuple) else (item, item)]
        ],
        filled=True,
        fill_color=WHITE,
        text_size=14,
        **kwargs,
    )


def panel(*controls, **kwargs):
    return ft.Container(
        ft.Column(list(controls), spacing=16),
        bgcolor=WHITE,
        padding=24,
        border_radius=18,
        border=ft.Border.all(1, LINE),
        **kwargs,
    )


def tag(label, color=TEAL, background="#E7F3F2"):
    return ft.Container(
        text(label, 12, color, True),
        bgcolor=background,
        border_radius=8,
        padding=ft.Padding.symmetric(horizontal=10, vertical=6),
    )


class App:
    def __init__(self, page, service):
        self.page = page
        self.service = service
        self.section = "Афиша"
        self.checkout_key = str(uuid4())
        self.busy_checkout = False
        self.picker = ft.FilePicker()
        self.page.services.append(self.picker)
        self.content = ft.Column(expand=True, spacing=20, scroll=ft.ScrollMode.AUTO)
        self.nav = ft.Column(spacing=7)

    def start(self):
        self.page.title = "EventSeat — события, которые запомнятся"
        self.page.bgcolor = BG
        self.page.padding = 0
        self.page.spacing = 0
        self.page.theme_mode = ft.ThemeMode.LIGHT
        self.page.theme = ft.Theme(
            color_scheme_seed=TEAL, font_family="Segoe UI", use_material3=True, scaffold_bgcolor=BG
        )
        self.page.window.width = 1280
        self.page.window.height = 850
        self.page.window.min_width = 1000
        self.page.window.min_height = 720
        self.auth("setup" if self.service.needs_setup() else "login")

    def safe(self, action):
        async def callback(e=None):
            try:
                result = action(e)
                if inspect.isawaitable(result):
                    await result
            except AppError as error:
                self.notice(str(error), error=True)
            except (ValueError, TypeError) as error:
                logging.getLogger(__name__).warning("Invalid input: %s", error)
                self.notice(
                    "Проверьте введённые значения. Дата: ДД.ММ.ГГГГ, время: ЧЧ:ММ; числа — без букв.",
                    error=True,
                )
            except Exception:
                logging.getLogger(__name__).exception("UI action failed")
                self.notice(
                    "Не удалось выполнить действие. Данные не потеряны. Повторите попытку; подробности записаны в журнал приложения.",
                    error=True,
                )

        return callback

    def button(self, label, action, icon=None, secondary=False, **kwargs):
        button_type = ft.OutlinedButton if secondary else ft.Button
        return button_type(
            content=label,
            icon=icon,
            on_click=self.safe(action),
            height=43,
            **({} if secondary else {"bgcolor": TEAL, "color": WHITE}),
            **kwargs,
        )

    def notice(self, message, error=False):
        self.page.show_dialog(
            ft.SnackBar(
                content=text(message, color=WHITE), bgcolor=RED if error else NAVY, duration=6000
            )
        )

    def dialog(self, title, controls, actions=None, width=540):
        dlg = ft.AlertDialog(
            title=text(title, 24, bold=True),
            modal=True,
            content=ft.Column(
                controls, tight=True, spacing=16, width=width, scroll=ft.ScrollMode.AUTO
            ),
            actions=actions
            or [self.button("Закрыть", lambda _: self.page.pop_dialog(), secondary=True)],
            scrollable=True,
        )
        self.page.show_dialog(dlg)
        return dlg

    def confirm(self, title, message, action, reason=False):
        reason_field = field("Причина отмены", multiline=True, min_lines=2)

        def apply(_):
            if reason and not reason_field.value.strip():
                raise AppError("Укажите причину отмены.")
            action(reason_field.value.strip() if reason else None)
            self.page.pop_dialog()

        self.dialog(
            title,
            [text(message), *([reason_field] if reason else [])],
            [
                self.button("Назад", lambda _: self.page.pop_dialog(), secondary=True),
                self.button("Подтвердить", apply),
            ],
        )

    def heading(self, title, subtitle="", actions=None):
        return ft.Row(
            [
                ft.Column(
                    [text(title, 30, bold=True), text(subtitle, 14, MUTED)], spacing=6, expand=True
                ),
                *(actions or []),
            ],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def empty(self, title, subtitle):
        return panel(
            ft.Icon(ft.Icons.EVENT_SEAT_OUTLINED, size=44, color=TEAL),
            text(title, 21, bold=True),
            text(subtitle, color=MUTED),
        )

    def show(self, *controls):
        self.content.controls = list(controls)
        self.page.update()

    def auth(self, mode="login"):
        setup, register = mode == "setup", mode == "register"
        login = field("Логин", autofocus=True, max_length=64)
        name = field("Отображаемое имя", max_length=100)
        password = field(
            "Пароль",
            password=True,
            can_reveal_password=True,
            helper="Не менее 8 символов" if setup or register else None,
        )
        repeat = field("Повторите пароль", password=True, can_reveal_password=True)
        title = "Добро пожаловать" if setup else "Создать аккаунт" if register else "С возвращением"

        def submit(_):
            if setup or register:
                if password.value != repeat.value:
                    raise AppError("Пароли не совпадают.")
                if setup:
                    self.service.setup_admin(login.value, name.value, password.value)
                else:
                    self.service.register(login.value, name.value, password.value)
                self.service.login(login.value, password.value)
            else:
                self.service.login(login.value, password.value)
            self.section = "Афиша"
            self.shell()
            self.catalogue()

        password.on_submit = self.safe(submit)
        repeat.on_submit = self.safe(submit)
        form = [
            tag("ПЕРВЫЙ ЗАПУСК" if setup else "ВАШ ЛИЧНЫЙ КАБИНЕТ"),
            text(title, 30, bold=True),
            text(
                "Создайте администратора — он управляет залами, афишей и сеансами."
                if setup
                else "Выберите событие. Найдите своё место. Сохраните впечатления.",
                color=MUTED,
            ),
            login,
            *([name] if setup or register else []),
            password,
            *([repeat] if setup or register else []),
            self.button(
                "Создать администратора"
                if setup
                else "Зарегистрироваться"
                if register
                else "Войти",
                submit,
                icon=ft.Icons.ARROW_FORWARD,
                width=370,
            ),
        ]
        if not setup:
            form += [
                ft.TextButton(
                    "Уже есть аккаунт? Войти" if register else "Нет аккаунта? Зарегистрироваться",
                    on_click=self.safe(lambda _: self.auth("login" if register else "register")),
                )
            ]
        visual = ft.Container(
            ft.Column(
                [
                    ft.Row(
                        [
                            ft.Icon(ft.Icons.EVENT_SEAT_ROUNDED, color="#58D2C6", size=36),
                            text("EventSeat", 28, WHITE, True),
                        ]
                    ),
                    ft.Container(height=50),
                    text("Ваше место\nсреди впечатлений.", 43, WHITE, True),
                    text(
                        "Кино, музыка, театр и идеи.\nОдин билет — начало новой истории.",
                        19,
                        "#B9CAD8",
                    ),
                    ft.Container(height=30),
                    ft.Row(
                        [
                            ft.Container(width=34, height=32, bgcolor=c, border_radius=9)
                            for c in ["#28465E", "#28465E", "#58D2C6", "#28465E", "#28465E"]
                        ],
                        spacing=14,
                    ),
                    ft.Container(height=25),
                    tag("ЛОКАЛЬНО · БЕЗ ОПЛАТЫ ОНЛАЙН", "#B9E4E1", "#25475A"),
                ],
                spacing=20,
            ),
            bgcolor=NAVY,
            padding=50,
            expand=1,
        )
        self.page.controls = [
            ft.Row(
                [
                    visual,
                    ft.Container(
                        ft.Column(form, spacing=15, width=370, scroll=ft.ScrollMode.AUTO),
                        alignment=ft.Alignment.CENTER,
                        padding=40,
                        expand=1,
                    ),
                ],
                expand=True,
                spacing=0,
                vertical_alignment=ft.CrossAxisAlignment.STRETCH,
            )
        ]
        self.page.update()

    def shell(self):
        user = self.service.current_user
        entries = [
            ("Афиша", ft.Icons.GRID_VIEW_ROUNDED, self.catalogue),
            ("Корзина", ft.Icons.SHOPPING_BAG_OUTLINED, self.cart),
            ("Мои бронирования", ft.Icons.CONFIRMATION_NUMBER_OUTLINED, self.bookings),
            ("Профиль", ft.Icons.PERSON_OUTLINE, self.profile),
        ]
        if user["role"] == "admin":
            entries.append(("Администрирование", ft.Icons.TUNE_ROUNDED, self.admin))
        self.nav.controls = [
            ft.Container(
                ft.Row([ft.Icon(icon, size=21, color=WHITE), text(label, 13, WHITE)], spacing=13),
                padding=14,
                border_radius=10,
                bgcolor="#28475D" if label == self.section else None,
                on_click=self.safe(
                    lambda _, action=action, label=label: self.navigate(label, action)
                ),
            )
            for label, icon, action in entries
        ]
        sidebar = ft.Container(
            ft.Column(
                [
                    ft.Row(
                        [
                            ft.Icon(ft.Icons.EVENT_SEAT_ROUNDED, color="#58D2C6", size=27),
                            text("EventSeat", 24, WHITE, True),
                        ],
                        spacing=10,
                    ),
                    text("БОЛЬШЕ, ЧЕМ БИЛЕТ", 10, "#8FA9BB"),
                    ft.Container(height=24),
                    self.nav,
                    ft.Container(expand=True),
                    ft.Container(
                        ft.Column(
                            [
                                text(user["name"], 14, WHITE, True, max_lines=2),
                                text(
                                    "Администратор"
                                    if user["role"] == "admin"
                                    else "Личный кабинет",
                                    12,
                                    "#8FA9BB",
                                ),
                            ]
                        ),
                        padding=12,
                    ),
                    ft.TextButton(
                        "Выйти из аккаунта",
                        icon=ft.Icons.LOGOUT,
                        on_click=self.safe(lambda _: self.logout()),
                        style=ft.ButtonStyle(color="#C4D6E2"),
                    ),
                    text("v1.0 · Локальное приложение", 10, "#8FA9BB"),
                ],
                spacing=10,
                expand=True,
            ),
            bgcolor=NAVY,
            width=238,
            padding=24,
        )
        self.page.controls = [
            ft.Row(
                [sidebar, ft.Container(self.content, padding=30, expand=True)],
                spacing=0,
                expand=True,
                vertical_alignment=ft.CrossAxisAlignment.STRETCH,
            )
        ]
        self.page.update()

    def navigate(self, label, action):
        self.section = label
        self.shell()
        action()

    def logout(self):
        self.service.logout()
        self.checkout_key = str(uuid4())
        self.auth()

    def cover(self, cover_path, width=270, height=174):
        fallback = ft.Container(
            ft.Icon(ft.Icons.LOCAL_ACTIVITY_OUTLINED, color="#7FABB3", size=48),
            bgcolor="#E4EEF1",
            alignment=ft.Alignment.CENTER,
            width=width,
            height=height,
        )
        if not cover_path:
            return fallback
        paths = [
            Path(cover_path),
            Path(asset_path(cover_path)),
            Path(asset_path("covers/" + cover_path)),
        ]
        source = next((path for path in paths if path.is_file()), None)
        if not source:
            return fallback
        return ft.Image(
            src=source.read_bytes(),
            width=width,
            height=height,
            fit=ft.BoxFit.COVER,
            error_content=fallback,
            border_radius=12,
        )

    def catalogue(self, search="", category="", day=""):
        search_field = field("Найти событие", search, prefix_icon=ft.Icons.SEARCH, width=285)
        category_field = select(
            "Категория", [("", "Все категории"), *CATEGORIES], category, width=200
        )
        date_field = field("Дата · ДД.ММ.ГГГГ", day, width=200)

        def apply(_):
            self.catalogue(
                search_field.value.strip(), category_field.value or "", date_field.value.strip()
            )

        search_field.on_submit = self.safe(apply)
        date_field.on_submit = self.safe(apply)
        date_filter = datetime.strptime(day, "%d.%m.%Y").date() if day else None
        events = self.service.list_events(search, category, date_filter)
        cards = []
        for event in events:
            cards.append(
                ft.Container(
                    ft.Column(
                        [
                            self.cover(event.get("cover_path", "")),
                            ft.Container(
                                ft.Column(
                                    [
                                        tag(event["category"].upper()),
                                        text(
                                            event["title"],
                                            20,
                                            bold=True,
                                            max_lines=2,
                                            overflow=ft.TextOverflow.ELLIPSIS,
                                        ),
                                        text(date_text(event.get("next_start")), 13, MUTED),
                                        ft.Row(
                                            [
                                                text(
                                                    "от " + money(event.get("min_price") or 0),
                                                    16,
                                                    bold=True,
                                                ),
                                                ft.Container(expand=True),
                                                ft.Icon(
                                                    ft.Icons.ARROW_FORWARD, color=TEAL, size=20
                                                ),
                                            ]
                                        ),
                                    ],
                                    spacing=12,
                                ),
                                padding=ft.Padding.only(left=16, right=16, bottom=18),
                            ),
                        ],
                        spacing=15,
                    ),
                    width=270,
                    bgcolor=WHITE,
                    border_radius=16,
                    border=ft.Border.all(1, LINE),
                    on_click=self.safe(lambda _, eid=event["id"]: self.event_detail(eid)),
                )
            )
        banner = ft.Container(
            ft.Row(
                [
                    ft.Column(
                        [
                            tag("АФИША ВПЕЧАТЛЕНИЙ", "#BEF0E7", "#2C5362"),
                            text("Хорошие планы начинаются здесь", 27, WHITE, True),
                            text("Выберите событие и места, которые вам по душе.", 14, "#BFD4DF"),
                        ],
                        spacing=12,
                        expand=True,
                    ),
                    ft.Icon(ft.Icons.EVENT_AVAILABLE_ROUNDED, color="#70D7C7", size=64),
                ]
            ),
            bgcolor=NAVY,
            border_radius=20,
            padding=28,
        )
        self.show(
            self.heading("Афиша", "Откройте для себя следующее событие"),
            banner,
            ft.Row(
                [search_field, category_field, date_field, self.button("Найти", apply)], wrap=True
            ),
            ft.Row(
                [
                    text(f"Событий: {len(events)}", 15, bold=True),
                    ft.TextButton(
                        "Сбросить фильтры", on_click=self.safe(lambda _: self.catalogue())
                    ),
                ]
            ),
            ft.Row(cards, wrap=True, spacing=20, run_spacing=20)
            if cards
            else self.empty("Пока нет событий", "Измените фильтры или вернитесь позже."),
        )

    def event_detail(self, event_id):
        event = self.service.get_event(event_id)
        sessions = self.service.list_sessions(event_id)
        controls = [
            self.button(
                "К афише", lambda _: self.catalogue(), icon=ft.Icons.ARROW_BACK, secondary=True
            ),
            ft.Row(
                [
                    self.cover(event.get("cover_path", ""), 290, 210),
                    ft.Column(
                        [
                            tag(event["category"].upper()),
                            text(event["title"], 30, bold=True),
                            text(f"{event['duration']} минут", color=MUTED),
                            text(event["description"], 15),
                        ],
                        spacing=14,
                        expand=True,
                    ),
                ],
                spacing=28,
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
            text("Выберите сеанс", 23, bold=True),
        ]
        for session in sessions:
            controls.append(
                panel(
                    ft.Row(
                        [
                            ft.Column(
                                [
                                    text(date_text(session["start"]), 19, bold=True),
                                    text(
                                        f"{session['hall_name']} · {session['free_count']} свободных из {session['total_count']}",
                                        color=MUTED,
                                    ),
                                ],
                                expand=True,
                            ),
                            text("от " + money(session.get("min_price") or 0), 17, bold=True),
                            self.button(
                                "Выбрать места",
                                lambda _, sid=session["id"]: self.seats(sid),
                                disabled=session["free_count"] == 0,
                            ),
                        ]
                    )
                )
            )
        if not sessions:
            controls.append(
                self.empty(
                    "Нет доступных сеансов", "Новые даты появятся после публикации расписания."
                )
            )
        self.show(*controls)

    def seats(self, session_id, admin=False):
        session = self.service.get_session(session_id)
        seats = self.service.seat_map(session_id)
        selected = set()
        tariff = select(
            "Тариф",
            [("standard", "Обычный"), ("concession", "Учебный льготный · −20%")],
            "standard",
            width=275,
        )
        selection = ft.Column(spacing=10)
        total = text("0,00 ₽", 27, bold=True)
        cells = {}

        def price(seat):
            from eventseat.domain import calculate_ticket_price

            return calculate_ticket_price(seat["price"], tariff.value)

        def repaint(_=None):
            selection.controls = [
                text(f"Ряд {s['row']} · место {s['number']}\n{s['category']} · {money(price(s))}")
                for s in seats
                if s["id"] in selected
            ]
            if not selection.controls:
                selection.controls = [text("Нажмите на свободные места на схеме.", color=MUTED)]
            total.value = money(sum(price(s) for s in seats if s["id"] in selected))
            for seat in seats:
                cells[seat["id"]].bgcolor = (
                    TEAL if seat["id"] in selected else self.seat_color(seat)
                )
                cells[seat["id"]].content.color = WHITE if seat["id"] in selected else INK
            self.page.update()

        def toggle(seat):
            if admin:
                if seat["status"] == "booked":
                    raise AppError(
                        "Занятое место нельзя закрыть. При необходимости отмените бронирование."
                    )
                self.service.set_seat_closed(session_id, seat["id"], seat["status"] != "closed")
                self.seats(session_id, True)
                return
            if seat["status"] != "free":
                raise AppError("Это место недоступно для бронирования.")
            selected.symmetric_difference_update({seat["id"]})
            repaint()

        rows = defaultdict(list)
        for position in session.get("layout", seats):
            rows[position["row"]].append(position)
        mapping = {(seat["row"], seat["number"]): seat for seat in seats}
        grid_rows = []
        for row, items in sorted(rows.items()):
            row_cells = [ft.Container(text(f"Ряд {row}", 11, MUTED), width=58)]
            for position in sorted(items, key=lambda p: p["number"]):
                number = position["number"]
                seat = mapping.get((row, number))
                if seat is None:
                    row_cells.append(ft.Container(width=38, height=36))
                    continue
                cell = ft.Container(
                    text(number, 12, bold=True),
                    width=38,
                    height=36,
                    border_radius=9,
                    alignment=ft.Alignment.CENTER,
                    bgcolor=self.seat_color(seat),
                    tooltip=f"Ряд {row}, место {number} · {seat['category']} · {money(seat['price'])}",
                    on_click=self.safe(lambda _, s=seat: toggle(s)),
                )
                cells[seat["id"]] = cell
                row_cells.append(cell)
            grid_rows.append(ft.Row(row_cells, spacing=7))
        tariff.on_select = self.safe(repaint)

        def add(_):
            if not selected:
                raise AppError("Выберите хотя бы одно место.")
            self.service.add_to_cart(session_id, sorted(selected), tariff.value)
            self.checkout_key = str(uuid4())
            self.navigate("Корзина", self.cart)
            self.notice("Места добавлены в корзину. Подтвердите бронирование, чтобы закрепить их.")

        legend = ft.Row(
            [
                ft.Row(
                    [
                        ft.Container(width=15, height=15, bgcolor=color, border_radius=4),
                        text(label, 12, MUTED),
                    ],
                    spacing=6,
                    tight=True,
                )
                for label, color in [
                    ("Эконом", CATEGORY_COLORS["эконом"]),
                    ("Стандарт", CATEGORY_COLORS["стандарт"]),
                    ("VIP", CATEGORY_COLORS["VIP"]),
                    ("Выбрано", TEAL),
                    ("Занято", "#C8CDD4"),
                    ("Закрыто", "#EAB9B9"),
                ]
            ],
            wrap=True,
            spacing=16,
        )
        stage = session.get("stage", "СЦЕНА / ЭКРАН")
        grid = panel(
            ft.Container(
                text(stage, 12, MUTED, True),
                height=36,
                bgcolor=BG,
                border_radius=8,
                alignment=ft.Alignment.CENTER,
            ),
            ft.Row([ft.Column(grid_rows, spacing=9)], scroll=ft.ScrollMode.ALWAYS),
            legend,
            col={"sm": 12, "lg": 8},
        )
        sidebar = panel(
            text("Управление местами" if admin else "Ваш выбор", 20, bold=True),
            *(
                [
                    text(
                        "Нажмите свободное место, чтобы закрыть его. Нажмите закрытое — чтобы открыть.",
                        color=MUTED,
                    ),
                    text(
                        f"Свободно: {session['free_count']} / {session['total_count']}",
                        18,
                        bold=True,
                    ),
                ]
                if admin
                else [
                    tariff,
                    text("Учебная скидка 20%. Подтверждение льготы не требуется.", 12, MUTED),
                    selection,
                    ft.Divider(color=LINE),
                    total,
                    self.button("Добавить в корзину", add),
                    text(
                        "В корзине места не удерживаются. Бронирование подтверждается без оплаты.",
                        12,
                        MUTED,
                    ),
                ]
            ),
            col={"sm": 12, "lg": 4},
        )
        self.show(
            self.button(
                "К сеансам" if admin else "К мероприятию",
                lambda _: self.admin("Сеансы") if admin else self.event_detail(session["event_id"]),
                icon=ft.Icons.ARROW_BACK,
                secondary=True,
            ),
            self.heading(
                session["title"], f"{date_text(session['start'])} · {session['hall_name']}"
            ),
            ft.ResponsiveRow([grid, sidebar], spacing=20, run_spacing=20),
        )
        if not admin:
            repaint()

    @staticmethod
    def seat_color(seat):
        return {"booked": "#C8CDD4", "closed": "#EAB9B9"}.get(
            seat["status"], CATEGORY_COLORS[seat["category"]]
        )

    def cart(self):
        items = self.service.get_cart()
        groups = defaultdict(list)
        for item in items:
            groups[item["session_id"]].append(item)
        controls = [self.heading("Корзина", "Места закрепляются только после подтверждения")]
        if not items:
            controls.append(
                self.empty(
                    "Ваша корзина пока пуста",
                    "Выберите событие в афише и добавьте понравившиеся места.",
                )
            )
        for group in groups.values():
            first = group[0]
            rows = [
                ft.Row(
                    [
                        ft.Column(
                            [
                                text(f"Ряд {item['row']} · место {item['number']}", 15, bold=True),
                                text(
                                    f"{item['category']} · {'льготный −20%' if item['tariff'] == 'concession' else 'обычный'}",
                                    12,
                                    MUTED,
                                ),
                                *(
                                    []
                                    if item["available"]
                                    else [text("Место недоступно — удалите из корзины", 12, RED)]
                                ),
                            ],
                            expand=True,
                        ),
                        text(money(item["price"]), 16, bold=True),
                        ft.IconButton(
                            icon=ft.Icons.DELETE_OUTLINE,
                            tooltip="Удалить из корзины",
                            on_click=self.safe(lambda _, iid=item["id"]: self.remove_item(iid)),
                        ),
                    ]
                )
                for item in group
            ]
            controls.append(
                panel(
                    text(first["title"], 21, bold=True),
                    text(f"{date_text(first['start'])} · {first['hall_name']}", color=MUTED),
                    *rows,
                )
            )
        if items:
            controls.append(
                panel(
                    ft.Row(
                        [
                            ft.Column(
                                [
                                    text(f"Билетов: {len(items)}", color=MUTED),
                                    text(
                                        money(sum(item["price"] for item in items)), 29, bold=True
                                    ),
                                ],
                                expand=True,
                            ),
                            self.button(
                                "Подтвердить бронирование",
                                lambda _: self.checkout(),
                                icon=ft.Icons.CHECK_CIRCLE_OUTLINE,
                                disabled=self.busy_checkout,
                            ),
                        ]
                    ),
                    text(
                        "Это бронирование, а не оплата. Доступность и цены будут проверены повторно.",
                        13,
                        MUTED,
                    ),
                )
            )
        self.show(*controls)

    def remove_item(self, item_id):
        self.service.remove_cart_item(item_id)
        self.checkout_key = str(uuid4())
        self.cart()

    def checkout(self):
        if self.busy_checkout:
            return
        self.busy_checkout = True
        try:
            result = self.service.checkout(self.checkout_key)
        except PriceChanged as error:
            self.checkout_key = str(uuid4())
            self.busy_checkout = False
            self.cart()
            self.dialog(
                "Цены изменились",
                [
                    text(str(error)),
                    text(
                        "Корзина пересчитана. Проверьте новую сумму и нажмите «Подтвердить бронирование» ещё раз."
                    ),
                ],
            )
            return
        except AppError:
            self.busy_checkout = False
            self.cart()
            raise
        finally:
            self.busy_checkout = False
        self.checkout_key = str(uuid4())
        self.navigate("Мои бронирования", self.bookings)
        self.notice(f"Бронирование подтверждено. Оформлено заказов: {len(result)}.")

    def bookings(self, admin=False, search=""):
        bookings = self.service.list_bookings(search=search, admin=admin)
        controls = [
            self.heading(
                "Все бронирования" if admin else "Мои бронирования",
                "Электронные билеты и история посещений",
            )
        ]
        if admin:
            query = field("Поиск по номеру, имени или логину", search, width=420)
            query.on_submit = self.safe(lambda _: self.bookings(True, query.value))
            controls.append(
                ft.Row([query, self.button("Найти", lambda _: self.bookings(True, query.value))])
            )
        for booking in bookings:
            status = {"active": "Активно", "cancelled": "Отменено", "completed": "Завершено"}[
                booking["status"]
            ]
            actions = [
                self.button(
                    "Электронный билет",
                    lambda _, bid=booking["id"]: self.ticket(bid),
                    secondary=True,
                )
            ]
            if booking["status"] == "active" and booking["start"] > datetime.now():
                actions.append(
                    self.button(
                        "Отменить",
                        lambda _, b=booking: self.confirm(
                            "Отменить бронирование?",
                            f"Бронирование {b['number']}. Все места этого заказа будут освобождены.",
                            lambda reason: self.cancel_booking(b["id"], reason, admin),
                            reason=admin,
                        ),
                        secondary=True,
                    )
                )
            controls.append(
                panel(
                    ft.Row(
                        [
                            tag(
                                status,
                                RED if booking["status"] == "cancelled" else TEAL,
                                "#F8E9E9" if booking["status"] == "cancelled" else "#E7F3F2",
                            ),
                            text(booking["number"], 13, MUTED, selectable=True),
                            ft.Container(expand=True),
                            text(money(booking["total"]), 20, bold=True),
                        ]
                    ),
                    text(booking["title"], 23, bold=True),
                    text(f"{date_text(booking['start'])} · {booking['hall_name']}", color=MUTED),
                    text(
                        " · ".join(
                            f"ряд {ticket['row']}, место {ticket['number']}"
                            for ticket in booking["tickets"]
                        )
                    ),
                    *([text("Владелец: " + booking["user_name"], color=MUTED)] if admin else []),
                    *(
                        [text("Причина отмены: " + booking["cancel_reason"], color=RED)]
                        if booking.get("cancel_reason")
                        else []
                    ),
                    ft.Row(actions, wrap=True),
                )
            )
        if not bookings:
            controls.append(
                self.empty("Бронирований пока нет", "Ваши подтверждённые билеты появятся здесь.")
            )
        self.show(*controls)

    def cancel_booking(self, booking_id, reason, admin):
        self.service.cancel_booking(booking_id, reason or "Отменено пользователем")
        self.bookings(admin)

    def ticket(self, booking_id):
        booking = self.service.get_booking(booking_id)
        self.dialog(
            "Электронный билет",
            [
                tag("EVENTSEAT · БРОНИРОВАНИЕ БЕЗ ОПЛАТЫ"),
                text(booking["number"], 26, bold=True, selectable=True),
                text(booking["title"], 23, bold=True),
                text(date_text(booking["start"]), 18),
                text(booking["hall_name"], 18),
                text("Владелец: " + booking["user_name"]),
                ft.Divider(color=LINE),
                *[
                    text(
                        f"Ряд {t['row']} · место {t['number']} · {t['category']}\n"
                        f"{'Льготный тариф −20%' if t['tariff'] == 'concession' else 'Обычный тариф'} · {money(t['price'])}"
                    )
                    for t in booking["tickets"]
                ],
                ft.Divider(color=LINE),
                text("Стоимость бронирования: " + money(booking["total"]), 18, bold=True),
                text(
                    "Состояние: "
                    + {"active": "активно", "cancelled": "отменено", "completed": "завершено"}[
                        booking["status"]
                    ]
                ),
                *(
                    [text("Причина отмены: " + booking["cancel_reason"], color=RED)]
                    if booking.get("cancel_reason")
                    else []
                ),
            ],
        )

    def profile(self):
        user = self.service.current_user
        name = field("Отображаемое имя", user["name"])
        old = field("Текущий пароль", password=True, can_reveal_password=True)
        new = field("Новый пароль", password=True, can_reveal_password=True)
        repeat = field("Повторите новый пароль", password=True, can_reveal_password=True)

        def save(_):
            if new.value != repeat.value:
                raise AppError("Новые пароли не совпадают.")
            self.service.update_profile(name.value, old.value, new.value)
            self.shell()
            self.profile()
            self.notice("Профиль сохранён.")

        self.show(
            self.heading("Профиль", "Ваши данные и безопасность аккаунта"),
            panel(
                tag("Администратор" if user["role"] == "admin" else "Пользователь"),
                text("Логин: " + user["login"], 18, bold=True),
                name,
                text("Смена пароля", 19, bold=True),
                text("Чтобы сохранить только имя, оставьте поля паролей пустыми.", color=MUTED),
                old,
                new,
                repeat,
                self.button("Сохранить изменения", save),
                width=570,
            ),
        )

    def admin(self, tab="Мероприятия"):
        from eventseat.ui_admin import AdminUI

        AdminUI(self).show(tab)
