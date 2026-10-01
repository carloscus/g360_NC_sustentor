import flet as ft

G360_GREEN = "#00d084"
G360_GRAY_DARK = "#94a3b8"
G360_GRAY_LIGHT = "#64748b"


class G360Signature(ft.Row):
    """Firma visual G360 (tres puntos + chevron + texto de marca).

    Widget reutilizable en modo "own" (lado autor) o "powered" (créditos).
    Detecta automáticamente el tema claro/oscuro para elegir los colores y
    responde al hover elevando su opacidad.
    """

    def __init__(
        self,
        mode: str = "own",
        version: str | None = None,
        opacity: float = 0.4,
        grayscale: bool = False,
        spacing: int = 4,
        on_hover=None,
        **kwargs,
    ):
        super().__init__(**kwargs)

        self._base_opacity = opacity
        self._grayscale = grayscale
        self.spacing = spacing
        self.vertical_alignment = ft.CrossAxisAlignment.CENTER

        self._mode = "own"
        self._version = None

        self.mode = mode
        self.version = version

        if on_hover is None:
            self.on_hover = self._default_on_hover
        else:
            self.on_hover = on_hover

        self.opacity = opacity

    def _default_on_hover(self, e: ft.HoverEvent):
        self.opacity = 1.0 if e.data == "true" else self._base_opacity
        self.update()

    def _is_dark(self) -> bool:
        if not self._is_mounted():
            return False
        mode = self.page.theme_mode
        if mode == ft.ThemeMode.DARK:
            return True
        if mode == ft.ThemeMode.LIGHT:
            return False
        bg = self.page.bgcolor
        if bg and isinstance(bg, str):
            bg_lower = bg.lower()
            if bg_lower.startswith("#"):
                r = int(bg_lower[1:3], 16)
                g = int(bg_lower[3:5], 16)
                b = int(bg_lower[5:7], 16)
                lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255
                return lum < 0.5
        return False

    def _get_colors(self):
        if self._grayscale:
            return G360_GRAY_DARK, G360_GRAY_DARK
        if self._is_dark():
            return G360_GREEN, G360_GRAY_DARK
        return G360_GREEN, G360_GRAY_LIGHT

    def _build(self):
        green, gray = self._get_colors()

        dots = ft.Column(
            controls=[
                ft.Container(width=4, height=4, border_radius=2, bgcolor=gray),
                ft.Container(width=4, height=4, border_radius=2, bgcolor=green),
                ft.Container(width=4, height=4, border_radius=2, bgcolor=gray),
            ],
            spacing=2,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )

        chevron = ft.Text(">", size=14, color=green, font_family="Consolas", selectable=False)

        isotype = ft.Row(
            controls=[dots, chevron], spacing=1, vertical_alignment=ft.CrossAxisAlignment.CENTER
        )

        if self.mode == "own":
            text_controls = [
                ft.Text(
                    "G360",
                    size=12,
                    color=green,
                    weight=ft.FontWeight.BOLD,
                    font_family="Consolas",
                    selectable=False,
                ),
                ft.Text(" by ccusi", size=12, color=gray, font_family="Consolas", selectable=False),
            ]
        else:
            text_controls = [
                ft.Text(
                    "powered by ", size=12, color=gray, font_family="Consolas", selectable=False
                ),
                ft.Text(
                    "G360",
                    size=12,
                    color=green,
                    weight=ft.FontWeight.BOLD,
                    font_family="Consolas",
                    selectable=False,
                ),
            ]

        if self.version:
            text_controls.append(
                ft.Text(
                    f" v{self.version}",
                    size=12,
                    color=gray,
                    opacity=0.7,
                    font_family="Consolas",
                    selectable=False,
                )
            )

        text_row = ft.Row(
            controls=text_controls, spacing=0, vertical_alignment=ft.CrossAxisAlignment.CENTER
        )
        self.controls = [isotype, text_row]

    def _is_mounted(self) -> bool:
        try:
            return self.page is not None
        except RuntimeError:
            return False

    @property
    def mode(self) -> str:
        return self._mode

    @mode.setter
    def mode(self, value: str):
        if value not in ("own", "powered"):
            raise ValueError(f"mode debe ser 'own' o 'powered', recibido: '{value}'")
        self._mode = value
        if self._is_mounted():
            self._build()

    @property
    def version(self) -> str | None:
        return self._version

    @version.setter
    def version(self, value: str | None):
        self._version = value
        if self._is_mounted():
            self._build()

    @property
    def grayscale(self) -> bool:
        return self._grayscale

    @grayscale.setter
    def grayscale(self, value: bool):
        self._grayscale = value
        if self._is_mounted():
            self._build()

    def did_mount(self):
        self._build()
        self.update()

    def update(self):
        if self._is_mounted():
            self._build()
        super().update()
