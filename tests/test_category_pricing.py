import json
import sqlite3
from datetime import timedelta

import pytest

from eventseat.domain import AppError
from eventseat.services import Service
from eventseat.storage import SCHEMA_VERSION

from .conftest import seat_ids


def save_hall_prices(admin, hall_id, prices):
    hall = admin.get_hall(hall_id)
    admin.save_hall(
        hall["name"],
        hall["rows"],
        hall["columns"],
        hall["stage"],
        prices,
        hall["seats"],
        hall_id=hall_id,
    )


def test_existing_session_keeps_price_snapshot_new_and_moved_sessions_use_hall_categories(system):
    admin = system["admin"]
    original = admin.seat_map(system["session"])
    new_prices = {category: amount + 5000 for category, amount in system["prices"].items()}
    save_hall_prices(admin, system["hall"], new_prices)
    admin.save_session(
        system["event"],
        system["hall"],
        system["start"] + timedelta(days=1),
        session_id=system["session"],
    )
    assert admin.seat_map(system["session"]) == original
    assert admin.get_session(system["session"])["category_prices"] == system["prices"]
    later = admin.save_session(system["event"], system["hall"], system["start"] + timedelta(days=2))
    assert all(seat["price"] == new_prices[seat["category"]] for seat in admin.seat_map(later))
    other_hall = admin.copy_hall(system["hall"], "Бесплатный зал")
    free_prices = {category: 0 for category in system["prices"]}
    save_hall_prices(admin, other_hall, free_prices)
    admin.save_session(system["event"], other_hall, system["start"], session_id=system["session"])
    assert admin.get_session(system["session"])["category_prices"] == free_prices
    assert {seat["price"] for seat in admin.seat_map(system["session"])} == {0}


@pytest.mark.parametrize("override", [0, 17777])
def test_hall_cannot_introduce_individual_prices_even_for_free_seats(system, override):
    with pytest.raises(AppError, match="категориями"):
        system["admin"].save_hall(
            "Новый зал",
            1,
            1,
            "Сцена",
            system["prices"],
            [{"row": 1, "number": 1, "category": "VIP", "price_override": override}],
        )


def test_hall_category_change_does_not_reprice_existing_cart_or_tickets(system):
    admin, user = system["admin"], system["user"]
    user.add_to_cart(system["session"], seat_ids(user, system["session"])[:2])
    before = user.get_cart()
    save_hall_prices(admin, system["hall"], {category: 1 for category in system["prices"]})
    assert user.get_cart() == before
    booking = user.checkout("category-edit-keeps-quote")[0]
    assert booking["total"] == sum(item["price"] for item in before)
    save_hall_prices(admin, system["hall"], {category: 999999 for category in system["prices"]})
    assert user.get_booking(booking["id"])["tickets"] == booking["tickets"]


def test_v4_migration_clears_only_hall_overrides_and_preserves_legacy_session_cart_and_ticket(
    system,
):
    admin, user, other = system["admin"], system["user"], system["other"]
    session = system["session"]
    selected = seat_ids(user, session)
    legacy_categories = {category: 22000 for category in system["prices"]}
    with sqlite3.connect(system["database"]) as db:
        db.execute("UPDATE seats SET price_override = 17777 WHERE hall_id = ?", (system["hall"],))
        db.execute(
            "UPDATE event_sessions SET category_prices = ? WHERE id = ?",
            (json.dumps(legacy_categories), session),
        )
        db.execute("UPDATE session_seats SET price = 22000 WHERE session_id = ?", (session,))
        db.execute(
            "UPDATE session_seats SET price = 17777, price_override = 17777 WHERE session_id = ? AND seat_id = ?",
            (session, selected[0]),
        )
    user.add_to_cart(session, selected[:1])
    booking = user.checkout("legacy-override-ticket")[0]
    other.add_to_cart(session, selected[1:2])
    before_cart = other.get_cart()
    before_seats = admin.seat_map(session)
    before_map = user.get_booking_seat_map(booking["id"])
    with sqlite3.connect(system["database"]) as db:
        db.execute("PRAGMA user_version = 4")
    reopened = system["connect"]()
    reopened.login("boris", "Boris-pass-123")
    assert reopened.get_cart() == before_cart
    assert reopened.seat_map(session) == before_seats
    assert reopened.checkout("legacy-cart-after-migration")[0]["total"] == 22000
    assert user.get_booking(booking["id"])["tickets"] == booking["tickets"]
    after_map = user.get_booking_seat_map(booking["id"])
    assert after_map["layout"] == before_map["layout"]
    assert after_map["booking"]["total"] == 17777
    admin.save_session(system["event"], system["hall"], system["start"], session_id=session)
    assert admin.get_session(session)["category_prices"] == legacy_categories
    assert [seat["price"] for seat in admin.seat_map(session)] == [
        seat["price"] for seat in before_seats
    ]
    later = admin.save_session(system["event"], system["hall"], system["start"] + timedelta(days=1))
    assert all(
        seat["price"] == system["prices"][seat["category"]] for seat in admin.seat_map(later)
    )
    with sqlite3.connect(system["database"]) as db:
        assert (
            db.execute("SELECT count(*) FROM seats WHERE price_override IS NOT NULL").fetchone()[0]
            == 0
        )
        assert (
            db.execute(
                "SELECT price_override FROM session_seats WHERE session_id = ? AND seat_id = ?",
                (session, selected[0]),
            ).fetchone()[0]
            == 17777
        )
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_new_demo_sessions_follow_their_hall_category_prices(tmp_path):
    service = Service(tmp_path / "demo.sqlite3")
    try:
        service.setup_admin("admin", "Администратор", "Admin-pass-123")
        for session in service.list_sessions(admin=True):
            prices = service.get_hall(session["hall_id"])["category_prices"]
            assert session["category_prices"] == prices
            assert all(
                seat["price"] == prices[seat["category"]]
                for seat in service.seat_map(session["id"])
            )
    finally:
        service.close()
