# Внутренний контракт реализации

`eventseat.services.Service(db_path: Path, seed: bool = True)` — независимый контекст входа; `close()` освобождает движок. Методы возвращают обычные словари, списки, числа. Даты Python datetime (локальное время). Деньги: int копеек. Ошибки: `eventseat.domain.AppError` с русским сообщением. `PriceChanged(AppError)` означает, что цены корзины обновлены и нужно повторить подтверждение с новым ключом.

- `needs_setup() -> bool`; `setup_admin(login, name, password) -> user`; `register(login, name, password) -> user` (не выполняет вход); `login(login,password) -> user`; `logout()`; `current_user -> dict | None` (id, login, name, role).
- `update_profile(name, old_password='', new_password='') -> user`.
- `list_events(search='', category='', date=None, admin=False) -> list[event]`: event id,title,description,category,cover_path,duration,published,next_start,min_price. category: кино/концерт/спектакль/лекция/другое. `get_event(event_id) -> event`.
- `save_event(title,description,category,duration,published,cover_path='',event_id=None) -> int` (copy uploaded image handled root/UI before call if needed).
- `list_sessions(event_id=None, admin=False) -> list[session]`: id,event_id,title,hall_id,hall_name,start,duration,status,cancel_reason,free_count,total_count,min_price. `get_session(session_id) -> session`.
- `save_session(event_id,hall_id,start,category_prices=None,seat_prices=None,session_id=None) -> int`; `cancel_session(session_id,reason)`.
- `list_halls() -> list[hall]`; `get_hall(hall_id) -> hall`: id,name,rows,columns,stage,category_prices,seats. seats: list[{id,row,number,category,price_override,enabled}]. row/number: positive int, category: эконом/стандарт/VIP. `save_hall(name,rows,columns,stage,category_prices,seats=None,hall_id=None) -> int`; seats passed as list[{row,number,category,price_override,enabled}], full grid including disabled aisles; `copy_hall(hall_id,name) -> int`. Prices dict category->kopecks.
- `seat_map(session_id) -> list[seat]`: id,row,number,category,price,status (free/booked/closed). `set_seat_closed(session_id,seat_id,closed)`; `set_session_prices(session_id,category_prices,seat_prices=None)`.
- `add_to_cart(session_id,seat_ids,tariff='standard')`; tariff standard/concession (учебная скидка 20%). `get_cart() -> list[item]`: id,session_id,seat_id,title,hall_name,start,row,number,category,tariff,price,available. `remove_cart_item(item_id)`; `checkout(request_key: str) -> list[booking]`. Idempotency key belongs to user; same key returns same result even after success.
- `list_bookings(search='',admin=False) -> list[booking]`; `get_booking(booking_id) -> booking`; `cancel_booking(booking_id,reason='Отменено пользователем')`. booking id,number,user_id,user_name,title,start,hall_name,status (active/cancelled/completed),cancel_reason,total,tickets (row,number,category,tariff,price). Group one booking per session per checkout.
- `statistics(session_id=None) -> dict`: active_tickets,occupancy_percent,active_amount,total_seats.

Первичное заполнение — только новая база, не создаёт аккаунтов. База SQLite SQLAlchemy, транзакции BEGIN IMMEDIATE для мутаций, частичный UNIQUE для активных мест. Предметные классы содержат правила, сервис проверяет права. UI не должен импортировать ORM.
