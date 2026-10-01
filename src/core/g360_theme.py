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
    CARD_RADIUS = 18
    BTN_RADIUS = 12

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
        ac = accent_color or cls.accent_color()
        return ft.Row(
            [
                ft.Icon(icon, size=14, color=ac),
                ft.Text(text, size=11, weight=ft.FontWeight.W_600, color=cls.text_primary_color()),
            ],
            spacing=6,
        )

    @classmethod
    def section_header_sub(cls, icon, text, subtitle="", accent_color=None):
        """Encabezado de sección con subtítulo para identificar cada bloque."""
        col = [cls.section_header(icon, text, accent_color)]
        if subtitle:
            col.append(ft.Text(subtitle, size=10, color=cls.text_muted_color()))
        return ft.Column(col, spacing=2)

    # ── Card Containers ──────────────────────────────────────────────────────

    @classmethod
    def card(cls, content, padding=24, border_radius=None, bgcolor=None, on_hover=None, key=None):
        br = border_radius if border_radius is not None else cls.CARD_RADIUS
        bc = bgcolor if bgcolor is not None else cls.surface_color()
        return ft.Container(
            key=key,
            content=content,
            padding=padding,
            bgcolor=bc,
            border_radius=br,
            border=ft.border.all(1, cls.border_color()),
            animate=ft.Animation(300, ft.AnimationCurve.DECELERATE),
            **(on_hover or {}),
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
                bgcolor={"": cls.PRIMARY, "hovered": cls.PRIMARY_HOVER, "disabled": "white10"},
                shape=ft.RoundedRectangleBorder(radius=cls.BTN_RADIUS),
                elevation={"hovered": 8, "": 2},
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
                bgcolor={"hovered": cls.with_opacity(0.12, cls.accent_color()), "": "transparent"},
                shape=ft.RoundedRectangleBorder(radius=12),
                elevation={"hovered": 4, "": 0},
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
                ft.Text(label, size=11, color=ft.Colors.ON_SURFACE_VARIANT),
            ],
            spacing=6,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        right = ft.Row(
            [
                ft.Text(value_text, size=11, color=value_color),
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
                    ft.Text("Click para seleccionar", size=11, color=cls.text_muted_color()),
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
                    sub_text, size=11, color=cls.text_muted_color(), text_align=ft.TextAlign.CENTER
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
