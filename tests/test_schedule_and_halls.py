from datetime import timedelta

import pytest

from eventseat.domain import AppError

from .conftest import seat_ids


def test_category_prices_aisles_and_session_snapshot(system):
    admin, user = system["admin"], system["user"]
    layout = [
        {"row": 1, "number": 1, "category": "эконом", "enabled": True},
        {"row": 1, "number": 2, "category": "VIP", "enabled": True},
        {"row": 1, "number": 3, "category": "стандарт", "enabled": False},
    ]
    hall = admin.save_hall("Зал с проходом", 1, 3, "Экран", system["prices"], layout)
    session = admin.save_session(system["event"], hall, system["start"])
    seats = user.seat_map(session)
    assert len(seats) == 2
    assert {(seat["number"], seat["price"]) for seat in seats} == {(1, 8000), (2, 15000)}
    admin.save_hall(
        "Новые цены шаблона",
        1,
        3,
        "Экран",
        {category: 50000 for category in system["prices"]},
        layout,
        hall_id=hall,
    )
    assert {(seat["number"], seat["price"]) for seat in user.seat_map(session)} == {
        (1, 8000),
        (2, 15000),
    }
    later = admin.save_session(system["event"], hall, system["start"] + timedelta(days=1))
    assert {(seat["number"], seat["price"]) for seat in user.seat_map(later)} == {
        (1, 50000),
        (2, 50000),
    }


def test_session_price_overrides_are_not_part_of_public_api(system):
    admin = system["admin"]
    hall = admin.get_hall(system["hall"])
    seat = next(seat for seat in hall["seats"] if seat["enabled"])
    for overrides in ({"category_prices": system["prices"]}, {"seat_prices": {seat["id"]: 23123}}):
        with pytest.raises(TypeError):
            admin.save_session(
                system["event"], system["hall"], system["start"] + timedelta(days=1), **overrides
            )
    assert not hasattr(admin, "set_session_prices")


def test_copy_hall_keeps_layout_but_has_independent_identity(system):
    admin = system["admin"]
    copy_id = admin.copy_hall(system["hall"], "Копия малого зала")
    original, copied = admin.get_hall(system["hall"]), admin.get_hall(copy_id)
    assert original["id"] != copied["id"]
    assert copied["name"] == "Копия малого зала"
    assert copied["category_prices"] == original["category_prices"]
    fields = ("row", "number", "category", "enabled")
    assert [tuple(seat[key] for key in fields) for seat in copied["seats"]] == [
        tuple(seat[key] for key in fields) for seat in original["seats"]
    ]


@pytest.mark.parametrize("offset", [-89, -1, 0, 1, 89])
def test_active_sessions_cannot_overlap_same_hall(system, offset):
    with pytest.raises(AppError):
        system["admin"].save_session(
            system["event"], system["hall"], system["start"] + timedelta(minutes=offset)
        )


def test_adjacent_sessions_and_simultaneous_different_halls_allowed(system):
    admin = system["admin"]
    adjacent = admin.save_session(
        system["event"], system["hall"], system["start"] + timedelta(minutes=90)
    )
    another_hall = admin.copy_hall(system["hall"], "Второй зал")
    parallel = admin.save_session(system["event"], another_hall, system["start"])
    assert adjacent != parallel


def test_schedule_edit_checks_overlap_and_cancelled_session_frees_slot(system):
    admin = system["admin"]
    later = admin.save_session(
        system["event"], system["hall"], system["start"] + timedelta(hours=3)
    )
    with pytest.raises(AppError):
        admin.save_session(
            system["event"],
            system["hall"],
            system["start"] + timedelta(minutes=30),
            session_id=later,
        )
    assert admin.get_session(later)["start"] == system["start"] + timedelta(hours=3)
    admin.cancel_session(system["session"], "Замена расписания")
    admin.save_session(system["event"], system["hall"], system["start"], session_id=later)
    assert admin.get_session(later)["start"] == system["start"]


def test_booked_session_cannot_change_time_hall_or_destroy_hall_layout(system):
    admin, user, session = system["admin"], system["user"], system["session"]
    user.add_to_cart(session, seat_ids(user, session)[:1])
    user.checkout("freeze-layout")
    with pytest.raises(AppError):
        admin.save_session(
            system["event"], system["hall"], system["start"] + timedelta(days=1), session_id=session
        )
    another_hall = admin.copy_hall(system["hall"], "Безопасная копия")
    with pytest.raises(AppError):
        admin.save_session(system["event"], another_hall, system["start"], session_id=session)
    with pytest.raises(AppError):
        admin.save_hall("Удаляем ряд", 1, 3, "Сцена", system["prices"], hall_id=system["hall"])
    assert admin.get_session(session)["hall_id"] == system["hall"]
    assert admin.get_session(session)["start"] == system["start"]


