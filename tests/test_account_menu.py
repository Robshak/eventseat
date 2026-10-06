import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import flet as ft

from eventseat.account_menu import AccountMenu


def menu_for(count=2):
    users = [
        {"id": i, "login": f"user{i}", "name": f"Пользователь {i}", "role": "user"}
        for i in range(count)
    ]
    app = SimpleNamespace(
        accounts=SimpleNamespace(users=users),
        page=SimpleNamespace(update=Mock(), run_task=Mock()),
        safe=lambda action: action,
        switch_account=Mock(),
        outside_view=Mock(),
    )
    menu = AccountMenu(app, users[0])
    app.account_menu = menu
    menu.listener.focus = AsyncMock()
    return app, menu


def test_menu_opens_above_anchor_with_wider_account_cards_and_closes_on_escape():
    app, menu = menu_for()
    asyncio.run(menu.toggle())
    assert menu.popup.visible and menu.backdrop.visible
    assert menu.popup.bottom > menu.FOOTER_HEIGHT + menu.trigger.height
    assert menu.popup.left == 24
    assert menu.popup.width > menu.trigger.width
    assert menu.popup.left + menu.popup.width < 1000
    assert all(item.tooltip is None for item in menu.items)
    assert menu.items[0].height < 90
    assert menu.popup.height <= 720 - menu.popup.bottom - 24
    menu.listener.focus.assert_awaited_once()
    menu.key_down(SimpleNamespace(key="Escape"))
    assert not menu.popup.visible and not menu.backdrop.visible
    app.page.run_task.assert_called_once_with(menu.restore_focus)


def test_many_accounts_scroll_inside_menu_and_only_selected_account_is_active():
    _, menu = menu_for(12)
    assert menu.popup.height == 312
    assert menu.listener.content.scroll == ft.ScrollMode.AUTO
    assert len(menu.items) == 12
    assert [item.data["account_id"] for item in menu.items if item.data["active"]] == [0]


def test_outside_click_dismisses_without_switching_and_choice_closes_before_switch():
    app, menu = menu_for()
    asyncio.run(menu.toggle())
    menu.backdrop.on_tap(None)
    assert not menu.opened
    app.switch_account.assert_not_called()
    asyncio.run(menu.toggle())

    def switched(_):
        assert not menu.popup.visible

    app.switch_account.side_effect = switched
    menu.items[1].on_click(None)
    app.switch_account.assert_called_once_with(1)
    assert not menu.backdrop.visible


def test_escape_and_queued_focus_from_replaced_menu_are_ignored():
    app, menu = menu_for()
    asyncio.run(menu.toggle())
    menu.trigger.focus = AsyncMock()
    app.account_menu = object()
    menu.key_down(SimpleNamespace(key="Escape"))
    app.page.run_task.assert_not_called()
    asyncio.run(menu.restore_focus())
    menu.trigger.focus.assert_not_awaited()
