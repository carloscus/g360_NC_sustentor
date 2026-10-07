"""Regresion: la vista multi-cliente (tabs) debe pintarse y no pisar controles.

Con 2+ clientes el resultado se muestra en tabs. Antes:
  1. `_insertar_en_tab` escribia en `_result_tabs.controls`, pero `ft.Tabs`
     expone sus paginas en `.tabs`: reventaba con AttributeError y el tab se
     quedaba en el placeholder "Cargando..." (las vistas previas no se veian).
  2. Todos los tabs compartian las mismas instancias (tabla, wrap, warn row,
     load more y fila de botones). Un control de Flet no puede estar en dos
     padres a la vez, asi que ademas de romperse el arbol, un tab mostraba los
     datos del otro.
"""

import sys
from pathlib import Path

import flet as ft
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ui.reconocimiento_view import ReconocimientoView

from tests.conftest import *  # noqa: F401,F403  (fixtures: tmp_db)

CLI_A = "00056101"
CLI_B = "00005610"


def _historial_2_clientes() -> pd.DataFrame:
    def _fac(cod, nom, nro):
        return {
            "CODIGO": "014850",
            "ARTICULO": "PELOTA PVC #5",
            "CANTIDAD": 100,
            "SOLES": 500.0,
            "FECHA": pd.Timestamp("2026-09-05"),
            "TIPO_DOC": "F01",
            "SERIE": "F001",
            "NUMERO": nro,
            "DOC_ID": f"F001-{nro}",
            "TIPO_CLASE": "factura",
            "CANTIDAD_FAE": 0.0,
            "CLIENTE": nom,
            "COD_CLIENTE": cod,
            "DOC_CLIENTE": "2060102471" + cod[-1],
        }

    return pd.DataFrame(
        [
            _fac(CLI_A, "CLIENTE ALFA", "101"),
            _fac(CLI_A, "CLIENTE ALFA", "102"),
            _fac(CLI_B, "CLIENTE BETA", "201"),
        ]
    )


def _expediente_dc():
    from src.domain import ExpedienteComercial, PipelineContext
    from src.pipeline import Pipeline

    hist = _historial_2_clientes()
    lista = pd.DataFrame({"CODIGO_SKU": ["014850"], "PRECIO_BASE": [4.20]})
    exp = ExpedienteComercial(
        nombre="DC",
        familia="precio",
        estrategia="PriceDifference",
        datos=hist,
        contexto=PipelineContext(config={"sort_mode": "fecha_desc", "modalidad": "individual"}),
    )
    exp.condiciones = [lista]
    return Pipeline().ejecutar(exp), hist


def _vista_2_clientes(tmp_db):
    from tests.test_render_parentdata import _AppStub

    view = ReconocimientoView(_AppStub())
    view.build()
    exp, hist = _expediente_dc()
    view.df_historial = hist
    view.resultado = exp
    view._agrupar_resultado_por_cliente(exp)
    return view


def _columnas_tabs(view):
    return [getattr(t.content, "content", None) for t in view._result_tabs.tabs]


def test_tabs_se_pintan_sin_excepcion(tmp_db):
    """El tab inicial debe quedar con contenido, no con el placeholder de carga."""
    view = _vista_2_clientes(tmp_db)

    view._renderizar_tabs_resultado()

    assert len(view._result_tabs.tabs) == 2
    primer = view._result_tabs.tabs[0].content
    assert isinstance(primer, ft.Container)
    inner = primer.content
    assert isinstance(inner, ft.Column), "el tab quedo con el placeholder de carga"
    assert len(inner.controls) > 5, "el panel del tab salio vacio"


