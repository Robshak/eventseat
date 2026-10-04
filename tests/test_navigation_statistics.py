import sqlite3
from datetime import timedelta

import pytest

from eventseat.domain import AppError
from eventseat.services import Service
from eventseat.storage import SCHEMA_VERSION

from .conftest import seat_ids


def book(system, *, user=None, session=None, count=1, key="map"):
    user = user or system["user"]
    session = session or system["session"]
    selected = seat_ids(user, session)[:count]
    user.add_to_cart(session, selected)
    return user.checkout(key)[0]


def test_booking_map_owner_admin_and_exact_selected_seats(system):
    booking = book(system, count=2)
    result = system["user"].get_booking_seat_map(booking["id"])
    assert result["booking"] == booking
    assert len(result["layout"]) == 6
    assert result["stage"] == "Сцена"
    assert result["hall_id"] == system["hall"]
    assert result["layout_is_partial"] is False
    assert result["selected_seat_ids"] == sorted(t["seat_id"] for t in booking["tickets"])
    assert {(cell["row"], cell["number"]) for cell in result["layout"] if cell["selected"]} == {
        (ticket["row"], ticket["number"]) for ticket in booking["tickets"]
    }
    assert system["admin"].get_booking_seat_map(booking["id"]) == result
    with pytest.raises(AppError):
        system["other"].get_booking_seat_map(booking["id"])
    with pytest.raises(AppError):
        system["connect"]().get_booking_seat_map(booking["id"])


def test_booking_map_snapshot_survives_template_edits_and_session_move(system):
    admin, user = system["admin"], system["user"]
    booking = book(system, count=2)
    before = user.get_booking_seat_map(booking["id"])
    user.cancel_booking(booking["id"])
    admin.save_hall(
        "Зал после ремонта", 1, 2, "Новая сцена", system["prices"], hall_id=system["hall"]
    )
    new_hall = admin.copy_hall(system["hall"], "Другой зал")
    admin.save_session(
        system["event"],
        new_hall,
        system["start"] + timedelta(days=1),
        session_id=system["session"],
    )
    after = user.get_booking_seat_map(booking["id"])
    assert after["layout"] == before["layout"]
    assert after["stage"] == before["stage"]
    assert after["hall_name"] == before["hall_name"]
    assert after["hall_id"] == before["hall_id"]
    assert after["session_changed"] is True
    assert after["booking"]["start"] == booking["start"]
    assert after["booking"]["tickets"] == booking["tickets"]
    assert after["session"]["hall_id"] == new_hall


def test_booking_map_keeps_aisles_and_custom_numbering(system):
    admin, user = system["admin"], system["user"]
    hall = admin.save_hall(
        "Зал с проходами",
        2,
        3,
        "Экран слева",
        system["prices"],
        seats=[
            {"row": row, "number": number, "category": "VIP", "enabled": number == 20}
            for row in (5, 10)
            for number in (10, 20, 30)
        ],
    )
    session = admin.save_session(system["event"], hall, system["start"])
    booking = book(system, session=session)
    result = user.get_booking_seat_map(booking["id"])
    assert result["stage"] == "Экран слева"
    assert len(result["layout"]) == 6
    assert sum(cell["enabled"] for cell in result["layout"]) == 2
    assert sum(cell["selected"] for cell in result["layout"]) == 1
    assert next(cell for cell in result["layout"] if cell["selected"])["number"] == 20


def test_related_session_requires_own_relationship_even_for_published_event(system):
    user, other = system["user"], system["other"]
    session = system["session"]
    user.add_to_cart(session, seat_ids(user, session)[:1])
    assert user.get_related_session(session)["bookable"] is True
    with pytest.raises(AppError):
        other.get_related_session(session)
    with pytest.raises(AppError):
        system["connect"]().get_related_session(session)
    assert system["admin"].get_related_session(session)["bookable"] is False


def test_related_hidden_cancelled_session_and_map_remain_available(system):
    admin, user = system["admin"], system["user"]
    booking = book(system)
    admin.save_event("Скрытое событие", "Описание", "концерт", 90, False, event_id=system["event"])
    admin.cancel_session(system["session"], "Отмена организатором")
    result = user.get_related_session(system["session"])
    assert result["status"] == "cancelled"
    assert result["published"] is False
    assert result["bookable"] is False
    assert user.get_booking_seat_map(booking["id"])["booking"]["status"] == "cancelled"
    with pytest.raises(AppError):
        system["other"].get_related_session(system["session"])
    with pytest.raises(AppError):
        system["other"].get_booking_seat_map(booking["id"])


