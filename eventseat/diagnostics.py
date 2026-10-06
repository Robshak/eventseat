import asyncio
import inspect
import json
import os
import re
import sqlite3
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import flet as ft

from eventseat.config import asset_path


def descendants(control):
    yield control
    for attr in (
        "content",
        "controls",
        "actions",
        "items",
        "title",
        "prefix",
        "suffix",
        "prefix_icon",
        "suffix_icon",
    ):
        child = getattr(control, attr, None)
        if isinstance(child, ft.Control):
            yield from descendants(child)
        elif isinstance(child, list):
            for item in child:
                if isinstance(item, ft.Control):
                    yield from descendants(item)


async def verify(app, folder: Path, phase: str):
    page = app.page
    report = {"phase": phase, "checks": [], "screenshots": [], "success": False}

    def check(condition, label):
        if not condition:
            raise AssertionError(label)
        report["checks"].append(label)

    def controls():
        roots = list(page.controls)
        roots.extend(d for d in page._dialogs.controls if d.open)
        return [child for root in roots for child in descendants(root)]

    def visible_texts(root=None):
        items = descendants(root) if root is not None else controls()
        return [c.value for c in items if isinstance(c, ft.Text)]

    def buttons(label):
        dialogs = [d for d in page._dialogs.controls if d.open and isinstance(d, ft.AlertDialog)]
        items = descendants(dialogs[-1]) if dialogs else controls()
        return [
            c
            for c in items
            if getattr(c, "content", None) == label and callable(getattr(c, "on_click", None))
        ]

    async def invoke(callback, event=None):
        result = callback(event)
        if inspect.isawaitable(result):
            await result
        page.update()
        await asyncio.sleep(0.15)

    def fill(label, value):
        matches = [c for c in controls() if isinstance(c, ft.TextField) and c.label == label]
        if len(matches) != 1:
            raise AssertionError(f"Поле {label}: найдено {len(matches)}")
        matches[0].value = value

    async def wire_edit(label, value):
        target = next(c for c in controls() if isinstance(c, ft.TextField) and c.label == label)
        input_filter = target.input_filter
        if input_filter is not None:
            # Model Flet 1.0.3's client-side hasMatch gate before its property
            # patch/change event. This is a protocol/render check, not a keypress.
            flags = (
                (re.MULTILINE if input_filter.multiline else 0)
                | (re.DOTALL if input_filter.dot_all else 0)
                | (0 if input_filter.case_sensitive else re.IGNORECASE)
            )
            check(
                re.search(input_filter.regex_string, value, flags) is not None,
                f"Клиентский фильтр допускает новое значение поля: {label}",
            )
        page.session.apply_patch(target._i, {"value": value})
        await page.session.dispatch_event(target._i, "change", value)
        page.update()
        await asyncio.sleep(0.15)
        check(target.value == value, f"Протокол ввода сохраняет значение поля: {label}")

    async def choose(label, value):
        if label == "Аккаунты":
            selector = next(c for c in controls() if getattr(c, "data", None) == "account-switcher")
            if not app.account_menu.opened:
                await invoke(selector.on_click)
            item = next(
                item
                for item in app.account_menu.items
                if isinstance(item.data, dict) and item.data.get("account_id") == int(value)
            )
            await invoke(item.on_click, SimpleNamespace(control=item))
            return
        matches = [c for c in controls() if isinstance(c, ft.Dropdown) and c.label == label]
        if len(matches) != 1:
            raise AssertionError(f"Список {label}: найдено {len(matches)}")
        matches[0].value = str(value)
        if matches[0].on_select:
            await invoke(matches[0].on_select, SimpleNamespace(control=matches[0]))

    async def press(label, index=None):
        matches = buttons(label)
        if not matches or (index is None and len(matches) != 1):
            raise AssertionError(f"Кнопка {label}: найдено {len(matches)}")
        button = matches[0 if index is None else index]
        check(not button.disabled, f"Доступна кнопка: {label}")
        await invoke(button.on_click)

    async def navigate(label):
        item = next(
            c
            for c in app.nav.controls
            if label in visible_texts(c) and callable(getattr(c, "on_click", None))
        )
        await invoke(item.on_click)

    async def booking_period(period):
        button = next(
            c
            for c in controls()
            if isinstance(getattr(c, "data", None), dict) and c.data.get("booking_period") == period
        )
        await invoke(button.on_click)

    async def calendar(tooltip, day):
        matches = [
            c
            for c in controls()
            if getattr(c, "tooltip", None) == tooltip and callable(getattr(c, "on_click", None))
        ]
        check(len(matches) == 1, f"Календарь доступен: {tooltip}")
        await invoke(matches[0].on_click)
        picker = next(c for c in controls() if getattr(c, "data", None) == "date-picker" and c.open)
        fields = {c.label: c for c in descendants(picker) if isinstance(c, ft.Dropdown)}
        for label, value in (("Год", day.year), ("Месяц", day.month)):
            fields[label].value = str(value)
            await invoke(fields[label].on_select, SimpleNamespace(control=fields[label]))
        chosen = next(
            c
            for c in descendants(picker)
            if isinstance(getattr(c, "data", None), dict)
            and c.data.get("date") == day.date().isoformat()
        )
        await invoke(chosen.on_click)
        if tooltip == "Выбрать начальную дату":
            await snapshot("date-calendar", dialog=True)
        await press("Выбрать")
        target = next(
            c
            for c in controls()
            if isinstance(c, ft.TextField) and getattr(c.suffix_icon, "tooltip", None) == tooltip
        )
        check(
            target.value == day.strftime("%d.%m.%Y"),
            "Календарь сохраняет выбранный день без сдвига",
        )

    async def type_date(label, digits):
        target = next(c for c in controls() if isinstance(c, ft.TextField) and c.label == label)
        target.value = digits
        target.selection = ft.TextSelection(len(digits), len(digits))
        await invoke(target.on_change, SimpleNamespace(control=target, data=digits))
        check(
            target.value == f"{digits[:2]}.{digits[2:4]}.{digits[4:]}",
            "Ввод даты автоматически расставляет точки",
        )

    async def hover(control, label, screenshot=None):
        check(callable(control.on_hover), f"Есть обработчик наведения: {label}")
        initial = (str(control.border), control.bgcolor)
        initial_widths = tuple(
            getattr(control.border, side).width for side in ("top", "right", "bottom", "left")
        )
        await invoke(control.on_hover, SimpleNamespace(control=control, data="true"))
        check(
            tuple(
                getattr(control.border, side).width for side in ("top", "right", "bottom", "left")
            )
            == initial_widths,
            f"Наведение не меняет толщину рамки и геометрию: {label}",
        )
        check(
            (str(control.border), control.bgcolor) != initial,
            f"Наведение визуально выделяет объект: {label}",
        )
        if screenshot:
            await snapshot(screenshot)
        await invoke(control.on_hover, SimpleNamespace(control=control, data="false"))
        check(
            (str(control.border), control.bgcolor) == initial,
            f"Снятие наведения возвращает оформление: {label}",
        )

    def dismiss():
        while page.pop_dialog() is not None:
            pass

    async def snapshot(name, dialog=False):
        await asyncio.sleep(0.25)
        if dialog:
            owner = next(
                d
                for d in reversed(page._dialogs.controls)
                if d.open and isinstance(d, ft.AlertDialog)
            )
            original = owner.content
            capture = ft.Screenshot(ft.Container(original, bgcolor="#FFFFFF", padding=20))
            owner.content = capture
        else:
            original = list(page.controls)
            capture = ft.Screenshot(
                ft.Container(
                    ft.Column(original, expand=True, spacing=0), bgcolor=page.bgcolor, expand=True
                ),
                expand=True,
            )
            page.controls = [capture]
        page.update()
        await asyncio.sleep(0.2)
        pixels = await capture.capture(pixel_ratio=1.0)
        check(len(pixels) > 1000, f"Отрисован экран: {name}")
        (folder / f"{phase}-{name}.png").write_bytes(pixels)
        report["screenshots"].append(f"{phase}-{name}.png")
        if dialog:
            owner.content = original
        else:
            page.controls = original
        page.update()

    try:
        await asyncio.sleep(0.5)
        if phase == "create":
            check(app.service.needs_setup(), "Первый запуск без встроенного администратора")
            await snapshot("first-launch")
            password = os.environ["EVENTSEAT_QA_PASSWORD"]
            fill("Логин", "qa_admin")
            fill("Отображаемое имя", "Администратор проверки")
            fill("Пароль", password)
            fill("Повторите пароль", password)
            await press("Создать администратора")
            check(
                app.service.current_user["role"] == "admin",
                "Создание администратора через обработчик формы",
            )
            admin_id = app.service.current_user["id"]
            check(
                "Корзина" not in visible_texts(app.nav)
                and "Мои бронирования" not in visible_texts(app.nav),
                "Администратору не показаны личная корзина и бронирования",
            )
            check("Личный кабинет" not in visible_texts(), "Убрана дублирующая подпись кабинета")
            check(not buttons("Выйти из аккаунта"), "Выход убран из боковой панели")
            await snapshot("catalogue")
            action_style = buttons("Найти")[0].style
            check(
                ft.ControlState.HOVERED in action_style.bgcolor
                and ft.ControlState.FOCUSED in action_style.bgcolor,
                "Кнопки имеют состояния наведения и клавиатурного фокуса",
            )
            nav_item = next(
                c for c in app.nav.controls if isinstance(c, ft.Container) and c.on_hover
            )
            await hover(nav_item, "пункт боковой навигации")
            for value in ("0", "", "8", ""):
                await wire_edit("С · ДД.ММ.ГГГГ", value)
            await snapshot("date-input-cleared")
            for name in ("cinema.png", "concert.png", "lecture.png"):
                check(asset_path(name).is_file(), f"Автономный ресурс: {name}")
            app.navigate("Администрирование", lambda: app.admin("Залы"))
            await press("Создать зал")
            await wire_edit("Эконом, ₽", "0")
            await wire_edit("Эконом, ₽", "")
            check(
                app.view_state.drafts["hall:new"]["prices"]["эконом"] == "",
                "Очистка последнего символа сохраняется в черновике без подстановки нуля",
            )
            await snapshot("input-cleared")
            await wire_edit("Эконом, ₽", "250")
            fill("Название зала", "Зал сквозной проверки")
            fill("Рядов", "3")
            fill("Мест в ряду", "6")
            fill("Первый ряд", "3")
            fill("Первое место", "5")
            fill("Эконом, ₽", "200")
            fill("Стандарт, ₽", "350")
            fill("Vip, ₽", "600")
            await press("Построить сетку")
            await press("Подтвердить")
            check(
                not any(
                    isinstance(c, ft.Dropdown) and c.label == "Действие при нажатии"
                    for c in controls()
                ),
                "В конструкторе нет меню действия по нажатию",
            )
            cell = next(
                c
                for c in controls()
                if isinstance(c, ft.Container)
                and str(getattr(c, "tooltip", "")).startswith("Ряд 3, место 5 ·")
            )
            await invoke(cell.on_click)
            check(
                "Параметры кресла" in visible_texts(),
                "Нажатие кресла открывает боковую панель параметров",
            )
            for region, label in (
                ("hall-grid-region", "сетки"),
                ("hall-inspector-region", "инспектора"),
            ):
                target = next(c for c in controls() if c.data == region)
                await invoke(target.on_tap)
                check(
                    "Выделено мест: 1" in visible_texts()
                    and next(c for c in controls() if c.data == "hall-inspector-region").visible,
                    f"Клик внутри {label} сохраняет выделенное кресло",
                )
            panel_region = next(c for c in controls() if c.data == "hall-grid-panel-region")
            await invoke(panel_region.on_tap)
            check(
                "Выделено мест: 0" in visible_texts()
                and not next(c for c in controls() if c.data == "hall-inspector-region").visible,
                "Клик вне сетки внутри её панели снимает выделение и скрывает инспектор",
            )
            await snapshot("hall-panel-deselected")
            cell = next(
                c
                for c in controls()
                if isinstance(getattr(c, "data", None), dict) and c.data.get("hall_seat_index") == 0
            )
            await invoke(cell.on_click)
            await choose("Тип места", "aisle")
            await press("Применить к выделенным")
            dismiss()
            economy_cell = next(
                c
                for c in controls()
                if isinstance(c, ft.Container)
                and str(getattr(c, "tooltip", "")).startswith("Ряд 3, место 6 ·")
            )
            await invoke(economy_cell.on_click)
            await choose("Категория мест", "эконом")
            await press("Применить к выделенным")
            dismiss()
            hall_cells = {
                c.data["hall_seat_index"]: c
                for c in controls()
                if isinstance(getattr(c, "data", None), dict) and "hall_seat_index" in c.data
            }
            await invoke(hall_cells[6].on_click)
            listener = next(c for c in controls() if isinstance(c, ft.KeyboardListener))
            await invoke(listener.on_key_down, SimpleNamespace(key="Shift Left"))
            hall_cells = {
                c.data["hall_seat_index"]: c
                for c in controls()
                if isinstance(getattr(c, "data", None), dict) and "hall_seat_index" in c.data
            }
            await invoke(hall_cells[13].on_click)
            await invoke(listener.on_key_up, SimpleNamespace(key="Shift Left"))
            check(
                "Выделено мест: 4" in visible_texts(),
                "Shift + клик выделяет прямоугольную группу мест",
            )
            await choose("Категория мест", "VIP")
            check(
                not any(
                    isinstance(c, (ft.TextField, ft.Dropdown))
                    and c.label in ("Цена выделенных мест", "Индивидуальная цена, ₽")
                    for c in controls()
                ),
                "В свойствах кресла нет индивидуальных цен",
            )
            await press("Применить к выделенным")
            dismiss()
            app.navigate("Профиль", app.profile)
            app.navigate("Администрирование", app.admin)
            check(
                "Конструктор зала" in visible_texts() and "Выделено мест: 0" in visible_texts(),
                "Выход из рабочей области снимает выделение, возврат восстанавливает конструктор",
            )
            restored_cell = next(
                c
                for c in controls()
                if isinstance(getattr(c, "data", None), dict) and c.data.get("hall_seat_index") == 6
            )
            await invoke(restored_cell.on_click)
            check(
                next(
                    c
                    for c in controls()
                    if isinstance(c, ft.Dropdown) and c.label == "Категория мест"
                ).value
                == "VIP",
                "Изменения кресел в черновике сохраняются при навигации",
            )
            await snapshot("hall-editor")
            background = next(c for c in controls() if c.data == "workspace-background")
            await invoke(background.on_tap)
            check("Выделено мест: 0" in visible_texts(), "Клик по фону снимает выделение кресла")
            await press("Предпросмотр")
            await snapshot("hall-preview", dialog=True)
            await press("Закрыть")
            await press("Сохранить зал")
            dismiss()
            hall_id = next(
                h["id"] for h in app.service.list_halls() if h["name"] == "Зал сквозной проверки"
            )
            check(
                app.service.get_hall(hall_id)["rows"] == 3,
                "Создание зала, нумерация, проход и цены через форму",
            )
            saved_hall = app.service.get_hall(hall_id)
            check(
                sum(
                    s.get("price_override") is None and s["category"] == "VIP"
                    for s in saved_hall["seats"]
                )
                == 4,
                "Категория применена ко всей группе, индивидуальных цен нет",
            )
            await snapshot("admin-halls")
            await press("Создать зал")
            fill("Рядов", "3")
            fill("Мест в ряду", "50")
            await press("Построить сетку")
            await press("Подтвердить")
            wide_panel = next(c for c in controls() if c.data == "hall-grid-panel")
            wide_cells = [
                c
                for c in controls()
                if isinstance(getattr(c, "data", None), dict) and "hall_seat_index" in c.data
            ]
            check(
                len(wide_cells) == 150
                and wide_panel.width <= 900
                and all(c.width < 39 for c in wide_cells),
                "Широкая сетка уменьшает кресла и укладывается в максимальную ширину",
            )
            header = next(c for c in controls() if c.data == "hall-header")
            check(
                {"Предпросмотр", "Сохранить зал"}
                <= {
                    c.content
                    for c in descendants(header)
                    if isinstance(getattr(c, "content", None), str)
                },
                "Предпросмотр и сохранение находятся в заголовке редактора",
            )
            await app.content.scroll_to(offset=300, duration=0)
            await snapshot("hall-wide-grid")
            await press("К залам")
            app.view_state.drafts.pop("hall:new", None)
            app.admin("Мероприятия")
            await press("Создать мероприятие")
            fill("Название", "Вечер в EventSeat")
            fill("Описание", "Сквозная проверка нового зала, расписания и билетов.")
            fill("Продолжительность, мин", "100")
            app.navigate("Профиль", app.profile)
            app.navigate("Администрирование", app.admin)
            check(
                next(
                    c for c in controls() if isinstance(c, ft.TextField) and c.label == "Название"
                ).value
                == "Вечер в EventSeat",
                "Форма мероприятия и введённый текст восстановлены после профиля",
            )
            await snapshot("event-editor")
            await press("Опубликовать")
            dismiss()
            event_id = next(
                e["id"]
                for e in app.service.list_events(admin=True)
                if e["title"] == "Вечер в EventSeat"
            )
            event_card = next(
                c
                for c in descendants(app.content)
                if isinstance(c, ft.Container)
                and c.on_click
                and c.on_hover
                and "Вечер в EventSeat" in visible_texts(c)
            )
            await invoke(event_card.on_click)
            check(
                next(
                    c for c in controls() if isinstance(c, ft.Dropdown) and c.label == "Мероприятие"
                ).value
                == str(event_id),
                "Карточка мероприятия открывает список его сеансов",
            )
            check(not buttons("Сеанс"), "Кнопка создания сеанса имеет однозначную подпись")
            await press("Создать сеанс")
            await choose("Мероприятие", event_id)
            await choose("Зал", hall_id)
            fill("Дата · ДД.ММ.ГГГГ", "12.")
            fill("Время · ЧЧ:ММ", "19:")
            await press("К залу")
            check(
                bool(buttons("К созданию сеанса"))
                and next(
                    c
                    for c in controls()
                    if isinstance(c, ft.TextField) and c.label == "Название зала"
                ).value
                == "Зал сквозной проверки",
                "Незавершённая форма создания сеанса открывает выбранный зал с контекстным возвратом",
            )
            await navigate("Профиль")
            await navigate("Администрирование")
            check(
                bool(buttons("К созданию сеанса")),
                "Возврат из профиля сохраняет путь от зала к созданию сеанса",
            )
            await snapshot("hall-from-session")
            await press("К созданию сеанса")
            restored = {
                c.label: c.value for c in controls() if isinstance(c, (ft.TextField, ft.Dropdown))
            }
            check(
                restored["Дата · ДД.ММ.ГГГГ"] == "12."
                and restored["Время · ЧЧ:ММ"] == "19:"
                and restored["Мероприятие"] == str(event_id)
                and restored["Зал"] == str(hall_id),
                "Контекстный возврат сохраняет незавершённые дату, время, мероприятие и зал",
            )
            await snapshot("session-draft-return")
            first_start = (datetime.now() + timedelta(days=10)).replace(
                hour=19, minute=0, second=0, microsecond=0
            )
            fill("Дата · ДД.ММ.ГГГГ", first_start.strftime("%d.%m.%Y"))
            fill("Время · ЧЧ:ММ", "19:00")
            app.navigate("Профиль", app.profile)
            app.navigate("Администрирование", app.admin)
            check(
                next(
                    c
                    for c in controls()
                    if isinstance(c, ft.TextField) and c.label == "Время · ЧЧ:ММ"
                ).value
                == "19:00",
                "Форма сеанса сохраняет дату, время и выбранные справочники при переходах",
            )
            await snapshot("session-editor")
            await press("Сохранить сеанс")
            dismiss()
            session_id = app.service.list_sessions(event_id)[0]["id"]
            check(
                app.service.get_session(session_id)["hall_id"] == hall_id,
                "Публикация мероприятия и создание сеанса через формы",
            )
            await snapshot("admin-sessions")
            session_card = next(
                c for c in controls() if str(getattr(c, "key", "")) == f"session-{session_id}"
            )
            check(
                session_card.bgcolor == "#E4F3F1",
                "Созданный сеанс выделен в списке и имеет ключ прокрутки",
            )
            await invoke(session_card.on_click)
            check(
                any(f"Сеанс №{session_id}" in str(value) for value in visible_texts(app.content)),
                "Карточка открывает конкретный сеанс с номером, временем и залом",
            )
            check(
                bool(buttons("Сохранить сеанс")) and not buttons("Цены мест"),
                "Карточка сразу открывает редактирование без отдельного экрана цен",
            )
            await snapshot("session-edit-direct")
            await press("К мероприятию")
            check(
                next(
                    c for c in controls() if isinstance(c, ft.TextField) and c.label == "Название"
                ).value
                == "Вечер в EventSeat",
                "Из сеанса открыт редактор связанного мероприятия",
            )
            await press("К редактированию сеанса")
            check(
                app._route.get("page") == "session_form"
                and app._route.get("session_id") == session_id,
                "Кнопка назад возвращает из мероприятия к редактированию конкретного сеанса",
            )
            await press("К залу")
            check(
                next(
                    c
                    for c in controls()
                    if isinstance(c, ft.TextField) and c.label == "Название зала"
                ).value
                == "Зал сквозной проверки",
                "Из сеанса открыт конструктор его зала",
            )
            await press("К редактированию сеанса")
            check(
                app._route.get("page") == "session_form"
                and app._route.get("session_id") == session_id,
                "Кнопка назад возвращает из зала к редактированию конкретного сеанса",
            )
            app.admin("Залы")
            hall_card = next(
                c
                for c in descendants(app.content)
                if isinstance(c, ft.Container)
                and "Зал сквозной проверки" in visible_texts(c)
                and any(
                    getattr(child, "content", None) == "Сеансы зала" for child in descendants(c)
                )
            )
            hall_sessions_button = next(
                c for c in descendants(hall_card) if getattr(c, "content", None) == "Сеансы зала"
            )
            await invoke(hall_sessions_button.on_click)
            check(
                next(c for c in controls() if isinstance(c, ft.Dropdown) and c.label == "Зал").value
                == str(hall_id),
                "Из карточки зала открыто его расписание",
            )
            app.admin("Бронирования")
            check(not buttons("Статистика"), "Отдельная вкладка статистики убрана")
            await choose("Мероприятие", event_id)
            await choose("Зал", hall_id)
            fill("С · ДД.ММ.ГГГГ", first_start.strftime("%d.%m.%Y"))
            fill("По · ДД.ММ.ГГГГ", first_start.strftime("%d.%m.%Y"))
            await choose("Состояние сеанса", "all")
            await choose("Состояние брони", "all")
            await press("Применить фильтры")
            check(
                any(
                    str(value).startswith("Сеансов: 1 · мест:")
                    for value in visible_texts(app.content)
                ),
                "Статистика фильтруется одновременно по мероприятию, залу, периоду и состояниям",
            )
            app.navigate("Профиль", app.profile)
            app.navigate("Администрирование", app.admin)
            check(
                bool(buttons("Применить фильтры"))
                and next(
                    c for c in controls() if isinstance(c, ft.Dropdown) and c.label == "Зал"
                ).value
                == str(hall_id),
                "Возвращение в администрирование восстанавливает объединённую вкладку бронирований и фильтры",
            )
            fill("С · ДД.ММ.ГГГГ", "12.")
            app.navigate("Профиль", app.profile)
            app.navigate("Администрирование", app.admin)
            check(
                next(
                    c
                    for c in controls()
                    if isinstance(c, ft.TextField) and c.label == "С · ДД.ММ.ГГГГ"
                ).value
                == "12."
                and any(
                    str(value).startswith("Сеансов: 1 · мест:")
                    for value in visible_texts(app.content)
                ),
                "Незавершённая дата фильтра восстанавливается без ошибки и не меняет применённую статистику",
            )
            fill("С · ДД.ММ.ГГГГ", first_start.strftime("%d.%m.%Y"))
            await press("Применить фильтры")
            await snapshot("admin-statistics")
            app.event_detail(event_id)
            check(not buttons("Выбрать места"), "У администратора нет выбора мест для покупки")
            await press("Посмотреть места")
            check(not buttons("Добавить в корзину"), "Схема администратора не предлагает покупку")
            check(
                not any(isinstance(c, ft.Dropdown) and c.label == "Тариф" for c in controls()),
                "В предпросмотре администратора нет покупательского тарифа",
            )
            await snapshot("admin-seat-preview")
            await press("Управление сеансами")
            check(
                any(
                    str(getattr(c, "key", "")) == f"session-{session_id}" and c.bgcolor == "#E4F3F1"
                    for c in controls()
                ),
                "Переход со схемы выделяет нужный сеанс и направляет прокрутку к нему",
            )
            await snapshot("focused-session")
            later_start = first_start + timedelta(days=10)
            hall = app.service.get_hall(hall_id)
            app.service.save_hall(
                hall["name"],
                hall["rows"],
                hall["columns"],
                hall["stage"],
                {"эконом": 120000, "стандарт": 135000, "VIP": 160000},
                hall["seats"],
                hall_id,
            )
            app.service.save_session(event_id, hall_id, later_start)
            check(
                app.service.get_session(session_id)["category_prices"]["эконом"] == 20000,
                "Изменение цен зала не меняет цены ранее созданного сеанса",
            )
            await press("Добавить аккаунт")
            await press("Нет аккаунта? Зарегистрироваться")
            fill("Логин", "qa_user")
            fill("Отображаемое имя", "Гость EventSeat")
            fill("Пароль", password)
            fill("Повторите пароль", password)
            await press("Зарегистрироваться")
            check(
                app.service.current_user["role"] == "user",
                "Регистрация создаёт только пользователя",
            )
            user_id = app.service.current_user["id"]
            check(
                {u["id"] for u in app.accounts.users} == {admin_id, user_id},
                "Добавление аккаунта сохраняет вход администратора в текущем процессе",
            )
            account_selector = next(
                c for c in controls() if getattr(c, "data", None) == "account-switcher"
            )
            check(
                isinstance(account_selector, ft.TextButton)
                and app.account_menu.popup.border.left.width >= 1,
                "Переключатель аккаунтов имеет отдельное меню с контрастной рамкой",
            )
            check(
                all(
                    any(isinstance(c, ft.Text) and c.color == "#FFFFFF" for c in descendants(item))
                    for item in app.account_menu.items
                ),
                "Имена в меню аккаунтов отображаются светлым текстом на тёмном фоне",
            )
            await invoke(account_selector.on_click)
            check(
                app.account_menu.popup.visible
                and app.account_menu.popup.bottom
                > app.account_menu.FOOTER_HEIGHT + account_selector.height
                and app.account_menu.popup.width > account_selector.width,
                "Меню раскрывается вверх и выходит за ширину боковой панели",
            )
            await snapshot("account-menu-open")
            await invoke(app.account_menu.listener.on_key_down, SimpleNamespace(key="Escape"))
            check(not app.account_menu.opened, "Escape закрывает меню аккаунтов")
            fill("Найти событие", "Вечер в EventSeat")
            await calendar("Выбрать начальную дату", later_start)
            await calendar("Выбрать конечную дату", later_start)
            await type_date("По · ДД.ММ.ГГГГ", later_start.strftime("%d%m%Y"))
            await press("Найти")
            from eventseat.ui import date_text, money

            check(
                "Событий: 1" in visible_texts(app.content)
                and date_text(later_start) in visible_texts(app.content)
                and "от " + money(120000) in visible_texts(app.content),
                "Обе границы включены; карточка показывает дату и цену сеанса внутри интервала",
            )
            fill("С · ДД.ММ.ГГГГ", "31.02.2026")
            await press("Найти")
            invalid = next(
                c for c in controls() if isinstance(c, ft.TextField) and c.label == "С · ДД.ММ.ГГГГ"
            )
            check(
                bool(invalid.error_text) and "Событий: 1" in visible_texts(app.content),
                "Невозможная дата показывает ошибку и сохраняет предыдущую выдачу",
            )
            dismiss()
            fill("С · ДД.ММ.ГГГГ", later_start.strftime("%d.%m.%Y"))
            await press("Найти")
            card = next(
                c
                for c in descendants(app.content)
                if isinstance(c, ft.Container)
                and c.on_click
                and c.on_hover
                and "Вечер в EventSeat" in visible_texts(c)
            )
            await hover(card, "карточка мероприятия", "event-hover")
            fill("С · ДД.ММ.ГГГГ", (first_start + timedelta(days=1)).strftime("%d.%m.%Y"))
            fill("По · ДД.ММ.ГГГГ", (later_start - timedelta(days=1)).strftime("%d.%m.%Y"))
            await press("Найти")
            check(
                "Событий: 0" in visible_texts(app.content),
                "Интервал без сеансов даёт пустой результат",
            )
            await press("Сбросить фильтры")
            check(
                all(
                    not c.value
                    for c in controls()
                    if isinstance(c, ft.TextField)
                    and c.label in ("Найти событие", "С · ДД.ММ.ГГГГ", "По · ДД.ММ.ГГГГ")
                ),
                "Сброс очищает поиск и обе границы интервала",
            )
            app.event_detail(event_id)
            await snapshot("event")
            await press("Выбрать места", index=0)
            await snapshot("seats")
            seat = app.service.seat_map(session_id)[0]
            cell = next(
                c
                for c in controls()
                if isinstance(c, ft.Container)
                and str(getattr(c, "tooltip", "")).startswith(
                    f"Ряд {seat['row']}, место {seat['number']} ·"
                )
            )
            await hover(cell, "свободное место")
            await invoke(cell.on_click)
            selected_color = cell.bgcolor
            await hover(cell, "выбранное место", "seat-hover")
            check(cell.bgcolor == selected_color, "Наведение сохраняет цвет выбранного места")
            await press("Добавить в корзину")
            dismiss()
            check(
                len(app.service.get_cart()) == 1,
                "Выбор места и добавление в корзину через обработчики",
            )
            await snapshot("cart")
            event_link = next(
                c
                for c in controls()
                if isinstance(c, ft.TextButton) and c.tooltip == "К мероприятию"
            )
            await invoke(event_link.on_click)
            check(
                "Вечер в EventSeat" in visible_texts(app.content) and bool(buttons("К корзине")),
                "Ссылка в заголовке корзины открывает мероприятие с контекстным возвратом",
            )
            await navigate("Профиль")
            await navigate("Афиша")
            check(
                app._route.get("event_id") == event_id and bool(buttons("К корзине")),
                "Возврат из профиля сохраняет мероприятие и путь обратно к корзине",
            )
            await press("К корзине")
            check(len(app.service.get_cart()) == 1, "Возврат к корзине сохраняет выбранные места")
            stale_checkout = buttons("Подтвердить бронирование")[0].on_click
            await choose("Аккаунты", admin_id)
            check(
                app.service.current_user["id"] == admin_id,
                "Переключение в администратора без пароля",
            )
            check(
                "Корзина" not in visible_texts(app.nav), "Переключение обновляет навигацию по роли"
            )
            admin_context = app.service
            admin_dialogs = [id(d) for d in page._dialogs.controls if d.open]
            await invoke(stale_checkout)
            check(
                app.service is admin_context
                and app.service.current_user["id"] == admin_id
                and [id(d) for d in page._dialogs.controls if d.open] == admin_dialogs,
                "Старый обработчик оформления игнорируется после смены аккаунта",
            )
            await choose("Аккаунты", user_id)
            check(
                app.service.current_user["id"] == user_id
                and len(app.service.get_cart()) == 1
                and not app.service.list_bookings(),
                "Переключение и устаревший обработчик не меняют корзину и брони пользователя",
            )
            await invoke(app.account_menu.trigger.on_click)
            check(
                app.account_menu.popup.width > app.account_menu.trigger.width
                and all(item.tooltip is None for item in app.account_menu.items),
                "Меню аккаунтов шире кнопки и не показывает лишние подсказки",
            )
            await snapshot("account-switcher")
            app.account_menu.close()
            await choose("Аккаунты", admin_id)
            app.navigate("Профиль", app.profile)
            await press("Выйти из аккаунта")
            check(
                app.service.current_user["id"] == user_id
                and [u["id"] for u in app.accounts.users] == [user_id],
                "Выход из профиля удаляет аккаунт из переключателя и открывает оставшийся",
            )
            app.navigate("Корзина", app.cart)
            await press("Подтвердить бронирование")
            dismiss()
            booking = app.service.list_bookings()[0]
            check(
                booking["status"] == "active" and not app.service.get_cart(),
                "Подтверждение бронирования и очистка корзины",
            )
            await snapshot("bookings")
            await booking_period("past")
            check(
                "Нет прошедших бронирований" in visible_texts(),
                "Пустая история понятна пользователю",
            )
            await booking_period("upcoming")
            await press("К мероприятию")
            check(
                "Вечер в EventSeat" in visible_texts(app.content)
                and bool(buttons("К бронированиям")),
                "Из подтверждённой брони открыт экран мероприятия",
            )
            await press("К бронированиям")
            await press("Электронный билет")
            await snapshot("ticket", dialog=True)
            ticket_dialog = next(
                d for d in page._dialogs.controls if d.open and isinstance(d, ft.AlertDialog)
            )
            ticket_map_button = next(
                c
                for c in descendants(ticket_dialog)
                if getattr(c, "content", None) == "Места на схеме"
            )
            await invoke(ticket_map_button.on_click)
            selected_positions = [
                c.data["seat_position"]
                for c in controls()
                if isinstance(getattr(c, "data", None), dict)
                and c.data.get("selected")
                and "seat_position" in c.data
            ]
            check(
                selected_positions == [[seat["row"], seat["number"]]],
                "Карта билета выделяет только места этого бронирования",
            )
            check(
                not any(
                    c.on_click
                    for c in controls()
                    if isinstance(c, ft.Container)
                    and isinstance(c.data, dict)
                    and "seat_position" in c.data
                ),
                "Карта билета доступна только для просмотра",
            )
            await snapshot("ticket-seat-map")
            await press("К сеансу")
            check(
                f"Сеанс #{session_id}" in visible_texts(app.content),
                "Из карты билета открыт конкретный сеанс",
            )
            await press("К схеме билета")
            check(
                "Места по билету" in visible_texts(app.content),
                "Возврат сохраняет карту конкретного билета",
            )
            dismiss()
            await press("Добавить аккаунт")
            fill("Логин", "qa_admin")
            fill("Пароль", password)
            await press("Войти")
            app.admin("Бронирования")
            fill("Поиск по номеру, имени или логину", booking["number"])
            await choose("Сеанс", session_id)
            await choose("Состояние брони", "active")
            await press("Применить фильтры")
            check(
                "Найдено бронирований: 1" in visible_texts(app.content)
                and any(
                    isinstance(getattr(c, "data", None), dict)
                    and c.data.get("booking_id") == booking["id"]
                    for c in controls()
                ),
                "Общие фильтры находят оформленную бронь по номеру, сеансу и статусу",
            )
            metrics = {}
            for c in descendants(app.content):
                if isinstance(c, ft.Container) and isinstance(c.content, ft.Column):
                    parts = c.content.controls
                    if len(parts) == 2 and all(isinstance(v, ft.Text) for v in parts):
                        metrics[parts[0].value] = parts[1].value
            check(
                metrics.get("Бронирований по фильтру") == "1"
                and metrics.get("Билетов по фильтру") == "1"
                and metrics.get("Сумма по фильтру") == money(booking["total"]),
                "Показатели соответствуют отфильтрованному списку бронирований",
            )
            await snapshot("combined-bookings")
            original_controls = list(app.content.controls)
            booking_card = next(
                c
                for c in controls()
                if isinstance(getattr(c, "data", None), dict)
                and c.data.get("booking_id") == booking["id"]
            )
            measured = {}

            def measure_card(event):
                measured.update(width=event.width, height=event.height)

            app.show(ft.Container(booking_card, on_size_change=measure_card))
            await asyncio.sleep(0.5)
            check(
                150 < measured.get("height", 0) < 600,
                "Карточка бронирования имеет компактную реальную высоту без серого блока",
            )
            report["booking_card_size"] = measured.copy()
            await snapshot("booking-card-full")
            measured.clear()
            app.show(ft.Container(booking_card, width=480, on_size_change=measure_card))
            await asyncio.sleep(0.5)
            check(
                150 < measured.get("height", 0) < 700 and measured.get("width", 0) <= 480,
                "Карточка в узкой области переносит строки и кнопки без лишней высоты",
            )
            report["narrow_booking_card_size"] = measured.copy()
            await snapshot("booking-card-narrow")
            app.show(*original_controls)
            fill("Поиск по номеру, имени или логину", "несуществующее бронирование")
            await press("Применить фильтры")
            check(
                "Найдено бронирований: 0" in visible_texts(app.content),
                "Общий поиск обновляет список без совпадений",
            )
            await choose("Аккаунты", user_id)
            dismiss()
            app.profile()
            fill("Отображаемое имя", "Имя в черновике профиля")
            fill("Текущий пароль", "Временное значение для проверки")
            app.navigate("Афиша", app.catalogue)
            app.navigate("Профиль", app.profile)
            check(
                next(
                    c
                    for c in controls()
                    if isinstance(c, ft.TextField) and c.label == "Отображаемое имя"
                ).value
                == "Имя в черновике профиля"
                and not next(
                    c
                    for c in controls()
                    if isinstance(c, ft.TextField) and c.label == "Текущий пароль"
                ).value,
                "Имя профиля сохраняется при навигации; пароль очищается",
            )
            await snapshot("profile")
            (folder / "state.json").write_text(
                json.dumps(
                    {
                        "booking_id": booking["id"],
                        "event_id": event_id,
                        "session_id": session_id,
                        "seat_id": seat["id"],
                    }
                ),
                encoding="utf-8",
            )
        else:
            state = json.loads((folder / "state.json").read_text(encoding="utf-8"))
            check(not app.service.needs_setup(), "Администратор сохранён после закрытия процесса")
            check(
                app.service.current_user is None and not app.accounts.users,
                "После нового запуска нет сохранённых входов и требуется пароль",
            )
            fill("Логин", "qa_user")
            fill("Пароль", os.environ["EVENTSEAT_QA_PASSWORD"])
            await press("Войти")
            booking = app.service.get_booking(state["booking_id"])
            check(booking["status"] == "active", "Билет сохранён после повторного запуска EXE")
            app.navigate("Мои бронирования", app.bookings)
            await snapshot("persisted-booking")
            await press("Отменить")
            await press("Подтвердить")
            check(
                app.service.get_booking(booking["id"])["status"] == "cancelled",
                "Отмена через обработчик диалога с сохранением истории",
            )
            check(
                next(
                    s
                    for s in app.service.seat_map(state["session_id"])
                    if s["id"] == state["seat_id"]
                )["status"]
                == "free",
                "Отмена освобождает место",
            )
            await snapshot("cancelled-booking")
            dismiss()
            await press("К мероприятию")
            check(
                "Вечер в EventSeat" in visible_texts(app.content)
                and bool(buttons("К бронированиям")),
                "Из отменённой брони можно перейти к мероприятию",
            )
            await press("К бронированиям")
            await press("Электронный билет")
            check("Состояние: отменено" in visible_texts(), "Отменённый билет доступен в истории")
            await snapshot("cancelled-ticket", dialog=True)
            ticket_dialog = next(
                d for d in page._dialogs.controls if d.open and isinstance(d, ft.AlertDialog)
            )
            ticket_map_button = next(
                c
                for c in descendants(ticket_dialog)
                if getattr(c, "content", None) == "Места на схеме"
            )
            await invoke(ticket_map_button.on_click)
            check(
                any(
                    isinstance(getattr(c, "data", None), dict) and c.data.get("selected")
                    for c in controls()
                ),
                "Карта отменённого билета сохраняет историческое выделение мест",
            )
            await snapshot("cancelled-ticket-map")
            dismiss()
            app.bookings()
            check(
                bool(buttons("Предстоящие · 1")),
                "Отменённая будущая бронь остаётся в предстоящих",
            )
            # Time travel affects only the isolated --verify-ui database.
            past = (datetime.now() - timedelta(days=1)).isoformat(sep=" ")
            with sqlite3.connect(app.service.store.path) as connection:
                connection.execute(
                    "UPDATE bookings SET start = ? WHERE id = ?", (past, booking["id"])
                )
            app.bookings()
            check(
                bool(buttons("Предстоящие · 0")) and bool(buttons("Прошедшие · 1")),
                "Бронь переходит в историю исключительно по времени сеанса",
            )
            await booking_period("past")
            await snapshot("past-bookings")
            app.navigate("Профиль", app.profile)
            app.navigate("Мои бронирования", lambda: app.restore_section("Мои бронирования"))
            check(
                app._route["tab"] == "past", "Вкладка истории сохраняется после перехода в профиль"
            )
        report["success"] = True
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        (folder / f"report-{phase}.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        app.close()
        await page.window.destroy()
