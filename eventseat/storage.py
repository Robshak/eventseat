from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    text,
)
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from eventseat.domain import AppError

SCHEMA_VERSION = 4


class Base(DeclarativeBase):
    pass


class UserRecord(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('user', 'admin')"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    login: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    password_hash: Mapped[str] = mapped_column(String(512))
    role: Mapped[str] = mapped_column(String(10), default="user")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class EventRecord(Base):
    __tablename__ = "events"
    __table_args__ = (CheckConstraint("duration > 0"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(20))
    cover_path: Mapped[str] = mapped_column(Text, default="")
    duration: Mapped[int] = mapped_column(Integer)
    published: Mapped[bool] = mapped_column(Boolean, default=False)


class HallRecord(Base):
    __tablename__ = "halls"
    __table_args__ = (CheckConstraint("rows > 0 AND columns > 0"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    rows: Mapped[int] = mapped_column(Integer)
    columns: Mapped[int] = mapped_column(Integer)
    stage: Mapped[str] = mapped_column(String(100), default="СЦЕНА")
    category_prices: Mapped[dict] = mapped_column(JSON)


class SeatRecord(Base):
    __tablename__ = "seats"
    __table_args__ = (
        UniqueConstraint("hall_id", "row", "number"),
        CheckConstraint("row > 0 AND number > 0"),
        CheckConstraint("price_override IS NULL OR price_override >= 0"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    hall_id: Mapped[int] = mapped_column(ForeignKey("halls.id", ondelete="RESTRICT"))
    row: Mapped[int] = mapped_column(Integer)
    number: Mapped[int] = mapped_column(Integer)
    category: Mapped[str] = mapped_column(String(20))
    price_override: Mapped[int | None] = mapped_column(Integer, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    in_layout: Mapped[bool] = mapped_column(Boolean, default=True)


class SessionRecord(Base):
    __tablename__ = "event_sessions"
    __table_args__ = (
        CheckConstraint("status IN ('available', 'cancelled')"),
        CheckConstraint("duration > 0"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="RESTRICT"))
    hall_id: Mapped[int] = mapped_column(ForeignKey("halls.id", ondelete="RESTRICT"))
    start: Mapped[datetime] = mapped_column(DateTime)
    duration: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(15), default="available")
    cancel_reason: Mapped[str] = mapped_column(Text, default="")
    category_prices: Mapped[dict] = mapped_column(JSON)
    stage: Mapped[str] = mapped_column(String(100), default="СЦЕНА")
    layout: Mapped[list] = mapped_column(JSON, default=list)


class SessionSeatRecord(Base):
    __tablename__ = "session_seats"
    __table_args__ = (
        UniqueConstraint("session_id", "seat_id"),
        CheckConstraint("price >= 0"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("event_sessions.id", ondelete="RESTRICT"))
    seat_id: Mapped[int] = mapped_column(ForeignKey("seats.id", ondelete="RESTRICT"))
    row: Mapped[int] = mapped_column(Integer)
    number: Mapped[int] = mapped_column(Integer)
    category: Mapped[str] = mapped_column(String(20))
    price_override: Mapped[int | None] = mapped_column(Integer, nullable=True)
    price: Mapped[int] = mapped_column(Integer)
    closed: Mapped[bool] = mapped_column(Boolean, default=False)
    in_layout: Mapped[bool] = mapped_column(Boolean, default=True)


class CartItemRecord(Base):
    __tablename__ = "cart_items"
    __table_args__ = (
        UniqueConstraint("user_id", "session_id", "seat_id"),
        ForeignKeyConstraint(
            ["session_id", "seat_id"],
            ["session_seats.session_id", "session_seats.seat_id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("price >= 0"),
        CheckConstraint("tariff IN ('standard', 'concession')"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    session_id: Mapped[int] = mapped_column(Integer)
    seat_id: Mapped[int] = mapped_column(Integer)
    tariff: Mapped[str] = mapped_column(String(20), default="standard")
    price: Mapped[int] = mapped_column(Integer)


class BookingRecord(Base):
    __tablename__ = "bookings"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'cancelled')"),
        CheckConstraint("total >= 0"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(40), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    session_id: Mapped[int] = mapped_column(ForeignKey("event_sessions.id", ondelete="RESTRICT"))
    title: Mapped[str] = mapped_column(String(160))
    hall_name: Mapped[str] = mapped_column(String(100))
    hall_id: Mapped[int | None] = mapped_column(ForeignKey("halls.id", ondelete="RESTRICT"))
    stage: Mapped[str] = mapped_column(String(100), default="СЦЕНА")
    layout: Mapped[list] = mapped_column(JSON, default=list)
    layout_is_partial: Mapped[bool] = mapped_column(Boolean, default=False)
    start: Mapped[datetime] = mapped_column(DateTime)
    duration: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(15), default="active")
    cancel_reason: Mapped[str] = mapped_column(Text, default="")
    total: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class TicketRecord(Base):
    __tablename__ = "tickets"
    __table_args__ = (
        ForeignKeyConstraint(
            ["session_id", "seat_id"],
            ["session_seats.session_id", "session_seats.seat_id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("price >= 0 AND base_price >= 0"),
        CheckConstraint("tariff IN ('standard', 'concession')"),
        Index(
            "uq_active_session_seat",
            "session_id",
            "seat_id",
            unique=True,
            sqlite_where=text("active = 1"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id", ondelete="RESTRICT"))
    session_id: Mapped[int] = mapped_column(Integer)
    seat_id: Mapped[int] = mapped_column(Integer)
    row: Mapped[int] = mapped_column(Integer)
    number: Mapped[int] = mapped_column(Integer)
    category: Mapped[str] = mapped_column(String(20))
    tariff: Mapped[str] = mapped_column(String(20))
    base_price: Mapped[int] = mapped_column(Integer)
    price: Mapped[int] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class CheckoutRequestRecord(Base):
    __tablename__ = "checkout_requests"
    __table_args__ = (UniqueConstraint("user_id", "request_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    request_key: Mapped[str] = mapped_column(String(160))
    booking_ids: Mapped[list] = mapped_column(JSON)


class SettingRecord(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class Store:
    def __init__(self, db_path: Path, seed_new: bool = False):
        self.path = Path(db_path)
        self._seed_new = seed_new
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            "sqlite:///" + self.path.resolve().as_posix(),
            connect_args={"check_same_thread": False, "timeout": 15},
        )
        event.listen(self.engine, "connect", self._configure_connection)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        try:
            self.fresh = self._migrate()
        except OperationalError as exc:
            self.engine.dispose()
            raise AppError(
                "Не удалось открыть базу данных. Проверьте доступ к папке данных и повторите запуск."
            ) from exc
        except BaseException:
            self.engine.dispose()
            raise

    @staticmethod
    def _configure_connection(connection, _) -> None:
        cursor = connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.execute("PRAGMA busy_timeout = 15000")
            deadline = time.monotonic() + 15
            while True:
                try:
                    if cursor.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
                        cursor.execute("PRAGMA journal_mode = WAL")
                    break
                except sqlite3.OperationalError as exc:
                    if "locked" not in str(exc).lower() or time.monotonic() >= deadline:
                        raise
                    time.sleep(0.05)
        finally:
            cursor.close()

    def _migrate(self) -> bool:
        with self.engine.connect() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                version = connection.exec_driver_sql("PRAGMA user_version").scalar_one()
                if version > SCHEMA_VERSION:
                    raise AppError(
                        "База данных создана более новой версией EventSeat. Обновите приложение."
                    )
                fresh = version == 0
                if version < 1:
                    Base.metadata.create_all(connection)
                    connection.exec_driver_sql("PRAGMA user_version = 1")
                if version < 2:
                    connection.exec_driver_sql(
                        "CREATE INDEX IF NOT EXISTS ix_sessions_hall_start ON event_sessions(hall_id, start)"
                    )
                    connection.exec_driver_sql(
                        "CREATE INDEX IF NOT EXISTS ix_bookings_user ON bookings(user_id)"
                    )
                    connection.exec_driver_sql(
                        "CREATE INDEX IF NOT EXISTS ix_cart_user ON cart_items(user_id)"
                    )
                    connection.exec_driver_sql("PRAGMA user_version = 2")
                if version < 3:
                    columns = {
                        row[1]
                        for row in connection.exec_driver_sql("PRAGMA table_info(event_sessions)")
                    }
                    if "stage" not in columns:
                        connection.exec_driver_sql(
                            "ALTER TABLE event_sessions ADD COLUMN stage TEXT NOT NULL DEFAULT 'СЦЕНА'"
                        )
                    if "layout" not in columns:
                        connection.exec_driver_sql(
                            "ALTER TABLE event_sessions ADD COLUMN layout JSON NOT NULL DEFAULT '[]'"
                        )
                    sessions = connection.exec_driver_sql(
                        "SELECT id, hall_id FROM event_sessions WHERE layout = '[]'"
                    ).all()
                    for session_id, hall_id in sessions:
                        stage = connection.exec_driver_sql(
                            "SELECT stage FROM halls WHERE id = ?", (hall_id,)
                        ).scalar_one()
                        snapshot_positions = {
                            (row, number)
                            for row, number in connection.exec_driver_sql(
                                "SELECT row, number FROM session_seats "
                                "WHERE session_id = ? AND in_layout = 1",
                                (session_id,),
                            )
                        }
                        template_positions = {
                            (row, number)
                            for row, number in connection.exec_driver_sql(
                                "SELECT row, number FROM seats WHERE hall_id = ? AND in_layout = 1",
                                (hall_id,),
                            )
                        }
                        layout = [
                            {
                                "row": row,
                                "number": number,
                                "enabled": (row, number) in snapshot_positions,
                            }
                            for row, number in sorted(snapshot_positions | template_positions)
                        ]
                        connection.exec_driver_sql(
                            "UPDATE event_sessions SET stage = ?, layout = ? WHERE id = ?",
                            (stage, json.dumps(layout), session_id),
                        )
                    connection.exec_driver_sql("PRAGMA user_version = 3")
                if version < 4:
                    self._migrate_booking_layouts(connection)
                    connection.exec_driver_sql("PRAGMA user_version = 4")
                if fresh and self._seed_new:
                    connection.exec_driver_sql(
                        "INSERT INTO settings (key, value) VALUES ('demo_pending', '1')"
                    )
                connection.commit()
                return fresh
            except BaseException:
                connection.rollback()
                raise

    @staticmethod
    def _migrate_booking_layouts(connection) -> None:
        columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(bookings)")}
        additions = {
            "hall_id": "INTEGER REFERENCES halls(id)",
            "stage": "TEXT NOT NULL DEFAULT 'СЦЕНА'",
            "layout": "JSON NOT NULL DEFAULT '[]'",
            "layout_is_partial": "BOOLEAN NOT NULL DEFAULT 0",
        }
        for name, definition in additions.items():
            if name not in columns:
                connection.exec_driver_sql(f"ALTER TABLE bookings ADD COLUMN {name} {definition}")
        bookings = connection.exec_driver_sql(
            "SELECT id, session_id FROM bookings WHERE layout = '[]'"
        ).all()
        for booking_id, session_id in bookings:
            session_hall, stage, raw_layout = connection.exec_driver_sql(
                "SELECT hall_id, stage, layout FROM event_sessions WHERE id = ?", (session_id,)
            ).one()
            tickets = connection.exec_driver_sql(
                "SELECT t.seat_id, t.row, t.number, t.category, s.hall_id "
                "FROM tickets t JOIN seats s ON s.id = t.seat_id "
                "WHERE t.booking_id = ? ORDER BY t.row, t.number",
                (booking_id,),
            ).all()
            seats = {
                seat_id: (row, number, category)
                for seat_id, row, number, category in connection.exec_driver_sql(
                    "SELECT seat_id, row, number, category FROM session_seats "
                    "WHERE session_id = ? AND in_layout = 1",
                    (session_id,),
                )
            }
            hall_id = tickets[0][4] if tickets else session_hall
            complete = bool(tickets) and all(
                original_hall == session_hall
                and seat_id in seats
                and seats[seat_id][:2] == (row, number)
                for seat_id, row, number, _, original_hall in tickets
            )
            if complete:
                positions = {
                    (row, number): (seat_id, category)
                    for seat_id, (row, number, category) in seats.items()
                }
                layout = json.loads(raw_layout)
                for cell in layout:
                    seat = positions.get((cell["row"], cell["number"]))
                    if seat:
                        cell["seat_id"], cell["category"] = seat
            else:
                # Version 3 did not retain the original full grid after a hall change.
                # Only ticket positions are certain; never present the new hall as history.
                stage = "Сцена: положение не сохранено"
                layout = [
                    {
                        "seat_id": seat_id,
                        "row": row,
                        "number": number,
                        "category": category,
                        "enabled": True,
                    }
                    for seat_id, row, number, category, _ in tickets
                ]
            connection.exec_driver_sql(
                "UPDATE bookings SET hall_id = ?, stage = ?, layout = ?, "
                "layout_is_partial = ? WHERE id = ?",
                (hall_id, stage, json.dumps(layout), not complete, booking_id),
            )

    @contextmanager
    def read(self) -> Iterator[Session]:
        with self.sessions() as session:
            try:
                yield session
            except OperationalError as exc:
                raise AppError(
                    "Не удалось прочитать данные. Проверьте доступ к папке данных и повторите попытку."
                ) from exc

    @contextmanager
    def write(self) -> Iterator[Session]:
        with self.sessions() as session:
            try:
                session.execute(text("BEGIN IMMEDIATE"))
                yield session
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise AppError(
                    "Изменения не сохранены: данные конфликтуют с другой операцией. Обновите страницу и повторите попытку."
                ) from exc
            except OperationalError as exc:
                session.rollback()
                raise AppError(
                    "Не удалось сохранить данные. Проверьте доступ к папке данных и повторите попытку."
                ) from exc
            except BaseException:
                session.rollback()
                raise

    def close(self) -> None:
        self.engine.dispose()