def test_related_completed_session_keeps_map_but_cannot_book(system, monkeypatch):
    booking = book(system)
    monkeypatch.setattr(Service, "_now", staticmethod(lambda: system["start"] + timedelta(hours=2)))
    assert system["user"].get_related_session(system["session"])["bookable"] is False
    assert system["user"].get_booking_seat_map(booking["id"])["booking"]["status"] == "completed"


def test_statistics_filters_event_hall_and_inclusive_dates(system):
    admin = system["admin"]
    first = book(system, count=2)
    other_event = admin.save_event("Другой концерт", "Описание", "концерт", 30, True)
    other_hall = admin.copy_hall(system["hall"], "Большой зал")
    last_start = (system["start"] + timedelta(days=2)).replace(hour=23, minute=59)
    last_session = admin.save_session(system["event"], other_hall, last_start)
    last = book(system, session=last_session, key="last-day")
    outside = admin.save_session(other_event, other_hall, last_start + timedelta(days=1))
    book(system, session=outside, key="outside")
    stats = admin.statistics(
        event_id=system["event"], date_from=system["start"].date(), date_to=last_start.date()
    )
    assert stats["session_count"] == 2
    assert stats["ticket_count"] == stats["active_tickets"] == 3
    assert stats["amount"] == first["total"] + last["total"]
    assert stats["booking_count"] == 2
    assert stats["total_seats"] == 12
    assert stats["occupancy_percent"] == 25
    only_last = admin.statistics(
        hall_id=other_hall, date_from=last_start.date(), date_to=last_start.date()
    )
    assert only_last["session_count"] == 1
    assert only_last["amount"] == last["total"]
    assert admin.statistics(event_id=other_event)["session_count"] == 1


def test_statistics_booking_cancellations_never_inflate_occupancy(system):
    first = book(system, count=2)
    system["user"].cancel_booking(first["id"])
    second = book(system, count=1, key="rebooked")
    admin = system["admin"]
    cancelled = admin.statistics(booking_status="cancelled")
    assert cancelled["ticket_count"] == cancelled["cancelled_tickets"] == 2
    assert cancelled["amount"] == first["total"]
    assert cancelled["active_tickets"] == 1
    assert cancelled["active_amount"] == second["total"]
    assert cancelled["occupancy_percent"] == 16.7
    all_stats = admin.statistics(booking_status="all")
    assert all_stats["ticket_count"] == 3
    assert all_stats["booking_count"] == 2
    assert all_stats["amount"] == first["total"] + second["total"]
    assert admin.statistics()["ticket_count"] == 1


def test_statistics_session_and_booking_status_filters_are_separate(system, monkeypatch):
    booking = book(system)
    admin = system["admin"]
    cancelled_session = admin.save_session(
        system["event"], system["hall"], system["start"] + timedelta(days=2)
    )
    cancelled_booking = book(system, session=cancelled_session, key="cancelled-session")
    admin.cancel_session(cancelled_session, "Причина отмены")
    monkeypatch.setattr(Service, "_now", staticmethod(lambda: system["start"] + timedelta(hours=2)))
    assert admin.statistics()["session_count"] == 0
    completed = admin.statistics(session_status="completed", booking_status="completed")
    assert completed["session_count"] == completed["booking_count"] == 1
    assert completed["amount"] == booking["total"]
    cancelled = admin.statistics(session_status="cancelled", booking_status="cancelled")
    assert cancelled["ticket_count"] == 1
    assert cancelled["amount"] == cancelled_booking["total"]
    assert cancelled["occupancy_percent"] == 0
    assert admin.statistics(session_status="all", booking_status="all")["ticket_count"] == 2


def test_empty_statistics_and_invalid_filters(system):
    admin = system["admin"]
    empty = admin.statistics(date_from=system["start"] + timedelta(days=20))
    assert all(value == 0 for value in empty.values())
    for filters in (
        {"session_status": "active"},
        {"booking_status": "upcoming"},
        {"date_from": "2030-01-02", "date_to": "2030-01-01"},
        {"date_from": "not-a-date"},
        {"hall_id": 999999},
    ):
        with pytest.raises(AppError):
            admin.statistics(**filters)
    with pytest.raises(AppError):
        system["user"].statistics(session_status="all")


