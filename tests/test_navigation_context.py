import json
from copy import deepcopy
from types import SimpleNamespace

import flet as ft
import pytest

from eventseat.ui_admin import AdminUI

from .conftest import seat_ids
from .test_view_state import (
    add_logged_in_account,
    button,
    descendants,
    form_field,
    invoke,
)
from .test_view_state import app_factory as app_factory


def go_back(app):
    control = next(
        c
        for c in descendants(app.content)
        if isinstance(c, (ft.Button, ft.OutlinedButton, ft.TextButton))
        and c.icon == ft.Icons.ARROW_BACK
    )
    invoke(control.on_click)


def open_session_card(app, session_id):
    card = next(
        c for c in descendants(app.content) if str(getattr(c, "key", "")) == f"session-{session_id}"
    )
    invoke(card.on_click)


def make_booking(system):
    user, session = system["user"], system["session"]
    user.add_to_cart(session, seat_ids(user, session)[:1])
    return user.checkout("navigation-context")[0]


def test_new_session_hall_back_restores_same_draft_and_event_context(app_factory, system):
    app = app_factory("admin")
    app.admin("Мероприятия")
    invoke(button(app, "Создать сеанс").on_click)
    assert app._route["event_id"] == system["event"]
    form_field(app, "Дата · ДД.ММ.ГГГГ").value = "12."
    form_field(app, "Время · ЧЧ:ММ").value = "19:"
    invoke(button(app, "К залу").on_click)
    assert app._route["page"] == "hall"
    invoke(button(app, "К созданию сеанса").on_click)
    assert app._route["page"] == "session_form"
    assert app._route["session_id"] is None
    assert app._route["event_id"] == system["event"]
    assert form_field(app, "Мероприятие").value == str(system["event"])
    assert form_field(app, "Дата · ДД.ММ.ГГГГ").value == "12."
    assert form_field(app, "Время · ЧЧ:ММ").value == "19:"
    go_back(app)
    assert app._route["page"] == "list"
    assert app._route["tab"] == "Мероприятия"


def test_event_save_returns_to_session_editor_without_losing_unsaved_time(app_factory, system):
    app = app_factory("admin")
    app.admin("Сеансы")
    open_session_card(app, system["session"])
    form_field(app, "Время · ЧЧ:ММ").value = "20:"
    invoke(button(app, "К мероприятию").on_click)
    form_field(app, "Название").value = "Северный свет — новое название"
    invoke(button(app, "Опубликовать").on_click)
    assert app._route["page"] == "session_form"
    assert app._route["session_id"] == system["session"]
    assert form_field(app, "Время · ЧЧ:ММ").value == "20:"
    assert system["admin"].get_event(system["event"])["title"] == "Северный свет — новое название"
    assert f"event:{system['event']}" not in app.view_state.drafts
    go_back(app)
    assert app._route["page"] == "list"
    assert app._route["tab"] == "Сеансы"


def test_hall_save_returns_to_session_and_clears_only_hall_draft(app_factory, system):
    app = app_factory("admin")
    app.admin("Сеансы")
    invoke(button(app, "Создать сеанс").on_click)
    form_field(app, "Время · ЧЧ:ММ").value = "22:"
    invoke(button(app, "К залу").on_click)
    form_field(app, "Название зала").value = "Малый зал — обновлённый"
    invoke(button(app, "Сохранить зал").on_click)
    assert app._route["page"] == "session_form"
    assert app._route["session_id"] is None
    assert form_field(app, "Время · ЧЧ:ММ").value == "22:"
    assert system["admin"].get_hall(system["hall"])["name"] == "Малый зал — обновлённый"
    assert f"hall:{system['hall']}" not in app.view_state.drafts
    go_back(app)
    assert app._route["page"] == "list" and app._route["tab"] == "Сеансы"


@pytest.mark.parametrize(
    "editor, label, tab",
    [
        ("event", "К мероприятиям", "Мероприятия"),
        ("session", "К сеансам", "Сеансы"),
        ("hall", "К залам", "Залы"),
    ],
)
def test_direct_editor_entry_retains_safe_list_fallback(app_factory, system, editor, label, tab):
    app = app_factory("admin")
    admin = AdminUI(app)
    getattr(admin, f"{editor}_form")(system[editor])
    assert "return_to" not in app._route
    invoke(button(app, label).on_click)
    assert app._route["page"] == "list" and app._route["tab"] == tab
    assert "return_to" not in app._route


def test_admin_booking_session_hall_chain_returns_to_filtered_bookings(app_factory, system):
    booking = make_booking(system)
    app = app_factory("admin")
    app.admin("Бронирования")
    form_field(app, "Поиск по номеру, имени или логину").value = booking["number"]
    invoke(button(app, "Применить фильтры").on_click)
    applied = deepcopy(app.view_state.drafts["admin:booking_applied"])
    form_field(app, "С · ДД.ММ.ГГГГ").value = "12."
    invoke(button(app, "К сеансу").on_click)
    invoke(button(app, "К залу").on_click)
    invoke(button(app, "К редактированию сеанса").on_click)
    assert app._route["session_id"] == system["session"]
    go_back(app)
    assert app._route["tab"] == "Бронирования"
    assert form_field(app, "Поиск по номеру, имени или логину").value == booking["number"]
    assert form_field(app, "С · ДД.ММ.ГГГГ").value == "12."
    assert app.view_state.drafts["admin:booking_applied"] == applied
    assert any(
        getattr(c, "data", None) == {"booking_id": booking["id"]} for c in descendants(app.content)
    )


