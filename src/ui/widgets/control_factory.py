"""Fábrica de controles de filtro compartidos entre secciones.

Compras (sección independiente) y Búsqueda (generador de sustento) usan los
MISMOS estilos para fechas, vendedor y búsqueda de cliente: aunque el dato se
repita, la simetría visual mantiene la app coherente y cada sección se lee
igual.
"""

from __future__ import annotations

import flet as ft

from src.core.g360_theme import G360Theme

# Estilo y medidas. Antes cada filtro se construia a mano y el mismo control
# aparecia con radios 12 y 15, con y sin `dense`, y con anchos 180/200/250/260/
# 400. La app se leia despareja: dos "Vendedor" con 400px y 260px, el mismo
# dropdown con distinta caja. Ahora todo sale de aca.
RADIUS = G360Theme.RADIUS_CONTROL
TEXT_SIZE = 13

# Anchos. 260 es el ancho de un filtro de la barra (fijado por
# test_geometria_del_card_de_busqueda); 180 el de un par de fechas, que van
# siempre juntos y asi se leen como un grupo.
WIDTH_FILTER = 260
WIDTH_DATE = 180
WIDTH_FIELD = 200

# Padding interno identico para dropdown y text_field: si difiere, las cajas
# tienen distinta altura aunque el radio y el texto coincidan.
CONTENT_PADDING = ft.padding.symmetric(horizontal=12, vertical=8)

# Altura unica de la barra de filtros. Antes los botones iban en 32 y los
# campos no tenian altura fija (la decidia el padding), asi que un "Buscar
# cliente" y el dropdown de al lado no Alineaban: mismo estilo, distinta
# altura. Fijando la misma constante en inputs y botones la fila se alinea por
# construccion.
#
# 38 y no 36: con text_size=13 y padding vertical 8+8 la caja interior pide
# ~37px, y en 36 el texto rozaba el borde (o se recortaba). Medido: 12px -> 35,
# 13px -> 37, 14px -> 38.
HEIGHT = 38


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
    value=None,
):
    """Dropdown de filtro con el estilo único de la app (fechas/filtros)."""
    kwargs = dict(
        label=label,
        border_radius=RADIUS,
        dense=True,
        text_size=TEXT_SIZE,
        options=[],
        hint_text=hint or "",
        content_padding=CONTENT_PADDING,
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
    if value is not None:
        kwargs["value"] = value
    return ft.Dropdown(**kwargs)


def text_field(
    label, *, width=None, expand=False, hint=None, on_change=None, keyboard=None, **extra
):
    """Campo de texto con la MISMA caja que `dropdown`.

    Existia aparte porque los campos de fecha se copiaron a mano: traian
    `border_radius=15` y sin `dense`, asi que un campo de fecha y un dropdown
    de al lado se veian de distinta altura.

    Lo que mas descuadraba: las copias no llevaban `content_padding`, y sin el
    la caja interior es mas baja que la de un dropdown vecino aunque el radio y
    el texto coincidan. Por eso el padding va aqui y no en el llamador.
    """
    kwargs = dict(
        label=label,
        border_radius=RADIUS,
        dense=True,
        text_size=TEXT_SIZE,
        content_padding=CONTENT_PADDING,
        height=HEIGHT,
        expand=expand,
    )
    if width is not None:
        kwargs["width"] = width
    if hint:
        kwargs["hint_text"] = hint
    if on_change is not None:
        kwargs["on_change"] = on_change
    if keyboard is not None:
        kwargs["keyboard_type"] = keyboard
    kwargs.update(extra)
    return ft.TextField(**kwargs)


def date_button(on_click, tooltip: str = "Elegir fecha"):
    """Botón compacto de calendario con el mismo acento que el resto."""
    return ft.ElevatedButton(
        content=ft.Icon(ft.Icons.CALENDAR_TODAY_OUTLINED, size=15, color=G360Theme.accent_color()),
        on_click=on_click,
        height=HEIGHT,
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
        size=12,
        color=G360Theme.accent_text_color() if active else G360Theme.text_muted_color(),
    )


def search_button(text: str, icon, on_click=None, tooltip: str | None = None):
    """Botón de búsqueda (abre picker/modal) con la paleta de acciones."""
    return ft.ElevatedButton(
        text,
        icon=icon,
        height=HEIGHT,
        on_click=on_click,
        tooltip=tooltip,
        style=ft.ButtonStyle(
            padding=ft.padding.symmetric(horizontal=10),
            bgcolor=ft.Colors.with_opacity(0.12, G360Theme.ACCENT_2),
        ),
    )
