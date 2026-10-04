from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class AccountViewState:
    """Navigation and unsaved non-secret form values for one signed-in account."""

    section: str = "Афиша"
    routes: dict[str, dict] = field(default_factory=dict)
    drafts: dict[str, dict] = field(default_factory=dict)
    scroll_offsets: dict[str, float] = field(default_factory=dict)

    @property
    def admin_route(self):
        return self.routes.get(
            "Администрирование",
            {"section": "Администрирование", "page": "list", "tab": "Мероприятия"},
        ).copy()

    @staticmethod
    def route_key(route):
        return json.dumps(route, sort_keys=True, ensure_ascii=False, default=str)


class ViewStates:
    def __init__(self):
        self._accounts = {}

    def for_account(self, user_id: int) -> AccountViewState:
        return self._accounts.setdefault(user_id, AccountViewState())

    def forget(self, user_id: int):
        self._accounts.pop(user_id, None)

    def clear(self):
        self._accounts.clear()
