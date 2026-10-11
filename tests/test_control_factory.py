"""Simetría de controles de entrada: el helper único y su uso efectivo.

Hallazgos del critique que estos tests fijan:
- El checkbox marcado aparecía en azul, verde o default de Flet según la
  pantalla, y su label iba en 12 o 14 (default sin label_style) entre
  controles vecinos del mismo panel.
- El chip de cliente viajaba entre Búsqueda y Reporte con dos bgcolor
  distintos (0.12/acento de sección vs 0.1/ACCENT_2).
- El único Switch de la app convivía con checkboxes para el mismo
  semántico "incluir/calcular NC".
- Siete dropdowns/textfields se construían a mano, fuera de la factory, con
  cajas distintas (altura, radio, padding).
"""

from __future__ import annotations

import flet as ft

from src.core.g360_theme import G360Theme
from src.ui.widgets import control_factory as cf


class TestHelperCheckbox:
    def test_label_en_type_body_y_acento_global(self):
        cb = cf.checkbox("Ocultar diferencias 0.00", value=True)
        assert cb.label == "Ocultar diferencias 0.00"
        assert cb.label_style.size == G360Theme.TYPE_BODY
        assert cb.active_color == G360Theme.accent_color()
        assert cb.value is True

    def test_activo_por_excepcion_documentada(self):
        """La columna Calcular usa SUCCESS: es semántico, no excepción de estilo."""
        cb = cf.checkbox("Usar", active_color=G360Theme.SUCCESS)
        assert cb.active_color == G360Theme.SUCCESS

    def test_pasa_parametros_extra(self):
        cb = cf.checkbox("Linea", data="L01", disabled=True)
        assert cb.data == "L01"
        assert cb.disabled is True

    def test_tooltip_para_el_estado_disabled(self):
        cb = cf.checkbox("Usar", disabled=True, tooltip="No aplica a este caso")
        assert cb.tooltip == "No aplica a este caso"


class TestHelperChips:
    def test_chip_cliente_un_solo_tono(self):
        """El chip que viaja entre secciones debe ser el mismo objeto de estilo."""
        chip = cf.chip_cliente("00068414", "CLIENTE ALFA S.A.")
        assert chip.bgcolor == ft.Colors.with_opacity(0.12, G360Theme.section_accent_color())
        assert chip.padding == cf.CHIP_PAD
        assert chip.label.size == G360Theme.TYPE_SUBTITLE
        assert "68414" in chip.label.value

    def test_chip_cliente_sin_nombre_muestra_la_clave(self):
        chip = cf.chip_cliente("00068414", "")
        assert chip.label.value == "00068414"

    def test_chip_documento_color_por_tipo(self):
        assert cf.chip_documento("pedido", "P-1").bgcolor == ft.Colors.with_opacity(
            0.1, G360Theme.ACCENT_3
        )
        assert cf.chip_documento("oc", "OC-9").bgcolor == ft.Colors.with_opacity(
            0.1, G360Theme.SUCCESS
        )
        assert cf.chip_documento("factura", "F01-204-1").bgcolor == ft.Colors.with_opacity(
            0.1, G360Theme.WARNING
        )

    def test_chip_documento_prefija_el_tipo(self):
        assert cf.chip_documento("pedido", "P-1").label.value == "Pedido · P-1"
        assert cf.chip_documento("oc", "OC-9").label.value == "O/C · OC-9"
        # La factura ya trae su propio identificador: no se prefija.
        assert cf.chip_documento("factura", "F01-204-1").label.value == "F01-204-1"


class TestFactoryCajaUnica:
    def test_dropdown_y_text_field_comparten_caja(self):
        dd = cf.dropdown("Vendedor", width=cf.WIDTH_FILTER)
        tf = cf.text_field("Ruta", width=cf.WIDTH_FILTER)
        for c in (dd, tf):
            assert c.border_radius == cf.RADIUS
            assert c.content_padding == cf.CONTENT_PADDING
            assert c.text_size == cf.TEXT_SIZE
            assert c.dense is True
        assert cf.text_field("Meta").height == cf.HEIGHT

    def test_text_field_acepta_extra(self):
        tf = cf.text_field("Contraseña", password=True, can_reveal_password=True)
        assert tf.password is True
        assert tf.can_reveal_password is True


