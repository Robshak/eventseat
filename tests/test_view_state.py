import asyncio
import inspect
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import flet as ft
import pytest

from eventseat.ui import App, hoverable
from eventseat.ui_admin import AdminUI
from eventseat.view_state import AccountViewState, ViewStates


class FakePage:
    def __init__(self):
        self.services = []
        self.controls = []
        self.dialogs = []
        self.tasks = []

    def update(self):
        pass

    def show_dialog(self, dialog):
        self.dialogs.append(dialog)

    def pop_dialog(self):
        return self.dialogs.pop() if self.dialogs else None

    def run_task(self, function, *args):
        self.tasks.append((function, args))


def descendants(value):
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from descendants(item)
    elif isinstance(value, ft.BaseControl):
        yield value
        for name in ("content", "controls", "actions", "title", "items"):
            yield from descendants(getattr(value, name, None))


def form_field(app, label):
    return next(
        control
        for control in descendants(app.page.controls)
        if isinstance(control, (ft.TextField, ft.Dropdown)) and control.label == label
    )


def button(app, label):
    return next(
        control
        for control in descendants(app.page.controls)
        if isinstance(control, (ft.Button, ft.OutlinedButton, ft.TextButton))
        and control.content == label
    )


def invoke(callback):
    result = callback(None)
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


@pytest.fixture
def app_factory(system):
    opened = []

    def create(role="user"):
        app = App(FakePage(), system[role])
        app.shell()
        app.catalogue()
        opened.append(app)
        return app

    yield create
    for app in opened:
        app.close()


def add_logged_in_account(app, login="boris", password="Boris-pass-123"):
    app.add_account()
    form_field(app, "Логин").value = login
    form_field(app, "Пароль").value = password
    invoke(button(app, "Войти").on_click)


def test_state_registry_isolates_routes_drafts_and_scroll_and_forgets_only_one_account():
    states = ViewStates()
    alice, boris = states.for_account(1), states.for_account(2)
    alice.drafts["profile"] = {"name": "Черновик Алисы"}
    alice.routes["Администрирование"] = {"page": "hall", "hall_id": 3}
    alice.scroll_offsets["profile"] = 100
    assert boris.drafts == boris.routes == boris.scroll_offsets == {}
    route = alice.admin_route
    route["hall_id"] = 99
    assert alice.admin_route["hall_id"] == 3
    assert AccountViewState.route_key({"page": "hall", "hall_id": 3}) == AccountViewState.route_key(
        {"hall_id": 3, "page": "hall"}
    )
    states.forget(1)
    assert states.for_account(1).drafts == {}
    assert states.for_account(2) is boris


def test_profile_draft_is_captured_before_add_and_switch_and_never_saves_passwords(app_factory):
    app = app_factory()
    alice = app.service.current_user["id"]
    app.profile()
    form_field(app, "Отображаемое имя").value = "Черновик Алисы"
    form_field(app, "Текущий пароль").value = "Alice-pass-123"
    form_field(app, "Новый пароль").value = "Unfinished-secret-123"
    add_logged_in_account(app)
    boris = app.service.current_user["id"]
    app.profile()
    assert form_field(app, "Отображаемое имя").value == "Борис"
    form_field(app, "Отображаемое имя").value = "Черновик Бориса"
    app.switch_account(alice)
    assert app.section == "Профиль"
    assert form_field(app, "Отображаемое имя").value == "Черновик Алисы"
    assert form_field(app, "Текущий пароль").value == ""
    assert form_field(app, "Новый пароль").value == ""
    saved = json.dumps(app.view_state.drafts, ensure_ascii=False)
    assert "Alice-pass" not in saved and "Unfinished-secret" not in saved
    app.switch_account(boris)
    assert form_field(app, "Отображаемое имя").value == "Черновик Бориса"


def test_cancel_pending_login_restores_previous_form_and_invalidates_old_auth_callbacks(
    app_factory,
):
    app = app_factory()
    alice = app.service.current_user["id"]
    app.profile()
    form_field(app, "Отображаемое имя").value = "Вернуться к этому имени"
    app.add_account()
    pending = app.service
    form_field(app, "Логин").value = "boris"
    form_field(app, "Пароль").value = "Boris-pass-123"
    old_login = button(app, "Войти").on_click
    app.cancel_add_account()
    assert pending.current_user is None
    assert app.service.current_user["id"] == alice
    assert form_field(app, "Отображаемое имя").value == "Вернуться к этому имени"
    invoke(old_login)
    assert app.service.current_user["id"] == alice
    assert len(app.accounts.users) == 1


