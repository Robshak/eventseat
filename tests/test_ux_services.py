from datetime import timedelta

import pytest

from eventseat.domain import AppError
from eventseat.storage import CartItemRecord, UserRecord

from .conftest import seat_ids


def hide_event(system):
    system["admin"].save_event(
        "Северный свет",
        "Концерт струнного квартета",
        "концерт",
        90,
        False,
        event_id=system["event"],
    )


def test_event_interval_includes_both_days_and_projects_only_matching_sessions(system):
    admin, user = system["admin"], system["user"]
    first_day = system["start"].replace(hour=0)
    last_start = (first_day + timedelta(days=2)).replace(hour=23, minute=59)

    def schedule(start, price):
        return admin.save_session(
            system["event"],
            system["hall"],
            start,
            category_prices={category: price for category in system["prices"]},
        )

    schedule(first_day - timedelta(days=1), 100)
    schedule(first_day, 30000)
    admin.set_session_prices(system["session"], {category: 40000 for category in system["prices"]})
    cancelled = schedule(first_day + timedelta(days=1), 200)
    admin.cancel_session(cancelled, "Сеанс исключён из афиши")
    schedule(last_start, 20000)
    schedule(first_day + timedelta(days=3, hours=6), 50)

    events = user.list_events(date_from=first_day.date(), date_to=last_start.date())
    assert len(events) == 1
    assert events[0]["next_start"] == first_day
    assert events[0]["min_price"] == 20000
    last_day = user.list_events(date_from=last_start.date(), date_to=last_start.date())
    assert last_day[0]["next_start"] == last_start
    assert last_day[0]["min_price"] == 20000
    assert (
        user.list_events(
            date_from=(first_day + timedelta(days=1)).date(),
            date_to=(first_day + timedelta(days=1)).date(),
        )
        == []
    )


def test_event_interval_supports_open_bounds_and_date_inputs(system):
    user = system["user"]
    day = system["start"].date()
    assert user.list_events(date_from=day.isoformat())[0]["id"] == system["event"]
    assert user.list_events(date_to=system["start"])[0]["id"] == system["event"]
    assert user.list_events(date_from=day + timedelta(days=1)) == []
    assert user.list_events(date_to=day - timedelta(days=1)) == []
    assert user.list_events(date_from="", date_to=None) == user.list_events()


@pytest.mark.parametrize(
    "filters",
    [
        {"date_from": "2030-05-02", "date_to": "2030-05-01"},
        {"date_from": "не дата"},
        {"date_to": 123},
        {"date": "2030-05-01", "date_from": "2030-05-01"},
    ],
)
def test_event_interval_rejects_invalid_filters(system, filters):
    with pytest.raises(AppError):
        system["user"].list_events(**filters)


def test_new_context_has_independent_login_and_does_not_keep_password(system):
    user = system["user"]
    user.add_to_cart(system["session"], seat_ids(user, system["session"])[:1])
    context = user.new_context()
    try:
        assert context.current_user is None
        with pytest.raises(AppError):
            context.login("alice", "wrong-password")
        assert user.current_user["login"] == "alice"
        context.login("boris", "Boris-pass-123")
        assert context.get_cart() == []
        assert len(user.get_cart()) == 1
        context.logout()
        assert user.current_user["login"] == "alice"
        context.login("alice", "Alice-pass-123")
        assert context.get_cart() == user.get_cart()
    finally:
        context.close()
    restarted = user.new_context()
    try:
        assert restarted.current_user is None
        assert user.current_user["login"] == "alice"
    finally:
        restarted.close()


def test_admin_cannot_add_to_cart_or_checkout_legacy_cart(system):
    admin, session = system["admin"], system["session"]
    selected = seat_ids(admin, session)[0]
    with pytest.raises(AppError, match="Администратор"):
        admin.add_to_cart(session, [selected])
    with admin.store.write() as db:
        db.add(
            CartItemRecord(
                user_id=admin.current_user["id"],
                session_id=session,
                seat_id=selected,
                tariff="standard",
                price=10000,
            )
        )
    with pytest.raises(AppError, match="Администратор"):
        admin.checkout("legacy-admin-cart")
    assert len(admin.get_cart()) == 1
    assert admin.list_bookings(admin=True) == []
    assert selected in seat_ids(admin, session)


