from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta

import pytest

from eventseat.domain import (
    AppError,
    Booking,
    Cart,
    ConcessionTicket,
    EventSession,
    Seat,
    StandardTicket,
    User,
    calculate_ticket_price,
)


@pytest.mark.parametrize("kopecks,expected", [(0, 0), (1, 1), (2, 2), (3, 2), (12345, 9876)])
def test_concession_rounds_to_nearest_kopeck(kopecks, expected):
    assert calculate_ticket_price(kopecks, "concession") == expected


def test_cart_composes_polymorphic_tickets_and_exposes_read_only_total():
    first = StandardTicket(Seat(1, 1), 10000)
    second = ConcessionTicket(Seat(1, 2, "VIP"), 15000)
    cart = Cart([first])
    cart.add(second)
    assert len(cart) == 2
    assert cart.total == 22000
    assert [ticket.price for ticket in cart] == [10000, 12000]
    assert "ряд 1, место 2" in str(second)
    with pytest.raises(AttributeError):
        cart.total = 0
    with pytest.raises(FrozenInstanceError):
        first.base_price = 0


def test_booking_state_transition_checks_owner_and_preserves_tickets():
    now = datetime(2030, 1, 1, 12, 0)
    owner = User(1, "owner", "Посетитель")
    other = User(2, "other", "Другой")
    ticket = StandardTicket(Seat(1, 1), 10000)
    booking = Booking("ES-1", owner, EventSession(now + timedelta(days=1), 90), (ticket,))
    with pytest.raises(AppError):
        booking.cancel(other, "Нет прав", now)
    assert booking.status_at(now) == "active"
    assert booking.cancel(owner, "Планы изменились", now)
    assert not booking.cancel(owner, "Повтор", now)
    assert booking.status_at(now) == "cancelled"
    assert booking.cancel_reason == "Планы изменились"
    assert booking.total == 10000
    assert list(booking) == [ticket]
    assert len(booking) == 1


def test_seat_override_can_be_zero_and_does_not_fall_back_to_category():
    seat = Seat(1, 1, "VIP", price_override=0)
    assert seat.price({"VIP": 15000}) == 0


@pytest.mark.parametrize("value", [-1, True, 10.5, "100"])
def test_invalid_money_rejected_before_ticket_construction(value):
    with pytest.raises(AppError):
        StandardTicket(Seat(1, 1), value)
