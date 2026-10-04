import asyncio
import inspect
import json
import os
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
        return [
            c
            for c in controls()
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

    async def choose(label, value):
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

    async def calendar(tooltip, day):
        matches = [
            c
            for c in controls()
            if getattr(c, "tooltip", None) == tooltip and callable(getattr(c, "on_click", None))
        ]
        check(len(matches) == 1, f"Календарь доступен: {tooltip}")
        await invoke(matches[0].on_click)
        picker = next(c for c in controls() if isinstance(c, ft.DatePicker) and c.open)
        picker.value = day
        await invoke(picker.on_change, SimpleNamespace(control=picker))
        picker.open = False
        page.update()

    async def hover(control, label, screenshot=None):
        check(callable(control.on_hover), f"Есть обработчик наведения: {label}")
        initial = (str(control.border), control.bgcolor)
        await invoke(control.on_hover, SimpleNamespace(control=control, data="true"))
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
            for name in ("cinema.png", "concert.png", "lecture.png"):
                check(asset_path(name).is_file(), f"Автономный ресурс: {name}")
            app.navigate("Администрирование", lambda: app.admin("Залы"))
            await press("Создать зал")
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
            await choose("Действие при нажатии", "aisle")
            cell = next(
                c
                for c in controls()
                if isinstance(c, ft.Container)
                and str(getattr(c, "tooltip", "")).startswith("Ряд 3, место 5 ·")
            )
            await cell.on_click(None)
            await choose("Действие при нажатии", "эконом")
            economy_cell = next(
                c
                for c in controls()
                if isinstance(c, ft.Container)
                and str(getattr(c, "tooltip", "")).startswith("Ряд 3, место 6 ·")
            )
            await invoke(economy_cell.on_click)
            await snapshot("hall-editor")
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
            await snapshot("admin-halls")
            app.admin("Мероприятия")
            await press("Создать мероприятие")
            fill("Название", "Вечер в EventSeat")
            fill("Описание", "Сквозная проверка нового зала, расписания и билетов.")
            fill("Продолжительность, мин", "100")
            await snapshot("event-editor", dialog=True)
            await press("Опубликовать")
            dismiss()
            event_id = next(
                e["id"]
                for e in app.service.list_events(admin=True)
                if e["title"] == "Вечер в EventSeat"
            )
            app.admin("Сеансы")
            await press("Создать сеанс")
            await choose("Мероприятие", event_id)
            await choose("Зал", hall_id)
            first_start = (datetime.now() + timedelta(days=10)).replace(
                hour=19, minute=0, second=0, microsecond=0
            )
            fill("Дата · ДД.ММ.ГГГГ", first_start.strftime("%d.%m.%Y"))
            fill("Время · ЧЧ:ММ", "19:00")
            await snapshot("session-editor", dialog=True)
            await press("Сохранить сеанс")
            dismiss()
            session_id = app.service.list_sessions(event_id)[0]["id"]
            check(
                app.service.get_session(session_id)["hall_id"] == hall_id,
                "Публикация мероприятия и создание сеанса через формы",
            )
            await snapshot("admin-sessions")
            app.admin("Статистика")
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
            later_start = first_start + timedelta(days=10)
            app.service.save_session(
                event_id,
                hall_id,
                later_start,
                {"эконом": 120000, "стандарт": 135000, "VIP": 160000},
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
            fill("Найти событие", "Вечер в EventSeat")
            await calendar("Выбрать начальную дату", later_start)
            await calendar("Выбрать конечную дату", later_start)
            await press("Найти")
            from eventseat.ui import date_text, money

            check(
                "Событий: 1" in visible_texts(app.content)
                and date_text(later_start) in visible_texts(app.content)
                and "от " + money(120000) in visible_texts(app.content),
                "Обе границы включены; карточка показывает дату и цену сеанса внутри интервала",
            )
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
            await press("К мероприятию")
            check(
                "Вечер в EventSeat" in visible_texts(app.content) and bool(buttons("К корзине")),
                "Из корзины открыт экран мероприятия",
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
            await snapshot("account-switcher")
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
            await press("К мероприятию")
            check(
                "Вечер в EventSeat" in visible_texts(app.content)
                and bool(buttons("К бронированиям")),
                "Из подтверждённой брони открыт экран мероприятия",
            )
            await press("К бронированиям")
            await press("Электронный билет")
            await snapshot("ticket", dialog=True)
            dismiss()
            app.profile()
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
        report["success"] = True
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        (folder / f"report-{phase}.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        app.close()
        await page.window.destroy()