class TestEscalaTonal:
    """Tono hundido en los inputs: se distinguen de la card y de los chips.

    Escala: card (surface) > chip/tile (variant) > input (sunken). El input se
    lee como un pozo recessed sobre la card, y dropdown y text_field comparten
    tono porque son el mismo tipo de control editable.
    """

    def test_input_en_tono_hundido(self):
        assert cf.COLOR_INPUT == G360Theme.surface_sunken_color()

    def test_dropdown_y_text_field_mismo_tono(self):
        dd = cf.dropdown("Vendedor")
        tf = cf.text_field("Ruta")
        assert dd.bgcolor == tf.bgcolor == cf.COLOR_INPUT
        assert cf.BORDER_INPUT.top.width == 1

    def test_el_tono_difiere_de_la_card_y_del_chip(self):
        card = G360Theme.surface_color()
        chip = G360Theme.surface_variant_color()
        assert cf.COLOR_INPUT != card, "el input debe leerse hundido sobre la card"
        assert cf.COLOR_INPUT != chip, "y distinto del tono elevado de chips/tiles"

    def test_la_escala_va_de_hundido_a_elevado(self):
        assert cf.COLOR_INPUT != G360Theme.surface_color()
        assert G360Theme.surface_color() != G360Theme.surface_variant_color()


class TestFiltersBlock:
    """Bloques de filtros: comparten la FORMA entre zonas, no los controles.

    El helper existe para que Reporte y Sustento se lean igual sin obligar a
    Sustento a tener una "salida" que en realidad no existe: alla los
    documentos son ENTRADA, acotan el historial.
    """

    def test_bloques_en_orden_y_con_headers(self):
        dd, tf, nc = cf.dropdown("Vendedor"), cf.text_field("SKU"), cf.checkbox("Generar NC")
        bloque = cf.filters_block(
            principales=[ft.Row([dd])],
            complementarios=[ft.Row([tf])],
            salida=[nc, ft.Text("Hojas")],
            principales_header="Uso cotidiano",
            complementarios_header="Criterio",
            salida_header="Empaque",
        )
        # 3 bloques x (header, contenido): el contenido de `salida` con 2
        # controles va en Column.
        assert len(bloque.controls) == 6
        assert [bloque.controls[i].controls[1].value for i in (0, 2, 4)] == [
            "Uso cotidiano",
            "Criterio",
            "Empaque",
        ]

    def test_bloque_unico_no_se_envuelve(self):
        """Un bloque de un solo control no necesita una Column alrededor."""
        dd = cf.dropdown("Vendedor")
        bloque = cf.filters_block(principales=[dd])
        assert len(bloque.controls) == 2
        assert bloque.controls[0].controls[1].value == "Filtros principales"
        assert bloque.controls[1] is dd, "un control suelto no se envuelve en Column"

    def test_bloques_opcionales_se_omiten_sin_hueco(self):
        """Sin salida ni complementarios no queda un header sin contenido."""
        dd = cf.dropdown("Vendedor")
        bloque = cf.filters_block(principales=[ft.Row([dd])], salida=None, complementarios=None)
        assert len(bloque.controls) == 2  # solo el bloque principal

    def test_none_en_lista_se_ignora(self):
        dd = cf.dropdown("Vendedor")
        bloque = cf.filters_block(principales=[ft.Row([dd]), None])
        assert len(bloque.controls) == 2

    def test_principales_vacio_devuelve_column_vacia(self):
        """Sin principales tampoco debe reventar: solo una columna vacia."""
        bloque = cf.filters_block(principales=[])
        assert isinstance(bloque, ft.Column)
        assert bloque.controls == []

    def test_cada_bloque_tiene_icone_propio(self):
        """Tres tunables iguales repetidos no informan: compiten con el texto."""
        dd = cf.dropdown("Vendedor")
        bloque = cf.filters_block(
            principales=[ft.Row([dd])],
            complementarios=[ft.Text("SKU")],
            salida=[ft.Text("NC")],
        )
        icons = [
            c.controls[0].name for c in (bloque.controls[0], bloque.controls[2], bloque.controls[4])
        ]
        assert icons == [
            ft.Icons.TUNE_OUTLINED,
            ft.Icons.FILTER_ALT_OUTLINED,
            ft.Icons.LAYERS_OUTLINED,
        ]
        assert len(set(icons)) == 3, "el icono repetido deja de informar"


class TestTokens:
    def test_escala_jerarquica_de_titulos(self):
        """H2 de sección (18) > H3 de bloque (14) > cuerpo (12) > bajada (11)."""
        assert G360Theme.TYPE_H2 == 18
        assert G360Theme.TYPE_H3 == 14
        assert G360Theme.TYPE_H2 > G360Theme.TYPE_TITLE > G360Theme.TYPE_BODY
        assert G360Theme.TYPE_BODY > G360Theme.TYPE_SUBTITLE

    def test_radio_de_card_en_la_escala(self):
        assert G360Theme.RADIUS_CHIP < G360Theme.RADIUS_CONTROL < G360Theme.RADIUS_CARD
