"""El paso final nunca se arma con un resultado desfasado.

Hallazgos que estos tests fijan:
- `_agregar_insumo` / `_quitar_insumo` solo llamaban `_verificar_puede_ejecutar`:
  se podia ejecutar, cambiar la lista de precios y generar el expediente con
  el calculo viejo (mas los antecedentes que se acababan de editar).
- El mismo hueco existia al quitar factura/pedido/O/C y al cambiar vendedor:
  `_invalidar_preview_busqueda` limpia el preview de BUSQUEDA, no el del
  RESULTADO ejecutado.
- Habia un boton 'Generar expediente' por tab de cliente y todos generaban lo
  mismo (`_tarea_generar_expediente` usa el `self.resultado` completo).
"""

from __future__ import annotations

import sys
from pathlib import Path

import flet as ft
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ui.reconocimiento_view import ReconocimientoView
from src.ui.view_handlers import _ViewHandlers

from tests.conftest import *  # noqa: F401,F403
from tests.test_render_parentdata import _AppStub


class TestGuardDeFirma:
    def test_sin_ejecucion_no_hay_que_comparar(self):
        view = ReconocimientoView(_AppStub())
        view.build()
        assert view._expediente_desfasado() is False

    def test_la_firma_cambia_al_tocar_las_entradas(self):
        view = ReconocimientoView(_AppStub())
        view.build()
        view._marcar_firma_ejecucion()
        assert view._expediente_desfasado() is False

        # Agregar un insumo es un cambio de entradas.
        view._insumo_files = [{"ruta": "C:/tmp/Lista_de_Precios.xlsx"}]
        assert view._expediente_desfasado() is True

    def test_cambiar_la_seleccion_tambien_altera_la_firma(self):
        view = ReconocimientoView(_AppStub())
        view.build()
        view._marcar_firma_ejecucion()
        view._sel_facturas = [("00056101", "F01-204-67375")]
        assert view._expediente_desfasado() is True

    def test_la_firma_es_estable_si_no_cambia_nada(self):
        view = ReconocimientoView(_AppStub())
        view.build()
        view._marcar_firma_ejecucion()
        primera = view._firma_entradas()
        assert view._firma_entradas() == primera
        assert view._expediente_desfasado() is False

    def test_el_guard_corta_la_generacion(self, monkeypatch):
        """Si las entradas cambiaron, no se escribe: se avisa y se corta."""
        app = _AppStub()
        avisos = []
        app.show_snackbar = lambda msg, *a, **k: avisos.append(msg)
        app.show_loading = lambda *a, **k: None
        view = ReconocimientoView(app)
        view.build()

        class _Res:
            resultado = object()

        view.resultado = _Res()
        view._marcar_firma_ejecucion()
        view._insumo_files = [{"ruta": "C:/tmp/otra.xlsx"}]

        llamado = []
        view._tarea_generar_expediente = lambda: llamado.append(1)
        view._generar_expediente(None)

        assert llamado == [], "no debe generar con un resultado desfasado"
        assert avisos and "vuelve a ejecutar" in avisos[0].lower()


class TestInvalidacionPorConfig:
    def test_agregar_insumo_invalida_el_resultado(self):
        view = ReconocimientoView(_AppStub())
        view.build()

        class _Res:
            resultado = object()

        view.resultado = _Res()
        view._agregar_insumo("C:/tmp/Lista_de_Precios.xlsx")
        assert view.resultado is None, "el preview debe caerse al cambiar insumos"

    def test_quitar_insumo_invalida_el_resultado(self):
        view = ReconocimientoView(_AppStub())
        view.build()

        class _Res:
            resultado = object()

        view.resultado = _Res()
        view._insumo_files = [{"ruta": "C:/tmp/Lista_de_Precios.xlsx"}]
        view._quitar_insumo(None, "C:/tmp/Lista_de_Precios.xlsx")
        assert view.resultado is None

    def test_invalidar_tambien_bloquea_el_paso_final(self):
        view = ReconocimientoView(_AppStub())
        view.build()
        view.btn_gen_exp.disabled = False
        view._invalidar_resultado_por_config("cambió algo")
        assert view.btn_gen_exp.disabled is True
        assert view._firma_ejecucion is None


class TestPasoFinalUnico:
    def test_la_barra_sticky_tiene_los_dos_pasos_en_orden(self):
        view = ReconocimientoView(_AppStub())
        view.build()
        fila = view.container.content.controls[1].content
        controles = fila.controls
        assert controles.index(view.btn_ejecutar) < controles.index(view.btn_gen_exp)

    def test_el_boton_final_arranca_deshabilitado(self):
        view = ReconocimientoView(_AppStub())
        view.build()
        assert view.btn_gen_exp.disabled is True

    def test_la_propiedad_apunta_al_boton_global(self):
        view = ReconocimientoView(_AppStub())
        view.build()
        assert view.btn_expediente is view.btn_gen_exp
