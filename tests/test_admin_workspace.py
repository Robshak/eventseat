from datetime import timedelta

import flet as ft
import pytest

from eventseat.ui import money
from eventseat.ui_admin import AdminUI

from .conftest import seat_ids
from .test_view_state import app_factory as app_factory
from .test_view_state import button, descendants, form_field, invoke


def button_labels(app):
    return [
        c.content
        for c in descendants(app.content)
        if isinstance(c, (ft.Button, ft.OutlinedButton, ft.TextButton))
        and isinstance(c.content, str)
    ]


def metric(app, label):
    for control in descendants(app.content):
        if not isinstance(control, ft.Container) or not isinstance(control.content, ft.Column):
            continue
        children = control.content.controls
        if len(children) == 2 and isinstance(children[0], ft.Text) and children[0].value == label:
            return children[1].value
    raise AssertionError(f"Метрика не найдена: {label}")


def booking_ids(app):
    return {
        c.data["booking_id"]
        for c in descendants(app.content)
        if isinstance(getattr(c, "data", None), dict) and "booking_id" in c.data
    }


def test_admin_has_four_tabs_and_statistics_route_opens_combined_bookings(app_factory):
    app = app_factory("admin")
    admin = AdminUI(app)
    assert [c.content for c in admin.tabs("Бронирования").controls] == [
        "Мероприятия",
        "Сеансы",
        "Залы",
        "Бронирования",
    ]
    admin.show("Статистика")
    assert app._route["tab"] == "Бронирования"
    assert "Статистика" not in button_labels(app)
    assert metric(app, "Бронирований по фильтру") == "0"
    assert "Применить фильтры" in button_labels(app)


def test_event_title_is_plain_text_and_card_opens_event_sessions(app_factory, system):
    app = app_factory("admin")
    app.admin("Мероприятия")
    card = next(c for c in descendants(app.content) if isinstance(c, ft.Container) and c.on_click)
    assert not any(isinstance(c, ft.TextButton) for c in descendants(card))
    invoke(card.on_click)
    assert app._route["tab"] == "Сеансы"
    assert form_field(app, "Мероприятие").value == str(system["event"])


def test_session_card_opens_editor_directly_and_prices_live_only_in_hall(app_factory, system):
    app = app_factory("admin")
    app.admin("Сеансы")
    card = next(
        c
        for c in descendants(app.content)
        if str(getattr(c, "key", "")) == f"session-{system['session']}"
    )
    assert not any(isinstance(c, ft.TextButton) for c in descendants(card))
    assert "К залу" in button_labels(app)
    assert "Цены мест" not in button_labels(app)
    invoke(card.on_click)
    assert app._route["page"] == "session_form"
    assert app._route["session_id"] == system["session"]
    assert form_field(app, "Дата · ДД.ММ.ГГГГ").value == system["start"].strftime("%d.%m.%Y")
    assert not any(isinstance(c, ft.Checkbox) for c in descendants(app.content))
    assert not any(isinstance(c, ft.TextField) and "₽" in c.label for c in descendants(app.content))
    assert {"К залу", "К мероприятию", "Сохранить сеанс"} <= set(button_labels(app))
    assert not hasattr(AdminUI, "session_prices")


@pytest.mark.parametrize("destination", ["К залу", "К мероприятию"])
def test_session_links_keep_unsaved_editor_values(app_factory, system, destination):
    app = app_factory("admin")
    AdminUI(app).session_form(system["session"])
    form_field(app, "Время · ЧЧ:ММ").value = "18:"
    invoke(button(app, destination).on_click)
    assert app._route["page"] == ("hall" if destination == "К залу" else "event_form")
    AdminUI(app).session_form(system["session"])
    assert form_field(app, "Время · ЧЧ:ММ").value == "18:"


def test_cancelled_session_opens_readonly_editor_and_legacy_detail_route_is_compatible(
    app_factory, system
):
    system["admin"].cancel_session(system["session"], "Отмена события")
    app = app_factory("admin")
    AdminUI(app).session_detail(system["session"])
    assert app._route["page"] == "session_form"
    assert "Сохранить сеанс" not in button_labels(app)
    assert all(
        form_field(app, label).disabled
        for label in ("Мероприятие", "Зал", "Дата · ДД.ММ.ГГГГ", "Время · ЧЧ:ММ")
    )
    assert {"К залу", "К мероприятию"} <= set(button_labels(app))


