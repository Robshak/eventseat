from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable, Iterator


class AppError(Exception):
    pass


class PriceChanged(AppError):
    pass


EVENT_CATEGORIES = ("кино", "концерт", "спектакль", "лекция", "другое")
SEAT_CATEGORIES = ("эконом", "стандарт", "VIP")
TARIFFS = ("standard", "concession")


def money(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100_000_000:
        raise AppError("Цена должна быть целым числом копеек от 0 до 1 000 000 ₽.")
    return value


def required_text(value: str, label: str, maximum: int = 200) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AppError(f"Заполните поле «{label}».")
    value = value.strip()
    if len(value) > maximum:
        raise AppError(f"Поле «{label}» должно содержать не более {maximum} символов.")
    return value


@dataclass(frozen=True)
class User:
    id: int
    login: str
    name: str
    role: str = "user"

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def require_admin(self) -> None:
        if not self.is_admin:
            raise AppError("Это действие доступно только администратору.")

    def require_owner(self, user_id: int, allow_admin: bool = False) -> None:
        if self.id != user_id and not (allow_admin and self.is_admin):
            raise AppError("Нет доступа к данным другого пользователя.")


@dataclass(frozen=True)
class Event:
    title: str
    description: str
    category: str
    duration: int
    published: bool = False

    def __post_init__(self) -> None:
        required_text(self.title, "Название", 160)
        if self.category not in EVENT_CATEGORIES:
            raise AppError("Выберите категорию мероприятия.")
        if (
            isinstance(self.duration, bool)
            or not isinstance(self.duration, int)
            or not 1 <= self.duration <= 1440
        ):
            raise AppError("Продолжительность должна быть от 1 до 1440 минут.")
        if not isinstance(self.description, str) or len(self.description) > 10_000:
            raise AppError("Описание должно содержать не более 10 000 символов.")


@dataclass(frozen=True)
class EventSession:
    start: datetime
    duration: int
    status: str = "available"

    @property
    def end(self) -> datetime:
        return self.start + timedelta(minutes=self.duration)

    def overlaps(self, other: EventSession) -> bool:
        return self.start < other.end and other.start < self.end

    def require_bookable(self, now: datetime) -> None:
        if self.status == "cancelled":
            raise AppError("Сеанс отменён. Выберите другой сеанс.")
        if self.start <= now:
            raise AppError("Сеанс уже начался. Бронирование и отмена недоступны.")

    def require_reschedule(self, active_tickets: int, changed: bool) -> None:
        if changed and active_tickets:
            raise AppError(
                "У сеанса есть активные бронирования. Отмените его и создайте новый для изменения зала или времени."
            )


@dataclass(frozen=True)
class Seat:
    row: int
    number: int
    category: str = "стандарт"
    enabled: bool = True

    def __post_init__(self) -> None:
        if any(
            isinstance(v, bool) or not isinstance(v, int) or v < 1 for v in (self.row, self.number)
        ):
            raise AppError("Номер ряда и места должен быть положительным целым числом.")
        if self.category not in SEAT_CATEGORIES:
            raise AppError("Неизвестная категория места.")

    def price(self, category_prices: dict[str, int]) -> int:
        return money(category_prices[self.category])

    def __str__(self) -> str:
        return f"ряд {self.row}, место {self.number}"


@dataclass(frozen=True)
class Hall:
    name: str
    rows: int
    columns: int
    stage: str
    category_prices: dict[str, int]
    seats: tuple[Seat, ...]

    def __post_init__(self) -> None:
        required_text(self.name, "Название зала", 100)
        required_text(self.stage, "Обозначение сцены", 100)
        if any(
            isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 50
            for v in (self.rows, self.columns)
        ):
            raise AppError("Укажите от 1 до 50 рядов и мест в ряду.")
        if set(self.category_prices) != set(SEAT_CATEGORIES):
            raise AppError("Укажите цены всех трёх категорий мест.")
        for price in self.category_prices.values():
            money(price)
        if len(self.seats) != self.rows * self.columns:
            raise AppError("Схема должна содержать все ячейки сетки, включая проходы.")
        positions = {(seat.row, seat.number) for seat in self.seats}
        if len(positions) != len(self.seats):
            raise AppError("В схеме повторяются номера мест в одном ряду.")
        if len({s.row for s in self.seats}) != self.rows:
            raise AppError("Число рядов в схеме не соответствует настройке зала.")
        for row in {s.row for s in self.seats}:
            if sum(s.row == row for s in self.seats) != self.columns:
                raise AppError("Каждый ряд должен содержать заданное число ячеек.")
        if not any(seat.enabled for seat in self.seats):
            raise AppError("Добавьте хотя бы одно доступное место.")

    @property
    def capacity(self) -> int:
        return sum(seat.enabled for seat in self.seats)

    def __len__(self) -> int:
        return self.capacity


@dataclass(frozen=True)
class Ticket(ABC):
    seat: Seat
    base_price: int

    def __post_init__(self) -> None:
        money(self.base_price)

    @property
    @abstractmethod
    def tariff(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def price(self) -> int:
        raise NotImplementedError

    def __str__(self) -> str:
        return f"{self.seat}, {self.price / 100:.2f} ₽"


@dataclass(frozen=True)
class StandardTicket(Ticket):
    @property
    def tariff(self) -> str:
        return "standard"

    @property
    def price(self) -> int:
        return self.base_price


@dataclass(frozen=True)
class ConcessionTicket(Ticket):
    DISCOUNT_PERCENT = 20

    @property
    def tariff(self) -> str:
        return "concession"

    @property
    def price(self) -> int:
        return int(
            (Decimal(self.base_price) * Decimal("0.8")).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )


def make_ticket(seat: Seat, base_price: int, tariff: str) -> Ticket:
    ticket_types = {"standard": StandardTicket, "concession": ConcessionTicket}
    if tariff not in ticket_types:
        raise AppError("Выберите обычный или льготный тариф.")
    return ticket_types[tariff](seat, base_price)


def calculate_ticket_price(price: int, tariff: str = "standard") -> int:
    return make_ticket(Seat(1, 1), price, tariff).price


class Cart:
    def __init__(self, tickets: Iterable[Ticket] = ()) -> None:
        self._tickets = list(tickets)

    @property
    def total(self) -> int:
        return sum(ticket.price for ticket in self._tickets)

    def add(self, ticket: Ticket) -> None:
        self._tickets.append(ticket)

    def __iter__(self) -> Iterator[Ticket]:
        return iter(self._tickets)

    def __len__(self) -> int:
        return len(self._tickets)


@dataclass
class Booking:
    number: str
    owner: User
    session: EventSession
    _tickets: tuple[Ticket, ...] = field(repr=False)
    _cancel_reason: str = ""

    @property
    def total(self) -> int:
        return sum(ticket.price for ticket in self._tickets)

    @property
    def cancel_reason(self) -> str:
        return self._cancel_reason

    def status_at(self, now: datetime) -> str:
        if self._cancel_reason:
            return "cancelled"
        return "completed" if self.session.start <= now else "active"

    def cancel(self, actor: User, reason: str, now: datetime) -> bool:
        actor.require_owner(self.owner.id, allow_admin=True)
        if self._cancel_reason:
            return False
        self.session.require_bookable(now)
        self._cancel_reason = required_text(reason, "Причина отмены", 1000)
        return True

    def __iter__(self) -> Iterator[Ticket]:
        return iter(self._tickets)

    def __len__(self) -> int:
        return len(self._tickets)
