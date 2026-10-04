import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import flet as ft

from eventseat.ui import field, rubles
from eventseat.ui_hall_editor import HallEditor


class EditorApp:
    def __init__(self):
        self.page = SimpleNamespace(update=lambda: None)
        self.view_state = SimpleNamespace(drafts={})
        self.capture_callback = None

    def safe(self, action):
        return action

    def button(self, label, action, **kwargs):
        return ft.Button(label, on_click=action)

    def heading(self, title, subtitle):
        return ft.Text(title)

    def capture_view(self):
        if self.capture_callback:
            self.capture_callback()

    def clear_capture(self):
        self.capture_callback = None

    def set_view(self, route, capture=None):
        self.capture_view()
        self.route = route
        self.capture_callback = capture

    def show(self, *controls):
        self.controls = controls

    def notice(self, *_):
        pass


def make_admin():
    hall = {
        "name": "Тестовый зал",
        "stage": "СЦЕНА",
        "rows": 2,
        "columns": 3,
        "category_prices": {"эконом": 10000, "стандарт": 20000, "VIP": 30000},
        "seats": [
            {
                "row": row,
                "number": number,
                "category": "стандарт",
                "enabled": True,
            }
            for row in (3, 4)
            for number in (5, 6, 7)
        ],
    }
    app = EditorApp()
    return SimpleNamespace(
        app=app,
        page=app.page,
        service=SimpleNamespace(get_hall=lambda _: deepcopy(hall)),
        price_fields=lambda prices: {
            category: field(category, f"{value / 100:.2f}") for category, value in prices.items()
        },
        read_prices=lambda fields: {key: rubles(control.value) for key, control in fields.items()},
        show=lambda _: None,
    )


def test_editor_restores_serializable_pending_fields_and_group_selection():
    admin = make_admin()
    editor = HallEditor(admin, 7)
    editor.show()
    editor.fields["name"].value = "Ещё не сохранённый зал"
    editor.fields["rows"].value = ""
    editor.selection.click(editor.seats, 0)
    editor.selection.click(editor.seats, 4, shift=True)
    editor.draw_inspector()
    editor.prices["VIP"].value = "123,"
    editor.property_fields["category"].value = "VIP"
    editor.capture()
    json.dumps(admin.app.view_state.drafts)
    restored = HallEditor(admin, 7)
    restored.show()
    assert restored.fields["name"].value == "Ещё не сохранённый зал"
    assert restored.fields["rows"].value == ""
    assert restored.selection.selected == {0, 1, 3, 4}
    assert restored.prices["VIP"].value == "123,"
    assert restored.property_fields["category"].value == "VIP"
    assert set(restored.property_fields) == {"category", "kind"}
    assert restored.inspector.visible is True


def test_keyboard_release_and_window_blur_clear_range_modifiers():
    admin = make_admin()
    editor = HallEditor(admin, 7)
    editor.show()
    editor.listener.focus = AsyncMock()
    asyncio.run(editor.click(0))
    editor.key_down(SimpleNamespace(key="Shift Left"))
    asyncio.run(editor.click(4))
    assert editor.selection.selected == {0, 1, 3, 4}
    editor.key_up(SimpleNamespace(key="Shift Left"))
    asyncio.run(editor.click(2))
    assert editor.selection.selected == {2}
    editor.key_down(SimpleNamespace(key="Control Left"))
    admin.app.on_view_blur()
    assert editor.pressed == set()
    asyncio.run(editor.click(5))
    assert editor.selection.selected == {5}


def test_group_properties_are_applied_in_inspector_without_a_dialog():
    admin = make_admin()
    editor = HallEditor(admin, 7)
    editor.show()
    editor.selection.selected = {0, 1, 3, 4}
    editor.draw_inspector()
    editor.property_fields["category"].value = "VIP"
    editor.property_fields["kind"].value = "aisle"
    editor.apply()
    assert all(editor.seats[index]["category"] == "VIP" for index in {0, 1, 3, 4})
    assert all(editor.seats[index]["enabled"] is False for index in {0, 1, 3, 4})
    assert all("price_override" not in seat for seat in editor.seats)
    assert editor.seats[2]["category"] == "стандарт"
