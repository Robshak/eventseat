from __future__ import annotations

from eventseat.domain import AppError


class AccountSessions:
    """Keep authenticated service contexts only for the lifetime of this app."""

    def __init__(self, service):
        self.current = service
        self._sessions = {}
        self._return_to = None
        if service.current_user:
            self.remember_current()

    @property
    def users(self):
        return [session.current_user for session in self._sessions.values()]

    @property
    def can_return(self):
        return self._return_to in self._sessions

    def remember_current(self):
        user = self.current.current_user
        if user is None:
            raise AppError("Сначала войдите в аккаунт.")
        previous = self._sessions.get(user["id"])
        if previous is not None and previous is not self.current:
            previous.close()
        self._sessions[user["id"]] = self.current
        self._return_to = None

    def begin_login(self):
        user = self.current.current_user
        if user is not None:
            self.remember_current()
            self._return_to = user["id"]
            self.current = self.current.new_context()

    def switch(self, user_id):
        context = self._sessions.get(user_id)
        if context is None or context.current_user is None:
            raise AppError("Для этого аккаунта необходимо войти снова.")
        previous = self.current
        self.current = context
        self._return_to = None
        if previous is not context and previous not in self._sessions.values():
            previous.close()

    def cancel_login(self):
        if self.can_return:
            self.switch(self._return_to)

    def logout(self):
        previous = self.current
        user = previous.current_user
        if user is not None:
            self._sessions.pop(user["id"], None)
        self.current = next(iter(self._sessions.values()), None) or previous.new_context()
        self._return_to = None
        previous.close()

    def close(self):
        for context in {self.current, *self._sessions.values()}:
            context.close()
        self._sessions.clear()
        self._return_to = None
