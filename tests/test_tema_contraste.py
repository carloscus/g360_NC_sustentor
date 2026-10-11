"""Contraste al cambiar de tema: ningun color puede quedar congelado.

`_color_map` mapea por VALOR EXACTO. Un token hex que no esta en el mapa no
se remapea: el control se queda con el color del tema con el que se
construyo. Asi es como un inputBuilt en dark seguia con fondo #121620 en
tema claro, con el texto de Flet ya en oscuro encima: oscuro sobre oscuro.
"""

from __future__ import annotations

import flet as ft
import pytest

from src.core.g360_theme import G360Theme
from src.ui.widgets import control_factory as cf


@pytest.fixture(autouse=True)
def _restaura_tema():
    """El tema es estado global de clase: ningun test puede dejarlo cambiado."""
    antes = G360Theme._is_dark
    yield
    G360Theme.set_theme_mode(antes)


def _cambiar(control, oscuro: bool):
    """Aplica el tema como lo hace la app (in-place, sin reconstruir)."""
    G360Theme.set_theme_mode(oscuro)
    G360Theme.apply_theme(control)


class TestColorDeInputs:
    def test_el_input_no_queda_con_el_fondo_del_tema_anterior(self):
        """El bug reportado: input built en dark, tema claro, sin contraste."""
        G360Theme.set_theme_mode(True)
        campo = cf.text_field("SKU")
        _cambiar(campo, False)
        assert campo.bgcolor != G360Theme.SURFACE_SUNKEN
        assert campo.bgcolor == G360Theme.surface_sunken_color()

    @pytest.mark.parametrize("campo", ["text_field", "dropdown"])
    def test_text_field_y_dropdown_se_mueven_juntos(self, campo):
        G360Theme.set_theme_mode(True)
        c = getattr(cf, campo)("Vendedor")
        _cambiar(c, False)
        assert c.bgcolor == cf.COLOR_INPUT or c.bgcolor == G360Theme.surface_sunken_color()

    def test_el_tono_hundido_sigue_diferente_de_la_card(self):
        """El fix no puede aplanar la escala: hundido vs superficie."""
        G360Theme.set_theme_mode(False)
        campo = cf.text_field("SKU")
        assert campo.bgcolor != G360Theme.surface_color()


class TestIdaYVuelta:
    """Si el mapa no va en los dos sentidos, volver a dark no restaura."""

    def test_el_input_vuelve_a_su_tono_dark(self):
        G360Theme.set_theme_mode(True)
        campo = cf.text_field("SKU")
        original = campo.bgcolor
        _cambiar(campo, False)
        _cambiar(campo, True)
        assert campo.bgcolor == original

    def test_el_texto_primario_vuelve_a_su_tono_dark(self):
        G360Theme.set_theme_mode(True)
        txt = ft.Text("Hola", color=G360Theme.text_primary_color())
        original = txt.color
        _cambiar(txt, False)
        _cambiar(txt, True)
        assert txt.color == original


class TestTokensNoCongelados:
    def test_ningun_token_de_superficie_o_texto_queda_fuera_del_mapa(self):
        """Guard contra el forgotismo: cada token hex tiene contraparte en el mapa.

        Los tokens que no son de tema (acento fijo, fondo de pagina) quedan
        fuera a proposito: no se usan en controles que se re-mapean.
        """
        fuera_de_alcance = {
            "BG_DARK",
            "BG_LIGHT",
            "ACCENT_4",
        }
        mapa = G360Theme._color_map()
        huerfanos = []
        for nombre in dir(G360Theme):
            if not nombre.isupper() or nombre in fuera_de_alcance:
                continue
            valor = getattr(G360Theme, nombre)
            if isinstance(valor, str) and valor.startswith("#") and valor not in mapa:
                huerfanos.append(nombre)
        assert huerfanos == [], f"tokens sin mapear: {huerfanos}"

    def test_el_fg_del_boton_alterna(self):
        """El texto del boton de acento: oscuro en dark, claro en light."""
        G360Theme.set_theme_mode(True)
        txt = ft.Text("X", color=G360Theme.section_button_fg())
        assert txt.color == G360Theme.SECTION_BTN_FG_DARK
        _cambiar(txt, False)
        assert txt.color == G360Theme.SECTION_BTN_FG_LIGHT

    def test_el_fg_del_boton_no_es_el_literal_white(self):
        """El literal "white" lo usan overlays y botones: mapearlo los rompe."""
        assert G360Theme.SECTION_BTN_FG_LIGHT != "white"

    def test_el_texto_primario_altera(self):
        G360Theme.set_theme_mode(True)
        txt = ft.Text("Hola", color=G360Theme.text_primary_color())
        assert txt.color == G360Theme.TEXT_PRIMARY
        _cambiar(txt, False)
        assert txt.color == G360Theme.TEXT_PRIMARY_LIGHT