def test_booking_search_filters_match_statistics_without_changing_session_occupancy(system):
    first = book(system, count=2)
    second = book(system, user=system["other"], key="boris-booking")
    admin = system["admin"]
    filters = {
        "event_id": system["event"],
        "hall_id": system["hall"],
        "session_id": system["session"],
        "date_from": system["start"].date(),
        "date_to": system["start"].date(),
        "session_status": "upcoming",
        "booking_status": "active",
        "search": " ALICE ",
    }
    bookings = admin.list_bookings(admin=True, **filters)
    stats = admin.statistics(**filters)
    assert [booking["id"] for booking in bookings] == [first["id"]]
    assert stats["booking_count"] == len(bookings) == 1
    assert stats["ticket_count"] == 2
    assert stats["amount"] == first["total"]
    assert stats["active_tickets"] == 3 and stats["occupancy_percent"] == 50
    filters["search"] = second["number"].lower()
    assert admin.list_bookings(admin=True, **filters)[0]["id"] == second["id"]
    assert system["user"].list_bookings(**filters) == []
    assert admin.statistics(**filters)["amount"] == second["total"]
    system["other"].cancel_booking(second["id"])
    assert admin.list_bookings(admin=True, **filters) == []
    filters["booking_status"] = "cancelled"
    assert admin.list_bookings(admin=True, **filters)[0]["id"] == second["id"]
    assert admin.statistics(**filters)["ticket_count"] == 1
    filters["search"] = "nobody-matches"
    empty = admin.statistics(**filters)
    assert empty["booking_count"] == empty["ticket_count"] == empty["amount"] == 0
    assert empty["total_seats"] == 6 and empty["active_tickets"] == 2


def test_admin_session_filters_and_public_visibility(system, monkeypatch):
    admin, user = system["admin"], system["user"]
    hall = admin.copy_hall(system["hall"], "Другой зал")
    draft = admin.save_event("Черновик", "Описание", "кино", 30, False)
    hidden_session = admin.save_session(draft, hall, system["start"])
    cancelled = admin.save_session(system["event"], hall, system["start"] + timedelta(days=1))
    admin.cancel_session(cancelled, "Отмена")
    assert {s["id"] for s in admin.list_sessions(admin=True, hall_id=hall)} == {
        hidden_session,
        cancelled,
    }
    assert [s["id"] for s in admin.list_sessions(admin=True, hall_id=hall, status="upcoming")] == [
        hidden_session
    ]
    assert [s["id"] for s in admin.list_sessions(admin=True, status="cancelled")] == [cancelled]
    assert user.list_sessions(hall_id=hall, status="all") == []
    assert user.list_sessions(status="cancelled") == []
    day = system["start"].date()
    assert len(admin.list_sessions(admin=True, date_from=day, date_to=day)) == 2
    monkeypatch.setattr(Service, "_now", staticmethod(lambda: system["start"] + timedelta(hours=2)))
    assert len(admin.list_sessions(admin=True, status="completed")) == 2
    assert user.list_sessions(status="completed") == []
    with pytest.raises(AppError):
        user.list_sessions(admin=True, hall_id=hall)


@pytest.mark.parametrize("moved", [False, True])
def test_v3_booking_map_migration_preserves_ticket_history_and_marks_partial(system, moved):
    admin, user = system["admin"], system["user"]
    booking = book(system, count=2)
    user.cancel_booking(booking["id"])
    if moved:
        new_hall = admin.copy_hall(system["hall"], "Другой зал")
        admin.save_session(
            system["event"],
            new_hall,
            system["start"] + timedelta(days=1),
            session_id=system["session"],
        )
    with sqlite3.connect(system["database"]) as connection:
        connection.execute(
            "CREATE TABLE bookings_v3 ("
            "id INTEGER PRIMARY KEY, number VARCHAR(40) NOT NULL UNIQUE, "
            "user_id INTEGER NOT NULL REFERENCES users(id), "
            "session_id INTEGER NOT NULL REFERENCES event_sessions(id), "
            "title VARCHAR(160) NOT NULL, hall_name VARCHAR(100) NOT NULL, "
            "start DATETIME NOT NULL, duration INTEGER NOT NULL, "
            "status VARCHAR(15) NOT NULL CHECK(status IN ('active', 'cancelled')), "
            "cancel_reason TEXT NOT NULL, total INTEGER NOT NULL CHECK(total >= 0), "
            "created_at DATETIME NOT NULL)"
        )
        connection.execute(
            "INSERT INTO bookings_v3 SELECT id, number, user_id, session_id, title, "
            "hall_name, start, duration, status, cancel_reason, total, created_at FROM bookings"
        )
        connection.execute("DROP TABLE bookings")
        connection.execute("ALTER TABLE bookings_v3 RENAME TO bookings")
        connection.execute("CREATE INDEX ix_bookings_user ON bookings(user_id)")
        connection.execute("PRAGMA user_version = 3")
    reopened = system["connect"]()
    reopened.login("alice", "Alice-pass-123")
    result = reopened.get_booking_seat_map(booking["id"])
    assert result["booking"]["tickets"] == booking["tickets"]
    assert result["booking"]["total"] == booking["total"]
    assert result["hall_id"] == system["hall"]
    assert result["layout_is_partial"] is moved
    assert len(result["layout"]) == (2 if moved else 6)
    assert sum(cell["selected"] for cell in result["layout"]) == 2
    with sqlite3.connect(system["database"]) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