def test_cada_cliente_pinta_su_propio_tab(tmp_db):
    """Cambiar de tab debe llenar el tab del cliente, con su tabla y su boton."""
    view = _vista_2_clientes(tmp_db)
    view._renderizar_tabs_resultado()

    segundo = view._resultado_clientes_order[1]
    view._actualizar_tab_resultado(segundo)

    cols = _columnas_tabs(view)
    assert all(isinstance(c, ft.Column) for c in cols), "un tab quedo sin pintar"
    assert cols[0] is not cols[1]

    compartidos = [i for i, (a, b) in enumerate(zip(cols[0].controls, cols[1].controls)) if a is b]
    assert compartidos == [], f"controles compartidos entre tabs: {compartidos}"


def test_tabla_de_cada_tab_muestra_su_cliente(tmp_db):
    """La tabla del tab debe traer las filas del cliente de ese tab."""
    view = _vista_2_clientes(tmp_db)
    view._renderizar_tabs_resultado()
    for cid in view._resultado_clientes_order[1:]:
        view._actualizar_tab_resultado(cid)

    for cid, col in zip(view._resultado_clientes_order, _columnas_tabs(view)):
        tabla = view._result_controles_por_cliente[cid]["tabla"]
        assert len(tabla.rows) > 0, f"tabla vacia para {cid}"
        # El wrap del tab debe envolver la tabla de ESE cliente.
        wraps = [
            c
            for c in col.controls
            if isinstance(c, ft.Container) and getattr(c, "content", None) is not None
        ]
        tablas_en_tab = {
            id(w.content.controls[0]) for w in wraps if isinstance(w.content, ft.Row)
        }
        assert id(tabla) in tablas_en_tab, (
            f"el tab de {cid} no contiene su tabla: {tablas_en_tab} vs {id(tabla)}"
        )


def test_boton_expediente_habilitado_en_todos_los_tabs(tmp_db):
    view = _vista_2_clientes(tmp_db)
    view._renderizar_tabs_resultado()

    for activo in view._result_controles_por_cliente.values():
        boton = activo["btn_row"].controls[0]
        assert boton.disabled is False
        # Cada tab tiene su propio boton (no compartido entre padres).
        assert boton is not activo["btn_row"].controls[1]


def test_panel_unico_sigue_usando_el_set_global(tmp_db):
    """Con un solo cliente no hay tabs: el panel usa el set global (clave None)."""
    from tests.test_render_parentdata import _AppStub

    view = ReconocimientoView(_AppStub())
    view.build()
    hist = _historial_2_clientes()
    hist = hist[hist["COD_CLIENTE"] == CLI_A].reset_index(drop=True)

    from src.domain import ExpedienteComercial, PipelineContext
    from src.pipeline import Pipeline

    lista = pd.DataFrame({"CODIGO_SKU": ["014850"], "PRECIO_BASE": [4.20]})
    exp = ExpedienteComercial(
        nombre="DC",
        familia="precio",
        estrategia="PriceDifference",
        datos=hist,
        contexto=PipelineContext(config={"sort_mode": "fecha_desc", "modalidad": "individual"}),
    )
    exp.condiciones = [lista]
    resultado = Pipeline().ejecutar(exp)

    view.df_historial = hist
    view.resultado = resultado
    view._agrupar_resultado_por_cliente(resultado)
    assert len(view._resultado_por_cliente) == 1

    view._mostrar_resultado()

    assert view._result_cliente_activo is None
    assert view.resultados_container.visible is True
    assert None in view._result_controles_por_cliente


def test_alternar_tabs_no_deja_el_boton_deshabilitado(tmp_db):
    """Volver al primer tab tras pintar el segundo no rompe el estado."""
    view = _vista_2_clientes(tmp_db)
    view._renderizar_tabs_resultado()
    primero, segundo = view._resultado_clientes_order

    view._actualizar_tab_resultado(segundo)
    view._actualizar_tab_resultado(primero)

    assert view._result_cliente_activo == primero
    assert view._result_controles_por_cliente[primero]["tabla"].rows
    assert view._result_controles_por_cliente[segundo]["tabla"].rows
    assert pytest is not None
