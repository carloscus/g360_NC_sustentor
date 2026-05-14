import flet as ft


class MigratedApp:
    def __init__(self, page: ft.Page):
        self.page = page
        self.setup_theme()
        self.create_ui()

    def setup_theme(self):
        self.page.title = "G360 - Migrated App"
        self.page.theme_mode = ft.ThemeMode.DARK
        self.page.padding = 20
        self.page.bgcolor = "#0b1220"

    def create_ui(self):
        self.page.add(
            ft.Column([
                self.create_header(),
                self.create_main_content(),
            ], spacing=20, expand=True)
        )

    def create_header(self):
        return ft.Container(
            content=ft.Row([
                ft.Text("G360 Migrated App", size=28, weight=ft.FontWeight.BOLD, color="#00d084"),
                ft.Container(expand=True),
                ft.Text("from tkinter/ctkinter", size=14, color="#94a3b8"),
            ]),
            padding=20,
            bgcolor="#151e2e",
            border_radius=12
        )

    def create_main_content(self):
        return ft.Container(
            content=ft.Column([
                ft.Text("Panel de Controles (antes tk.Button)", size=20, color="#f0f4f8"),
                ft.Row([
                    ft.ElevatedButton("Aceptar", bgcolor="#00d084", color="#0b1220"),
                    ft.ElevatedButton("Cancelar", bgcolor="#ef4444", color="#ffffff"),
                    ft.ElevatedButton("Guardar", bgcolor="#00796B", color="#ffffff"),
                ], spacing=15),
                ft.Container(height=30),
                ft.Text("Campos de Entrada (antes tk.Entry)", size=20, color="#f0f4f8"),
                ft.Column([
                    ft.TextField(label="Usuario", hint_text="Ingrese usuario", width=300),
                    ft.TextField(label="Contraseña", hint_text="Ingrese contraseña", password=True, width=300),
                ], spacing=15),
                ft.Container(height=30),
                ft.Text("Casillas y Opciones (antes tk.Checkbutton/tk.Radiobutton)", size=20, color="#f0f4f8"),
                ft.Column([
                    ft.Checkbox(label="Recordarme"),
                    ft.Checkbox(label="Aceptar términos"),
                ], spacing=10),
            ], scroll=ft.ScrollMode.AUTO),
            expand=True,
            padding=20
        )


def main(page: ft.Page):
    MigratedApp(page)


if __name__ == "__main__":
    ft.app(target=main)