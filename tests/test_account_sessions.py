import pytest

from eventseat.account_sessions import AccountSessions
from eventseat.domain import AppError
from eventseat.services import Service


def test_failed_addition_preserves_authenticated_account(system):
    accounts = AccountSessions(system["user"])
    original = accounts.current
    accounts.begin_login()
    pending = accounts.current
    assert pending is not original
    assert pending.current_user is None
    with pytest.raises(AppError):
        pending.login("boris", "wrong-password")
    with pytest.raises(AppError):
        accounts.remember_current()
    assert [user["login"] for user in accounts.users] == ["alice"]
    assert accounts.can_return
    accounts.cancel_login()
    assert accounts.current is original
    assert accounts.current.current_user["login"] == "alice"
    assert pending.current_user is None
    assert not accounts.can_return


def test_switch_keeps_separate_carts_and_authenticated_contexts(system):
    accounts = AccountSessions(system["user"])
    alice_id = accounts.current.current_user["id"]
    seat = accounts.current.seat_map(system["session"])[0]["id"]
    accounts.current.add_to_cart(system["session"], [seat])
    accounts.begin_login()
    accounts.current.login("boris", "Boris-pass-123")
    accounts.remember_current()
    boris_id = accounts.current.current_user["id"]
    assert accounts.current.get_cart() == []
    accounts.switch(alice_id)
    assert accounts.current.current_user["login"] == "alice"
    assert accounts.current.get_cart()[0]["seat_id"] == seat
    accounts.switch(boris_id)
    assert accounts.current.current_user["login"] == "boris"
    assert accounts.current.get_cart() == []
    accounts.close()


def test_logout_removes_only_current_account_and_requires_password_to_return(system):
    accounts = AccountSessions(system["user"])
    accounts.begin_login()
    accounts.current.login("boris", "Boris-pass-123")
    accounts.remember_current()
    logged_out = accounts.current
    boris_id = logged_out.current_user["id"]
    accounts.logout()
    assert accounts.current.current_user["login"] == "alice"
    assert [user["login"] for user in accounts.users] == ["alice"]
    assert logged_out.current_user is None
    with pytest.raises(AppError, match="войти снова"):
        accounts.switch(boris_id)
    assert accounts.current.current_user["login"] == "alice"
    accounts.close()


def test_last_logout_keeps_cart_but_leaves_no_authenticated_context(system):
    accounts = AccountSessions(system["user"])
    user_id = accounts.current.current_user["id"]
    seat = accounts.current.seat_map(system["session"])[0]["id"]
    accounts.current.add_to_cart(system["session"], [seat])
    accounts.logout()
    assert accounts.users == []
    assert accounts.current.current_user is None
    with pytest.raises(AppError):
        accounts.switch(user_id)
    accounts.current.login("alice", "Alice-pass-123")
    accounts.remember_current()
    assert accounts.current.get_cart()[0]["seat_id"] == seat
    accounts.close()


def test_duplicate_login_replaces_context_without_duplicating_account(system):
    accounts = AccountSessions(system["user"])
    original = accounts.current
    accounts.begin_login()
    accounts.current.login("alice", "Alice-pass-123")
    accounts.remember_current()
    assert len(accounts.users) == 1
    assert accounts.current.current_user["login"] == "alice"
    assert original.current_user is None
    accounts.close()


def test_close_clears_all_accounts_including_pending_login_and_restart_is_signed_out(system):
    accounts = AccountSessions(system["user"])
    alice = accounts.current
    accounts.begin_login()
    accounts.current.login("boris", "Boris-pass-123")
    accounts.remember_current()
    boris = accounts.current
    accounts.begin_login()
    pending = accounts.current
    accounts.close()
    assert accounts.users == []
    assert not accounts.can_return
    assert all(context.current_user is None for context in [alice, boris, pending])
    restarted = AccountSessions(Service(system["database"], seed=False))
    assert restarted.users == []
    assert restarted.current.current_user is None
    restarted.close()
