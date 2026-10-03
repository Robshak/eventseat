from datetime import datetime, timedelta

import pytest

from eventseat.services import Service


@pytest.fixture
def system(tmp_path):
    opened = []
    database = tmp_path / "eventseat.sqlite3"

    def connect():
        service = Service(database, seed=False)
        opened.append(service)
        return service

    admin = connect()
    admin.setup_admin("admin", "Администратор", "Admin-pass-123")
    admin.login("admin", "Admin-pass-123")
    user = connect()
    user.register("alice", "Алиса", "Alice-pass-123")
    user.login("alice", "Alice-pass-123")
    other = connect()
    other.register("boris", "Борис", "Boris-pass-123")
    other.login("boris", "Boris-pass-123")
    prices = {"эконом": 8000, "стандарт": 10000, "VIP": 15000}
    hall = admin.save_hall("Малый зал", 2, 3, "Сцена", prices)
    event = admin.save_event("Северный свет", "Концерт струнного квартета", "концерт", 90, True)
    start = (datetime.now() + timedelta(days=7)).replace(hour=18, minute=0, second=0, microsecond=0)
    session = admin.save_session(event, hall, start)
    yield {
        "admin": admin,
        "user": user,
        "other": other,
        "connect": connect,
        "database": database,
        "hall": hall,
        "event": event,
        "session": session,
        "start": start,
        "prices": prices,
    }
    for service in reversed(opened):
        service.close()


def seat_ids(service, session):
    return [seat["id"] for seat in service.seat_map(session) if seat["status"] == "free"]
