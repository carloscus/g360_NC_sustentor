"""Fábrica de controles de filtro compartidos entre secciones.

Compras (sección independiente) y Búsqueda (generador de sustento) usan los
MISMOS estilos para fechas, vendedor y búsqueda de cliente: aunque el dato se
repita, la simetría visual mantiene la app coherente y cada sección se lee
igual.
"""

from __future__ import annotations

import flet as ft

from src.core.g360_theme import G360Theme
from src.core.utils import cliente_visible

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

# Fondo de los inputs: el tono hundido de la escala de superficies. Asi el
# campo se lee como un pozo recessed sobre la card (tono base) y se distingue
# de los chips/tiles (tono elevado, surface_variant). Dropdown y text_field
# comparten el mismo tono a proposito: son el mismo tipo de control editable,
# y separarlos seria volver a la duplicacion de estilos que esta fabrica
# elimino.
COLOR_INPUT = G360Theme.surface_sunken_color()
BORDER_INPUT = ft.border.all(1, G360Theme.border_subtle_color())

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
        bgcolor=COLOR_INPUT,
        border=BORDER_INPUT,
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
        bgcolor=COLOR_INPUT,
        border=BORDER_INPUT,
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


# ── Esqueleto de filtros ────────────────────────────────────────────────────
# Orden de lectura fijo: uso cotidiano primero, criterios específicos después,
# y por último cómo se empaqueta el resultado. Antes los controles de las dos
# zonas vivían apilados sin jerarquía y todo competía por la atención.
def filters_block(
    *,
    principales=(),
    complementarios=None,
    salida=None,
    principales_header="Filtros principales",
    complementarios_header="Filtros complementarios",
    salida_header="Opciones de salida",
    principales_icon=ft.Icons.TUNE_OUTLINED,
    complementarios_icon=ft.Icons.FILTER_ALT_OUTLINED,
    salida_icon=ft.Icons.LAYERS_OUTLINED,
) -> ft.Column:
    """Columna de filtros en bloques: principales / complementarios / salida.

    Los bloques son opcionales salvo `principales`: una zona que no tiene
    "salida" (el flujo de sustento: sus documentos son ENTRADA, acotan el
    historial) simplemente no la pasa y el esqueleto no deja hueco.

    Comparte la FORMA, no los controles: cada zona pasa los suyos porque el
    significado de "SKU" no es el mismo en las dos (filtro de vista previa
    en Reporte, insumo del cálculo en Sustento).

    Cada bloque lleva su propio ícono: con el mismo tunable repetido tres
    veces el ícono deja de informar y solo compite con el texto del header.
    """
    bloques = []

    def _add(controls, header, icon):
        items = [c for c in (controls or ()) if c is not None]
        if not items:
            return
        bloques.append(G360Theme.section_header(icon, header))
        bloques.append(
            ft.Column(items, spacing=G360Theme.SPACE_SM, tight=True) if len(items) > 1 else items[0]
        )

    _add(principales, principales_header, principales_icon)
    _add(complementarios, complementarios_header, complementarios_icon)
    _add(salida, salida_header, salida_icon)

    return ft.Column(bloques, spacing=G360Theme.SPACE_MD, tight=True)


# ── Checkboxes ────────────────────────────────────────────────────────────
# Antes cada checkbox se construia a mano: el color activo era azul, verde o
# default de Flet segun la pantalla, y el label iba en 12 o 14 (default sin
# label_style) entre controles vecinos del mismo panel. Aqui sale unificado.
def checkbox(
    label: str,
    *,
    value: bool = False,
    disabled: bool = False,
    on_change=None,
    active_color: str | None = None,
    expand: bool = False,
    tooltip: str | None = None,
    **extra,
):
    """Checkbox con label TYPE_BODY (12) y active_color del acento global.

    Excepción documentada: la columna "Calcular" del histórico usa SUCCESS
    (verde) porque es un semántico propio (entra en el cálculo), no una
    selección de pantalla.
    """
    kwargs = dict(
        label=label,
        value=value,
        disabled=disabled,
        on_change=on_change,
        label_style=ft.TextStyle(size=G360Theme.TYPE_BODY),
        active_color=active_color or G360Theme.accent_color(),
        expand=expand,
        tooltip=tooltip,
    )
    kwargs.update(extra)
    return ft.Checkbox(**kwargs)


# ── Chips ─────────────────────────────────────────────────────────────────
# El chip de cliente viaja entre secciones (la Búsqueda lo suma al Reporte),
# asi que debe verse IGUAL en ambas: un solo bgcolor (0.12 sobre el acento de
# seccion, que ya alterna dark/light). Antes Reporte usaba 0.12/ACCENT_2_BRIGHT
# y Búsqueda 0.1/ACCENT_2: mismo control, dos tonos.
CHIP_PAD = ft.padding.symmetric(horizontal=8, vertical=2)

# Colores semanticos de los chips de documento (pedido/O/C/factura). No son
# decoracion: el color identifica el tipo de documento sin leer el label.
_CHIP_DOC_BG = {
    "pedido": G360Theme.ACCENT_3,
    "oc": G360Theme.SUCCESS,
    "factura": G360Theme.WARNING,
}
_CHIP_DOC_PREFIJO = {"pedido": "Pedido · ", "oc": "O/C · ", "factura": ""}


def chip_cliente(cid: str, nom: str, on_delete=None) -> ft.Chip:
    """Chip de cliente con el estilo único de la app (reporte y búsqueda)."""
    texto = f"{nom[:28]} ({cliente_visible(cid)})" if nom else cid
    return ft.Chip(
        label=ft.Text(texto, size=G360Theme.TYPE_SUBTITLE),
        on_delete=on_delete,
        delete_icon_color=G360Theme.error_color(),
        bgcolor=ft.Colors.with_opacity(0.12, G360Theme.section_accent_color()),
        padding=CHIP_PAD,
    )


def chip_documento(tipo: str, valor: str, on_delete=None) -> ft.Chip:
    """Chip de documento (pedido | oc | factura) con su color semántico."""
    return ft.Chip(
        label=ft.Text(f"{_CHIP_DOC_PREFIJO[tipo]}{valor}", size=G360Theme.TYPE_SUBTITLE),
        on_delete=on_delete,
        delete_icon_color=G360Theme.error_color(),
        bgcolor=ft.Colors.with_opacity(0.1, _CHIP_DOC_BG[tipo]),
        padding=CHIP_PAD,
    )
