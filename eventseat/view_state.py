from __future__ import annotations

import json
from copy import deepcopy
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
        route = self.routes.get(
            "Администрирование",
            {"section": "Администрирование", "page": "list", "tab": "Мероприятия"},
        )
        return deepcopy(route)

    @staticmethod
    def route_key(route):
        destination = {key: value for key, value in route.items() if key != "return_to"}
        return json.dumps(destination, sort_keys=True, ensure_ascii=False, default=str)

    @staticmethod
    def same_destination(first, second):
        keys = (
            "section",
            "page",
            "tab",
            "event_id",
            "session_id",
            "hall_id",
            "booking_id",
            "admin",
        )
        return all(first.get(key) == second.get(key) for key in keys)

    @staticmethod
    def filter_keys(route):
        if route.get("page") == "catalogue":
            return ("catalogue:fields", "catalogue:applied")
        if route.get("page") == "bookings":
            return ("bookings:filters",)
        if route.get("section") == "Администрирование" and route.get("page") == "list":
            if route.get("tab") == "Сеансы":
                return ("admin:session_filters", "admin:session_applied")
            if route.get("tab") == "Бронирования":
                return ("admin:booking_filters", "admin:booking_applied")
        return ()

    @staticmethod
    def return_label(route):
        page = route.get("page")
        if page in ("session_form", "session_detail"):
            return "К редактированию сеанса" if route.get("session_id") else "К созданию сеанса"
        if page == "event_form":
            return (
                "К редактированию мероприятия"
                if route.get("event_id")
                else "К созданию мероприятия"
            )
        if page == "list":
            return {
                "Мероприятия": "К мероприятиям",
                "Сеансы": "К сеансам",
                "Залы": "К залам",
                "Бронирования": "К бронированиям",
            }.get(route.get("tab"), "К администрированию")
        return {
            "hall": "К редактору зала",
            "catalogue": "К афише",
            "cart": "К корзине",
            "bookings": "К бронированиям",
            "event": "К мероприятию",
            "session": "К сеансу",
            "seats": "К схеме мест",
            "booking_map": "К схеме билета",
            "profile": "К профилю",
        }.get(page, "Назад")


class ViewStates:
    def __init__(self):
        self._accounts = {}

    def for_account(self, user_id: int) -> AccountViewState:
        return self._accounts.setdefault(user_id, AccountViewState())

    def forget(self, user_id: int):
        self._accounts.pop(user_id, None)

    def clear(self):
        self._accounts.clear()
