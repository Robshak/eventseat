import asyncio
import inspect
import json
import os
import traceback
from datetime import datetime, timedelta
from pathlib import Path

import flet as ft

from eventseat.config import asset_path


def descendants(control):
    yield control
    for attr in ("content", "controls", "actions", "title"):
        child = getattr(control, attr, None)
        if isinstance(child, ft.Control):
            yield from descendants(child)
        elif isinstance(child, list):
            for item in child:
                if isinstance(item, ft.Control):
                    yield from descendants(item)


async def verify(app, folder: Path, phase: str):
    page, service = app.page, app.service
    report = {"phase": phase, "checks": [], "screenshots": [], "success": False}

    def check(condition, label):
        if not condition:
            raise AssertionError(label)
        report["checks"].append(label)

    def controls():
        roots = list(page.controls)
        roots.extend(d for d in page._dialogs.controls if d.open)
        return [child for root in roots for child in descendants(root)]

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
            await matches[0].on_select(None)

    async def press(label):
        matches = [
            c
            for c in controls()
            if getattr(c, "content", None) == label and callable(getattr(c, "on_click", None))
        ]
        if len(matches) != 1:
            raise AssertionError(f"Кнопка {label}: найдено {len(matches)}")
        result = matches[0].on_click(None)
        if inspect.isawaitable(result):
            await result
        page.update()
        await asyncio.sleep(0.15)

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
            check(service.needs_setup(), "Первый запуск без встроенного администратора")
            await snapshot("first-launch")
            password = os.environ["EVENTSEAT_QA_PASSWORD"]
            fill("Логин", "qa_admin")
            fill("Отображаемое имя", "Администратор проверки")
            fill("Пароль", password)
            fill("Повторите пароль", password)
            await press("Создать администратора")
            check(
                service.current_user["role"] == "admin",
                "Создание администратора через обработчик формы",
            )
            await snapshot("catalogue")
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
            await snapshot("hall-editor")
            await press("Предпросмотр")
            await snapshot("hall-preview", dialog=True)
            await press("Закрыть")
            await press("Сохранить зал")
            dismiss()
            hall_id = next(
                h["id"] for h in service.list_halls() if h["name"] == "Зал сквозной проверки"
            )
            check(
                service.get_hall(hall_id)["rows"] == 3,
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
                for e in service.list_events(admin=True)
                if e["title"] == "Вечер в EventSeat"
            )
            app.admin("Сеансы")
            await press("Создать сеанс")
            await choose("Мероприятие", event_id)
            await choose("Зал", hall_id)
            fill("Дата · ДД.ММ.ГГГГ", (datetime.now() + timedelta(days=10)).strftime("%d.%m.%Y"))
            fill("Время · ЧЧ:ММ", "19:00")
            await snapshot("session-editor", dialog=True)
            await press("Сохранить сеанс")
            dismiss()
            session_id = service.list_sessions(event_id)[0]["id"]
            check(
                service.get_session(session_id)["hall_id"] == hall_id,
                "Публикация мероприятия и создание сеанса через формы",
            )
            await snapshot("admin-sessions")
            app.admin("Статистика")
            await snapshot("admin-statistics")
            app.logout()
            app.auth("register")
            fill("Логин", "qa_user")
            fill("Отображаемое имя", "Гость EventSeat")
            fill("Пароль", password)
            fill("Повторите пароль", password)
            await press("Зарегистрироваться")
            check(service.current_user["role"] == "user", "Регистрация создаёт только пользователя")
            app.event_detail(event_id)
            await snapshot("event")
            await press("Выбрать места")
            await snapshot("seats")
            seat = service.seat_map(session_id)[0]
            cell = next(
                c
                for c in controls()
                if isinstance(c, ft.Container)
                and str(getattr(c, "tooltip", "")).startswith(
                    f"Ряд {seat['row']}, место {seat['number']} ·"
                )
            )
            await cell.on_click(None)
            await press("Добавить в корзину")
            dismiss()
            check(
                len(service.get_cart()) == 1, "Выбор места и добавление в корзину через обработчики"
            )
            await snapshot("cart")
            await press("Подтвердить бронирование")
            dismiss()
            booking = service.list_bookings()[0]
            check(
                booking["status"] == "active" and not service.get_cart(),
                "Подтверждение бронирования и очистка корзины",
            )
            await snapshot("bookings")
            await press("Электронный билет")
            await snapshot("ticket", dialog=True)
            dismiss()
            app.profile()
            await snapshot("profile")
            (folder / "state.json").write_text(
                json.dumps(
                    {"booking_id": booking["id"], "session_id": session_id, "seat_id": seat["id"]}
                ),
                encoding="utf-8",
            )
        else:
            state = json.loads((folder / "state.json").read_text(encoding="utf-8"))
            check(not service.needs_setup(), "Администратор сохранён после закрытия процесса")
            fill("Логин", "qa_user")
            fill("Пароль", os.environ["EVENTSEAT_QA_PASSWORD"])
            await press("Войти")
            booking = service.get_booking(state["booking_id"])
            check(booking["status"] == "active", "Билет сохранён после повторного запуска EXE")
            app.navigate("Мои бронирования", app.bookings)
            await snapshot("persisted-booking")
            await press("Отменить")
            await press("Подтвердить")
            check(
                service.get_booking(booking["id"])["status"] == "cancelled",
                "Отмена через обработчик диалога с сохранением истории",
            )
            check(
                next(
                    s for s in service.seat_map(state["session_id"]) if s["id"] == state["seat_id"]
                )["status"]
                == "free",
                "Отмена освобождает место",
            )
            await snapshot("cancelled-booking")
        report["success"] = True
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        (folder / f"report-{phase}.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        service.close()
        await page.window.destroy()