def test_logout_discards_only_departing_account_state_and_restores_remaining_account(app_factory):
    app = app_factory()
    alice = app.service.current_user["id"]
    app.profile()
    form_field(app, "Отображаемое имя").value = "Черновик Алисы"
    add_logged_in_account(app)
    boris = app.service.current_user["id"]
    app.profile()
    form_field(app, "Отображаемое имя").value = "Черновик Бориса"
    app.switch_account(alice)
    old_save = button(app, "Сохранить изменения").on_click
    app.logout()
    assert app.service.current_user["id"] == boris
    assert form_field(app, "Отображаемое имя").value == "Черновик Бориса"
    assert alice not in app._view_states._accounts
    invoke(old_save)
    assert app.service.current_user["name"] == "Борис"
    assert form_field(app, "Отображаемое имя").value == "Черновик Бориса"


def test_incomplete_catalogue_filter_restores_without_applying_invalid_date(app_factory, system):
    app = app_factory()
    day = system["start"].strftime("%d.%m.%Y")
    app.catalogue("Север", "концерт", day, day)
    form_field(app, "Найти событие").value = "Не законченный запрос"
    form_field(app, "С · ДД.ММ.ГГГГ").value = "12."
    app.navigate("Профиль", app.profile)
    app.navigate("Афиша", lambda: app.restore_section("Афиша"))
    assert form_field(app, "Найти событие").value == "Не законченный запрос"
    assert form_field(app, "С · ДД.ММ.ГГГГ").value == "12."
    assert app.view_state.drafts["catalogue:applied"]["date_from"] == day
    assert any(
        isinstance(control, ft.Text) and control.value == "Северный свет"
        for control in descendants(app.content)
    )
    invoke(form_field(app, "С · ДД.ММ.ГГГГ").on_submit)
    assert app.page.dialogs
    app.navigate("Профиль", app.profile)
    app.navigate("Афиша", lambda: app.restore_section("Афиша"))
    assert form_field(app, "С · ДД.ММ.ГГГГ").value == "12."


def test_catalogue_event_back_preserves_unsent_search_and_date_edits(app_factory):
    app = app_factory()
    form_field(app, "Найти событие").value = "Ещё редактирую запрос"
    form_field(app, "С · ДД.ММ.ГГГГ").value = "12."
    card = next(
        control
        for control in descendants(app.content)
        if isinstance(control, ft.Container) and control.width == 270 and control.on_click
    )
    invoke(card.on_click)
    assert app._route["page"] == "event"
    invoke(button(app, "К афише").on_click)
    assert app._route["page"] == "catalogue"
    assert form_field(app, "Найти событие").value == "Ещё редактирую запрос"
    assert form_field(app, "С · ДД.ММ.ГГГГ").value == "12."


def test_reauthenticating_remembered_account_keeps_its_draft_and_rejects_old_callbacks(app_factory):
    app = app_factory()
    alice_context = app.service
    app.profile()
    form_field(app, "Отображаемое имя").value = "Черновик до повторного входа"
    stale_save = button(app, "Сохранить изменения").on_click
    add_logged_in_account(app)
    add_logged_in_account(app, "alice", "Alice-pass-123")
    assert app.service is not alice_context
    assert alice_context.current_user is None
    assert form_field(app, "Отображаемое имя").value == "Черновик до повторного входа"
    assert len(app.accounts.users) == 2
    invoke(stale_save)
    assert app.service.current_user["name"] == "Алиса"


