from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from eventseat.services import Service
from eventseat.storage import Store

from .conftest import seat_ids


def test_complete_booking_workflow_survives_restart(system):
    user = system["user"]
    session = system["session"]
    selected = seat_ids(user, session)[:2]
    user.add_to_cart(session, selected)
    user.close()
    restarted = system["connect"]()
    restarted.login("alice", "Alice-pass-123")
    assert {item["seat_id"] for item in restarted.get_cart()} == set(selected)
    booking = restarted.checkout("persistent-checkout")[0]
    assert len(booking["tickets"]) == 2
    assert booking["number"]
    restarted.close()
    reopened = system["connect"]()
    reopened.login("alice", "Alice-pass-123")
    persisted = reopened.get_booking(booking["id"])
    assert persisted["number"] == booking["number"]
    assert persisted["total"] == booking["total"]
    assert persisted["tickets"] == booking["tickets"]
    assert reopened.checkout("persistent-checkout")[0]["id"] == booking["id"]
    reopened.cancel_booking(booking["id"])
    assert reopened.get_booking(booking["id"])["status"] == "cancelled"
    assert set(selected) <= set(seat_ids(reopened, session))


def test_demonstration_data_is_seeded_once_and_never_creates_accounts(tmp_path):
    database = tmp_path / "demo.sqlite3"
    first = Service(database, seed=True)
    try:
        assert first.needs_setup()
        events = first.list_events()
        assert events
        before_ids = {event["id"] for event in events}
    finally:
        first.close()
    second = Service(database, seed=True)
    try:
        assert second.needs_setup()
        assert {event["id"] for event in second.list_events()} == before_ids
    finally:
        second.close()


def test_concurrent_first_launch_seeds_demonstrations_exactly_once(tmp_path):
    database = tmp_path / "concurrent-first-launch.sqlite3"
    barrier = Barrier(6)

    def start():
        barrier.wait(timeout=10)
        service = Service(database, seed=True)
        try:
            assert service.needs_setup()
            return {event["id"] for event in service.list_events()}, len(service.list_sessions())
        finally:
            service.close()

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(start) for _ in range(6)]
        results = [future.result(timeout=30) for future in futures]
    assert all(result == results[0] for result in results)
    assert len(results[0][0]) == 3
    assert results[0][1] == 6


def test_interrupted_first_launch_recovers_pending_demo_seed(tmp_path):
    database = tmp_path / "interrupted-setup.sqlite3"
    store = Store(database, seed_new=True)
    store.close()
    service = Service(database, seed=True)
    try:
        assert service.needs_setup()
        assert len(service.list_events()) == 3
    finally:
        service.close()
    restarted = Service(database, seed=True)
    try:
        assert len(restarted.list_events()) == 3
    finally:
        restarted.close()


def test_existing_unseeded_database_is_never_filled_on_restart(tmp_path):
    database = tmp_path / "intentionally-empty.sqlite3"
    service = Service(database, seed=False)
    service.close()
    restarted = Service(database, seed=True)
    try:
        assert restarted.list_events() == []
        assert restarted.needs_setup()
    finally:
        restarted.close()