def test_origin_chain_survives_profile_and_account_switch_as_plain_values(app_factory, system):
    app = app_factory("admin")
    admin_id = app.service.current_user["id"]
    app.admin("Мероприятия")
    invoke(button(app, "Создать сеанс").on_click)
    form_field(app, "Время · ЧЧ:ММ").value = "21:"
    invoke(button(app, "К залу").on_click)
    expected = deepcopy(app._route)
    json.dumps(expected)
    app.navigate("Профиль", app.profile)
    app.navigate("Администрирование", app.admin)
    assert app._route == expected
    add_logged_in_account(app)
    assert app.service.current_user["id"] != admin_id
    assert "Администрирование" not in app.view_state.routes
    app.switch_account(admin_id)
    assert app._route == expected
    invoke(button(app, "К созданию сеанса").on_click)
    assert app._route["event_id"] == system["event"]
    assert form_field(app, "Время · ЧЧ:ММ").value == "21:"
    go_back(app)
    assert app._route["tab"] == "Мероприятия"


def test_nested_session_list_restores_parent_raw_applied_filters_and_scroll(app_factory, system):
    app = app_factory("admin")
    app.admin("Сеансы")
    day = system["start"].strftime("%d.%m.%Y")
    form_field(app, "Мероприятие").value = str(system["event"])
    form_field(app, "С · ДД.ММ.ГГГГ").value = day
    form_field(app, "По · ДД.ММ.ГГГГ").value = day
    invoke(button(app, "Применить").on_click)
    applied = deepcopy(app.view_state.drafts["admin:session_applied"])
    form_field(app, "С · ДД.ММ.ГГГГ").value = "12."
    app.content.on_scroll(SimpleNamespace(pixels=427.0))
    open_session_card(app, system["session"])
    invoke(button(app, "К мероприятию").on_click)
    invoke(button(app, "Сеансы мероприятия").on_click)
    assert form_field(app, "С · ДД.ММ.ГГГГ").value == ""
    app.content.on_scroll(SimpleNamespace(pixels=93.0))
    go_back(app)
    assert app._route["page"] == "event_form"
    go_back(app)
    assert app._route["page"] == "session_form"
    go_back(app)
    assert app._route["page"] == "list" and app._route["tab"] == "Сеансы"
    assert form_field(app, "С · ДД.ММ.ГГГГ").value == "12."
    assert form_field(app, "По · ДД.ММ.ГГГГ").value == day
    assert app.view_state.drafts["admin:session_applied"] == applied
    scroll_task, args = app.page.tasks[-1]
    assert scroll_task == app._restore_scroll and args[1] == 427.0


def test_cart_event_seats_chain_survives_profile_and_returns_to_cart(app_factory, system):
    system["user"].add_to_cart(system["session"], seat_ids(system["user"], system["session"])[:1])
    app = app_factory()
    app.cart()
    invoke(button(app, "К мероприятию").on_click)
    invoke(button(app, "Выбрать места").on_click)
    assert app._route["page"] == "seats"
    app.navigate("Профиль", app.profile)
    app.navigate("Афиша", lambda: app.restore_section("Афиша"))
    assert app._route["page"] == "seats"
    go_back(app)
    assert app._route["page"] == "event"
    go_back(app)
    assert app._route["page"] == "cart"
    assert len(app.service.get_cart()) == 1


def test_booking_map_session_event_back_chain_keeps_ticket_origin(app_factory, system):
    booking = make_booking(system)
    app = app_factory()
    app.bookings()
    invoke(button(app, "Места на схеме").on_click)
    invoke(button(app, "К сеансу").on_click)
    invoke(button(app, "К мероприятию").on_click)
    go_back(app)
    assert app._route["page"] == "session"
    go_back(app)
    assert app._route["page"] == "booking_map"
    assert app._route["booking_id"] == booking["id"]
    go_back(app)
    assert app._route["page"] == "bookings"


def test_queued_double_create_does_not_make_new_session_its_own_origin(app_factory, system):
    app = app_factory("admin")
    app.admin("Мероприятия")
    create = button(app, "Создать сеанс").on_click
    invoke(create)
    first_route = deepcopy(app._route)
    form_field(app, "Время · ЧЧ:ММ").value = "21:"
    invoke(create)
    assert app._route == first_route
    assert form_field(app, "Время · ЧЧ:ММ").value == "21:"
    go_back(app)
    assert app._route["page"] == "list" and app._route["tab"] == "Мероприятия"


def test_queued_double_hall_link_keeps_session_draft_and_single_return_step(app_factory, system):
    app = app_factory("admin")
    app.admin("Сеансы")
    open_session_card(app, system["session"])
    form_field(app, "Время · ЧЧ:ММ").value = "20:"
    forward = button(app, "К залу").on_click
    invoke(forward)
    first_route = deepcopy(app._route)
    form_field(app, "Название зала").value = "Черновик после первого клика"
    invoke(forward)
    assert app._route == first_route
    assert form_field(app, "Название зала").value == "Черновик после первого клика"
    go_back(app)
    assert app._route["page"] == "session_form"
    assert app._route["session_id"] == system["session"]
    assert form_field(app, "Время · ЧЧ:ММ").value == "20:"
    go_back(app)
    assert app._route["page"] == "list" and app._route["tab"] == "Сеансы"


def test_stale_hall_back_callback_cannot_pop_the_restored_session_origin(app_factory, system):
    app = app_factory("admin")
    app.admin("Сеансы")
    open_session_card(app, system["session"])
    form_field(app, "Время · ЧЧ:ММ").value = "20:"
    invoke(button(app, "К залу").on_click)
    back = button(app, "К редактированию сеанса").on_click
    invoke(back)
    restored_route = deepcopy(app._route)
    invoke(back)
    assert app._route == restored_route
    assert app._route["page"] == "session_form"
    assert form_field(app, "Время · ЧЧ:ММ").value == "20:"
    go_back(app)
    assert app._route["page"] == "list" and app._route["tab"] == "Сеансы"
