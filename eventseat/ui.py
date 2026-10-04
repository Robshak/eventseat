from __future__ import annotations

import asyncio
import inspect
import logging
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4

import flet as ft

from eventseat import __version__
from eventseat.account_sessions import AccountSessions
from eventseat.config import asset_path
from eventseat.domain import AppError, PriceChanged
from eventseat.view_state import AccountViewState, ViewStates

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
        label=label,
        value=str(value),
        filled=True,
        fill_color=WHITE,
        hover_color="#F0F8F7",
        focused_border_color=TEAL,
        focused_border_width=2,
        text_size=14,
        **kwargs,
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


def hoverable(control, background=None):
    """Highlight interactive surfaces without changing a seat's status colour."""
    normal_background = control.bgcolor
    normal_border = control.border or ft.Border.all(1, "#00000000")
    hover_border = ft.Border(
        top=ft.BorderSide(normal_border.top.width, TEAL),
        right=ft.BorderSide(normal_border.right.width, TEAL),
        bottom=ft.BorderSide(normal_border.bottom.width, TEAL),
        left=ft.BorderSide(normal_border.left.width, TEAL),
    )
    control.border = normal_border
    control.ink = True
    control.ink_color = "#18087F8C"
    control.animate = ft.Animation(140, ft.AnimationCurve.EASE_OUT)

    def hover(event):
        active = str(event.data).lower() == "true"
        control.border = hover_border if active else normal_border
        if background is not None:
            control.bgcolor = background if active else normal_background
        control.update()

    control.on_hover = hover
    return control


