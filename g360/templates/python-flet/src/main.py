import flet as ft
import sys


class G360App:
    def __init__(self, page: ft.Page):
        self.page = page
        self.setup_theme()
        self.create_ui()

    def setup_theme(self):
        self.page.title = "G360 Desktop App"
        self.page.theme_mode = ft.ThemeMode.DARK
        self.page.padding = 20
        self.page.bgcolor = "#0b1220"
        self.page.window_width = 1200
        self.page.window_height = 800
        self.page.window_resizable = True
        self.page.window_center()

    def create_ui(self):
        self.page.add(
            ft.Container(
                content=ft.Column([
                    self.create_header(),
                    self.create_content(),
                ]),
                expand=True,
                spacing=20
            )
        )

    def create_header(self):
        return ft.Container(
            content=ft.Row([
                ft.Text(
                    "G360",
                    size=32,
                    weight=ft.FontWeight.BOLD,
                    color="#00d084"
                ),
                ft.Text(
                    "Desktop App",
                    size=20,
                    color="#94a3b8"
                ),
                ft.Container(expand=True),
                ft.IconButton(
                    icon=ft.icons.SETTINGS,
                    on_click=self.on_settings
                ),
            ], alignment=ft.MainAxisAlignment.START),
            padding=20,
            bgcolor="#151e2e",
            border_radius=12
        )

    def create_content(self):
        return ft.Container(
            content=ft.Column([
                ft.Text(
                    "Bienvenido a G360 Desktop",
                    size=24,
                    color="#f0f4f8"
                ),
                ft.Container(height=20),
                ft.Row([
                    self.create_card("Usuarios", "Gestión de usuarios", ft.icons.PEOPLE),
                    self.create_card("Reportes", "Ver reportes", ft.icons.ANALYTICS),
                    self.create_card("Configuración", "Ajustes del sistema", ft.icons.SETTINGS),
                ], spacing=20),
            ], scroll=ft.ScrollMode.AUTO),
            expand=True,
            padding=20
        )

    def create_card(self, title, subtitle, icon):
        return ft.Container(
            content=ft.Column([
                ft.Icon(icon, size=48, color="#00d084"),
                ft.Text(title, size=18, weight=ft.FontWeight.BOLD, color="#f0f4f8"),
                ft.Text(subtitle, size=14, color="#94a3b8"),
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=10),
            width=200,
            height=180,
            bgcolor="#151e2e",
            border_radius=12,
            padding=20,
            alignment=ft.alignment.center
        )

    def on_settings(self, e):
        print("Settings clicked")


def main(page: ft.Page):
    G360App(page)


if __name__ == "__main__":
    ft.app(target=main)