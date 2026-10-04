import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest

from eventseat.domain import AppError, PriceChanged
from eventseat.services import Service

from .conftest import seat_ids


def test_cart_and_booking_are_isolated_between_users(system):
    user, other, session = system["user"], system["other"], system["session"]
    user.add_to_cart(session, seat_ids(user, session)[:1])
    item = user.get_cart()[0]
    assert other.get_cart() == []
    with pytest.raises(AppError):
        other.remove_cart_item(item["id"])
    assert len(user.get_cart()) == 1
    booking = user.checkout("isolation")[0]
    assert other.list_bookings() == []
    with pytest.raises(AppError):
        other.get_booking(booking["id"])
    with pytest.raises(AppError):
        other.cancel_booking(booking["id"])
    assert user.get_booking(booking["id"])["status"] == "active"


def test_cart_does_not_hold_seats_and_conflict_rolls_back_every_session(system):
    admin, user, other = system["admin"], system["user"], system["other"]
    first = system["session"]
    second = admin.save_session(
        system["event"], system["hall"], system["start"] + timedelta(days=1)
    )
    first_seat, second_seat = seat_ids(user, first)[0], seat_ids(user, second)[0]
    user.add_to_cart(first, [first_seat])
    user.add_to_cart(second, [second_seat])
    other.add_to_cart(second, [second_seat])
    other.checkout("other-wins")
    with pytest.raises(AppError):
        user.checkout("conflicting-multi-session")
    assert user.list_bookings() == []
    assert len(user.get_cart()) == 2
    assert (
        next(seat for seat in user.seat_map(first) if seat["id"] == first_seat)["status"] == "free"
    )


def test_successful_multi_session_checkout_and_repeat_are_atomic(system):
    admin, user = system["admin"], system["user"]
    first = system["session"]
    second = admin.save_session(
        system["event"], system["hall"], system["start"] + timedelta(days=1)
    )
    user.add_to_cart(first, seat_ids(user, first)[:2])
    user.add_to_cart(second, seat_ids(user, second)[:1])
    expected = sum(item["price"] for item in user.get_cart())
    result = user.checkout("two-sessions")
    repeated = user.checkout("two-sessions")
    assert len(result) == 2
    assert sum(len(booking["tickets"]) for booking in result) == 3
    assert sum(booking["total"] for booking in result) == expected
    assert {booking["id"] for booking in result} == {booking["id"] for booking in repeated}
    assert len(user.list_bookings()) == 2
    assert user.get_cart() == []


def test_idempotency_key_is_scoped_to_user(system):
    user, other, session = system["user"], system["other"], system["session"]
    first, second = seat_ids(user, session)[:2]
    user.add_to_cart(session, [first])
    other.add_to_cart(session, [second])
    a = user.checkout("same-button-key")[0]
    b = other.checkout("same-button-key")[0]
    assert a["id"] != b["id"]
    assert a["user_id"] != b["user_id"]


def test_repeated_cart_add_does_not_duplicate_the_seat(system):
    user, session = system["user"], system["session"]
    selected = seat_ids(user, session)[:1]
    user.add_to_cart(session, selected)
    user.add_to_cart(session, selected)
    assert len(user.get_cart()) == 1
    user.remove_cart_item(user.get_cart()[0]["id"])
    assert user.get_cart() == []


def test_same_physical_seat_can_be_booked_for_different_sessions(system):
    admin, user, first = system["admin"], system["user"], system["session"]
    second = admin.save_session(
        system["event"], system["hall"], system["start"] + timedelta(days=1)
    )
    a, b = user.seat_map(first)[0], user.seat_map(second)[0]
    assert (a["row"], a["number"]) == (b["row"], b["number"])
    user.add_to_cart(first, [a["id"]])
    user.checkout("first-date")
    assert user.seat_map(second)[0]["status"] == "free"
    user.add_to_cart(second, [b["id"]])
    user.checkout("second-date")
    assert len(user.list_bookings()) == 2


def test_price_change_requires_second_confirmation_and_keeps_ticket_price(system):
    user, session = system["user"], system["session"]
    user.add_to_cart(session, seat_ids(user, session)[:1])
    before = user.get_cart()[0]["price"]
    # Defensive compatibility with stale quotes left by a legacy writer/import.
    with sqlite3.connect(system["database"]) as db:
        db.execute("UPDATE session_seats SET price = price + 5500 WHERE session_id = ?", (session,))
    with pytest.raises(PriceChanged):
        user.checkout("old-price")
    assert user.list_bookings() == []
    new_price = user.get_cart()[0]["price"]
    assert new_price == before + 5500
    with pytest.raises(PriceChanged):
        user.checkout("old-price")
    assert user.list_bookings() == []
    booking = user.checkout("accepted-price")[0]
    assert booking["total"] == new_price
    with sqlite3.connect(system["database"]) as db:
        db.execute("UPDATE session_seats SET price = 99900 WHERE session_id = ?", (session,))
    persisted = user.get_booking(booking["id"])
    assert persisted["total"] == new_price
    assert persisted["tickets"][0]["price"] == new_price


def test_concession_tariff_is_twenty_percent_and_precise(system):
    admin, user = system["admin"], system["user"]
    hall = admin.save_hall(
        "Зал со скидками", 1, 2, "Сцена", {category: 12345 for category in system["prices"]}
    )
    session = admin.save_session(system["event"], hall, system["start"])
    user.add_to_cart(session, seat_ids(user, session)[:1], tariff="concession")
    assert user.get_cart()[0]["price"] == 9876
    booking = user.checkout("educational-discount")[0]
    assert booking["total"] == 9876
    assert booking["tickets"][0]["tariff"] == "concession"