@pytest.mark.parametrize("editor", ["event", "session"])
def test_admin_form_route_and_unfinished_values_survive_profile_roundtrip(
    app_factory, system, editor
):
    app = app_factory("admin")
    admin = AdminUI(app)
    if editor == "event":
        admin.event_form(system["event"])
        label, draft, return_button = "Продолжительность, мин", "1.", "К мероприятиям"
    else:
        admin.session_form(system["session"])
        label, draft, return_button = "Время · ЧЧ:ММ", "19:", "К сеансам"
    expected_route = dict(app._route)
    form_field(app, label).value = draft
    app.navigate("Профиль", app.profile)
    app.navigate("Администрирование", app.admin)
    assert app._route == expected_route
    assert form_field(app, label).value == draft
    invoke(button(app, return_button).on_click)
    assert app._route["page"] == "list"
    app.navigate("Профиль", app.profile)
    app.navigate("Администрирование", app.admin)
    assert app._route["page"] == "list"
    if editor == "event":
        AdminUI(app).event_form(system["event"])
    else:
        AdminUI(app).session_form(system["session"])
    assert form_field(app, label).value == draft


@pytest.mark.parametrize("tab", ["Сеансы", "Статистика"])
def test_admin_partial_filter_survives_profile_roundtrip(app_factory, tab):
    app = app_factory("admin")
    app.admin(tab)
    form_field(app, "С · ДД.ММ.ГГГГ").value = "12."
    app.navigate("Профиль", app.profile)
    app.navigate("Администрирование", app.admin)
    assert form_field(app, "С · ДД.ММ.ГГГГ").value == "12."
    assert app._route["tab"] == tab


@pytest.mark.parametrize("tab, prefix", [("Сеансы", "session"), ("Статистика", "statistics")])
def test_invalid_admin_apply_keeps_last_valid_result_and_raw_draft(
    app_factory, system, tab, prefix
):
    app = app_factory("admin")
    app.admin(tab)
    day = system["start"].strftime("%d.%m.%Y")
    apply_label = "Применить фильтры" if tab == "Статистика" else "Применить"
    form_field(app, "С · ДД.ММ.ГГГГ").value = day
    form_field(app, "По · ДД.ММ.ГГГГ").value = day
    invoke(button(app, apply_label).on_click)
    applied = dict(app.view_state.drafts[f"admin:{prefix}_applied"])
    form_field(app, "С · ДД.ММ.ГГГГ").value = "12."
    invoke(button(app, apply_label).on_click)
    assert app.page.dialogs
    assert app.view_state.drafts[f"admin:{prefix}_applied"] == applied
    assert app.view_state.drafts[f"admin:{prefix}_filters"]["date_from"] == "12."
    app.navigate("Профиль", app.profile)
    app.navigate("Администрирование", app.admin)
    assert form_field(app, "С · ДД.ММ.ГГГГ").value == "12."
    assert app.view_state.drafts[f"admin:{prefix}_applied"]["date_from"] == day


def test_navigation_remembers_each_scroll_offset_and_old_restore_cannot_scroll_new_page(
    app_factory,
):
    app = app_factory()
    app.content.on_scroll(SimpleNamespace(pixels=420.0))
    app.navigate("Профиль", app.profile)
    app.content.on_scroll(SimpleNamespace(pixels=81.0))
    app.navigate("Афиша", lambda: app.restore_section("Афиша"))
    function, args = app.page.tasks[-1]
    assert function == app._restore_scroll and args[1] == 420.0
    old_generation = args[0]
    app.navigate("Профиль", app.profile)
    assert app.page.tasks[-1][1][1] == 81.0
    original_content = app.content
    app.content = SimpleNamespace(page=True, scroll_to=AsyncMock())
    asyncio.run(app._restore_scroll(old_generation, 420.0))
    app.content.scroll_to.assert_not_awaited()
    app.content = original_content


@pytest.mark.parametrize("width", [None, 1, 2])
def test_hover_never_changes_card_geometry_or_border_width(monkeypatch, width):
    control = ft.Container(
        width=150,
        height=70,
        padding=12,
        bgcolor="#FFFFFF",
        border=ft.Border.all(width, "#123456") if width is not None else None,
    )
    monkeypatch.setattr(control, "update", lambda: None)
    hoverable(control, "#EEEEEE")
    initial = control.border
    for data in ("true", "false", "true", "false"):
        control.on_hover(SimpleNamespace(data=data))
        assert control.width == 150 and control.height == 70 and control.padding == 12
        for side in ("top", "right", "bottom", "left"):
            assert getattr(control.border, side).width == getattr(initial, side).width
    assert control.bgcolor == "#FFFFFF"
