import flet as ft


def main(page: ft.Page):
    page.title = "EventSeat — проверка упаковки"
    page.add(ft.Text("EventSeat", size=32), ft.Text("Проверка автономного Windows-приложения"))


if __name__ == "__main__":
    ft.run(main)
