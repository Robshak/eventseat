"""Selection and atomic property updates for the hall editor's in-memory draft."""

from __future__ import annotations

from dataclasses import dataclass, field

from eventseat.domain import AppError


@dataclass
class SeatSelection:
    """Indexes stay stable when a seat number changes; ranges use visible coordinates."""

    selected: set[int] = field(default_factory=set)
    anchor: int | None = None

    def click(self, seats: list[dict], index: int, *, shift=False, ctrl=False):
        if not 0 <= index < len(seats):
            raise AppError("Выбранное место больше не существует в схеме.")
        if shift and self.anchor is not None and 0 <= self.anchor < len(seats):
            first, last = seats[self.anchor], seats[index]
            low_row, high_row = sorted((first["row"], last["row"]))
            low_number, high_number = sorted((first["number"], last["number"]))
            rectangle = {
                i
                for i, seat in enumerate(seats)
                if low_row <= seat["row"] <= high_row
                and low_number <= seat["number"] <= high_number
            }
            self.selected = self.selected | rectangle if ctrl else rectangle
        else:
            if ctrl:
                self.selected.symmetric_difference_update({index})
            else:
                self.selected = {index}
            self.anchor = index

    def clear(self):
        self.selected.clear()
        self.anchor = None


def apply_properties(
    seats: list[dict],
    selected: set[int],
    *,
    category: str | None = None,
    enabled: bool | None = None,
    price_mode: str = "unchanged",
    price: int | None = None,
    number: int | None = None,
):
    """Validate the entire change before mutating any selected seat."""
    if not selected or any(index < 0 or index >= len(seats) for index in selected):
        raise AppError("Сначала выделите кресло или группу кресел.")
    if category is not None and category not in {"эконом", "стандарт", "VIP"}:
        raise AppError("Выберите категорию кресла.")
    if price_mode not in {"unchanged", "category", "custom"}:
        raise AppError("Выберите способ расчёта цены.")
    if price_mode == "custom" and (price is None or price < 0):
        raise AppError("Укажите неотрицательную индивидуальную цену.")
    if number is not None:
        if len(selected) != 1:
            raise AppError("Номер можно изменить только у одного выделенного кресла.")
        if not 1 <= number <= 999:
            raise AppError("Номер места должен быть от 1 до 999.")
        index = next(iter(selected))
        if any(
            i != index and seat["row"] == seats[index]["row"] and seat["number"] == number
            for i, seat in enumerate(seats)
        ):
            raise AppError("Такой номер уже есть в этом ряду.")
    for index in selected:
        seat = seats[index]
        if category is not None:
            seat["category"] = category
        if enabled is not None:
            seat["enabled"] = enabled
        if price_mode != "unchanged":
            seat["price_override"] = price if price_mode == "custom" else None
        if number is not None:
            seat["number"] = number
