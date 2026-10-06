"""Exercise real Flet property patches, event dispatch and outgoing wire patches."""

import asyncio
from types import SimpleNamespace

import flet as ft
import msgpack
import pytest
from flet.messaging.connection import Connection
from flet.messaging.protocol import configure_encode_object_for_msgpack
from flet.messaging.session import Session
from flet.pubsub.pubsub_hub import PubSubHub

from eventseat.date_input import DateInput
from eventseat.text_input import TextInput
from eventseat.ui import field

ENCODE = configure_encode_object_for_msgpack(ft.BaseControl)


def wire(value):
    return msgpack.unpackb(msgpack.packb(value, default=ENCODE), raw=False, strict_map_key=False)


class RecordingConnection(Connection):
    def __init__(self):
        super().__init__()
        self.pubsubhub = PubSubHub()
        self.loop = asyncio.get_running_loop()
        self.messages = []

    def send_message(self, message):
        self.messages.append(wire(message))


def mount(session, control):
    session.page.controls = [control]
    # Serialization registers the same baseline used by the desktop connection.
    wire(session.get_page_patch())


async def edit(session, control, value, *, apply_property=True):
    incoming = wire({"id": control._i, "props": {"value": value}})
    if apply_property:
        session.apply_patch(incoming["id"], incoming["props"])
    event = wire({"target": control._i, "name": "change", "data": value})
    await session.dispatch_event(event["target"], event["name"], event["data"])


def value_patches(connection):
    return [
        operation[3]
        for message in connection.messages
        for operation in message.get("body", {}).get("patch", [])[1:]
        if isinstance(operation, list)
        and len(operation) == 4
        and operation[0] in (0, 1)
        and operation[2] == "value"
    ]


@pytest.mark.parametrize("initial", ["0", "я", "полный текст"])
def test_clear_is_empty_in_callback_and_outgoing_patch_after_page_redraw(initial):
    async def scenario():
        connection = RecordingConnection()
        session = Session(connection)
        captured = []

        def capture(event):
            captured.append(event.control.value)
            session.page.update()

        control = TextInput(value=initial, on_change=capture)
        mount(session, control)
        await edit(session, control, "")
        assert captured == [""]
        assert control.value == ""
        assert value_patches(connection) == [""]
        session.page.update()
        assert value_patches(connection) == [""]
        await edit(session, control, "7")
        assert captured == ["", "7"]
        assert control.value == "7"

    asyncio.run(scenario())


def test_field_without_callback_still_requests_changes_and_acknowledges_clear():
    async def scenario():
        connection = RecordingConnection()
        session = Session(connection)
        control = field("Цена", 0)
        assert isinstance(control, TextInput)
        assert control.value == "0"
        assert wire(control)["on_change"] is True
        mount(session, control)
        await edit(session, control, "")
        assert control.value == ""
        assert value_patches(connection) == [""]

    asyncio.run(scenario())


def test_replaced_handler_receives_change_text_before_capture_even_without_property_patch():
    async def scenario():
        connection = RecordingConnection()
        session = Session(connection)
        captured = []
        control = TextInput(value="0")

        async def capture(event):
            captured.append(event.control.value)

        control.on_change = capture
        mount(session, control)
        await edit(session, control, "", apply_property=False)
        assert captured == [""]
        assert value_patches(connection) == [""]
        await edit(session, control, "12", apply_property=False)
        assert captured == ["", "12"]
        assert control.value == "12"

    asyncio.run(scenario())


def test_date_last_digit_clear_updates_draft_and_stays_empty_after_validation():
    async def scenario():
        connection = RecordingConnection()
        session = Session(connection)
        app = SimpleNamespace(page=session.page, safe=lambda callback: callback)
        captured = []
        control = DateInput(app, "С", "0", on_change=lambda e: captured.append(e.control.value))
        mount(session, control)
        await edit(session, control, "")
        assert captured == [""]
        assert control.value == control._previous_value == ""
        assert control.read() is None
        assert value_patches(connection) == [""]
        assert (control.selection.base_offset, control.selection.extent_offset) == (0, 0)
        session.page.update()
        assert control.value == ""

    asyncio.run(scenario())


def test_non_change_event_does_not_replace_text_with_event_metadata():
    control = TextInput(value="оставить")
    control.before_event(ft.ControlEvent(control=control, name="blur", data=""))
    assert control.value == "оставить"


def test_queued_clear_followed_by_typing_keeps_latest_value_and_wire_patch():
    async def scenario():
        connection = RecordingConnection()
        session = Session(connection)
        captured = []
        control = TextInput(value="9", on_change=lambda e: captured.append(e.control.value))
        mount(session, control)
        tasks = []
        # Socket property patches are synchronous, while each event starts a task.
        # Do not yield between arrivals: all props precede all event callbacks.
        for value in ("0", "", "12"):
            session.apply_patch(control._i, wire({"value": value}))
            tasks.append(asyncio.create_task(session.dispatch_event(control._i, "change", value)))
        await asyncio.gather(*tasks)
        assert captured == ["0", "", "12"]
        assert control.value == "12"
        assert value_patches(connection)[-1] == "12"
        assert "" in value_patches(connection)
        session.page.update()
        assert control.value == "12"
        assert value_patches(connection)[-1] == "12"

    asyncio.run(scenario())


def test_queued_date_selection_and_typing_keep_caret_after_inserted_separator():
    async def scenario():
        connection = RecordingConnection()
        session = Session(connection)
        app = SimpleNamespace(page=session.page, safe=lambda callback: callback)
        control = DateInput(app, "Дата")
        mount(session, control)
        tasks = []
        for value in ("0", "08"):
            selected = {"base_offset": len(value), "extent_offset": len(value)}
            session.apply_patch(control._i, wire({"selection": selected}))
            tasks.append(
                asyncio.create_task(
                    session.dispatch_event(
                        control._i,
                        "selection_change",
                        {"selection": selected, "selected_text": ""},
                    )
                )
            )
            session.apply_patch(control._i, wire({"value": value}))
            tasks.append(asyncio.create_task(session.dispatch_event(control._i, "change", value)))
        await asyncio.gather(*tasks)
        assert control.value == "08."
        assert (control.selection.base_offset, control.selection.extent_offset) == (3, 3)
        assert value_patches(connection)[-1] == "08."

    asyncio.run(scenario())


def test_date_change_without_selection_event_uses_current_property_selection():
    async def scenario():
        connection = RecordingConnection()
        session = Session(connection)
        app = SimpleNamespace(page=session.page, safe=lambda callback: callback)
        control = DateInput(app, "Дата", "0")
        mount(session, control)
        session.apply_patch(control._i, {"selection": {"base_offset": 2, "extent_offset": 2}})
        await edit(session, control, "08")
        assert control.value == "08."
        assert (control.selection.base_offset, control.selection.extent_offset) == (3, 3)

    asyncio.run(scenario())
