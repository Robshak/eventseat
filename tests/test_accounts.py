import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from eventseat.domain import AppError
from eventseat.services import Service


def test_first_admin_is_one_time_and_registration_never_promotes(tmp_path):
    service = Service(tmp_path / "accounts.sqlite3", seed=False)
    try:
        assert service.needs_setup()
        administrator = service.setup_admin("root", "Первый администратор", "Root-pass-123")
        assert administrator["role"] == "admin"
        assert not service.needs_setup()
        with pytest.raises(AppError):
            service.setup_admin("another", "Другой", "Other-pass-123")
        user = service.register("regular", "Посетитель", "User-pass-123")
        assert user["role"] == "user"
        assert service.login("regular", "User-pass-123")["id"] == user["id"]
        service.logout()
        assert service.current_user is None
    finally:
        service.close()


def test_first_admin_creation_is_safe_across_independent_engines(tmp_path):
    database = tmp_path / "first-admin.sqlite3"
    first, second = Service(database, seed=False), Service(database, seed=False)
    barrier = Barrier(2)

    def setup(service, login):
        barrier.wait(timeout=10)
        try:
            return service.setup_admin(login, "Администратор", "Setup-pass-123")["role"]
        except AppError:
            return "rejected"

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            a = executor.submit(setup, first, "first-admin")
            b = executor.submit(setup, second, "second-admin")
            assert sorted([a.result(timeout=30), b.result(timeout=30)]) == ["admin", "rejected"]
        with sqlite3.connect(database) as connection:
            assert (
                connection.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'").fetchone()[0]
                == 1
            )
    finally:
        first.close()
        second.close()


def test_registration_cannot_bypass_initial_admin_setup(tmp_path):
    service = Service(tmp_path / "setup.sqlite3", seed=False)
    try:
        with pytest.raises(AppError):
            service.register("visitor", "Посетитель", "Visitor-pass-123")
        assert service.needs_setup()
    finally:
        service.close()


@pytest.mark.parametrize(
    "login,password", [("alice", "wrong-password"), ("unknown", "Alice-pass-123")]
)
def test_login_rejects_bad_credentials(system, login, password):
    visitor = system["connect"]()
    with pytest.raises(AppError):
        visitor.login(login, password)
    assert visitor.current_user is None


def test_duplicate_login_and_weak_password_rejected(system):
    service = system["connect"]()
    with pytest.raises(AppError):
        service.register("alice", "Двойник", "Duplicate-pass-123")
    with pytest.raises(AppError):
        service.register("weak", "Слабый пароль", "123")


def test_profile_change_requires_current_password_and_survives_restart(system):
    user = system["user"]
    with pytest.raises(AppError):
        user.update_profile("Алиса", "bad-password", "New-Alice-pass-123")
    updated = user.update_profile("Алиса Новая", "Alice-pass-123", "New-Alice-pass-123")
    assert updated["name"] == "Алиса Новая"
    visitor = system["connect"]()
    with pytest.raises(AppError):
        visitor.login("alice", "Alice-pass-123")
    assert visitor.login("alice", "New-Alice-pass-123")["name"] == "Алиса Новая"


def test_password_is_argon2_hash_not_plaintext(system):
    with sqlite3.connect(system["database"]) as connection:
        persisted = "\n".join(connection.iterdump())
    assert "$argon2id$" in persisted
    assert "Alice-pass-123" not in persisted
    assert "Admin-pass-123" not in persisted


@pytest.mark.parametrize(
    "operation",
    [
        lambda service, data: service.save_event("Чужое", "Описание", "кино", 60, True),
        lambda service, data: service.save_hall("Чужой зал", 2, 2, "Экран", data["prices"]),
        lambda service, data: service.save_session(data["event"], data["hall"], data["start"]),
        lambda service, data: service.cancel_session(data["session"], "Нет прав"),
        lambda service, data: service.set_session_prices(data["session"], data["prices"]),
        lambda service, data: service.list_bookings(admin=True),
        lambda service, data: service.list_events(admin=True),
        lambda service, data: service.statistics(),
    ],
)
def test_admin_permissions_are_enforced_by_service(system, operation):
    with pytest.raises(AppError):
        operation(system["user"], system)


@pytest.mark.parametrize(
    "operation",
    [
        lambda service: service.get_cart(),
        lambda service: service.list_bookings(),
        lambda service: service.checkout("anonymous"),
        lambda service: service.update_profile("Аноним"),
    ],
)
def test_personal_operations_require_login(system, operation):
    visitor = system["connect"]()
    with pytest.raises(AppError):
        operation(visitor)
