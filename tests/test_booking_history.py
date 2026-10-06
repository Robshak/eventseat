from copy import deepcopy
from datetime import datetime, timedelta

import flet as ft
import pytest

from .conftest import seat_ids
from .test_navigation_statistics import book
from .test_view_state import add_logged_in_account, button, descendants, invoke
from .test_view_state import app_factory as app_factory


def period_button(app, period):
    return next(
        control
        for control in descendants(app.content)
        if isinstance(getattr(control, "data", None), dict)
        and control.data.get("booking_period") == period
    )


def displayed_ids(app):
    return [
        control.data["booking_id"]
        for control in descendants(app.content)
        if isinstance(getattr(control, "data", None), dict) and "booking_id" in control.data
    ]


def test_history_groups_only_by_start_with_nearest_future_and_latest_past_first(
    app_factory, system, monkeypatch
):
    booking = book(system)
    now = datetime(2026, 10, 6, 19)

    class Clock:
        @staticmethod
        def now():
            return now

    monkeypatch.setattr("eventseat.ui.datetime", Clock)
    history = []
    for bid, delta, status in [
        (1, 1, "cancelled"),
        (2, -3, "completed"),
        (3, 3, "active"),
        (4, -1, "cancelled"),
        (5, 0, "active"),
    ]:
        item = deepcopy(booking)
        item.update(id=bid, start=now + timedelta(hours=delta), status=status)
        history.append(item)
    app = app_factory()
    monkeypatch.setattr(app.service, "list_bookings", lambda **kwargs: history)
    app.bookings()
    assert displayed_ids(app) == [1, 3]
    assert period_button(app, "upcoming").content == "Предстоящие · 2"
    invoke(period_button(app, "past").on_click)
    assert displayed_ids(app) == [5, 4, 2]
    assert period_button(app, "past").content == "Прошедшие · 3"
    assert not any(
        getattr(control, "content", None) == "Отменить" for control in descendants(app.content)
    )


def test_history_period_survives_navigation_event_return_and_account_switch(
    app_factory, system, monkeypatch
):
    booking = book(system)
    booking["start"] = datetime.now() - timedelta(days=1)
    booking["status"] = "completed"
    app = app_factory()
    monkeypatch.setattr(app.service, "list_bookings", lambda **kwargs: [booking])
    alice = app.service.current_user["id"]
    app.bookings(period="past")
    invoke(button(app, "К мероприятию").on_click)
    invoke(button(app, "К бронированиям").on_click)
    assert app._route["tab"] == "past"
    assert displayed_ids(app) == [booking["id"]]
    app.navigate("Профиль", app.profile)
    app.navigate("Мои бронирования", lambda: app.restore_section("Мои бронирования"))
    assert app._route["tab"] == "past"
    add_logged_in_account(app)
    app.bookings()
    assert app._route["tab"] == "upcoming"
    app.switch_account(alice)
    assert app._route["tab"] == "past"


@pytest.mark.parametrize("period", ["upcoming", "past"])
def test_empty_history_has_clear_message_and_other_period_available(app_factory, period):
    app = app_factory()
    app.bookings(period=period)
    assert displayed_ids(app) == []
    assert period_button(app, "upcoming").content == "Предстоящие · 0"
    assert period_button(app, "past").content == "Прошедшие · 0"
    assert any(
        isinstance(control, ft.Text)
        and control.value
        == (
            "Нет предстоящих бронирований" if period == "upcoming" else "Нет прошедших бронирований"
        )
        for control in descendants(app.content)
    )


def test_new_checkout_opens_upcoming_even_after_viewing_past(app_factory, system):
    app = app_factory()
    app.bookings(period="past")
    app.service.add_to_cart(system["session"], seat_ids(app.service, system["session"])[:1])
    app.checkout()
    assert app._route["tab"] == "upcoming"
    assert displayed_ids(app) == [app.service.list_bookings()[0]["id"]]
