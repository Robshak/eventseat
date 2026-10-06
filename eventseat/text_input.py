"""Text inputs that explicitly synchronize clearing through Flet's event transport."""

from __future__ import annotations

import flet as ft


class TextInput(ft.TextField):
    """Keep an empty edit authoritative before any form callback redraws the page.

    In Flet 1.0.3 a client patch equal to a property's default is removed from
    its sparse value store without marking that property dirty. Consequently a
    change followed by a page update need not send an explicit empty ``value``
    back to the client. A change event carries the complete text, so accept it
    before application callbacks and acknowledge clearing explicitly.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Even fields without a form callback must receive change events.
        if self.on_change is None:
            self.on_change = self._accept_change

    @staticmethod
    def _accept_change(_event):
        pass

    def before_event(self, event):
        if event.name == "change" and isinstance(event.data, str):
            self.value = event.data
            if event.data == "":
                # Narrow compatibility hook for our pinned Flet 1.0.3 runtime.
                # Prop.__set__ otherwise sees the already-applied default and
                # skips the outgoing update. Preserve the real empty value;
                # do not use whitespace, a numeric fallback, or a sentinel.
                self._dirty["value"] = None
        return super().before_event(event)
