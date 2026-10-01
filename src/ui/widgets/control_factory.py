"""Fábrica de controles de filtro compartidos entre secciones.

Compras (sección independiente) y Búsqueda (generador de sustento) usan los
MISMOS estilos para fechas, vendedor y búsqueda de cliente: aunque el dato se
repita, la simetría visual mantiene la app coherente y cada sección se lee
igual.
"""

from __future__ import annotations

import flet as ft

from src.core.g360_theme import G360Theme

RADIUS = 12
TEXT_SIZE = 13


def dropdown(
    label,
    *,
    icon=None,
    expand=False,
    width=None,
    hint=None,
    search=False,
    editable=False,
    on_change=None,
    menu_height=320,
):
    """Dropdown de filtro con el estilo único de la app (fechas/filtros)."""
    kwargs = dict(
        label=label,
        border_radius=RADIUS,
        dense=True,
        text_size=TEXT_SIZE,
        options=[],
        hint_text=hint or "",
        content_padding=ft.padding.symmetric(horizontal=12, vertical=8),
        menu_height=menu_height,
        expand=expand,
    )
    if width is not None:
        kwargs["width"] = width
    if icon is not None:
        kwargs["leading_icon"] = icon
    if search:
        kwargs["enable_search"] = True
    if editable:
        kwargs["editable"] = True
    if on_change is not None:
        kwargs["on_change"] = on_change
    return ft.Dropdown(**kwargs)


def date_button(on_click, tooltip: str = "Elegir fecha"):
    """Botón compacto de calendario con el mismo acento que el resto."""
    return ft.ElevatedButton(
        content=ft.Icon(ft.Icons.CALENDAR_TODAY_OUTLINED, size=15, color=G360Theme.accent_color()),
        on_click=on_click,
        height=32,
        width=34,
        tooltip=tooltip,
        style=ft.ButtonStyle(
            padding=ft.padding.all(4),
            shape=ft.RoundedRectangleBorder(radius=10),
            bgcolor=ft.Colors.with_opacity(0.10, G360Theme.accent_color()),
        ),
    )


def date_label(text: str, active: bool = True):
    """Etiqueta de fecha: acento cuando hay filtro, gris cuando es 'todas'."""
    return ft.Text(
        text,
        size=11,
        color=G360Theme.accent_text_color() if active else G360Theme.text_muted_color(),
    )


def search_button(text: str, icon, on_click=None, tooltip: str | None = None):
    """Botón de búsqueda (abre picker/modal) con la paleta de acciones."""
    return ft.ElevatedButton(
        text,
        icon=icon,
        height=32,
        on_click=on_click,
        tooltip=tooltip,
        style=ft.ButtonStyle(
            padding=ft.padding.symmetric(horizontal=10),
            bgcolor=ft.Colors.with_opacity(0.12, G360Theme.ACCENT_2),
        ),
    )
