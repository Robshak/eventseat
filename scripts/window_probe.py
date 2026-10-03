import asyncio
from pathlib import Path

import flet as ft


async def main(page: ft.Page):
    page.title = "EventSeat — проверка рендеринга"
    capture = ft.Screenshot(ft.Container(ft.Text("EventSeat · Windows", size=32), padding=40))
    page.add(capture)
    await asyncio.sleep(1)
    folder = Path(".qa")
    folder.mkdir(exist_ok=True)
    (folder / "window-probe.png").write_bytes(await capture.capture())
    await page.window.destroy()


if __name__ == "__main__":
    ft.run(main)
