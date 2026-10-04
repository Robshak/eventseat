import flet as ft


class AccountMenu:
    WIDTH = 190
    TRIGGER_HEIGHT = 66
    FOOTER_HEIGHT = 98

    def __init__(self, app, user):
        self.app = app
        self.opened = False
        self.items = []
        self.arrow = ft.Icon(ft.Icons.EXPAND_LESS, color="#91DAD3", size=19)
        self.trigger = ft.TextButton(
            content=ft.Column(
                [
                    ft.Text("Аккаунты", size=11, color="#B9CAD8"),
                    ft.Row(
                        [
                            ft.Text(
                                "@" + user["login"],
                                size=13,
                                color="#FFFFFF",
                                weight=ft.FontWeight.W_600,
                                max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS,
                                expand=True,
                            ),
                            self.arrow,
                        ]
                    ),
                ],
                spacing=5,
                alignment=ft.MainAxisAlignment.CENTER,
            ),
            width=self.WIDTH,
            height=self.TRIGGER_HEIGHT,
            tooltip="Переключить аккаунт",
            data="account-switcher",
            on_click=app.safe(self.toggle),
            style=ft.ButtonStyle(
                bgcolor={
                    ft.ControlState.DEFAULT: "#203B53",
                    ft.ControlState.HOVERED: "#35566D",
                },
                padding=12,
                shape=ft.RoundedRectangleBorder(radius=10),
                side=ft.BorderSide(1, "#66869D"),
                alignment=ft.Alignment.CENTER_LEFT,
            ),
        )
        for account in app.accounts.users:
            active = account["id"] == user["id"]
            role = "Администратор" if account["role"] == "admin" else "Пользователь"
            self.items.append(
                ft.TextButton(
                    content=ft.Row(
                        [
                            ft.Icon(
                                ft.Icons.CHECK_CIRCLE_OUTLINE
                                if active
                                else ft.Icons.PERSON_OUTLINE,
                                color="#91DAD3",
                                size=19,
                            ),
                            ft.Column(
                                [
                                    ft.Text(
                                        account["name"],
                                        size=13,
                                        color="#FFFFFF",
                                        weight=ft.FontWeight.W_600,
                                        max_lines=2,
                                        overflow=ft.TextOverflow.ELLIPSIS,
                                    ),
                                    ft.Text(
                                        "@" + account["login"],
                                        size=11,
                                        color="#C7D8E4",
                                        max_lines=1,
                                        overflow=ft.TextOverflow.ELLIPSIS,
                                    ),
                                    ft.Text(role, size=11, color="#C7D8E4"),
                                ],
                                spacing=3,
                                expand=True,
                            ),
                        ],
                        spacing=8,
                    ),
                    height=96,
                    tooltip=f"{account['name']} · @{account['login']} · {role}",
                    data={"account_id": account["id"], "active": active},
                    on_click=app.safe(lambda _, uid=account["id"]: self.choose(uid)),
                    style=ft.ButtonStyle(
                        bgcolor={
                            ft.ControlState.DEFAULT: "#28475D" if active else "#142C43",
                            ft.ControlState.HOVERED: "#35566D",
                            ft.ControlState.FOCUSED: "#35566D",
                        },
                        padding=8,
                        shape=ft.RoundedRectangleBorder(radius=8),
                        alignment=ft.Alignment.CENTER_LEFT,
                    ),
                )
            )
        self.listener = ft.KeyboardListener(
            ft.Column(self.items, spacing=4, scroll=ft.ScrollMode.AUTO, expand=True),
            on_key_down=app.safe(self.key_down),
        )
        self.popup = ft.Container(
            self.listener,
            left=24,
            bottom=self.FOOTER_HEIGHT + self.TRIGGER_HEIGHT + 8,
            width=self.WIDTH,
            height=min(312, len(self.items) * 100 + 8),
            padding=6,
            bgcolor="#142C43",
            border=ft.Border.all(1, "#66869D"),
            border_radius=12,
            visible=False,
            data="account-menu",
        )
        self.backdrop = ft.Container(
            left=0,
            top=0,
            right=0,
            bottom=0,
            bgcolor="#00000000",
            on_click=lambda _: self.close(),
            visible=False,
            data="account-menu-backdrop",
        )

    async def toggle(self, _=None):
        if self.opened:
            self.close()
            return
        self.opened = True
        self.popup.visible = self.backdrop.visible = True
        self.arrow.icon = ft.Icons.EXPAND_MORE
        self.app.page.update()
        await self.listener.focus()

    def close(self):
        if not self.opened:
            return
        self.opened = False
        self.popup.visible = self.backdrop.visible = False
        self.arrow.icon = ft.Icons.EXPAND_LESS
        self.app.page.update()

    def key_down(self, event):
        if event.key == "Escape" and self.opened and self.app.account_menu is self:
            self.close()
            self.app.page.run_task(self.restore_focus)

    async def restore_focus(self):
        if self.app.account_menu is self:
            await self.trigger.focus()

    def choose(self, user_id):
        self.close()
        self.app.switch_account(user_id)
