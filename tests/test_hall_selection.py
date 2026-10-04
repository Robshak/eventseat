from copy import deepcopy

import pytest

from eventseat.domain import AppError
from eventseat.hall_selection import SeatSelection, apply_properties


@pytest.fixture
def layout():
    return [
        {
            "row": row,
            "number": number,
            "category": "стандарт",
            "enabled": True,
        }
        for row in (3, 4, 5)
        for number in (5, 6, 7, 8)
    ]


def test_shift_rectangle_uses_row_and_seat_numbers_in_both_directions(layout):
    selection = SeatSelection()
    selection.click(layout, 1)
    selection.click(layout, 10, shift=True)
    assert selection.selected == {1, 2, 5, 6, 9, 10}
    assert selection.anchor == 1
    selection.click(layout, 10)
    selection.click(layout, 1, shift=True)
    assert selection.selected == {1, 2, 5, 6, 9, 10}


def test_ctrl_toggles_and_ctrl_shift_adds_rectangle(layout):
    selection = SeatSelection()
    selection.click(layout, 11)
    selection.click(layout, 0, ctrl=True)
    assert selection.selected == {0, 11}
    selection.click(layout, 5, ctrl=True, shift=True)
    assert selection.selected == {0, 1, 4, 5, 11}
    selection.click(layout, 11, ctrl=True)
    assert selection.selected == {0, 1, 4, 5}
    selection.click(layout, 2)
    assert selection.selected == {2}


def test_shift_without_anchor_and_clear(layout):
    selection = SeatSelection()
    selection.click(layout, 3, shift=True)
    assert selection.selected == {3}
    selection.clear()
    assert not selection.selected and selection.anchor is None


def test_rectangle_selects_aisles_and_handles_custom_sparse_numbering(layout):
    layout[1]["enabled"] = False
    layout[2]["number"] = 40
    selection = SeatSelection()
    selection.click(layout, 0)
    selection.click(layout, 6, shift=True)
    assert selection.selected == {0, 1, 4, 5, 6}


def test_apply_group_retains_unselected_seats_and_unchanged_values(layout):
    original = deepcopy(layout)
    selected = {1, 2, 5, 6}
    apply_properties(layout, selected, category="VIP", enabled=False)
    for index, seat in enumerate(layout):
        if index in selected:
            assert seat["category"] == "VIP"
            assert seat["enabled"] is False
            assert seat["number"] == original[index]["number"]
        else:
            assert seat == original[index]
    apply_properties(layout, selected, enabled=True)
    assert all(layout[index]["enabled"] for index in selected)
    assert all(layout[index]["category"] == "VIP" for index in selected)


@pytest.mark.parametrize(
    "properties",
    [
        {"category": "неизвестная"},
        {"number": 33},
    ],
)
def test_invalid_group_change_is_atomic(layout, properties):
    original = deepcopy(layout)
    with pytest.raises(AppError):
        apply_properties(layout, {0, 1}, enabled=False, **properties)
    assert layout == original


def test_rename_validation_preserves_draft_and_selected_index(layout):
    original = deepcopy(layout)
    with pytest.raises(AppError):
        apply_properties(layout, {0}, number=6, category="VIP")
    assert layout == original
    selection = SeatSelection({0}, 0)
    apply_properties(layout, selection.selected, number=11)
    assert layout[0]["number"] == 11
    assert selection.selected == {0} and selection.anchor == 0


def test_invalid_index_never_mutates_any_seat(layout):
    original = deepcopy(layout)
    with pytest.raises(AppError):
        apply_properties(layout, {0, 99}, category="VIP")
    assert layout == original