def test_event_duration_edit_rolls_back_when_future_sessions_overlap(system):
    admin = system["admin"]
    later = admin.save_session(
        system["event"], system["hall"], system["start"] + timedelta(minutes=120)
    )
    with pytest.raises(AppError):
        admin.save_event("Северный свет", "Концерт", "концерт", 150, True, event_id=system["event"])
    assert admin.get_event(system["event"])["duration"] == 90
    assert admin.get_session(system["session"])["duration"] == 90
    assert admin.get_session(later)["duration"] == 90


def test_hall_layout_edits_do_not_change_existing_session_snapshot(system):
    admin, user, session = system["admin"], system["user"], system["session"]
    original = user.seat_map(session)
    admin.save_hall("Меньший зал", 1, 3, "Сцена", system["prices"], hall_id=system["hall"])
    assert user.seat_map(session) == original
    later = admin.save_session(system["event"], system["hall"], system["start"] + timedelta(days=1))
    assert len(user.seat_map(later)) == 3
    assert len(user.seat_map(session)) == 6


def test_full_grid_snapshot_keeps_disabled_borders_and_custom_numbering(system):
    admin = system["admin"]
    layout = [
        {
            "row": row,
            "number": number,
            "category": "стандарт",
            "enabled": number == 110,
        }
        for row in (10, 20)
        for number in (100, 110, 120)
    ]
    hall = admin.save_hall("Нестандартная схема", 2, 3, "Экран слева", system["prices"], layout)
    session = admin.save_session(system["event"], hall, system["start"])
    snapshot = admin.get_session(session)
    assert len(snapshot["layout"]) == 6
    assert sum(cell["enabled"] for cell in snapshot["layout"]) == 2
    assert snapshot["stage"] == "Экран слева"
    for seat in layout:
        seat["enabled"] = True
    admin.save_hall(
        "Изменённая схема", 2, 3, "Сцена справа", system["prices"], layout, hall_id=hall
    )
    persisted = admin.get_session(session)
    assert persisted["layout"] == snapshot["layout"]
    assert persisted["stage"] == "Экран слева"
    assert {(seat["row"], seat["number"]) for seat in admin.seat_map(session)} == {
        (10, 110),
        (20, 110),
    }


def test_reschedule_after_cancelling_booking_preserves_historical_ticket(system):
    admin, user, session = system["admin"], system["user"], system["session"]
    user.add_to_cart(session, seat_ids(user, session)[:1])
    booking = user.checkout("history-before-reschedule")[0]
    user.cancel_booking(booking["id"])
    new_hall = admin.copy_hall(system["hall"], "Новый зал")
    admin.save_session(
        system["event"], new_hall, system["start"] + timedelta(days=1), session_id=session
    )
    history = user.get_booking(booking["id"])
    assert history["status"] == "cancelled"
    assert history["hall_name"] == booking["hall_name"]
    assert history["start"] == booking["start"]
    assert history["tickets"] == booking["tickets"]
    assert admin.get_session(session)["hall_id"] == new_hall


def test_poster_requires_publication_and_available_session_and_applies_filters(system):
    admin, user = system["admin"], system["user"]
    draft = admin.save_event("Закрытая репетиция", "Описание", "лекция", 30, False)
    admin.save_session(draft, system["hall"], system["start"] + timedelta(days=2))
    no_sessions = admin.save_event("Без сеансов", "Описание", "кино", 30, True)
    assert {event["id"] for event in user.list_events()} == {system["event"]}
    assert user.list_events(search="неизвестное") == []
    assert user.list_events(category="кино") == []
    assert [
        event["id"]
        for event in user.list_events(
            search="СЕВЕР", category="концерт", date=system["start"].date()
        )
    ] == [system["event"]]
    assert user.list_events(date=(system["start"] + timedelta(days=1)).date()) == []
    assert {draft, no_sessions, system["event"]} <= {
        event["id"] for event in admin.list_events(admin=True)
    }
    admin.cancel_session(system["session"], "Отмена концерта")
    assert user.list_events() == []


def test_statistics_count_active_bookings_and_amount_not_cancelled_tickets(system):
    admin, user, session = system["admin"], system["user"], system["session"]
    user.add_to_cart(session, seat_ids(user, session)[:2])
    booking = user.checkout("statistics")[0]
    stats = admin.statistics(session)
    assert stats["active_tickets"] == 2
    assert stats["total_seats"] == 6
    assert stats["occupancy_percent"] == pytest.approx(100 / 3, abs=0.1)
    assert stats["active_amount"] == booking["total"]
    user.cancel_booking(booking["id"])
    after = admin.statistics(session)
    assert after["active_tickets"] == 0
    assert after["active_amount"] == 0


@pytest.mark.parametrize(
    "prices",
    [
        {"эконом": -1, "стандарт": 10000, "VIP": 15000},
        {"эконом": 8000, "стандарт": 10.5, "VIP": 15000},
    ],
)
def test_money_accepts_only_nonnegative_integer_kopecks(system, prices):
    with pytest.raises(AppError):
        system["admin"].save_hall("Неверные цены", 1, 2, "Сцена", prices)
