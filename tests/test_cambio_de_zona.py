"""Cambio de zona (Sustento <-> Reporte) sin perder la barra de acciones.

Hallazgos que fijan estos tests:
- `mostrar_sustento` montaba `_workflow_scroll` (el scroll suelto) en vez de
  la raiz completa: al volver de la zona Reporte los botones de la barra
  sticky (Ejecutar / Generar expediente) desaparecian y solo volvian al
  cambiar el tipo de caso.
- Las raices NO deben llevar `expand`: el contenedor de la vista ya es un
  Column scrolleable (ver `ReconocimientoView.build`), asi que un hijo con
  expand=1 dentro no tiene altura que expandir y la zona no repinta.
"""

from __future__ import annotations

import sys
from pathlib import Path

import flet as ft

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ui.reconocimiento_view import ReconocimientoView

from tests.test_render_parentdata import _AppStub


def _vista():
    app = _AppStub()
    app.page = None
    view = ReconocimientoView(app)
    view.build()
    return view


class TestCambioDeZona:
    def test_ir_a_reporte_monta_el_raiz_de_reporte(self):
        view = _vista()
        assert view.ir_a_reporte() is True
        assert view._vista_actual == "reporte"
        assert view.container.content is view._raiz_reporte()

    def test_volver_al_sustento_monta_la_raiz_completa(self):
        """El bug: montaba el scroll suelto y la barra sticky se caia."""
        view = _vista()
        view.ir_a_reporte()
        assert view.mostrar_sustento() is True
        assert view.container.content is view._raiz_sustento
        assert view._sticky_bar in view.container.content.controls

    def test_alternar_zonas_no_pierde_los_botones(self):
        view = _vista()
        for _ in range(3):
            view.ir_a_reporte()
            view.mostrar_sustento()
            assert view.btn_ejecutar in view._sticky_bar.content.controls
            assert view.btn_gen_exp in view._sticky_bar.content.controls

    def test_las_raices_no_llevan_expand(self):
        """Con expand dentro del Column scrolleable de la vista no hay altura
        que expandir y la zona no repinta."""
        view = _vista()
        assert view._raiz_sustento.expand is not True
        assert view._raiz_reporte().expand is not True

    def test_el_raiz_de_reporte_se_cachea(self):
        """Un control de Flet no puede estar en dos padres: la raiz se
        construye una vez y se reutiliza."""
        view = _vista()
        primera = view._raiz_reporte()
        assert view._raiz_reporte() is primera


class TestCambioDeTipo:
    def _cambiar(self, view, cod):
        view.tipo_dropdown.value = cod
        try:
            view._on_tipo_change(None)
        except AssertionError:
            pass  # stub sin page real: el estado ya quedo aplicado

    def test_cambiar_tipo_mantiene_la_barra(self):
        view = _vista()
        for cod in ("DO", "ANF", "DF", "DC"):
            self._cambiar(view, cod)
            assert view.tipo_actual == cod
            assert view._sticky_bar in view.container.content.controls, cod

    def test_cambiar_tipo_en_reporte_no_saca_de_la_zona(self):
        view = _vista()
        view.ir_a_reporte()
        self._cambiar(view, "PROM")
        assert view._vista_actual == "reporte"
        assert view.container.content is view._raiz_reporte()

    def test_volver_despues_de_cambiar_tipo_en_reporte(self):
        view = _vista()
        view.ir_a_reporte()
        self._cambiar(view, "PROM")
        view.mostrar_sustento()
        assert view._vista_actual == "sustento"
        assert view.tipo_actual == "PROM"
        assert view._sticky_bar in view.container.content.controls