def test_admin_role_cannot_replay_an_earlier_checkout(system):
    user = system["user"]
    user.add_to_cart(system["session"], seat_ids(user, system["session"])[:1])
    booking = user.checkout("before-role-change")[0]
    with user.store.write() as db:
        db.get(UserRecord, booking["user_id"]).role = "admin"
    with pytest.raises(AppError, match="Администратор"):
        user.checkout("before-role-change")
    assert user.get_booking(booking["id"])["id"] == booking["id"]


def test_cart_event_link_allows_only_its_owner_to_read_hidden_event(system):
    user, other = system["user"], system["other"]
    user.add_to_cart(system["session"], seat_ids(user, system["session"])[:1])
    item = user.get_cart()[0]
    assert item["event_id"] == system["event"]
    assert item["session_id"] == system["session"]
    hide_event(system)
    with pytest.raises(AppError):
        user.get_event(item["event_id"])
    with pytest.raises(AppError):
        other.get_related_event(item["event_id"])
    with pytest.raises(AppError):
        system["connect"]().get_related_event(item["event_id"])
    related = user.get_related_event(item["event_id"])
    assert related["title"] == item["title"]
    assert related["published"] is False
    assert related["next_start"] is None
    assert related["min_price"] == 0
    with pytest.raises(AppError):
        user.checkout("unpublished-event")
    user.remove_cart_item(item["id"])
    with pytest.raises(AppError):
        user.get_related_event(item["event_id"])


def test_cancelled_booking_retains_event_link_and_ticket_after_event_is_hidden(system):
    user = system["user"]
    user.add_to_cart(system["session"], seat_ids(user, system["session"])[:1])
    booking = user.checkout("historical-event-link")[0]
    assert booking["event_id"] == system["event"]
    assert booking["session_id"] == system["session"]
    system["admin"].cancel_session(system["session"], "Мероприятие отменено")
    hide_event(system)
    assert user.get_related_event(booking["event_id"])["id"] == system["event"]
    history = user.get_booking(booking["id"])
    assert history["status"] == "cancelled"
    assert history["tickets"] == booking["tickets"]
    assert history["total"] == booking["total"]
    with pytest.raises(AppError):
        system["other"].get_related_event(booking["event_id"])


def test_related_event_does_not_grant_access_to_unrelated_draft(system):
    draft = system["admin"].save_event("Черновик", "Описание", "кино", 60, False)
    assert system["admin"].get_related_event(draft)["id"] == draft
    with pytest.raises(AppError):
        system["user"].get_related_event(draft)


def test_session_with_booking_history_cannot_be_reassigned_to_another_event(system):
    admin, user, session = system["admin"], system["user"], system["session"]
    user.add_to_cart(session, seat_ids(user, session)[:1])
    booking = user.checkout("permanent-event-link")[0]
    user.cancel_booking(booking["id"])
    other_event = admin.save_event("Другое событие", "Описание", "лекция", 90, False)
    with pytest.raises(AppError, match="история бронирований"):
        admin.save_session(other_event, system["hall"], system["start"], session_id=session)
    assert user.get_booking(booking["id"])["event_id"] == system["event"]
    with pytest.raises(AppError):
        user.get_related_event(other_event)


def test_session_in_cart_cannot_be_reassigned_to_unrelated_draft(system):
    admin, user, session = system["admin"], system["user"], system["session"]
    user.add_to_cart(session, seat_ids(user, session)[:1])
    other_event = admin.save_event("Чужой черновик", "Описание", "лекция", 90, False)
    with pytest.raises(AppError, match="корзины"):
        admin.save_session(other_event, system["hall"], system["start"], session_id=session)
    assert user.get_cart()[0]["event_id"] == system["event"]
    with pytest.raises(AppError):
        user.get_related_event(other_event)