def test_session_save_uses_hall_snapshot_without_price_arguments(app_factory, system, monkeypatch):
    app = app_factory("admin")
    admin = system["admin"]
    original = admin.save_session
    calls = []

    def record(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(admin, "save_session", record)
    AdminUI(app).session_form(system["session"])
    day = system["start"] + timedelta(days=1)
    form_field(app, "Дата · ДД.ММ.ГГГГ").value = day.strftime("%d.%m.%Y")
    invoke(button(app, "Сохранить сеанс").on_click)
    assert len(calls) == 1 and len(calls[0][0]) == 3
    assert calls[0][1] == {"session_id": system["session"]}
    assert admin.get_session(system["session"])["category_prices"] == system["prices"]


def test_shared_booking_filters_update_list_and_metrics_together(app_factory, system):
    user, other, session = system["user"], system["other"], system["session"]
    user.add_to_cart(session, seat_ids(user, session)[:2])
    alice = user.checkout("admin-ui-alice")[0]
    other.add_to_cart(session, seat_ids(other, session)[:1])
    boris = other.checkout("admin-ui-boris")[0]
    app = app_factory("admin")
    app.admin("Бронирования")
    assert booking_ids(app) == {alice["id"], boris["id"]}
    assert metric(app, "Бронирований по фильтру") == "2"
    assert metric(app, "Билетов по фильтру") == "3"
    assert metric(app, "Заполненность сеансов") == "50.0%"
    for label, value in {
        "Поиск по номеру, имени или логину": "alice",
        "Мероприятие": str(system["event"]),
        "Зал": str(system["hall"]),
        "Сеанс": str(session),
        "Состояние сеанса": "upcoming",
        "Состояние брони": "active",
        "С · ДД.ММ.ГГГГ": system["start"].strftime("%d.%m.%Y"),
        "По · ДД.ММ.ГГГГ": system["start"].strftime("%d.%m.%Y"),
    }.items():
        form_field(app, label).value = value
    invoke(button(app, "Применить фильтры").on_click)
    assert booking_ids(app) == {alice["id"]}
    assert metric(app, "Бронирований по фильтру") == "1"
    assert metric(app, "Билетов по фильтру") == "2"
    assert metric(app, "Сумма по фильтру") == money(alice["total"])
    assert metric(app, "Заполненность сеансов") == "50.0%"
    app.navigate("Профиль", app.profile)
    app.navigate("Администрирование", app.admin)
    assert booking_ids(app) == {alice["id"]}
    assert form_field(app, "Поиск по номеру, имени или логину").value == "alice"
    user.cancel_booking(alice["id"])
    form_field(app, "Состояние брони").value = "cancelled"
    invoke(button(app, "Применить фильтры").on_click)
    assert booking_ids(app) == {alice["id"]}
    assert metric(app, "Билетов по фильтру") == "2"
    assert metric(app, "Заполненность сеансов") == "16.7%"
    form_field(app, "Поиск по номеру, имени или логину").value = "нет такого пользователя"
    invoke(button(app, "Применить фильтры").on_click)
    assert booking_ids(app) == set()
    assert metric(app, "Бронирований по фильтру") == "0"
    assert metric(app, "Заполненность сеансов") == "16.7%"


def test_ticket_map_return_preserves_combined_booking_filters(app_factory, system):
    user, session = system["user"], system["session"]
    user.add_to_cart(session, seat_ids(user, session)[:1])
    booking = user.checkout("admin-map-return")[0]
    app = app_factory("admin")
    app.admin("Бронирования")
    form_field(app, "Поиск по номеру, имени или логину").value = booking["number"]
    invoke(button(app, "Применить фильтры").on_click)
    applied = dict(app.view_state.drafts["admin:booking_applied"])
    invoke(button(app, "Места на схеме").on_click)
    assert app._route["page"] == "booking_map"
    invoke(button(app, "К бронированиям").on_click)
    assert app._route["tab"] == "Бронирования"
    assert app.view_state.drafts["admin:booking_applied"] == applied
    assert booking_ids(app) == {booking["id"]}


@pytest.mark.parametrize("cancelled", [False, True])
def test_booking_card_keeps_long_details_and_actions_without_empty_flexible_space(
    app_factory, system, cancelled
):
    user, session = system["user"], system["session"]
    user.add_to_cart(session, seat_ids(user, session))
    booking = user.checkout("long-booking-card")[0]
    if cancelled:
        user.cancel_booking(booking["id"])
    app = app_factory("admin")
    app.admin("Бронирования")
    booking = app.service.list_bookings(admin=True)[0]
    booking.update(
        title="Большое музыкальное представление для всей семьи в вечернем формате",
        user_name="Александр Константинович Константинопольский",
        user_login="alexander_konstantinopolsky",
        hall_name="Большой концертный зал имени Петра Ильича Чайковского",
        total=123456789,
    )
    admin = AdminUI(app)
    card = admin.booking_card(booking)
    app.content.controls = [card]
    texts = [str(c.value) for c in descendants(card) if isinstance(c, ft.Text)]
    for expected in (
        booking["number"],
        money(booking["total"]),
        booking["title"],
        booking["user_name"],
        booking["hall_name"],
        "ряд 2, место 3",
        "Отменено" if cancelled else "Активно",
    ):
        assert any(expected in value for value in texts)
    assert ("Отменить" in button_labels(app)) is not cancelled
    for row in (c for c in descendants(card) if isinstance(c, ft.Row) and c.wrap):
        assert all(not getattr(c, "expand", False) for c in row.controls)
    invoke(button(app, "К сеансу").on_click)
    assert app._route["page"] == "session_form"
    assert app._route["session_id"] == session
