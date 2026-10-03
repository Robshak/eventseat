import sqlite3
from datetime import datetime, timedelta

import pytest

from eventseat.domain import AppError
from eventseat.storage import SCHEMA_VERSION

from .conftest import seat_ids


def test_database_itself_rejects_second_active_ticket(system):
    user, session = system["user"], system["session"]
    user.add_to_cart(session, seat_ids(user, session)[:1])
    user.checkout("database-constraint")
    with sqlite3.connect(system["database"]) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO tickets (booking_id, session_id, seat_id, row, number, category, "
                "tariff, base_price, price, active) "
                "SELECT booking_id, session_id, seat_id, row, number, category, "
                "tariff, base_price, price, 1 FROM tickets LIMIT 1"
            )
        assert (
            connection.execute("SELECT COUNT(*) FROM tickets WHERE active = 1").fetchone()[0] == 1
        )


def test_database_failure_after_first_ticket_rolls_back_checkout(system):
    user, session = system["user"], system["session"]
    selected = seat_ids(user, session)[:2]
    user.add_to_cart(session, selected)
    with sqlite3.connect(system["database"]) as connection:
        connection.execute(
            "CREATE TRIGGER fail_second_ticket BEFORE INSERT ON tickets "
            "WHEN (SELECT COUNT(*) FROM tickets) > 0 "
            "BEGIN SELECT RAISE(ABORT, 'injected write failure'); END"
        )
    with pytest.raises(AppError):
        user.checkout("injected-failure")
    assert user.list_bookings() == []
    assert len(user.get_cart()) == 2
    with sqlite3.connect(system["database"]) as connection:
        assert connection.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM checkout_requests").fetchone()[0] == 0
        connection.execute("DROP TRIGGER fail_second_ticket")
    assert len(user.checkout("injected-failure")[0]["tickets"]) == 2


def test_started_session_rejected_at_add_and_checkout(system):
    user, other, session = system["user"], system["other"], system["session"]
    selected = seat_ids(user, session)[:2]
    user.add_to_cart(session, selected[:1])
    with sqlite3.connect(system["database"]) as connection:
        connection.execute(
            "UPDATE event_sessions SET start = ? WHERE id = ?",
            ((datetime.now() - timedelta(minutes=1)).isoformat(sep=" "), session),
        )
    with pytest.raises(AppError):
        other.add_to_cart(session, selected[1:])
    with pytest.raises(AppError):
        user.checkout("too-late")
    assert user.list_bookings() == []


def test_completed_booking_keeps_ticket_and_cannot_be_cancelled(system):
    user, session = system["user"], system["session"]
    user.add_to_cart(session, seat_ids(user, session)[:1])
    booking = user.checkout("past-booking")[0]
    past = (datetime.now() - timedelta(hours=2)).isoformat(sep=" ")
    with sqlite3.connect(system["database"]) as connection:
        connection.execute("UPDATE event_sessions SET start = ? WHERE id = ?", (past, session))
        connection.execute("UPDATE bookings SET start = ? WHERE id = ?", (past, booking["id"]))
    completed = user.get_booking(booking["id"])
    assert completed["status"] == "completed"
    assert completed["total"] == booking["total"]
    with pytest.raises(AppError):
        user.cancel_booking(booking["id"])
    assert user.get_booking(booking["id"])["status"] == "completed"


def test_cancelled_session_in_existing_cart_rejected_at_checkout(system):
    user, admin, session = system["user"], system["admin"], system["session"]
    user.add_to_cart(session, seat_ids(user, session)[:1])
    admin.cancel_session(session, "Отмена после выбора места")
    with pytest.raises(AppError):
        user.checkout("cancelled-between-clicks")
    assert user.list_bookings() == []


def test_schema_version_and_foreign_keys_enabled(system):
    with system["admin"].engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA user_version").scalar_one() == SCHEMA_VERSION
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1


def test_incremental_migration_preserves_bookings(system):
    user, session = system["user"], system["session"]
    user.add_to_cart(session, seat_ids(user, session)[:1])
    booking = user.checkout("before-migration")[0]
    with sqlite3.connect(system["database"]) as connection:
        connection.execute("PRAGMA user_version = 1")
        connection.execute("DROP INDEX IF EXISTS ix_bookings_user")
    reopened = system["connect"]()
    reopened.login("alice", "Alice-pass-123")
    assert reopened.get_booking(booking["id"])["total"] == booking["total"]
    with sqlite3.connect(system["database"]) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_real_v2_schema_adds_grid_snapshot_without_losing_tickets(system):
    user, session = system["user"], system["session"]
    user.add_to_cart(session, seat_ids(user, session)[:1])
    booking = user.checkout("v2-ticket")[0]
    with sqlite3.connect(system["database"]) as connection:
        connection.execute("ALTER TABLE event_sessions DROP COLUMN stage")
        connection.execute("ALTER TABLE event_sessions DROP COLUMN layout")
        connection.execute("PRAGMA user_version = 2")
    reopened = system["connect"]()
    reopened.login("alice", "Alice-pass-123")
    upgraded = reopened.get_session(session)
    assert upgraded["stage"] == "Сцена"
    assert len(upgraded["layout"]) == 6
    assert all(cell["enabled"] for cell in upgraded["layout"])
    assert reopened.get_booking(booking["id"])["tickets"] == booking["tickets"]
    with sqlite3.connect(system["database"]) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_v2_migration_keeps_session_seats_removed_from_hall_template(system):
    admin = system["admin"]
    admin.save_hall("Уменьшенный зал", 1, 3, "Сцена", system["prices"], hall_id=system["hall"])
    with sqlite3.connect(system["database"]) as connection:
        connection.execute("ALTER TABLE event_sessions DROP COLUMN stage")
        connection.execute("ALTER TABLE event_sessions DROP COLUMN layout")
        connection.execute("PRAGMA user_version = 2")
    reopened = system["connect"]()
    upgraded = reopened.get_session(system["session"])
    assert {(cell["row"], cell["number"]) for cell in upgraded["layout"] if cell["enabled"]} == {
        (row, number) for row in (1, 2) for number in (1, 2, 3)
    }
    assert len(reopened.seat_map(system["session"])) == 6


def test_newer_database_is_not_silently_downgraded(system):
    with sqlite3.connect(system["database"]) as connection:
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    with pytest.raises(AppError, match="новой версией"):
        system["connect"]()
