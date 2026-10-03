import argparse
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import flet as ft

from eventseat.config import asset_path, data_dir
from eventseat.domain import AppError
from eventseat.services import Service
from eventseat.ui import App

verification_folder = None


def main(page: ft.Page):
    try:
        service = Service(data_dir() / "eventseat.db")
    except (AppError, OSError) as error:
        logging.exception("Startup failed")
        page.title = "EventSeat — ошибка открытия базы"
        page.add(ft.Text("Не удалось открыть EventSeat", size=26), ft.Text(str(error)))
        return
    page.on_disconnect = lambda _: service.close()
    app = App(page, service)
    app.start()
    if verification_folder is not None:
        if len(os.environ.get("EVENTSEAT_QA_PASSWORD", "")) < 12:
            raise SystemExit(
                "Для проверки задайте временный EVENTSEAT_QA_PASSWORD (не менее 12 символов)."
            )
        from eventseat.diagnostics import verify

        page.run_task(verify, app, verification_folder, args.phase)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="EventSeat")
    parser.add_argument("--verify-ui", type=Path, help="Каталог изолированной проверки интерфейса")
    parser.add_argument("--phase", choices=["create", "resume"], default="create")
    args = parser.parse_args()
    verification_folder = args.verify_ui.resolve() if args.verify_ui else None
    if verification_folder is not None:
        verification_folder.mkdir(parents=True, exist_ok=True)
        if args.phase == "create" and (verification_folder / "eventseat.db").exists():
            raise SystemExit("Для проверки первого запуска укажите новый пустой каталог.")
        if args.phase == "resume" and not (verification_folder / "state.json").is_file():
            raise SystemExit("Сначала выполните фазу create в этом каталоге.")
        os.environ["EVENTSEAT_DATA_DIR"] = str(verification_folder)

        def offline_guard(event, arguments):
            if event == "socket.connect":
                address = arguments[1]
                if isinstance(address, tuple) and address[0] not in {
                    "127.0.0.1",
                    "::1",
                    "localhost",
                }:
                    raise OSError("Проверка автономности: внешние соединения запрещены.")

        sys.addaudithook(offline_guard)
    logging.basicConfig(
        level=logging.WARNING,
        handlers=[
            RotatingFileHandler(
                data_dir() / "eventseat.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8"
            )
        ],
        format="%(asctime)s %(levelname)s %(message)s",
    )
    ft.run(main, assets_dir=str(asset_path("")))