class App:
    def __init__(self, page, service):
        self.page = page
        self.accounts = AccountSessions(service)
        self._account_generation = 0
        self._view_generation = 0
        self._view_states = ViewStates()
        self._capture = None
        self._capture_user_id = None
        self._route = {}
        self.on_view_blur = None
        self.section = "Афиша"
        self.checkout_key = str(uuid4())
        self.busy_checkout = False
        self.picker = ft.FilePicker()
        self.page.services.append(self.picker)
        self.content = ft.Column(expand=True, spacing=20, scroll=ft.ScrollMode.AUTO)
        self.nav = ft.Column(spacing=7)

    @property
    def service(self):
        return self.accounts.current

    @property
    def view_state(self):
        user = self.service.current_user
        return self._view_states.for_account(user["id"] if user else 0)

    def capture_view(self):
        user = self.service.current_user
        if self._capture and user and user["id"] == self._capture_user_id:
            self._capture()

    def clear_capture(self):
        self._capture = None
        self._capture_user_id = None

    def set_view(self, route, capture=None):
        self.capture_view()
        if self.on_view_blur:
            self.on_view_blur()
        self.on_view_blur = None
        self._capture = capture
        self._capture_user_id = self.service.current_user["id"]
        self._view_generation += 1
        self._route = dict(route)
        section = route["section"]
        changed_section = self.section != section
        self.section = section
        self.view_state.section = section
        self.view_state.routes[section] = dict(route)
        key = AccountViewState.route_key(route)
        state = self.view_state
        self.content.on_scroll = lambda event: state.scroll_offsets.__setitem__(
            key, max(0, event.pixels)
        )
        if changed_section:
            self.shell()

    async def _restore_scroll(self, generation, offset):
        await asyncio.sleep(0.06)
        if generation == self._view_generation and self.content.page:
            await self.content.scroll_to(offset=offset, duration=0)

    def close(self):
        self._view_generation += 1
        self.clear_capture()
        self._view_states.clear()
        self.accounts.close()

    def start(self):
        self.page.title = "EventSeat — события, которые запомнятся"
        self.page.bgcolor = BG
        self.page.padding = 0
        self.page.spacing = 0
        self.page.theme_mode = ft.ThemeMode.LIGHT
        link_style = ft.ButtonStyle(
            color=TEAL,
            overlay_color={
                ft.ControlState.HOVERED: "#22087F8C",
                ft.ControlState.FOCUSED: "#33087F8C",
                ft.ControlState.PRESSED: "#44087F8C",
            },
            side={ft.ControlState.FOCUSED: ft.BorderSide(2, TEAL)},
            mouse_cursor=ft.MouseCursor.CLICK,
        )
        self.page.theme = ft.Theme(
            color_scheme_seed=TEAL,
            font_family="Segoe UI",
            use_material3=True,
            scaffold_bgcolor=BG,
            text_button_theme=ft.TextButtonTheme(style=link_style),
            icon_button_theme=ft.IconButtonTheme(style=link_style),
        )
        self.page.locale_configuration = ft.LocaleConfiguration(
            supported_locales=[ft.Locale("ru", "RU"), ft.Locale("en", "US")],
            current_locale=ft.Locale("ru", "RU"),
        )
        self.page.window.width = 1280
        self.page.window.height = 850
        self.page.window.min_width = 1000
        self.page.window.min_height = 720

        def window_event(event):
            if event.type in (ft.WindowEventType.BLUR, ft.WindowEventType.HIDE):
                if self.on_view_blur:
                    self.on_view_blur()

        self.page.window.on_event = window_event
        self.auth("setup" if self.service.needs_setup() else "login")

    def safe(self, action):
        generation = self._account_generation

        async def callback(e=None):
            if generation != self._account_generation:
                return
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
        style = ft.ButtonStyle(
            bgcolor={
                ft.ControlState.DISABLED: "#E2E7EA",
                ft.ControlState.HOVERED: "#E4F3F1" if secondary else "#066875",
                ft.ControlState.FOCUSED: "#D4ECE8" if secondary else "#065B67",
                ft.ControlState.PRESSED: "#CCE7E2" if secondary else "#044B55",
                ft.ControlState.DEFAULT: WHITE if secondary else TEAL,
            },
            color={
                ft.ControlState.DISABLED: "#87939E",
                ft.ControlState.DEFAULT: TEAL if secondary else WHITE,
            },
            side={
                ft.ControlState.FOCUSED: ft.BorderSide(2, INK),
                ft.ControlState.DEFAULT: ft.BorderSide(1, LINE if secondary else TEAL),
            },
            animation_duration=140,
            mouse_cursor=ft.MouseCursor.CLICK,
        )
        return button_type(
            content=label,
            icon=icon,
            on_click=self.safe(action),
            height=43,
            style=style,
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
        if self.service.current_user and not self._route.get("focus_session_id"):
            offset = self.view_state.scroll_offsets.get(AccountViewState.route_key(self._route), 0)
            self.page.run_task(self._restore_scroll, self._view_generation, offset)

    def date_field(self, label, value="", width=200):
        target = field(label, value, width=width)

        def calendar(_):
            try:
                selected = datetime.strptime(target.value, "%d.%m.%Y") if target.value else None
            except ValueError:
                selected = None

            def changed(event):
                if event.control.value:
                    target.value = event.control.value.strftime("%d.%m.%Y")
                    target.update()

            self.page.show_dialog(
                ft.DatePicker(
                    value=selected,
                    first_date=datetime(2000, 1, 1),
                    last_date=datetime(2100, 12, 31),
                    on_change=self.safe(changed),
                    help_text=label,
                    cancel_text="Отмена",
                    confirm_text="Выбрать",
                    field_label_text="Дата",
                    field_hint_text="ДД.ММ.ГГГГ",
                )
            )

        target.suffix_icon = ft.IconButton(
            icon=ft.Icons.CALENDAR_MONTH_OUTLINED,
            tooltip="Выбрать дату: " + label,
            on_click=self.safe(calendar),
        )
        return target

    def auth(self, mode="login"):
        if self.service.current_user is not None:
            self.accounts.begin_login()
            self.reset_account_view()
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
            self.accounts.remember_current()
            self.activate_account()

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
        if self.accounts.can_return:
            form.append(
                self.button(
                    "Вернуться в аккаунт",
                    lambda _: self.cancel_add_account(),
                    icon=ft.Icons.ARROW_BACK,
                    secondary=True,
                )
            )
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
        entries = [("Афиша", ft.Icons.GRID_VIEW_ROUNDED, self.catalogue)]
        if user["role"] == "admin":
            entries.append(("Администрирование", ft.Icons.TUNE_ROUNDED, self.admin))
        else:
            entries += [
                ("Корзина", ft.Icons.SHOPPING_BAG_OUTLINED, self.cart),
                ("Мои бронирования", ft.Icons.CONFIRMATION_NUMBER_OUTLINED, self.bookings),
            ]
        entries.append(("Профиль", ft.Icons.PERSON_OUTLINE, self.profile))
        self.nav.controls = [
            hoverable(
                ft.Container(
                    ft.Row(
                        [ft.Icon(icon, size=21, color=WHITE), text(label, 13, WHITE)], spacing=13
                    ),
                    padding=14,
                    border_radius=10,
                    bgcolor="#28475D" if label == self.section else NAVY,
                    on_click=self.safe(
                        lambda _, label=label: self.navigate(
                            label, lambda: self.restore_section(label)
                        )
                    ),
                ),
                background="#35566D",
            )
            for label, icon, action in entries
        ]
        account_selector = ft.PopupMenuButton(
            content=ft.Container(
                ft.Column(
                    [
                        text("Аккаунты", 11, "#B9CAD8"),
                        ft.Row(
                            [
                                text(
                                    "@" + user["login"],
                                    13,
                                    WHITE,
                                    True,
                                    expand=True,
                                    max_lines=1,
                                    overflow=ft.TextOverflow.ELLIPSIS,
                                ),
                                ft.Icon(ft.Icons.UNFOLD_MORE, color="#91DAD3", size=19),
                            ]
                        ),
                    ],
                    spacing=5,
                ),
                bgcolor="#203B53",
                border=ft.Border.all(1, "#66869D"),
                border_radius=10,
                padding=12,
            ),
            items=[
                ft.PopupMenuItem(
                    content=ft.Row(
                        [
                            ft.Icon(
                                ft.Icons.CHECK_CIRCLE_OUTLINE
                                if account["id"] == user["id"]
                                else ft.Icons.PERSON_OUTLINE,
                                color="#91DAD3",
                                size=22,
                            ),
                            ft.Column(
                                [
                                    text(
                                        account["name"],
                                        14,
                                        WHITE,
                                        True,
                                        max_lines=1,
                                        overflow=ft.TextOverflow.ELLIPSIS,
                                    ),
                                    text(
                                        "@"
                                        + account["login"]
                                        + (
                                            " · Администратор"
                                            if account["role"] == "admin"
                                            else " · Пользователь"
                                        ),
                                        12,
                                        "#C7D8E4",
                                        max_lines=1,
                                        overflow=ft.TextOverflow.ELLIPSIS,
                                    ),
                                ],
                                spacing=3,
                                expand=True,
                            ),
                        ],
                        spacing=12,
                    ),
                    height=68,
                    padding=12,
                    data={"account_id": account["id"]},
                    on_click=self.safe(lambda _, uid=account["id"]: self.switch_account(uid)),
                )
                for account in self.accounts.users
            ],
            bgcolor=NAVY,
            padding=0,
            menu_padding=6,
            shape=ft.RoundedRectangleBorder(radius=12, side=ft.BorderSide(1, "#66869D")),
            size_constraints=ft.BoxConstraints(min_width=300, max_width=380, max_height=400),
            tooltip="Переключить аккаунт",
            data="account-switcher",
        )
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
                    text(
                        user["name"],
                        14,
                        WHITE,
                        True,
                        max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                    account_selector,
                    ft.TextButton(
                        "Добавить аккаунт",
                        icon=ft.Icons.PERSON_ADD_ALT_1,
                        on_click=self.safe(lambda _: self.add_account()),
                        style=ft.ButtonStyle(
                            color="#C4D6E2",
                            overlay_color={
                                ft.ControlState.HOVERED: "#35566D",
                                ft.ControlState.FOCUSED: "#35566D",
                            },
                        ),
                    ),
                    text(f"v{__version__} · Локальное приложение", 10, "#8FA9BB"),
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
        self.capture_view()
        self.clear_capture()
        self.section = label
        self.shell()
        action()

    def logout(self):
        user_id = self.service.current_user["id"]
        self.clear_capture()
        self._view_states.forget(user_id)
        self.accounts.logout()
        self.reset_account_view()
        if self.service.current_user:
            self.shell()
            self.restore_section(self.view_state.section)
        else:
            self.auth()

    def reset_account_view(self):
        self.clear_capture()
        if self.on_view_blur:
            self.on_view_blur()
        self.on_view_blur = None
        self._view_generation += 1
        self._account_generation += 1
        while self.page.pop_dialog() is not None:
            pass
        self.section = "Афиша"
        self._route = {}
        self.content.controls = []
        self.checkout_key = str(uuid4())
        self.busy_checkout = False

    def activate_account(self):
        self.reset_account_view()
        self.shell()
        self.restore_section(self.view_state.section)

    def add_account(self):
        self.capture_view()
        self.clear_capture()
        self.accounts.begin_login()
        self.reset_account_view()
        self.auth()

    def cancel_add_account(self):
        self.accounts.cancel_login()
        self.activate_account()

    def switch_account(self, user_id):
        self.capture_view()
        self.clear_capture()
        self.accounts.switch(user_id)
        self.activate_account()

    def restore_section(self, section):
        route = self.view_state.routes.get(section, {})
        if section == "Администрирование":
            self.admin()
        elif section == "Профиль":
            self.profile()
        elif section == "Корзина":
            self.cart()
        elif section == "Мои бронирования":
            if route.get("page") == "booking_map":
                self.booking_map(route["booking_id"])
            else:
                self.bookings()
        elif route.get("page") == "seats":
            self.seats(route["session_id"], route.get("admin", False))
        elif route.get("page") == "event":
            self.event_detail(route["event_id"], route.get("related", False))
        elif route.get("page") == "session":
            self.session_detail(route["session_id"], route.get("related", False))
        else:
            self.catalogue()

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

    def catalogue(self, search=None, category="", date_from="", date_to=""):
        self.capture_view()
        restoring = search is None
        applied = (
            self.view_state.drafts.get("catalogue:applied", {})
            if restoring
            else {
                "search": search,
                "category": category,
                "date_from": date_from,
                "date_to": date_to,
            }
        )
        search = applied.get("search", "")
        category = applied.get("category", "")
        date_from, date_to = applied.get("date_from", ""), applied.get("date_to", "")
        values = self.view_state.drafts.get("catalogue:fields", applied) if restoring else applied
        search_field = field(
            "Найти событие", values.get("search", ""), prefix_icon=ft.Icons.SEARCH, width=230
        )
        category_field = select(
            "Категория", [("", "Все категории"), *CATEGORIES], values.get("category", ""), width=165
        )
        from_field = field("С · ДД.ММ.ГГГГ", values.get("date_from", ""), width=200)
        to_field = field("По · ДД.ММ.ГГГГ", values.get("date_to", ""), width=200)

        def calendar(target, label):
            try:
                selected = datetime.strptime(target.value, "%d.%m.%Y") if target.value else None
            except ValueError:
                selected = None

            def changed(event):
                if event.control.value:
                    target.value = event.control.value.strftime("%d.%m.%Y")
                    target.update()

            self.page.show_dialog(
                ft.DatePicker(
                    value=selected,
                    first_date=datetime(2000, 1, 1),
                    last_date=datetime(2100, 12, 31),
                    help_text=label,
                    cancel_text="Отмена",
                    confirm_text="Выбрать",
                    field_label_text="Дата",
                    field_hint_text="ДД.ММ.ГГГГ",
                    on_change=self.safe(changed),
                )
            )

        for date_field, label in [
            (from_field, "Выбрать начальную дату"),
            (to_field, "Выбрать конечную дату"),
        ]:
            date_field.suffix_icon = ft.IconButton(
                icon=ft.Icons.CALENDAR_MONTH_OUTLINED,
                tooltip=label,
                on_click=self.safe(
                    lambda _, target=date_field, label=label: calendar(target, label)
                ),
            )

        def apply(_):
            self.catalogue(
                search_field.value.strip(),
                category_field.value or "",
                from_field.value.strip(),
                to_field.value.strip(),
            )

        search_field.on_submit = self.safe(apply)
        from_field.on_submit = self.safe(apply)
        to_field.on_submit = self.safe(apply)
        events = self.service.list_events(
            search,
            category,
            date_from=datetime.strptime(date_from, "%d.%m.%Y").date() if date_from else None,
            date_to=datetime.strptime(date_to, "%d.%m.%Y").date() if date_to else None,
        )

        def capture():
            self.view_state.drafts["catalogue:fields"] = {
                "search": search_field.value,
                "category": category_field.value,
                "date_from": from_field.value,
                "date_to": to_field.value,
            }

        self.set_view({"section": "Афиша", "page": "catalogue", **applied}, capture=capture)
        self.view_state.drafts["catalogue:applied"] = dict(applied)
        capture()
        cards = []
        for event in events:
            cards.append(
                hoverable(
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
                        on_click=self.safe(
                            lambda _, eid=event["id"]: self.event_detail(
                                eid,
                                back=(
                                    "К афише",
                                    self.catalogue,
                                ),
                            )
                        ),
                    ),
                    background="#F1F9F8",
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
                [
                    search_field,
                    category_field,
                    from_field,
                    to_field,
                    self.button("Найти", apply, width=89),
                ],
                wrap=True,
            ),
            text("Период включает обе даты. Можно указать только начало или конец.", 12, MUTED),
            ft.Row(
                [
                    text(f"Событий: {len(events)}", 15, bold=True),
                    ft.TextButton(
                        "Сбросить фильтры", on_click=self.safe(lambda _: self.catalogue(""))
                    ),
                ]
            ),
            ft.Row(cards, wrap=True, spacing=20, run_spacing=20)
            if cards
            else self.empty("Пока нет событий", "Измените фильтры или вернитесь позже."),
        )

    def event_detail(self, event_id, related=False, back=None):
        self.set_view(
            {"section": "Афиша", "page": "event", "event_id": event_id, "related": related}
        )
        event = (
            self.service.get_related_event(event_id)
            if related
            else self.service.get_event(event_id)
        )
        sessions = self.service.list_sessions(event_id) if event["published"] else []
        preview = self.service.current_user["role"] == "admin"
        back_label, back_action = back or ("К афише", self.catalogue)
        controls = [
            self.button(
                back_label, lambda _: back_action(), icon=ft.Icons.ARROW_BACK, secondary=True
            ),
            ft.Row(
                [
                    self.cover(event.get("cover_path", ""), 290, 210),
                    ft.Column(
                        [
                            tag(event["category"].upper()),
                            *(
                                []
                                if event["published"]
                                else [tag("Снято с публикации", RED, "#F8E9E9")]
                            ),
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
            text("Просмотр сеансов" if preview else "Выберите сеанс", 23, bold=True),
        ]
        if preview:
            controls.append(text("Режим администратора: просмотр без бронирования.", color=MUTED))
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
                                "Посмотреть места" if preview else "Выбрать места",
                                lambda _, sid=session["id"]: self.seats(sid),
                                disabled=not preview and session["free_count"] == 0,
                            ),
                        ]
                    )
                )
            )
        if not sessions:
            controls.append(
                self.empty(
                    "Мероприятие снято с публикации"
                    if not event["published"]
                    else "Нет доступных сеансов",
                    "Сведения о событии и ваши электронные билеты сохранены. Новые бронирования недоступны."
                    if not event["published"]
                    else "Новые даты появятся после публикации расписания.",
                )
            )
        self.show(*controls)

    def seats(self, session_id, admin=False):
        self.capture_view()
        is_admin = self.service.current_user["role"] == "admin"
        if admin and not is_admin:
            raise AppError("Управление местами доступно только администратору.")
        preview = is_admin and not admin
        session = self.service.get_session(session_id)
        seats = self.service.seat_map(session_id)
        draft_key = f"seat-selection:{session_id}"
        draft = self.view_state.drafts.get(draft_key, {}) if not admin and not preview else {}
        available = {seat["id"] for seat in seats if seat["status"] == "free"}
        selected = set(draft.get("selected", [])) & available
        tariff = select(
            "Тариф",
            [("standard", "Обычный"), ("concession", "Учебный льготный · −20%")],
            draft.get("tariff", "standard"),
            width=275,
        )
        selection = ft.Column(spacing=10)
        total = text("0,00 ₽", 27, bold=True)
        cells = {}

        def capture():
            if not admin and not preview:
                self.view_state.drafts[draft_key] = {
                    "selected": sorted(selected),
                    "tariff": tariff.value,
                }

        self.set_view(
            {
                "section": "Администрирование" if admin else "Афиша",
                "page": "seats",
                "session_id": session_id,
                "admin": admin,
            },
            capture=capture,
        )

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
            if preview:
                return
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
                    on_click=self.safe(lambda _, s=seat: toggle(s))
                    if not preview and (admin or seat["status"] == "free")
                    else None,
                )
                if cell.on_click:
                    hoverable(cell)
                cells[seat["id"]] = cell
                row_cells.append(cell)
            grid_rows.append(ft.Row(row_cells, spacing=7))
        tariff.on_select = self.safe(repaint)

        def add(_):
            if not selected:
                raise AppError("Выберите хотя бы одно место.")
            self.service.add_to_cart(session_id, sorted(selected), tariff.value)
            selected.clear()
            self.view_state.drafts.pop(draft_key, None)
            self.clear_capture()
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
            text(
                "Управление местами" if admin else "Просмотр схемы" if preview else "Ваш выбор",
                20,
                bold=True,
            ),
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
                    text(
                        "Администратор может просматривать схему без выбора и бронирования мест.",
                        color=MUTED,
                    ),
                    text(
                        f"Свободно: {session['free_count']} / {session['total_count']}",
                        18,
                        bold=True,
                    ),
                    self.button(
                        "Управление сеансами",
                        lambda _: self.admin("Сеансы", focus_session_id=session_id),
                        secondary=True,
                    ),
                ]
                if preview
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
        if not admin and not preview:
            repaint()

    @staticmethod
    def seat_color(seat):
        return {"booked": "#C8CDD4", "closed": "#EAB9B9"}.get(
            seat["status"], CATEGORY_COLORS[seat["category"]]
        )

    def cart(self):
        if self.service.current_user["role"] == "admin":
            self.navigate("Афиша", self.catalogue)
            return
        self.set_view({"section": "Корзина", "page": "cart"})
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
                    self.event_link(first["title"], first["event_id"], ("К корзине", self.cart)),
                    text(f"{date_text(first['start'])} · {first['hall_name']}", color=MUTED),
                    *rows,
                    self.button(
                        "К мероприятию",
                        lambda _, eid=first["event_id"]: self.event_detail(
                            eid, related=True, back=("К корзине", self.cart)
                        ),
                        icon=ft.Icons.ARROW_FORWARD,
                        secondary=True,
                    ),
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
        admin = admin or self.service.current_user["role"] == "admin"
        self.set_view(
            {
                "section": "Администрирование" if admin else "Мои бронирования",
                "page": "list" if admin else "bookings",
                "tab": "Бронирования",
                "search": search,
            }
        )
        bookings = self.service.list_bookings(search=search, admin=admin)
        controls = [
            self.heading(
                "Все бронирования" if admin else "Мои бронирования",
                "Электронные билеты и история посещений",
            )
        ]
        if admin:
            query = field("Поиск по номеру, имени или логину", search, width=420)
            query.on_submit = self.safe(lambda _: self.admin("Бронирования", query.value))
            controls.append(
                ft.Row(
                    [query, self.button("Найти", lambda _: self.admin("Бронирования", query.value))]
                )
            )

        def return_to_bookings():
            if admin:
                self.admin("Бронирования", search)
            else:
                self.bookings(False, search)

        for booking in bookings:
            status = {"active": "Активно", "cancelled": "Отменено", "completed": "Завершено"}[
                booking["status"]
            ]
            actions = [
                self.button(
                    "К мероприятию",
                    lambda _, eid=booking["event_id"]: self.event_detail(
                        eid,
                        related=True,
                        back=("К бронированиям", return_to_bookings),
                    ),
                    icon=ft.Icons.ARROW_FORWARD,
                    secondary=True,
                ),
                self.button(
                    "Электронный билет",
                    lambda _, bid=booking["id"]: self.ticket(bid),
                    secondary=True,
                ),
                self.button(
                    "Места на схеме",
                    lambda _, bid=booking["id"]: self.booking_map(bid),
                    icon=ft.Icons.EVENT_SEAT_OUTLINED,
                    secondary=True,
                ),
                self.button(
                    "К сеансу",
                    lambda _, sid=booking["session_id"]: self.session_detail(sid, related=True),
                    secondary=True,
                ),
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
                    self.event_link(
                        booking["title"],
                        booking["event_id"],
                        ("К бронированиям", return_to_bookings),
                    ),
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

    def event_link(self, title, event_id, back):
        return ft.TextButton(
            content=text(title, 22, TEAL, bold=True, text_align=ft.TextAlign.LEFT),
            tooltip="К мероприятию",
            style=ft.ButtonStyle(
                alignment=ft.Alignment.CENTER_LEFT,
                padding=ft.Padding.symmetric(horizontal=4, vertical=8),
            ),
            on_click=self.safe(lambda _: self.event_detail(event_id, related=True, back=back)),
        )

    def cancel_booking(self, booking_id, reason, admin):
        self.service.cancel_booking(booking_id, reason or "Отменено пользователем")
        self.admin("Бронирования") if admin else self.bookings()

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
            actions=[
                self.button(
                    "Места на схеме",
                    lambda _: self.open_ticket_target(lambda: self.booking_map(booking_id)),
                    secondary=True,
                ),
                self.button(
                    "К сеансу",
                    lambda _: self.open_ticket_target(
                        lambda: self.session_detail(booking["session_id"], related=True)
                    ),
                    secondary=True,
                ),
                self.button("Закрыть", lambda _: self.page.pop_dialog(), secondary=True),
            ],
        )

    def open_ticket_target(self, action):
        self.page.pop_dialog()
        action()

    def readonly_map(self, layout, stage, selected_label="Места в билете"):
        rows = defaultdict(list)
        for position in layout:
            rows[position["row"]].append(position)
        grid = []
        for row, positions in sorted(rows.items()):
            cells = [ft.Container(text(f"Ряд {row}", 11, MUTED), width=58)]
            for position in sorted(positions, key=lambda item: item["number"]):
                selected = position.get("selected", False)
                if not position.get("enabled", True):
                    cells.append(ft.Container(width=38, height=36))
                    continue
                cells.append(
                    ft.Container(
                        text(position["number"], 12, WHITE if selected else INK, bold=True),
                        width=38,
                        height=36,
                        border_radius=9,
                        alignment=ft.Alignment.CENTER,
                        bgcolor=TEAL if selected else "#E1E7EC",
                        border=ft.Border.all(1, TEAL if selected else "#CFD8E0"),
                        tooltip=f"Ряд {row}, место {position['number']}"
                        + (" · " + selected_label if selected else ""),
                        data={"seat_position": [row, position["number"]], "selected": selected},
                    )
                )
            grid.append(ft.Row(cells, spacing=7))
        return panel(
            ft.Container(
                text(stage or "СЦЕНА / ЭКРАН", 12, MUTED, True),
                height=36,
                bgcolor=BG,
                border_radius=8,
                alignment=ft.Alignment.CENTER,
            ),
            ft.Row([ft.Column(grid, spacing=9)], scroll=ft.ScrollMode.ALWAYS),
            ft.Row(
                [
                    ft.Container(width=16, height=16, bgcolor=TEAL, border_radius=4),
                    text(selected_label, 13, MUTED),
                ],
                tight=True,
            ),
        )

    def booking_map(self, booking_id):
        result = self.service.get_booking_seat_map(booking_id)
        booking = result["booking"]
        admin = self.service.current_user["role"] == "admin"
        self.set_view(
            {
                "section": "Администрирование" if admin else "Мои бронирования",
                "page": "booking_map",
                "booking_id": booking_id,
            }
        )

        def back():
            self.admin("Бронирования") if admin else self.bookings()

        controls = [
            self.button(
                "К бронированиям", lambda _: back(), icon=ft.Icons.ARROW_BACK, secondary=True
            ),
            self.heading("Места по билету", f"{booking['number']} · {booking['title']}"),
            text(
                f"{date_text(booking['start'])} · {booking['hall_name']} · сеанс #{booking['session_id']}",
                17,
                bold=True,
            ),
            tag(
                {"active": "Активно", "cancelled": "Отменено", "completed": "Завершено"}[
                    booking["status"]
                ]
            ),
        ]
        if result["layout_is_partial"]:
            controls.append(
                text(
                    "Для этого старого билета полная схема не сохранилась. Показаны известные ряды и места билета.",
                    color=MUTED,
                )
            )
        if result["session_changed"]:
            controls.append(
                text(
                    "После оформления сеанс был перенесён. Здесь показаны зал и места исходного билета; переход к сеансу откроет его актуальные сведения.",
                    color=MUTED,
                )
            )
        controls += [
            self.readonly_map(result["layout"], result["stage"], "Места этого билета"),
            text(
                "Это схема на момент оформления. Отмена билета освобождает места; карта не показывает их текущую занятость.",
                13,
                MUTED,
            ),
            ft.Row(
                [
                    self.button(
                        "К мероприятию",
                        lambda _: self.event_detail(
                            result["event_id"],
                            related=True,
                            back=("К схеме билета", lambda: self.booking_map(booking_id)),
                        ),
                        secondary=True,
                    ),
                    self.button(
                        "К сеансу",
                        lambda _: self.session_detail(
                            result["session_id"],
                            related=True,
                            back=("К схеме билета", lambda: self.booking_map(booking_id)),
                        ),
                        secondary=True,
                    ),
                    self.button(
                        "Электронный билет", lambda _: self.ticket(booking_id), secondary=True
                    ),
                ],
                wrap=True,
            ),
        ]
        self.show(*controls)

    def session_detail(self, session_id, related=False, back=None):
        if self.service.current_user["role"] == "admin":
            from eventseat.ui_admin import AdminUI

            AdminUI(self).session_detail(session_id)
            return
        session = (
            self.service.get_related_session(session_id)
            if related
            else self.service.get_session(session_id)
        )
        self.set_view(
            {"section": "Афиша", "page": "session", "session_id": session_id, "related": related}
        )
        cancelled = session["status"] == "cancelled"
        future = session["start"] > datetime.now()
        state = "Отменён" if cancelled else "Предстоящий" if future else "Завершён"
        back_label, back_action = back or (
            ("К бронированиям", self.bookings)
            if related
            else ("К мероприятию", lambda: self.event_detail(session["event_id"]))
        )
        controls = [
            self.button(
                back_label, lambda _: back_action(), secondary=True, icon=ft.Icons.ARROW_BACK
            ),
            self.heading(f"Сеанс #{session_id}", session["title"]),
            panel(
                text(date_text(session["start"]), 24, bold=True),
                text(f"{session['hall_name']} · {session['duration']} минут", 18),
                tag(state, RED if cancelled else TEAL),
                *(
                    [text("Причина отмены: " + session["cancel_reason"], color=RED)]
                    if cancelled
                    else []
                ),
            ),
            self.button(
                "К мероприятию",
                lambda _: self.event_detail(session["event_id"], related=related),
                secondary=True,
            ),
        ]
        if session.get("bookable", not cancelled and future) and session["free_count"]:
            controls.append(self.button("Выбрать места", lambda _: self.seats(session_id)))
        else:
            controls.append(text("Новые бронирования на этот сеанс недоступны.", color=MUTED))
        self.show(*controls)

    def profile(self):
        self.capture_view()
        user = self.service.current_user
        name = field(
            "Отображаемое имя", self.view_state.drafts.get("profile", {}).get("name", user["name"])
        )
        old = field("Текущий пароль", password=True, can_reveal_password=True)
        new = field("Новый пароль", password=True, can_reveal_password=True)
        repeat = field("Повторите новый пароль", password=True, can_reveal_password=True)
        self.set_view(
            {"section": "Профиль", "page": "profile"},
            capture=lambda: self.view_state.drafts.__setitem__("profile", {"name": name.value}),
        )

        def save(_):
            if new.value != repeat.value:
                raise AppError("Новые пароли не совпадают.")
            self.service.update_profile(name.value, old.value, new.value)
            self.clear_capture()
            self.view_state.drafts.pop("profile", None)
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
            panel(
                text("Вход в аккаунт", 19, bold=True),
                text(
                    "Переключение слева сохраняет вход до закрытия приложения. Выход удалит этот аккаунт из списка; корзина и бронирования останутся сохранены.",
                    color=MUTED,
                ),
                self.button(
                    "Выйти из аккаунта",
                    lambda _: self.logout(),
                    icon=ft.Icons.LOGOUT,
                    secondary=True,
                ),
                width=570,
            ),
        )

    def admin(self, tab=None, search="", **kwargs):
        from eventseat.ui_admin import AdminUI

        self.capture_view()
        admin = AdminUI(self)
        if tab is not None:
            admin.show(tab, search, **kwargs)
            return
        route = self.view_state.admin_route
        page = route.get("page", "list")
        if page == "hall":
            admin.hall_form(route.get("hall_id"))
        elif page == "event_form":
            admin.event_form(route.get("event_id"))
        elif page == "session_form":
            admin.session_form(route.get("session_id"), route.get("event_id"))
        elif page == "session_detail":
            admin.session_detail(route["session_id"])
        elif page == "seats":
            self.seats(route["session_id"], True)
        elif page == "booking_map":
            self.booking_map(route["booking_id"])
        else:
            admin.show(
                route.get("tab", "Мероприятия"),
                route.get("search", ""),
                **{
                    key: route[key]
                    for key in ("event_id", "hall_id", "focus_session_id")
                    if key in route
                },
            )