def test_cancellation_releases_seat_and_keeps_history_idempotently(system):
    user, other, session = system["user"], system["other"], system["session"]
    selected = seat_ids(user, session)[:1]
    user.add_to_cart(session, selected)
    booking = user.checkout("cancel-me")[0]
    user.cancel_booking(booking["id"])
    user.cancel_booking(booking["id"])
    history = user.get_booking(booking["id"])
    assert history["status"] == "cancelled"
    assert history["total"] == booking["total"]
    assert history["tickets"] == booking["tickets"]
    other.add_to_cart(session, selected)
    replacement = other.checkout("released-seat")[0]
    assert replacement["status"] == "active"
    assert len(user.list_bookings()) == 1


def test_admin_cancellation_is_visible_to_all_ticket_owners(system):
    admin, user, other, session = (
        system["admin"],
        system["user"],
        system["other"],
        system["session"],
    )
    first, second = seat_ids(user, session)[:2]
    user.add_to_cart(session, [first])
    other.add_to_cart(session, [second])
    a, b = user.checkout("session-cancel-a")[0], other.checkout("session-cancel-b")[0]
    admin.cancel_session(session, "Технические работы")
    for actor, booking in [(user, a), (other, b)]:
        stored = actor.get_booking(booking["id"])
        assert stored["status"] == "cancelled"
        assert "Технические работы" in stored["cancel_reason"]
    with pytest.raises(AppError):
        user.add_to_cart(session, [first])


def test_admin_can_cancel_booking_with_reason(system):
    admin, user, session = system["admin"], system["user"], system["session"]
    user.add_to_cart(session, seat_ids(user, session)[:1])
    booking = user.checkout("admin-cancel")[0]
    admin.cancel_booking(booking["id"], "Ошибка организации")
    stored = user.get_booking(booking["id"])
    assert stored["status"] == "cancelled"
    assert stored["cancel_reason"] == "Ошибка организации"


def test_closed_seat_cannot_be_added_and_booked_seat_cannot_be_closed(system):
    admin, user, session = system["admin"], system["user"], system["session"]
    first = seat_ids(user, session)[0]
    admin.set_seat_closed(session, first, True)
    assert (
        next(seat for seat in user.seat_map(session) if seat["id"] == first)["status"] == "closed"
    )
    with pytest.raises(AppError):
        user.add_to_cart(session, [first])
    admin.set_seat_closed(session, first, False)
    user.add_to_cart(session, [first])
    user.checkout("reopened-seat")
    with pytest.raises(AppError):
        admin.set_seat_closed(session, first, True)


def test_seat_from_another_hall_cannot_be_added_to_session(system):
    admin, user = system["admin"], system["user"]
    other_hall = admin.copy_hall(system["hall"], "Другой зал")
    other_session = admin.save_session(system["event"], other_hall, system["start"])
    unrelated = seat_ids(user, other_session)[0]
    with pytest.raises(AppError):
        user.add_to_cart(system["session"], [unrelated])
    assert user.get_cart() == []


def test_changed_price_old_key_remains_invalid_after_restart(system):
    user, session = system["user"], system["session"]
    user.add_to_cart(session, seat_ids(user, session)[:1])
    with sqlite3.connect(system["database"]) as db:
        db.execute("UPDATE session_seats SET price = 20000 WHERE session_id = ?", (session,))
    with pytest.raises(PriceChanged):
        user.checkout("outdated-click")
    user.close()
    reopened = system["connect"]()
    reopened.login("alice", "Alice-pass-123")
    with pytest.raises(PriceChanged):
        reopened.checkout("outdated-click")
    assert reopened.list_bookings() == []
    assert reopened.checkout("new-consent")[0]["total"] == 20000


def test_concurrent_duplicate_confirmations_return_same_booking(system):
    user, session = system["user"], system["session"]
    user.add_to_cart(session, seat_ids(user, session)[:1])
    barrier = Barrier(2)

    def confirm():
        service = Service(system["database"], seed=False)
        try:
            service.login("alice", "Alice-pass-123")
            barrier.wait(timeout=10)
            return service.checkout("double-click")[0]["id"]
        finally:
            service.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = executor.submit(confirm), executor.submit(confirm)
        assert first.result(timeout=30) == second.result(timeout=30)
    assert len(user.list_bookings()) == 1


def test_concurrent_independent_engines_cannot_double_book(system):
    user, other, session = system["user"], system["other"], system["session"]
    selected = seat_ids(user, session)[:1]
    user.add_to_cart(session, selected)
    other.add_to_cart(session, selected)
    barrier = Barrier(2)

    def attempt(login, password):
        service = Service(system["database"], seed=False)
        try:
            service.login(login, password)
            barrier.wait(timeout=10)
            try:
                return ("booked", service.checkout("racing-checkout"))
            except AppError as error:
                return ("conflict", str(error))
        finally:
            service.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(attempt, "alice", "Alice-pass-123")
        second = executor.submit(attempt, "boris", "Boris-pass-123")
        results = [first.result(timeout=30), second.result(timeout=30)]
    assert sorted(outcome for outcome, _ in results) == ["booked", "conflict"]
    assert system["admin"].statistics(session)["active_tickets"] == 1
    assert len(user.list_bookings()) + len(other.list_bookings()) == 1
    assert len(user.get_cart()) + len(other.get_cart()) == 1
