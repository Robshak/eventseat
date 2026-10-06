import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import flet as ft
import pytest

from eventseat.ui import field, rubles
from eventseat.ui_hall_editor import HallEditor, fit_hall_grid


class EditorApp:
    def __init__(self):
        self.page = SimpleNamespace(update=lambda: None)
        self.view_state = SimpleNamespace(drafts={})
        self.capture_callback = None

    def safe(self, action):
        return action

    def button(self, label, action, **kwargs):
        return ft.Button(label, on_click=action)

    def back_button(self, label, action):
        return self.button(label, lambda _: self.go_back(action))

    def go_back(self, fallback):
        if getattr(self, "on_view_outside_click", None):
            self.on_view_outside_click()
        return fallback()

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


def test_outside_click_clears_selection_but_keeps_applied_draft():
    admin = make_admin()
    editor = HallEditor(admin, 7)
    editor.show()
    editor.selection.selected = {0, 1}
    editor.selection.anchor = 0
    editor.draw_inspector()
    editor.property_fields["category"].value = "VIP"
    editor.apply()
    admin.app.on_view_outside_click()
    assert not editor.selection.selected and editor.selection.anchor is None
    assert not editor.inspector.visible
    assert editor.seats[0]["category"] == editor.seats[1]["category"] == "VIP"
    assert admin.app.view_state.drafts[editor.draft_key]["selected"] == []
    assert editor.background.data == "hall-editor-background"
    assert editor.background.on_click is None
    assert editor.background_region.on_tap is not None


def test_grid_and_inspector_background_clicks_preserve_pending_properties():
    admin = make_admin()
    editor = HallEditor(admin, 7)
    editor.show()
    editor.selection.selected = {0, 1}
    editor.draw_inspector()
    editor.property_fields["category"].value = "VIP"
    editor.grid_region.on_tap(None)
    editor.inspector_region.on_tap(None)
    editor.property_fields["category"].on_focus(None)
    assert editor.selection.selected == {0, 1}
    assert editor.property_fields["category"].value == "VIP"
    editor.fields["name"].on_focus(None)
    assert not editor.selection.selected


def test_noninteractive_background_regions_use_basic_cursor_and_back_clears_before_capture():
    admin = make_admin()
    editor = HallEditor(admin, 7)
    editor.show()
    for region, surface in [
        (editor.background_region, editor.background),
        (editor.panel_region, editor.grid_panel),
        (editor.inspector_region, editor.inspector),
    ]:
        assert region.mouse_cursor == ft.MouseCursor.BASIC
        assert surface.on_click is None
        assert region.content is surface
    editor.selection.selected = {0, 1}
    editor.selection.anchor = 0
    seen = []
    admin.show = lambda tab: seen.append((tab, set(editor.selection.selected)))
    back = editor.background.content.controls[0]
    assert back.content == "К залам"
    back.on_click(None)
    assert seen == [("Залы", set())]


def test_only_actual_seat_matrix_is_protected_inside_the_white_panel():
    admin = make_admin()
    editor = HallEditor(admin, 7)
    editor.show()
    editor.selection.selected = {0, 1}
    editor.selection.anchor = 0
    editor.draw()
    editor.draw_inspector()
    assert editor.grid_region.content is editor.grid
    assert editor.grid_region in editor.grid_panel.content.controls
    assert editor.panel_region.content is editor.grid_panel
    assert editor.workspace.controls[0] is editor.panel_region
    # Small halls leave white space to the right; that space must belong to the
    # clearing panel rather than the protected seat matrix.
    assert editor.grid_region.width < editor.grid_panel.width - 50
    actual_width = max(
        sum(control.width for control in row.controls) + row.spacing * (len(row.controls) - 1)
        for row in editor.grid.controls
    )
    assert editor.grid_region.width == pytest.approx(actual_width)
    editor.grid_region.on_tap(None)
    assert editor.selection.selected == {0, 1}
    editor.panel_region.on_tap(None)
    assert editor.selection.selected == set()
    assert not editor.inspector.visible


def test_panel_footer_stage_and_padding_are_outside_the_retaining_region():
    admin = make_admin()
    editor = HallEditor(admin, 7)
    editor.show()
    protected_children = [
        control
        for control in editor.grid_panel.content.controls
        if isinstance(control, ft.GestureDetector)
    ]
    assert protected_children == [editor.grid_region]
    assert editor.summary in editor.grid_panel.content.controls
    assert editor.selection_summary in editor.grid_panel.content.controls
    assert editor.summary not in editor.grid.controls
    assert editor.selection_summary not in editor.grid.controls
    # Buttons keep their own action inside the clearing region; in Flutter the
    # descendant tap recognizer wins over the parent's background recognizer.
    action_row = editor.grid_panel.content.controls[-2]
    select_all = next(button for button in action_row.controls if button.content == "Выделить всё")
    select_all.on_click(None)
    assert editor.selection.selected == set(range(len(editor.seats)))
    editor.panel_region.on_tap(None)
    assert not editor.selection.selected


def test_resize_keeps_pending_properties_and_resizes_actual_cells():
    admin = make_admin()
    editor = HallEditor(admin, 7)
    editor.show()
    editor.fields["rows"].value = "3"
    editor.fields["columns"].value = "50"
    editor.generate()
    editor.selection.selected = {0, 1}
    editor.draw_inspector()
    editor.property_fields["category"].value = "VIP"
    editor.resize(SimpleNamespace(width=702))
    assert editor.property_fields["category"].value == "VIP"
    assert editor.layout.inspector_below is True
    for row in editor.grid.controls:
        rendered_width = sum(control.width for control in row.controls)
        rendered_width += row.spacing * (len(row.controls) - 1)
        assert rendered_width <= editor.grid_panel.width - 50 + 0.001
    assert editor.grid_panel.width <= 702
    assert all(cell.width < 39 for cell in editor.grid.controls[0].controls[1:])
    assert editor.grid_panel.expand is None


@pytest.mark.parametrize("columns", [1, 3, 10, 20, 50])
@pytest.mark.parametrize("available", [680, 980, 1600])
@pytest.mark.parametrize("inspector", [False, True])
def test_layout_never_overflows_the_available_panel(columns, available, inspector):
    layout = fit_hall_grid(columns, available, inspector)
    assert 0 < layout.panel_width <= min(900, available)
    assert (
        layout.label_width + columns * (layout.cell_width + layout.gap)
        <= layout.panel_width - 50 + 0.001
    )
    assert 0 < layout.cell_width <= 39
    if inspector and not layout.inspector_below:
        assert layout.panel_width + 280 + 16 <= available
    elif inspector:
        assert layout.panel_width + 280 + 16 > available


def test_small_grid_stays_compact_and_actions_share_heading_row():
    admin = make_admin()
    editor = HallEditor(admin, 7)
    editor.show()
    editor.resize(SimpleNamespace(width=1400))
    assert editor.grid_panel.width == 380
    assert editor.header.controls == [editor.header_title, editor.header_actions]
    assert [button.content for button in editor.header_actions.controls] == [
        "Предпросмотр",
        "Сохранить зал",
    ]
