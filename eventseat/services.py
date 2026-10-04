from __future__ import annotations

import re
import uuid
from collections import defaultdict
from datetime import date as Date
from datetime import datetime, timedelta
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import func, select

from eventseat.domain import (
    AppError,
    Booking,
    Cart,
    Event,
    EventSession,
    Hall,
    PriceChanged,
    Seat,
    User,
    calculate_ticket_price,
    make_ticket,
    money,
    required_text,
)
from eventseat.storage import (
    BookingRecord,
    CartItemRecord,
    CheckoutRequestRecord,
    EventRecord,
    HallRecord,
    SeatRecord,
    SessionRecord,
    SessionSeatRecord,
    SettingRecord,
    Store,
    TicketRecord,
    UserRecord,
)


class Service:
    def __init__(self, db_path: Path, seed: bool = True):
        self.store = Store(db_path, seed_new=seed)
        self.engine = self.store.engine
        self._user_id: int | None = None
        self._hasher = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
        if seed:
            self._seed_demo()

    def close(self) -> None:
        self.logout()
        self.store.close()

    def new_context(self) -> Service:
        """Open an independent, unauthenticated session on this application's database."""
        return Service(self.store.path.resolve(), seed=False)

    @staticmethod
    def _now() -> datetime:
        return datetime.now()

    @staticmethod
    def _user_dict(record: UserRecord) -> dict:
        return {"id": record.id, "login": record.login, "name": record.name, "role": record.role}

    @staticmethod
    def _user_domain(record: UserRecord) -> User:
        return User(record.id, record.login, record.name, record.role)

    def _actor(self, db, admin: bool = False) -> User:
        record = db.get(UserRecord, self._user_id) if self._user_id is not None else None
        if record is None:
            raise AppError("Войдите в аккаунт, чтобы продолжить.")
        user = self._user_domain(record)
        if admin:
            user.require_admin()
        return user

    def _is_admin(self, db) -> bool:
        record = db.get(UserRecord, self._user_id) if self._user_id is not None else None
        return record is not None and record.role == "admin"

    def _booking_actor(self, db) -> User:
        actor = self._actor(db)
        if actor.role == "admin":
            raise AppError(
                "Администратор управляет мероприятиями. Для бронирования войдите в аккаунт посетителя."
            )
        return actor

    @property
    def current_user(self) -> dict | None:
        if self._user_id is None:
            return None
        with self.store.read() as db:
            record = db.get(UserRecord, self._user_id)
            return self._user_dict(record) if record else None

    def needs_setup(self) -> bool:
        with self.store.read() as db:
            return not db.scalar(select(func.count()).select_from(UserRecord))

    @staticmethod
    def _validate_credentials(login: str, name: str, password: str) -> tuple[str, str]:
        login = required_text(login, "Логин", 40).casefold()
        if not re.fullmatch(r"[a-z0-9_.-]{3,40}", login):
            raise AppError(
                "Логин: от 3 до 40 латинских букв, цифр, точек, дефисов или подчёркиваний."
            )
        name = required_text(name, "Имя", 80)
        Service._validate_password(password)
        return login, name

    @staticmethod
    def _validate_password(password: str) -> None:
        if not isinstance(password, str) or not 8 <= len(password) <= 128:
            raise AppError("Пароль должен содержать от 8 до 128 символов.")

    def setup_admin(self, login: str, name: str, password: str) -> dict:
        login, name = self._validate_credentials(login, name, password)
        password_hash = self._hasher.hash(password)
        with self.store.write() as db:
            if db.scalar(select(func.count()).select_from(UserRecord)):
                raise AppError(
                    "Первоначальная настройка уже выполнена. Войдите или зарегистрируйтесь."
                )
            record = UserRecord(login=login, name=name, password_hash=password_hash, role="admin")
            db.add(record)
            db.flush()
            result = self._user_dict(record)
        self._user_id = result["id"]
        return result

    def register(self, login: str, name: str, password: str) -> dict:
        login, name = self._validate_credentials(login, name, password)
        password_hash = self._hasher.hash(password)
        with self.store.write() as db:
            if not db.scalar(select(func.count()).select_from(UserRecord)):
                raise AppError("Сначала создайте администратора при первоначальной настройке.")
            if db.scalar(select(UserRecord).where(UserRecord.login == login)):
                raise AppError("Этот логин уже занят. Выберите другой.")
            record = UserRecord(login=login, name=name, password_hash=password_hash, role="user")
            db.add(record)
            db.flush()
            return self._user_dict(record)

    def login(self, login: str, password: str) -> dict:
        login = str(login).strip().casefold()
        self._user_id = None
        with self.store.write() as db:
            record = db.scalar(select(UserRecord).where(UserRecord.login == login))
            try:
                if record is None or not isinstance(password, str):
                    raise AppError("Неверный логин или пароль.")
                self._hasher.verify(record.password_hash, password)
            except (VerificationError, InvalidHashError) as exc:
                raise AppError("Неверный логин или пароль.") from exc
            if self._hasher.check_needs_rehash(record.password_hash):
                record.password_hash = self._hasher.hash(password)
            result = self._user_dict(record)
        self._user_id = result["id"]
        return result

    def logout(self) -> None:
        self._user_id = None

    def update_profile(self, name: str, old_password: str = "", new_password: str = "") -> dict:
        name = required_text(name, "Имя", 80)
        with self.store.write() as db:
            actor = self._actor(db)
            record = db.get(UserRecord, actor.id)
            if new_password:
                self._validate_password(new_password)
                try:
                    self._hasher.verify(record.password_hash, old_password)
                except (VerificationError, InvalidHashError) as exc:
                    raise AppError("Текущий пароль указан неверно.") from exc
                record.password_hash = self._hasher.hash(new_password)
            record.name = name
            return self._user_dict(record)

    def _event_record(self, db, event_id: int, public: bool = True) -> EventRecord:
        record = db.get(EventRecord, event_id)
        if record is None or (public and not record.published and not self._is_admin(db)):
            raise AppError("Мероприятие не найдено или ещё не опубликовано.")
        return record

    def _session_record(self, db, session_id: int, public: bool = True) -> SessionRecord:
        record = db.get(SessionRecord, session_id)
        if record is None:
            raise AppError("Сеанс не найден.")
        self._event_record(db, record.event_id, public)
        return record

    @staticmethod
    def _session_domain(record: SessionRecord) -> EventSession:
        return EventSession(record.start, record.duration, record.status)

    def _event_dict(self, db, record: EventRecord, sessions: list | None = None) -> dict:
        if sessions is None:
            sessions = list(
                db.scalars(
                    select(SessionRecord)
                    .where(
                        SessionRecord.event_id == record.id,
                        SessionRecord.status == "available",
                        SessionRecord.start > self._now(),
                    )
                    .order_by(SessionRecord.start)
                )
            )
        prices = []
        for item in sessions:
            price = db.scalar(
                select(func.min(SessionSeatRecord.price)).where(
                    SessionSeatRecord.session_id == item.id, SessionSeatRecord.in_layout.is_(True)
                )
            )
            if price is not None:
                prices.append(price)
        return {
            "id": record.id,
            "title": record.title,
            "description": record.description,
            "category": record.category,
            "cover_path": record.cover_path,
            "duration": record.duration,
            "published": record.published,
            "next_start": min((item.start for item in sessions), default=None),
            "min_price": min(prices, default=0),
        }

    @staticmethod
    def _filter_date(value) -> Date | None:
        if value is None or value == "":
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, Date):
            return value
        if isinstance(value, str):
            try:
                return Date.fromisoformat(value.strip())
            except ValueError as exc:
                raise AppError("Укажите дату в формате ГГГГ-ММ-ДД.") from exc
        raise AppError("Укажите корректную дату.")

    def list_events(
        self,
        search: str = "",
        category: str = "",
        date=None,
        admin: bool = False,
        *,
        date_from=None,
        date_to=None,
    ) -> list[dict]:
        selected_date = self._filter_date(date)
        first, last = self._filter_date(date_from), self._filter_date(date_to)
        if selected_date is not None:
            if first is not None or last is not None:
                raise AppError("Укажите одну дату или интервал дат.")
            first = last = selected_date
        if first is not None and last is not None and first > last:
            raise AppError("Начало интервала не может быть позже его окончания.")
        filtered_by_date = first is not None or last is not None
        with self.store.read() as db:
            if admin:
                self._actor(db, admin=True)
            records = db.scalars(select(EventRecord).order_by(EventRecord.id.desc()))
            result = []
            for record in records:
                if not admin and not record.published:
                    continue
                if search.strip().casefold() not in record.title.casefold():
                    continue
                if category and record.category != category:
                    continue
                sessions = list(
                    db.scalars(
                        select(SessionRecord).where(
                            SessionRecord.event_id == record.id,
                            SessionRecord.status == "available",
                            SessionRecord.start > self._now(),
                        )
                    )
                )
                if filtered_by_date:
                    sessions = [
                        item
                        for item in sessions
                        if (first is None or item.start.date() >= first)
                        and (last is None or item.start.date() <= last)
                    ]
                if (not admin or filtered_by_date) and not sessions:
                    continue
                result.append(self._event_dict(db, record, sessions))
            return sorted(result, key=lambda item: item["next_start"] or datetime.max)

    def get_event(self, event_id: int) -> dict:
        with self.store.read() as db:
            return self._event_dict(db, self._event_record(db, event_id))

    def get_related_event(self, event_id: int) -> dict:
        """Read an event linked to the actor's cart or booking, including hidden events."""
        with self.store.read() as db:
            actor = self._actor(db)
            in_cart = db.scalar(
                select(CartItemRecord.id)
                .join(SessionRecord, CartItemRecord.session_id == SessionRecord.id)
                .where(CartItemRecord.user_id == actor.id, SessionRecord.event_id == event_id)
                .limit(1)
            )
            in_history = db.scalar(
                select(BookingRecord.id)
                .join(SessionRecord, BookingRecord.session_id == SessionRecord.id)
                .where(BookingRecord.user_id == actor.id, SessionRecord.event_id == event_id)
                .limit(1)
            )
            if actor.role != "admin" and in_cart is None and in_history is None:
                raise AppError("Мероприятие не связано с вашей корзиной или бронированиями.")
            record = self._event_record(db, event_id, public=False)
            return self._event_dict(db, record, [] if not record.published else None)

    def save_event(
        self, title, description, category, duration, published, cover_path="", event_id=None
    ) -> int:
        title = required_text(title, "Название", 160)
        Event(title, description, category, duration, bool(published))
        with self.store.write() as db:
            self._actor(db, admin=True)
            record = (
                self._event_record(db, event_id, public=False)
                if event_id is not None
                else EventRecord()
            )
            if event_id is not None and record.duration != duration:
                sessions = list(
                    db.scalars(
                        select(SessionRecord).where(
                            SessionRecord.event_id == event_id,
                            SessionRecord.status == "available",
                            SessionRecord.start > self._now(),
                        )
                    )
                )
                for item in sessions:
                    item.duration = duration
                db.flush()
                for item in sessions:
                    self._ensure_no_overlap(db, item.hall_id, self._session_domain(item), item.id)
            record.title = title
            record.description = description.strip()
            record.category = category
            record.duration = duration
            record.published = bool(published)
            record.cover_path = str(cover_path or "")
            db.add(record)
            db.flush()
            return record.id

    @staticmethod
    def _hall_record(db, hall_id: int) -> HallRecord:
        record = db.get(HallRecord, hall_id)
        if record is None:
            raise AppError("Зал не найден.")
        return record

    @staticmethod
    def _hall_seats(db, hall_id: int) -> list[SeatRecord]:
        return list(
            db.scalars(
                select(SeatRecord)
                .where(SeatRecord.hall_id == hall_id, SeatRecord.in_layout.is_(True))
                .order_by(SeatRecord.row, SeatRecord.number)
            )
        )

    def _hall_dict(self, db, record: HallRecord) -> dict:
        return {
            "id": record.id,
            "name": record.name,
            "rows": record.rows,
            "columns": record.columns,
            "stage": record.stage,
            "category_prices": dict(record.category_prices),
            "seats": [
                {
                    "id": s.id,
                    "row": s.row,
                    "number": s.number,
                    "category": s.category,
                    "enabled": s.enabled,
                }
                for s in self._hall_seats(db, record.id)
            ],
        }

    def list_halls(self) -> list[dict]:
        with self.store.read() as db:
            return [
                self._hall_dict(db, record)
                for record in db.scalars(select(HallRecord).order_by(HallRecord.name))
            ]

    def get_hall(self, hall_id: int) -> dict:
        with self.store.read() as db:
            return self._hall_dict(db, self._hall_record(db, hall_id))

    def save_hall(
        self, name, rows, columns, stage, category_prices, seats=None, hall_id=None
    ) -> int:
        name = required_text(name, "Название зала", 100)
        stage = required_text(stage, "Обозначение сцены", 100)
        if any(
            isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 50
            for v in (rows, columns)
        ):
            raise AppError("Укажите от 1 до 50 рядов и мест в ряду.")
        if seats is None:
            seats = [
                {"row": row, "number": number, "category": "стандарт"}
                for row in range(1, rows + 1)
                for number in range(1, columns + 1)
            ]
        try:
            if any(s.get("price_override") is not None for s in seats):
                raise AppError("Цены мест задаются только категориями зала.")
            domain_seats = tuple(
                Seat(
                    s["row"],
                    s["number"],
                    s.get("category", "стандарт"),
                    bool(s.get("enabled", True)),
                )
                for s in seats
            )
            Hall(name, rows, columns, stage, dict(category_prices), domain_seats)
        except (KeyError, TypeError) as exc:
            raise AppError("Проверьте схему зала и цены категорий.") from exc
        with self.store.write() as db:
            self._actor(db, admin=True)
            record = self._hall_record(db, hall_id) if hall_id is not None else HallRecord()
            if hall_id is not None:
                old_seats = self._hall_seats(db, hall_id)
                before = {(s.row, s.number, s.category, s.enabled) for s in old_seats}
                after = {(s.row, s.number, s.category, s.enabled) for s in domain_seats}
                structural = before != after or record.rows != rows or record.columns != columns
                active_count = db.scalar(
                    select(func.count())
                    .select_from(TicketRecord)
                    .join(SessionRecord, TicketRecord.session_id == SessionRecord.id)
                    .where(
                        SessionRecord.hall_id == hall_id,
                        TicketRecord.active.is_(True),
                        SessionRecord.start > self._now(),
                    )
                )
                if structural and active_count:
                    raise AppError(
                        "В зале есть сеансы с активными бронированиями. Создайте копию зала для новой схемы."
                    )
            record.name, record.rows, record.columns, record.stage = name, rows, columns, stage
            record.category_prices = dict(category_prices)
            db.add(record)
            db.flush()
            existing = {
                (s.row, s.number): s
                for s in db.scalars(select(SeatRecord).where(SeatRecord.hall_id == record.id))
            }
            for item in existing.values():
                item.in_layout = False
            for seat in domain_seats:
                item = existing.get((seat.row, seat.number))
                if item is None:
                    item = SeatRecord(hall_id=record.id, row=seat.row, number=seat.number)
                    db.add(item)
                item.category, item.price_override = seat.category, None
                item.enabled, item.in_layout = seat.enabled, True
            db.flush()
            return record.id

    def copy_hall(self, hall_id: int, name: str) -> int:
        with self.store.read() as db:
            self._actor(db, admin=True)
            data = self._hall_dict(db, self._hall_record(db, hall_id))
        return self.save_hall(
            name,
            data["rows"],
            data["columns"],
            data["stage"],
            data["category_prices"],
            data["seats"],
        )

    @staticmethod
    def _prices(prices: dict) -> dict:
        if not isinstance(prices, dict) or set(prices) != {"эконом", "стандарт", "VIP"}:
            raise AppError("Укажите цены всех трёх категорий мест.")
        return {category: money(price) for category, price in prices.items()}

    def _ensure_no_overlap(
        self, db, hall_id: int, candidate: EventSession, exclude_id=None
    ) -> None:
        records = db.scalars(
            select(SessionRecord).where(
                SessionRecord.hall_id == hall_id, SessionRecord.status == "available"
            )
        )
        for record in records:
            if record.id != exclude_id and candidate.overlaps(self._session_domain(record)):
                raise AppError(
                    f"Зал занят другим сеансом {record.start:%d.%m.%Y в %H:%M}. Измените время или зал."
                )

    @staticmethod
    def _snapshot_seats(db, session_id: int) -> list[SessionSeatRecord]:
        return list(
            db.scalars(
                select(SessionSeatRecord)
                .where(
                    SessionSeatRecord.session_id == session_id,
                    SessionSeatRecord.in_layout.is_(True),
                )
                .order_by(SessionSeatRecord.row, SessionSeatRecord.number)
            )
        )

    def _copy_session_seats(
        self, db, record: SessionRecord, hall: HallRecord, prices: dict
    ) -> None:
        hall_seats = self._hall_seats(db, hall.id)
        record.stage = hall.stage
        record.layout = [
            {"row": seat.row, "number": seat.number, "enabled": seat.enabled} for seat in hall_seats
        ]
        source = [seat for seat in hall_seats if seat.enabled]
        existing = {
            s.seat_id: s
            for s in db.scalars(
                select(SessionSeatRecord).where(SessionSeatRecord.session_id == record.id)
            )
        }
        for item in existing.values():
            item.in_layout = False
        for seat in source:
            item = existing.get(seat.id)
            if item is None:
                item = SessionSeatRecord(session_id=record.id, seat_id=seat.id)
                db.add(item)
            item.row, item.number, item.category = seat.row, seat.number, seat.category
            item.price_override = None
            item.price = Seat(seat.row, seat.number, seat.category).price(prices)
            item.closed, item.in_layout = False, True

    def save_session(self, event_id, hall_id, start, *, session_id=None) -> int:
        if not isinstance(start, datetime):
            raise AppError("Укажите корректные дату и время сеанса.")
        if start.tzinfo is not None:
            start = start.astimezone().replace(tzinfo=None)
        start = start.replace(second=0, microsecond=0)
        with self.store.write() as db:
            self._actor(db, admin=True)
            event = self._event_record(db, event_id, public=False)
            hall = self._hall_record(db, hall_id)
            candidate = EventSession(start, event.duration)
            candidate.require_bookable(self._now())
            record = (
                self._session_record(db, session_id, public=False)
                if session_id is not None
                else SessionRecord()
            )
            old_hall_id = record.hall_id
            if session_id is not None:
                self._session_domain(record).require_bookable(self._now())
                if record.event_id != event_id and db.scalar(
                    select(BookingRecord.id).where(BookingRecord.session_id == session_id).limit(1)
                ):
                    raise AppError(
                        "У сеанса есть история бронирований. Для другого мероприятия создайте новый сеанс."
                    )
                if record.event_id != event_id and db.scalar(
                    select(CartItemRecord.id)
                    .where(CartItemRecord.session_id == session_id)
                    .limit(1)
                ):
                    raise AppError(
                        "Сеанс добавлен в корзины. Для другого мероприятия создайте новый сеанс."
                    )
                active_tickets = db.scalar(
                    select(func.count())
                    .select_from(TicketRecord)
                    .where(TicketRecord.session_id == session_id, TicketRecord.active.is_(True))
                )
                candidate.require_reschedule(
                    active_tickets,
                    old_hall_id != hall_id or record.start != start or record.event_id != event_id,
                )
            self._ensure_no_overlap(db, hall_id, candidate, session_id)
            record.event_id, record.hall_id = event_id, hall_id
            record.start, record.duration = start, event.duration
            record.status, record.cancel_reason = "available", ""
            new_snapshot = session_id is None or old_hall_id != hall_id
            prices = self._prices(hall.category_prices if new_snapshot else record.category_prices)
            record.category_prices = prices
            db.add(record)
            db.flush()
            if new_snapshot:
                self._copy_session_seats(db, record, hall, prices)
            return record.id

    def _session_dict(self, db, record: SessionRecord) -> dict:
        event = db.get(EventRecord, record.event_id)
        hall = db.get(HallRecord, record.hall_id)
        seats = self._snapshot_seats(db, record.id)
        occupied = set(
            db.scalars(
                select(TicketRecord.seat_id).where(
                    TicketRecord.session_id == record.id, TicketRecord.active.is_(True)
                )
            )
        )
        return {
            "id": record.id,
            "event_id": event.id,
            "title": event.title,
            "hall_id": hall.id,
            "hall_name": hall.name,
            "start": record.start,
            "duration": record.duration,
            "status": record.status,
            "cancel_reason": record.cancel_reason,
            "free_count": sum(not s.closed and s.seat_id not in occupied for s in seats),
            "total_count": len(seats),
            "min_price": min((s.price for s in seats), default=0),
            "category_prices": dict(record.category_prices),
            "stage": record.stage,
            "layout": [dict(cell) for cell in record.layout],
        }

    def _session_filters(
        self,
        query,
        *,
        session_id=None,
        event_id=None,
        hall_id=None,
        date_from=None,
        date_to=None,
        status="all",
    ):
        first, last = self._filter_date(date_from), self._filter_date(date_to)
        if first is not None and last is not None and first > last:
            raise AppError("Начало интервала не может быть позже его окончания.")
        if status not in {"all", "upcoming", "completed", "cancelled"}:
            raise AppError("Неизвестный статус сеанса.")
        if session_id is not None:
            query = query.where(SessionRecord.id == session_id)
        if event_id is not None:
            query = query.where(SessionRecord.event_id == event_id)
        if hall_id is not None:
            query = query.where(SessionRecord.hall_id == hall_id)
        if first is not None:
            query = query.where(func.date(SessionRecord.start) >= first.isoformat())
        if last is not None:
            query = query.where(func.date(SessionRecord.start) <= last.isoformat())
        if status == "cancelled":
            query = query.where(SessionRecord.status == "cancelled")
        elif status in {"upcoming", "completed"}:
            query = query.where(SessionRecord.status == "available")
            query = query.where(
                SessionRecord.start > self._now()
                if status == "upcoming"
                else SessionRecord.start <= self._now()
            )
        return query

    def list_sessions(
        self,
        event_id=None,
        admin: bool = False,
        *,
        hall_id=None,
        date_from=None,
        date_to=None,
        status="all",
    ) -> list[dict]:
        with self.store.read() as db:
            if admin:
                self._actor(db, admin=True)
            query = select(SessionRecord).join(EventRecord).order_by(SessionRecord.start)
            if event_id is not None:
                self._event_record(db, event_id)
            if hall_id is not None:
                self._hall_record(db, hall_id)
            query = self._session_filters(
                query,
                event_id=event_id,
                hall_id=hall_id,
                date_from=date_from,
                date_to=date_to,
                status=status,
            )
            if not admin:
                query = query.where(
                    EventRecord.published.is_(True),
                    SessionRecord.status == "available",
                    SessionRecord.start > self._now(),
                )
            return [self._session_dict(db, item) for item in db.scalars(query)]

    def get_session(self, session_id: int) -> dict:
        with self.store.read() as db:
            return self._session_dict(db, self._session_record(db, session_id))

    def get_related_session(self, session_id: int) -> dict:
        """Read a session linked to the actor's own cart or booking, including history."""
        with self.store.read() as db:
            actor = self._actor(db)
            in_cart = db.scalar(
                select(CartItemRecord.id)
                .where(
                    CartItemRecord.user_id == actor.id,
                    CartItemRecord.session_id == session_id,
                )
                .limit(1)
            )
            in_history = db.scalar(
                select(BookingRecord.id)
                .where(
                    BookingRecord.user_id == actor.id,
                    BookingRecord.session_id == session_id,
                )
                .limit(1)
            )
            if actor.role != "admin" and in_cart is None and in_history is None:
                raise AppError("Сеанс не связан с вашей корзиной или бронированиями.")
            record = self._session_record(db, session_id, public=False)
            result = self._session_dict(db, record)
            event = self._event_record(db, record.event_id, public=False)
            result["published"] = event.published
            result["bookable"] = (
                actor.role != "admin"
                and event.published
                and record.status == "available"
                and record.start > self._now()
            )
            return result

    def cancel_session(self, session_id: int, reason: str) -> None:
        reason = required_text(reason, "Причина отмены", 1000)
        with self.store.write() as db:
            self._actor(db, admin=True)
            record = self._session_record(db, session_id, public=False)
            if record.status == "cancelled":
                return
            self._session_domain(record).require_bookable(self._now())
            record.status, record.cancel_reason = "cancelled", reason
            for booking in db.scalars(
                select(BookingRecord).where(
                    BookingRecord.session_id == session_id, BookingRecord.status == "active"
                )
            ):
                self._cancel_booking_record(db, booking, f"Сеанс отменён: {reason}")

    def seat_map(self, session_id: int) -> list[dict]:
        with self.store.read() as db:
            self._session_record(db, session_id)
            occupied = set(
                db.scalars(
                    select(TicketRecord.seat_id).where(
                        TicketRecord.session_id == session_id, TicketRecord.active.is_(True)
                    )
                )
            )
            return [
                {
                    "id": seat.seat_id,
                    "row": seat.row,
                    "number": seat.number,
                    "category": seat.category,
                    "price": seat.price,
                    "status": "booked"
                    if seat.seat_id in occupied
                    else "closed"
                    if seat.closed
                    else "free",
                }
                for seat in self._snapshot_seats(db, session_id)
            ]

    def set_seat_closed(self, session_id: int, seat_id: int, closed: bool) -> None:
        with self.store.write() as db:
            self._actor(db, admin=True)
            record = self._session_record(db, session_id, public=False)
            self._session_domain(record).require_bookable(self._now())
            seat = db.scalar(
                select(SessionSeatRecord).where(
                    SessionSeatRecord.session_id == session_id,
                    SessionSeatRecord.seat_id == seat_id,
                    SessionSeatRecord.in_layout.is_(True),
                )
            )
            if seat is None:
                raise AppError("Место не найдено на этом сеансе.")
            occupied = db.scalar(
                select(TicketRecord.id).where(
                    TicketRecord.session_id == session_id,
                    TicketRecord.seat_id == seat_id,
                    TicketRecord.active.is_(True),
                )
            )
            if closed and occupied:
                raise AppError("Место уже забронировано. Сначала отмените бронирование.")
            seat.closed = bool(closed)

    def _cart_context(self, db, item: CartItemRecord) -> tuple:
        record = db.get(SessionRecord, item.session_id)
        event = db.get(EventRecord, record.event_id)
        hall = db.get(HallRecord, record.hall_id)
        seat = db.scalar(
            select(SessionSeatRecord).where(
                SessionSeatRecord.session_id == item.session_id,
                SessionSeatRecord.seat_id == item.seat_id,
            )
        )
        occupied = db.scalar(
            select(TicketRecord.id).where(
                TicketRecord.session_id == item.session_id,
                TicketRecord.seat_id == item.seat_id,
                TicketRecord.active.is_(True),
            )
        )
        available = (
            event.published
            and record.status == "available"
            and record.start > self._now()
            and seat is not None
            and seat.in_layout
            and not seat.closed
            and not occupied
        )
        return record, event, hall, seat, bool(available)

    def add_to_cart(self, session_id: int, seat_ids, tariff: str = "standard") -> None:
        calculate_ticket_price(0, tariff)
        try:
            seat_ids = list(dict.fromkeys(seat_ids))
        except TypeError as exc:
            raise AppError("Выберите места на схеме.") from exc
        if not seat_ids:
            raise AppError("Выберите хотя бы одно место.")
        with self.store.write() as db:
            actor = self._booking_actor(db)
            record = self._session_record(db, session_id)
            if not db.get(EventRecord, record.event_id).published:
                raise AppError("Бронирование доступно только для опубликованных мероприятий.")
            self._session_domain(record).require_bookable(self._now())
            for seat_id in seat_ids:
                seat = db.scalar(
                    select(SessionSeatRecord).where(
                        SessionSeatRecord.session_id == session_id,
                        SessionSeatRecord.seat_id == seat_id,
                        SessionSeatRecord.in_layout.is_(True),
                    )
                )
                if seat is None:
                    raise AppError("Одно из мест отсутствует на этом сеансе. Обновите схему.")
                occupied = db.scalar(
                    select(TicketRecord.id).where(
                        TicketRecord.session_id == session_id,
                        TicketRecord.seat_id == seat_id,
                        TicketRecord.active.is_(True),
                    )
                )
                if seat.closed or occupied:
                    raise AppError(
                        f"Недоступно: ряд {seat.row}, место {seat.number}. Выберите другое место."
                    )
                item = db.scalar(
                    select(CartItemRecord).where(
                        CartItemRecord.user_id == actor.id,
                        CartItemRecord.session_id == session_id,
                        CartItemRecord.seat_id == seat_id,
                    )
                )
                if item is None:
                    item = CartItemRecord(user_id=actor.id, session_id=session_id, seat_id=seat_id)
                    db.add(item)
                item.tariff, item.price = tariff, calculate_ticket_price(seat.price, tariff)

    def get_cart(self) -> list[dict]:
        with self.store.read() as db:
            actor = self._actor(db)
            result = []
            for item in db.scalars(
                select(CartItemRecord)
                .where(CartItemRecord.user_id == actor.id)
                .order_by(CartItemRecord.session_id, CartItemRecord.id)
            ):
                session, event, hall, seat, available = self._cart_context(db, item)
                result.append(
                    {
                        "id": item.id,
                        "event_id": event.id,
                        "session_id": session.id,
                        "seat_id": item.seat_id,
                        "title": event.title,
                        "hall_name": hall.name,
                        "start": session.start,
                        "row": seat.row,
                        "number": seat.number,
                        "category": seat.category,
                        "tariff": item.tariff,
                        "price": item.price,
                        "available": available,
                    }
                )
            return result

    def remove_cart_item(self, item_id: int) -> None:
        with self.store.write() as db:
            actor = self._actor(db)
            item = db.get(CartItemRecord, item_id)
            if item is None:
                return
            actor.require_owner(item.user_id)
            db.delete(item)

    def checkout(self, request_key: str) -> list[dict]:
        request_key = required_text(request_key, "Ключ подтверждения", 160)
        changed = False
        result = []
        with self.store.write() as db:
            actor = self._booking_actor(db)
            previous = db.scalar(
                select(CheckoutRequestRecord).where(
                    CheckoutRequestRecord.user_id == actor.id,
                    CheckoutRequestRecord.request_key == request_key,
                )
            )
            if previous:
                if not previous.booking_ids:
                    raise PriceChanged(
                        "Цены этого подтверждения устарели. Проверьте новую сумму и подтвердите корзину заново."
                    )
                return [
                    self._booking_dict(db, db.get(BookingRecord, booking_id))
                    for booking_id in previous.booking_ids
                ]
            items = list(
                db.scalars(
                    select(CartItemRecord)
                    .where(CartItemRecord.user_id == actor.id)
                    .order_by(CartItemRecord.id)
                )
            )
            if not items:
                raise AppError("Корзина пуста. Добавьте места перед подтверждением.")
            contexts, conflicts = {}, []
            for item in items:
                context = self._cart_context(db, item)
                contexts[item.id] = context
                session, event, _, seat, available = context
                if not available:
                    conflicts.append(
                        f"{event.title}, {session.start:%d.%m %H:%M}, ряд {seat.row}, место {seat.number}"
                    )
            if conflicts:
                raise AppError(
                    "Не удалось оформить корзину. Недоступны: "
                    + "; ".join(conflicts)
                    + ". Удалите эти позиции или выберите другие места."
                )
            for item in items:
                seat = contexts[item.id][3]
                current_price = calculate_ticket_price(seat.price, item.tariff)
                if current_price != item.price:
                    item.price = current_price
                    changed = True
            if changed:
                db.add(
                    CheckoutRequestRecord(user_id=actor.id, request_key=request_key, booking_ids=[])
                )
            else:
                groups = defaultdict(list)
                for item in items:
                    groups[item.session_id].append(item)
                booking_ids = []
                for session_id, group in groups.items():
                    session, event, hall, _, _ = contexts[group[0].id]
                    cart = Cart(
                        make_ticket(
                            Seat(
                                contexts[item.id][3].row,
                                contexts[item.id][3].number,
                                contexts[item.id][3].category,
                            ),
                            contexts[item.id][3].price,
                            item.tariff,
                        )
                        for item in group
                    )
                    record = BookingRecord(
                        number="ES-" + uuid.uuid4().hex[:16].upper(),
                        user_id=actor.id,
                        session_id=session_id,
                        title=event.title,
                        hall_name=hall.name,
                        hall_id=hall.id,
                        stage=session.stage,
                        layout=self._booking_layout(db, session),
                        layout_is_partial=False,
                        start=session.start,
                        duration=session.duration,
                        total=cart.total,
                    )
                    db.add(record)
                    db.flush()
                    booking_ids.append(record.id)
                    for item, ticket in zip(group, cart, strict=True):
                        db.add(
                            TicketRecord(
                                booking_id=record.id,
                                session_id=session_id,
                                seat_id=item.seat_id,
                                row=ticket.seat.row,
                                number=ticket.seat.number,
                                category=ticket.seat.category,
                                tariff=ticket.tariff,
                                base_price=ticket.base_price,
                                price=ticket.price,
                                active=True,
                            )
                        )
                    db.flush()
                    result.append(self._booking_dict(db, record))
                db.add(
                    CheckoutRequestRecord(
                        user_id=actor.id, request_key=request_key, booking_ids=booking_ids
                    )
                )
                for item in items:
                    db.delete(item)
        if changed:
            raise PriceChanged(
                "Цены изменились. Корзина пересчитана. Проверьте новую сумму и подтвердите бронирование ещё раз."
            )
        return result

    @staticmethod
    def _booking_tickets(db, booking_id: int) -> list[TicketRecord]:
        return list(
            db.scalars(
                select(TicketRecord)
                .where(TicketRecord.booking_id == booking_id)
                .order_by(TicketRecord.row, TicketRecord.number)
            )
        )

    def _booking_layout(self, db, session: SessionRecord) -> list[dict]:
        positions = {(seat.row, seat.number): seat for seat in self._snapshot_seats(db, session.id)}
        layout = [dict(cell) for cell in session.layout]
        for cell in layout:
            seat = positions.get((cell["row"], cell["number"]))
            if seat is not None:
                cell.update(seat_id=seat.seat_id, category=seat.category)
        return layout

    def _booking_domain(self, db, record: BookingRecord) -> Booking:
        owner = self._user_domain(db.get(UserRecord, record.user_id))
        tickets = tuple(
            make_ticket(Seat(t.row, t.number, t.category), t.base_price, t.tariff)
            for t in self._booking_tickets(db, record.id)
        )
        return Booking(
            record.number,
            owner,
            EventSession(record.start, record.duration),
            tickets,
            record.cancel_reason if record.status == "cancelled" else "",
        )

    def _booking_dict(self, db, record: BookingRecord) -> dict:
        domain = self._booking_domain(db, record)
        return {
            "id": record.id,
            "number": record.number,
            "user_id": record.user_id,
            "user_name": domain.owner.name,
            "user_login": domain.owner.login,
            "session_id": record.session_id,
            "event_id": db.get(SessionRecord, record.session_id).event_id,
            "title": record.title,
            "start": record.start,
            "hall_name": record.hall_name,
            "status": domain.status_at(self._now()),
            "cancel_reason": record.cancel_reason,
            "total": record.total,
            "created_at": record.created_at,
            "tickets": [
                {
                    "row": t.row,
                    "number": t.number,
                    "category": t.category,
                    "tariff": t.tariff,
                    "price": t.price,
                    "seat_id": t.seat_id,
                }
                for t in self._booking_tickets(db, record.id)
            ],
        }

    def _filter_bookings(self, db, records, *, search="", status="all"):
        if status not in {"valid", "all", "active", "completed", "cancelled"}:
            raise AppError("Неизвестный статус бронирования.")
        owners = {
            owner.id: owner
            for owner in db.scalars(
                select(UserRecord).where(UserRecord.id.in_({r.user_id for r in records}))
            )
        }
        search = search.strip().casefold()
        now = self._now()
        result = []
        for record in records:
            actual = (
                "cancelled"
                if record.status == "cancelled"
                else "completed"
                if record.start <= now
                else "active"
            )
            if not (
                status == "all" or status == actual or status == "valid" and actual != "cancelled"
            ):
                continue
            owner = owners[record.user_id]
            if search not in f"{record.number} {owner.name} {owner.login}".casefold():
                continue
            result.append(record)
        return result

    def list_bookings(
        self,
        search: str = "",
        admin: bool = False,
        *,
        event_id=None,
        hall_id=None,
        session_id=None,
        date_from=None,
        date_to=None,
        session_status="all",
        booking_status="all",
    ) -> list[dict]:
        with self.store.read() as db:
            actor = self._actor(db, admin=admin)
            query = (
                select(BookingRecord)
                .join(SessionRecord)
                .order_by(BookingRecord.created_at.desc(), BookingRecord.id.desc())
            )
            query = self._session_filters(
                query,
                event_id=event_id,
                hall_id=hall_id,
                session_id=session_id,
                date_from=date_from,
                date_to=date_to,
                status=session_status,
            )
            if not admin:
                query = query.where(BookingRecord.user_id == actor.id)
            records = self._filter_bookings(
                db,
                list(db.scalars(query)),
                search=search,
                status=booking_status,
            )
            return [self._booking_dict(db, record) for record in records]

    def get_booking(self, booking_id: int) -> dict:
        with self.store.read() as db:
            actor = self._actor(db)
            record = db.get(BookingRecord, booking_id)
            if record is None:
                raise AppError("Бронирование не найдено.")
            actor.require_owner(record.user_id, allow_admin=True)
            return self._booking_dict(db, record)

    def get_booking_seat_map(self, booking_id: int) -> dict:
        """Return the immutable hall layout and purchased seats, visible only to its owner/admin."""
        with self.store.read() as db:
            actor = self._actor(db)
            record = db.get(BookingRecord, booking_id)
            if record is None:
                raise AppError("Бронирование не найдено.")
            actor.require_owner(record.user_id, allow_admin=True)
            booking = self._booking_dict(db, record)
            selected = {ticket["seat_id"] for ticket in booking["tickets"]}
            layout = [
                {**cell, "selected": cell.get("seat_id") in selected} for cell in record.layout
            ]
            session = self._session_record(db, record.session_id, public=False)
            event = self._event_record(db, session.event_id, public=False)
            return {
                "booking": booking,
                "session": self._session_dict(db, session),
                "event_id": event.id,
                "session_id": session.id,
                "hall_id": record.hall_id,
                "hall_name": record.hall_name,
                "stage": record.stage,
                "layout": layout,
                "selected_seat_ids": sorted(selected),
                "layout_is_partial": record.layout_is_partial,
                "published": event.published,
                "session_changed": (
                    record.hall_id != session.hall_id or record.start != session.start
                ),
            }

    @staticmethod
    def _cancel_booking_record(db, record: BookingRecord, reason: str) -> None:
        record.status, record.cancel_reason = "cancelled", reason
        for ticket in db.scalars(select(TicketRecord).where(TicketRecord.booking_id == record.id)):
            ticket.active = False

    def cancel_booking(self, booking_id: int, reason: str = "Отменено пользователем") -> None:
        with self.store.write() as db:
            actor = self._actor(db)
            record = db.get(BookingRecord, booking_id)
            if record is None:
                raise AppError("Бронирование не найдено.")
            domain = self._booking_domain(db, record)
            if domain.cancel(actor, reason, self._now()):
                self._cancel_booking_record(db, record, domain.cancel_reason)

    def statistics(
        self,
        session_id=None,
        *,
        event_id=None,
        hall_id=None,
        date_from=None,
        date_to=None,
        session_status="upcoming",
        booking_status="valid",
        search="",
    ) -> dict:
        """Aggregate selected sessions; dates refer to session starts, amounts are kopecks.

        The booking filter affects booking_count/ticket_count/amount. Occupancy always
        counts non-cancelled tickets, so refunded historical tickets cannot fill a hall.
        """
        with self.store.read() as db:
            self._actor(db, admin=True)
            if booking_status not in {"valid", "all", "active", "completed", "cancelled"}:
                raise AppError("Неизвестный статус бронирования.")
            if session_id is not None:
                self._session_record(db, session_id, public=False)
            if event_id is not None:
                self._event_record(db, event_id, public=False)
            if hall_id is not None:
                self._hall_record(db, hall_id)
            session_query = self._session_filters(
                select(SessionRecord.id),
                session_id=session_id,
                event_id=event_id,
                hall_id=hall_id,
                date_from=date_from,
                date_to=date_to,
                status=session_status,
            )
            session_ids = list(db.scalars(session_query))
            total_seats = db.scalar(
                select(func.count())
                .select_from(SessionSeatRecord)
                .where(
                    SessionSeatRecord.session_id.in_(session_ids),
                    SessionSeatRecord.in_layout.is_(True),
                )
            )
            bookings = list(
                db.scalars(select(BookingRecord).where(BookingRecord.session_id.in_(session_ids)))
            )
            matching = {
                record.id
                for record in self._filter_bookings(
                    db,
                    bookings,
                    search=search,
                    status=booking_status,
                )
            }
            all_tickets = list(
                db.scalars(select(TicketRecord).where(TicketRecord.session_id.in_(session_ids)))
            )
            tickets = [ticket for ticket in all_tickets if ticket.active]
            filtered = [ticket for ticket in all_tickets if ticket.booking_id in matching]
            return {
                "active_tickets": len(tickets),
                "occupancy_percent": round(len(tickets) / total_seats * 100, 1)
                if total_seats
                else 0.0,
                "active_amount": sum(t.price for t in tickets),
                "total_seats": total_seats,
                "session_count": len(session_ids),
                "booking_count": len(matching),
                "ticket_count": len(filtered),
                "amount": sum(ticket.price for ticket in filtered),
                "cancelled_tickets": sum(not ticket.active for ticket in all_tickets),
            }

    def _seed_demo(self) -> None:
        with self.store.write() as db:
            pending = db.get(SettingRecord, "demo_pending")
            if pending is None:
                return
            if db.get(SettingRecord, "demo_seeded") or db.scalar(
                select(func.count()).select_from(EventRecord)
            ):
                db.delete(pending)
                return
            prices = {"эконом": 45000, "стандарт": 65000, "VIP": 95000}
            hall = HallRecord(
                name="Зал «Горизонт»",
                rows=6,
                columns=10,
                stage="ЭКРАН / СЦЕНА",
                category_prices=prices,
            )
            db.add(hall)
            db.flush()
            for row in range(1, 7):
                for number in range(1, 11):
                    category = (
                        "VIP"
                        if row in (3, 4) and 3 <= number <= 8
                        else "эконом"
                        if row == 1
                        else "стандарт"
                    )
                    db.add(
                        SeatRecord(
                            hall_id=hall.id,
                            row=row,
                            number=number,
                            category=category,
                            enabled=not (number == 5 and row in (5, 6)),
                            in_layout=True,
                        )
                    )
            chamber = HallRecord(
                name="Камерный зал", rows=4, columns=8, stage="СЦЕНА", category_prices=prices
            )
            db.add(chamber)
            db.flush()
            for row in range(1, 5):
                for number in range(1, 9):
                    db.add(
                        SeatRecord(
                            hall_id=chamber.id,
                            row=row,
                            number=number,
                            category="VIP" if row == 1 else "стандарт",
                            enabled=True,
                            in_layout=True,
                        )
                    )
            events = [
                (
                    "За пределами орбиты",
                    "Космическое путешествие о смелости, дружбе и поиске своего места во Вселенной. Атмосферная научная фантастика на большом экране.",
                    "кино",
                    120,
                    "cinema.png",
                    hall,
                ),
                (
                    "Музыка после заката",
                    "Живой вечер современной инструментальной музыки. Фортепиано, струнные и электроника встречаются на одной сцене.",
                    "концерт",
                    90,
                    "concert.png",
                    chamber,
                ),
                (
                    "Город будущего",
                    "Лекция и открытый разговор об архитектуре, технологиях и людях, которые меняют облик наших городов.",
                    "лекция",
                    75,
                    "lecture.png",
                    hall,
                ),
            ]
            tomorrow = (self._now() + timedelta(days=1)).replace(
                hour=19, minute=0, second=0, microsecond=0
            )
            for index, (
                title,
                description,
                category,
                duration,
                cover,
                selected_hall,
            ) in enumerate(events):
                event = EventRecord(
                    title=title,
                    description=description,
                    category=category,
                    duration=duration,
                    cover_path=cover,
                    published=True,
                )
                db.add(event)
                db.flush()
                for day in (index + 1, index + 4):
                    session = SessionRecord(
                        event_id=event.id,
                        hall_id=selected_hall.id,
                        start=tomorrow + timedelta(days=day),
                        duration=duration,
                        category_prices=dict(selected_hall.category_prices),
                        status="available",
                    )
                    db.add(session)
                    db.flush()
                    self._copy_session_seats(db, session, selected_hall, session.category_prices)
            db.add(SettingRecord(key="demo_seeded", value="1"))
            db.delete(pending)
