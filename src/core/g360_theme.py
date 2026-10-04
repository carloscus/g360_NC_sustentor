import functools
import logging
import traceback

import flet as ft

logger = logging.getLogger("g360.app")


def safe_handler(fn):
    """Decorator que atrapa excepciones en callbacks Flet y muestra snackbar de error."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            logger.error(f"Error en {fn.__name__}: {e}\n{traceback.format_exc()}")
            page = None
            for arg in args:
                if hasattr(arg, "page") and arg.page:
                    page = arg.page
                    break
            if page:
                page.snack_bar = ft.SnackBar(
                    content=ft.Row(
                        [
                            ft.Icon(ft.Icons.ERROR_OUTLINE, color="#f0f4f8", size=20),
                            ft.Text(f"Error: {str(e)[:100]}", color="#f0f4f8", size=14),
                        ]
                    ),
                    bgcolor="#b91c1c",
                    duration=5000,
                )
                page.snack_bar.open = True
                page.update()

    return wrapper


class G360Theme:
    """Paleta centralizada de colores y estilos G360.

    Colores de marca (siempre igual):
        ACCENT=#34d399, SUCCESS=#34d399, WARNING=#fbbf24, ERROR=#f87171

    Colores de superficie (adaptables al tema):
        En dark mode usan hex fijos; en light mode se pueden reemplazar
        por ft.Colors.SURFACE / SURFACE_VARIANT llamando a set_theme_mode().
    """

    BLUE = "#34d399"
    SUCCESS = "#34d399"
    WARNING = "#fbbf24"
    ERROR = "#f87171"
    ACCENT = "#3b82f6"
    ACCENT_2 = "#8b5cf6"
    ACCENT_3 = "#06b6d4"
    ACCENT_4 = "#f59e0b"

    # Variantes optimizadas para TEXTO (no para rellenos):
    # el tono base no alcanza 4.5:1 como texto pequeño en un tema.
    ACCENT_TEXT = "#60A5FA"  # azul claro: texto en dark
    ACCENT_2_BRIGHT = "#A78BFA"  # violeta claro: texto en dark
    ACCENT_2_LIGHT = "#6D28D9"  # violeta oscuro: texto en light
    ACCENT_3_LIGHT = "#155E75"  # cyan oscuro: texto en light
    OK_LIGHT = "#047857"  # verde oscuro: texto en light

    PRIMARY = "#1e3a5f"
    PRIMARY_HOVER = "#2563eb"

    ACCENT_LIGHT = "#1e3a5f"
    WARNING_LIGHT = "#b45309"
    ERROR_LIGHT = "#b91c1c"

    SURFACE = "#1a1f2e"
    SURFACE_VARIANT = "#232838"
    SURFACE_VARIANT_LIGHT = "#e4ebf3"
    SURFACE_SUNKEN = "#121620"
    BG_DARK = "#0f1219"
    BG_LIGHT = "#f0f4f8"

    BORDER = "white12"
    BORDER_SUBTLE = "white08"
    TEXT_MUTED = "white60"
    TEXT_SECONDARY = "white70"
    TEXT_PRIMARY = "#F1F5FB"

    SHADOW_COLOR = ft.Colors.with_opacity(0.06, ft.Colors.BLACK)
    SHADOW_BLUR = 20

    # ── Escalas ──────────────────────────────────────────────────────────────
    # Antes estas medidas vivian como literales sueltos en las vistas (11 radios,
    # 16 spacings, 18 tamanos de texto). Se declaran aqui para que "12" signific
    # una sola cosa. Rango de radio de card 12-16: el radio de card va en 14.
    SPACE_XS = 4  # gap dentro de un par (label + valor)
    SPACE_SM = 8  # gap entre items de un grupo
    SPACE_MD = 12  # gap entre sub-bloques
    SPACE_LG = 16  # padding interno de card
    SPACE_XL = 24  # separacion entre secciones

    RADIUS_CHIP = 10  # chips, tiles metricas, inset compacto
    RADIUS_CONTROL = 12  # inputs, botones, contenedores de lista
    RADIUS_CARD = 14  # card
    RADIUS_PILL = 999  # badges y pills (se recortan con el alto)

    TYPE_KPI = 18  # valor numerico destacado
    TYPE_TITLE = 16  # titulo de card (subió desde 14: paso de 1px no jerarquiza)
    TYPE_SECTION = 13  # header de seccion dentro de una card
    TYPE_BODY = 12  # texto corriente
    TYPE_SUBTITLE = 11  # bajada: la línea que explica el titulo
    TYPE_SMALL = 10  # metadato
    TYPE_MICRO = 10  # el piso: 8/9 quedan vetados en Flet (ilegibles)

    BTN_HEIGHT = 36  # boton estandar
    BTN_HEIGHT_SM = 32  # boton compacto / en toolbar
    BTN_HEIGHT_XS = 28  # chevron / disclosure

    CARD_RADIUS = RADIUS_CARD
    BTN_RADIUS = RADIUS_CONTROL

    _is_dark = True

    PIE_CHART_COLORS = [
        ft.Colors.CYAN_400,
        ft.Colors.PURPLE_400,
        ft.Colors.ORANGE_400,
        ft.Colors.LIGHT_GREEN_400,
        ft.Colors.PINK_400,
        ft.Colors.BLUE_400,
        ft.Colors.AMBER_400,
        ft.Colors.TEAL_400,
        ft.Colors.DEEP_PURPLE_400,
        ft.Colors.RED_400,
        ft.Colors.INDIGO_400,
        ft.Colors.LIME_400,
        ft.Colors.DEEP_ORANGE_400,
        ft.Colors.LIGHT_BLUE_400,
        ft.Colors.YELLOW_600,
    ]

    @classmethod
    def set_theme_mode(cls, is_dark: bool):
        """Actualiza el modo de tema y retorna colores adaptativos."""
        cls._is_dark = is_dark

    @classmethod
    def bg_color(cls) -> str:
        return cls.BG_DARK if cls._is_dark else cls.BG_LIGHT

    @classmethod
    def surface_color(cls) -> str:
        return cls.SURFACE if cls._is_dark else ft.Colors.SURFACE

    @classmethod
    def surface_variant_color(cls) -> str:
        return cls.SURFACE_VARIANT if cls._is_dark else cls.SURFACE_VARIANT_LIGHT

    @classmethod
    def border_color(cls) -> str:
        return cls.BORDER if cls._is_dark else ft.Colors.OUTLINE_VARIANT

    @classmethod
    def border_subtle_color(cls) -> str:
        return cls.BORDER_SUBTLE if cls._is_dark else ft.Colors.OUTLINE_VARIANT

    @classmethod
    def text_muted_color(cls) -> str:
        return cls.TEXT_MUTED if cls._is_dark else ft.Colors.ON_SURFACE_VARIANT

    @classmethod
    def accent_color(cls) -> str:
        return cls.ACCENT if cls._is_dark else cls.ACCENT_LIGHT

    @classmethod
    def accent_text_color(cls) -> str:
        """Azul para texto pequeño (labels, ghost buttons): ≥4.5:1 en card."""
        return cls.ACCENT_TEXT if cls._is_dark else cls.ACCENT_LIGHT

    # ── Helpers de jerarquía ──────────────────────────────────────────────────
    # Antes cada título se escribía a mano con su propio size (12, 13, 14, 16,
    # 18 convivían como "título") y la bajada iba en 10 gris. Resultado: cards
    # con el título del mismo tamaño que el cuerpo, que es indistinguible.
    # Estos helpers hacen la diferencia explícita y en un solo lugar.

    @classmethod
    def card_title(cls, text: str, icon=None):
        """Título de card. W_700 y TYPE_TITLE (16): 3px sobre la sección."""
        import flet as ft

        return ft.Text(
            text,
            size=cls.TYPE_TITLE,
            weight=ft.FontWeight.W_700,
            color=cls.text_primary_color(),
        )

    @classmethod
    def subtitle(cls, text: str, align=None):
        """Bajada: explica el título. Tintada desde el acento, no gris."""
        import flet as ft

        return ft.Text(
            text,
            size=cls.TYPE_SUBTITLE,
            color=cls.subtitle_color(),
            text_align=align,
        )

    @classmethod
    def meta(cls, text: str):
        """Metadato: la capa más baja. Solo para datos, nunca para explicar."""
        import flet as ft

        return ft.Text(text, size=cls.TYPE_SMALL, color=cls.text_muted_color())

    @classmethod
    def subtitle_color(cls) -> str:
        """Color de la bajada (la línea que explica un título).

        No es gris: está tintado desde el acento. El gris puro sobre superficie
        oscura hace que la bajada se lea como texto plano y el título no se
        sostenga; tintándola conserva la jerarquía y sigue cumpliendo
        contraste.
        """
        if cls._is_dark:
            # 0.88 y no 0.72: medido sobre la card dark (#1a1f2e) y sobre la
            # card teñida de la sección de Reporte. A 0.72 daba 3.78:1, por
            # debajo del 4.5:1 mínimo; 0.85 es el primer valor que pasa en
            # ambas (5.06 y 4.71), y 0.88 deja margen.
            return ft.Colors.with_opacity(0.88, cls.ACCENT_TEXT)
        return ft.Colors.with_opacity(0.88, cls.ACCENT_LIGHT)

    @classmethod
    def section_accent_color(cls) -> str:
        """Acento de una sección independiente: el Reporte de Compras.

        Usa el violeta (ACCENT_2) en vez del azul global para que la card se
        lea como su propia zona y no como otra piece del flujo principal. En
        dark va la variante brillante y en light la oscura, para no perder
        contraste sobre el fondo de la card.
        """
        return cls.ACCENT_2_BRIGHT if cls._is_dark else cls.ACCENT_2_LIGHT

    @classmethod
    def section_surface_color(cls, opacity: float = 0.06) -> str:
        """Fondo de la sección: un velo del acento sobre la superficie.

        Delimita la zona sin un borde fuerte: el borde de 1px sigue estando, pero
        teñido, para que la separación sea de color y no de líneas.
        """
        return ft.Colors.with_opacity(opacity, cls.section_accent_color())

    @classmethod
    def section_border_color(cls, opacity: float = 0.28) -> str:
        return ft.Colors.with_opacity(opacity, cls.section_accent_color())

    @classmethod
    def accent_2_color(cls) -> str:
        """Violeta para texto/iconos: brillante en dark, oscuro en light."""
        return cls.ACCENT_2_BRIGHT if cls._is_dark else cls.ACCENT_2_LIGHT

    @classmethod
    def accent_3_color(cls) -> str:
        """Cyan para texto/iconos: base en dark, oscuro en light."""
        return cls.ACCENT_3 if cls._is_dark else cls.ACCENT_3_LIGHT

    @classmethod
    def ok_color(cls) -> str:
        """Verde semántico (estados ok): brillante en dark, oscuro en light."""
        return cls.SUCCESS if cls._is_dark else cls.OK_LIGHT

    @classmethod
    def button_color(cls) -> str:
        """Fondo de botón primario con texto blanco: ≥4.5:1 en ambos temas."""
        return cls.PRIMARY_HOVER if cls._is_dark else cls.PRIMARY

    @classmethod
    def primary_color(cls) -> str:
        """Fondo de acciones primarias: mismo verde profundo en ambos temas."""
        return cls.PRIMARY

    @classmethod
    def success_color(cls) -> str:
        return cls.accent_color()

    @classmethod
    def warning_color(cls) -> str:
        return cls.WARNING if cls._is_dark else cls.WARNING_LIGHT

    @classmethod
    def error_color(cls) -> str:
        return cls.ERROR if cls._is_dark else cls.ERROR_LIGHT

    @classmethod
    def with_opacity(cls, opacity, color_hex):
        return ft.Colors.with_opacity(opacity, color_hex)

    # ── Section Header ──────────────────────────────────────────────────────

    @classmethod
    def surface_sunken_color(cls) -> str:
        return cls.SURFACE_SUNKEN if cls._is_dark else cls.SURFACE_VARIANT_LIGHT

    @classmethod
    def text_primary_color(cls) -> str:
        return cls.TEXT_PRIMARY if cls._is_dark else "#0F172A"

    @classmethod
    def accent_soft_color(cls, opacity: float = 0.08) -> str:
        return cls.with_opacity(opacity, cls.ACCENT)

    @classmethod
    def section_header(cls, icon, text, accent_color=None):
        """Header de sección: TYPE_SECTION (13), un paso bajo el título de card.

        Antes iba en 12, que es justo el tamaño del cuerpo: el encabezado se
        perdía dentro del texto y la card se leía plana.
        """
        ac = accent_color or cls.accent_color()
        return ft.Row(
            [
                ft.Icon(icon, size=14, color=ac),
                ft.Text(
                    text,
                    size=cls.TYPE_SECTION,
                    weight=ft.FontWeight.W_600,
                    color=cls.text_primary_color(),
                ),
            ],
            spacing=6,
        )

    @classmethod
    def section_header_sub(cls, icon, text, subtitle="", accent_color=None):
        """Encabezado de sección con bajada.

        La bajada va a TYPE_SUBTITLE (11) y tintada desde el
        acento, no en 10 gris: en gris la bajada se leía como texto suelto y no
        sostenía al título.
        """
        col = [cls.section_header(icon, text, accent_color)]
        if subtitle:
            col.append(ft.Text(subtitle, size=cls.TYPE_SUBTITLE, color=cls.subtitle_color()))
        return ft.Column(col, spacing=2)

    # ── Card Containers ──────────────────────────────────────────────────────

    @classmethod
    def card(
        cls,
        content,
        padding=SPACE_LG,
        border_radius=None,
        bgcolor=None,
        on_hover=None,
        key=None,
        border_color=None,
    ):
        """Card base del sistema.

        Defaults alineados con lo que la app ya usaba de facto (padding 16,
        radio 14) en vez de los valores declarados que nadie usaba (24 / 18).
        `border_color` existe porque antes no se podia pasar: las cards
        construidas a mano tenian que reimplementar el borde, y por eso medio
        dozen de contenedores se quedaban sin el.
        """
        br = border_radius if border_radius is not None else cls.RADIUS_CARD
        bc = bgcolor if bgcolor is not None else cls.surface_color()
        return ft.Container(
            key=key,
            content=content,
            padding=padding,
            bgcolor=bc,
            border_radius=br,
            border=ft.border.all(1, border_color or cls.border_color()),
            animate=ft.Animation(300, ft.AnimationCurve.DECELERATE),
            **(on_hover or {}),
        )

    @classmethod
    def inset(cls, content, padding=None, radius=None, bgcolor=None, border_color=None):
        """Sub-superficie dentro de una card (bloque de config, fila de estado).

        Existe para que los ~30 contenedores "inset" hechos a mano no inventen
        su propia combinacion de padding/radio/fondo.
        """
        return ft.Container(
            content=content,
            padding=padding
            if padding is not None
            else ft.padding.symmetric(horizontal=cls.SPACE_MD, vertical=10),
            border_radius=radius if radius is not None else cls.RADIUS_CONTROL,
            bgcolor=bgcolor if bgcolor is not None else cls.surface_variant_color(),
            border=ft.border.all(1, border_color or cls.border_subtle_color()),
        )

    @classmethod
    def section_card(cls, header_content, body_content, padding=24, border_radius=None):
        return cls.card(
            content=ft.Column(
                [
                    header_content,
                    ft.Divider(
                        height=1,
                        color=cls.border_subtle_color(),
                        margin=ft.margin.only(bottom=16, top=12),
                    ),
                    body_content,
                ],
                spacing=0,
            ),
            padding=padding,
            border_radius=border_radius,
        )

    # ── Stat / Metric Card ───────────────────────────────────────────────────

    @classmethod
    def stat_card(cls, label, value, icon, value_color=None, sub_text=None):
        vc = value_color or cls.accent_color()
        inner = ft.Column(
            [
                ft.Icon(icon, size=18, color=ft.Colors.ON_SURFACE_VARIANT),
                ft.Text(value, size=22, weight=ft.FontWeight.W_700, color=vc),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=6,
        )
        if sub_text:
            inner.controls.append(ft.Text(sub_text, size=10, color=cls.text_muted_color()))
        return ft.Container(
            content=inner,
            padding=ft.padding.symmetric(horizontal=20, vertical=14),
            border_radius=12,
            bgcolor=cls.surface_variant_color(),
            border=ft.border.all(1, cls.border_subtle_color()),
        )

    # ── Buttons ──────────────────────────────────────────────────────────────

    @classmethod
    def accent_button(cls, text, icon, on_click, disabled=False, width=450, height=65):
        return ft.ElevatedButton(
            content=ft.Row(
                [
                    ft.Icon(icon, size=22),
                    ft.Text(text, size=15, weight=ft.FontWeight.BOLD),
                ],
                alignment=ft.MainAxisAlignment.CENTER,
                spacing=12,
            ),
            style=ft.ButtonStyle(
                color="white",
                bgcolor=dict(
                    [("", cls.PRIMARY), ("hovered", cls.PRIMARY_HOVER), ("disabled", "white10")]
                ),
                shape=ft.RoundedRectangleBorder(radius=cls.BTN_RADIUS),
                elevation=dict([("hovered", 8), ("", 2)]),
                animation_duration=300,
                padding=ft.padding.symmetric(horizontal=28, vertical=10),
            ),
            height=height,
            width=width,
            disabled=disabled,
            on_click=on_click,
        )

    @classmethod
    def ghost_button(cls, text, icon=None, on_click=None, width=None, height=36):
        ic = ft.Icon(icon, size=16, color=cls.accent_text_color()) if icon else None
        content = ft.Row(
            [ic, ft.Text(text, size=13, color=cls.accent_text_color())]
            if ic
            else [ft.Text(text, size=13, color=cls.accent_text_color())],
            spacing=6,
            alignment=ft.MainAxisAlignment.CENTER,
        )
        return ft.ElevatedButton(
            content=content,
            style=ft.ButtonStyle(
                color=cls.accent_text_color(),
                bgcolor=dict(
                    [("hovered", cls.with_opacity(0.12, cls.accent_color())), ("", "transparent")]
                ),
                shape=ft.RoundedRectangleBorder(radius=12),
                elevation=dict([("hovered", 4), ("", 0)]),
                padding=ft.padding.symmetric(horizontal=16, vertical=4),
            ),
            height=height,
            width=width,
            on_click=on_click,
        )

    # ── File Status Row ──────────────────────────────────────────────────────

    @classmethod
    def file_status_row(cls, label, value_text, value_color, clear_btn=None, icon=None):
        left = ft.Row(
            [
                ft.Icon(icon or ft.Icons.CHECK_CIRCLE_OUTLINED, size=14, color=value_color)
                if value_text != "Ninguno"
                else ft.Icon(ft.Icons.UPLOAD_FILE_OUTLINED, size=14, color=cls.text_muted_color()),
                ft.Text(label, size=12, color=ft.Colors.ON_SURFACE_VARIANT),
            ],
            spacing=6,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        right = ft.Row(
            [
                ft.Text(value_text, size=12, color=value_color),
                clear_btn or ft.Container(width=24),
            ],
            spacing=4,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            expand=True,
        )
        return ft.Row([left, right], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)

    # ── Form Fields ──────────────────────────────────────────────────────────

    @classmethod
    def styled_textfield(
        cls,
        label,
        hint_text="",
        prefix_icon=None,
        expand=True,
        width=None,
        on_change=None,
        multiline=False,
        min_lines=1,
        max_lines=1,
        keyboard_type=None,
    ):
        kw = {}
        if multiline:
            kw.update(multiline=True, min_lines=min_lines, max_lines=max_lines)
        if keyboard_type:
            kw["keyboard_type"] = keyboard_type
        return ft.TextField(
            label=label,
            hint_text=hint_text,
            prefix_icon=prefix_icon,
            border_radius=12,
            bgcolor=(
                ft.Colors.with_opacity(0.06, ft.Colors.WHITE)
                if cls._is_dark
                else ft.Colors.with_opacity(0.04, ft.Colors.BLACK)
            ),
            border=ft.border.outline(color=cls.border_subtle_color()),
            text_size=13,
            expand=expand,
            width=width,
            on_change=on_change,
            **kw,
        )

    # ── Badges ───────────────────────────────────────────────────────────────

    @classmethod
    def badge(cls, text, accent=False, color=None, bg=None):
        bg_c = bg or (cls.primary_color() if accent else cls.surface_variant_color())
        txt_c = color or ("white" if (accent or cls._is_dark) else "#1a2333")
        return ft.Container(
            content=ft.Text(text, size=10, weight=ft.FontWeight.W_700, color=txt_c),
            padding=ft.padding.symmetric(horizontal=10, vertical=6),
            bgcolor=bg_c,
            border_radius=6,
        )

    # ── File Upload Card (clickable) ─────────────────────────────────────────

    @classmethod
    def file_status_card(cls, title, icon, on_tap=None):
        card = ft.Container(
            content=ft.Column(
                [
                    ft.Icon(icon, size=32, color=cls.text_muted_color()),
                    ft.Text(title, size=13, weight=ft.FontWeight.W_600, color=ft.Colors.ON_SURFACE),
                    ft.Text("Click para seleccionar", size=12, color=cls.text_muted_color()),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=8,
            ),
            padding=20,
            bgcolor=cls.surface_color(),
            border_radius=cls.CARD_RADIUS,
            expand=True,
            border=ft.border.all(1, cls.border_color()),
            animate=ft.Animation(300, ft.AnimationCurve.DECELERATE),
            shadow=ft.BoxShadow(
                spread_radius=0,
                blur_radius=15,
                color=cls.SHADOW_COLOR,
                blur_style=ft.ShadowBlurStyle.OUTER,
            ),
        )
        if on_tap:
            return ft.GestureDetector(
                content=card, on_tap=on_tap, mouse_cursor=ft.MouseCursor.CLICK
            )
        return card

    # ── Horizontal Rule ─────────────────────────────────────────────────────

    @classmethod
    def hr(cls, color=None):
        return ft.Divider(height=1, color=color or cls.border_subtle_color())

    # ── Empty State ──────────────────────────────────────────────────────────

    @classmethod
    def empty_state(cls, icon, text, sub_text=None):
        inner = ft.Column(
            [
                ft.Icon(icon, size=48, color=cls.text_muted_color()),
                ft.Text(
                    text,
                    size=14,
                    color=ft.Colors.ON_SURFACE_VARIANT,
                    text_align=ft.TextAlign.CENTER,
                ),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=8,
        )
        if sub_text:
            inner.controls.append(
                ft.Text(
                    sub_text, size=12, color=cls.text_muted_color(), text_align=ft.TextAlign.CENTER
                )
            )
        return ft.Container(
            content=inner,
            padding=ft.padding.symmetric(vertical=40, horizontal=20),
            border_radius=cls.CARD_RADIUS,
            bgcolor=cls.surface_color(),
            border=ft.border.all(1, cls.border_subtle_color()),
        )

    # ── Theme Propagation ─────────────────────────────────────────────────────

    @classmethod
    def _color_map(cls) -> dict:
        """Mapea colores G360 (dark y light) al valor del tema actual."""
        return {
            cls.SURFACE: cls.surface_color(),
            cls.SURFACE_VARIANT: cls.surface_variant_color(),
            cls.BORDER: cls.border_color(),
            cls.BORDER_SUBTLE: cls.border_subtle_color(),
            cls.TEXT_MUTED: cls.text_muted_color(),
            cls.TEXT_SECONDARY: cls.text_muted_color(),
            ft.Colors.SURFACE: cls.surface_color(),
            ft.Colors.OUTLINE_VARIANT: cls.border_subtle_color(),
            ft.Colors.ON_SURFACE_VARIANT: cls.text_muted_color(),
            cls.ACCENT: cls.accent_color(),
            cls.ACCENT_LIGHT: cls.accent_color(),
            cls.ACCENT_TEXT: cls.accent_text_color(),
            cls.ACCENT_2: cls.accent_2_color(),
            cls.ACCENT_2_BRIGHT: cls.accent_2_color(),
            cls.ACCENT_2_LIGHT: cls.accent_2_color(),
            cls.ACCENT_3: cls.accent_3_color(),
            cls.ACCENT_3_LIGHT: cls.accent_3_color(),
            cls.SUCCESS: cls.ok_color(),
            cls.OK_LIGHT: cls.ok_color(),
            cls.PRIMARY: cls.primary_color(),
            cls.PRIMARY_HOVER: cls.primary_color(),
            cls.WARNING: cls.warning_color(),
            cls.WARNING_LIGHT: cls.warning_color(),
            cls.ERROR: cls.error_color(),
            cls.ERROR_LIGHT: cls.error_color(),
        }

    @classmethod
    def _map_color(cls, color):
        if isinstance(color, str):
            if "," in color:
                base, sep, opacity = color.rpartition(",")
                mapped = cls._color_map().get(base, base)
                if mapped != base:
                    return f"{mapped},{opacity}"
            return cls._color_map().get(color, color)
        return color

    @classmethod
    def _map_border_side(cls, side):
        try:
            if side is not None and hasattr(side, "color"):
                side.color = cls._map_color(side.color)
        except Exception:
            pass

    @classmethod
    def _map_border(cls, border):
        if border is None:
            return
        try:
            if isinstance(border, ft.Border):
                for side in (border.top, border.right, border.bottom, border.left):
                    cls._map_border_side(side)
            elif isinstance(border, ft.BorderSide):
                cls._map_border_side(border)
        except Exception:
            pass

    @classmethod
    def _theme_children(cls, control):
        for attr in ("content", "leading", "title", "trailing", "controls", "actions"):
            try:
                v = getattr(control, attr, None)
                if isinstance(v, ft.Control):
                    yield v
                elif isinstance(v, list):
                    for child in v:
                        if isinstance(child, ft.Control):
                            yield child
            except Exception:
                pass
        try:
            for col in getattr(control, "columns", None) or []:
                yield col
            for row in getattr(control, "rows", None) or []:
                yield row
            for cell in getattr(control, "cells", None) or []:
                yield cell
        except Exception:
            pass

    @classmethod
    def apply_theme(cls, control):
        """Refresca recursivamente los colores G360 de un control y sus hijos,
        propagando el tema actual sin reconstruir el arbol de controles."""
        if control is None:
            return
        try:
            bg = getattr(control, "bgcolor", None)
            mapped = cls._map_color(bg)
            if mapped != bg:
                try:
                    control.bgcolor = mapped
                except Exception:
                    pass
            for attr in ("color", "icon_color"):
                val = getattr(control, attr, None)
                mapped = cls._map_color(val)
                if mapped != val:
                    try:
                        setattr(control, attr, mapped)
                    except Exception:
                        pass
            cls._map_border(getattr(control, "border", None))
            for b_attr in ("horizontal_lines", "vertical_lines"):
                cls._map_border(getattr(control, b_attr, None))
        except Exception:
            pass
        for child in cls._theme_children(control):
            cls.apply_theme(child)
