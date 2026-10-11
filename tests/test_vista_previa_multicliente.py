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
    """Resumen de cada tab (baja por el scroll interno del tab).

    Estructura: Tab -> Container -> Column(scroll) -> [resumen Column].
    Si el tab no tiene esa forma (placeholder o aviso), devuelve el contenido
    tal cual para que el test que lo usa falle con mensaje claro.
    """
    cols = []
    for t in view._result_tabs.tabs:
        cont = getattr(t.content, "content", None)
        if (
            isinstance(cont, ft.Column)
            and len(cont.controls) == 1
            and isinstance(cont.controls[0], ft.Column)
        ):
            cols.append(cont.controls[0])
        else:
            cols.append(cont)
    return cols


def test_tabs_se_pintan_sin_excepcion(tmp_db):
    """El tab inicial debe quedar con contenido, no con el placeholder de carga."""
    view = _vista_2_clientes(tmp_db)

    view._renderizar_tabs_resultado()

    assert len(view._result_tabs.tabs) == 2
    primer = view._result_tabs.tabs[0].content
    assert isinstance(primer, ft.Container)
    # Contenedor del tab -> columna con scroll interno -> resumen.
    inner = primer.content
    assert isinstance(inner, ft.Column), "el tab quedo con el placeholder de carga"
    assert inner.scroll is not None, "el tab no tiene scroll interno"
    resumen = inner.controls[0]
    assert isinstance(resumen, ft.Column)
    assert len(resumen.controls) > 5, "el panel del tab salio vacio"


def test_tabs_con_altura_acotada(tmp_db):
    """Los tabs viven en un area de alto fijo.

    ft.Tabs es un viewport y exige alto finito: dentro de la columna con
    scroll de la vista el cliente Flet dejaba el area en blanco sin error
    en Python. El alto fijo + scroll interno lo evita.
    """
    from src.ui.view_handlers import _ALTURA_TABS_RESULTADO

    view = _vista_2_clientes(tmp_db)

    view._renderizar_tabs_resultado()

    area = view._result_tabs_container.controls[1]
    assert isinstance(area, ft.Container)
    assert area.height == _ALTURA_TABS_RESULTADO


def test_sin_header_resultado_duplicado(tmp_db):
    """La card ya titula 'Resultado del reconocimiento': el contenido no repite 'RESULTADO'."""
    view = _vista_2_clientes(tmp_db)

    view._renderizar_tabs_resultado()

    for tab in view._result_tabs.tabs:
        assert "RESULTADO" not in _textos(tab.content)


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
        tablas_en_tab = {id(w.content.controls[0]) for w in wraps if isinstance(w.content, ft.Row)}
        assert id(tabla) in tablas_en_tab, (
            f"el tab de {cid} no contiene su tabla: {tablas_en_tab} vs {id(tabla)}"
        )


def test_boton_expediente_es_unico_y_global(tmp_db):
    """Un solo boton de expediente (barra sticky), no uno por tab.

    Antes cada tab tenia el suyo y TODOS generaban lo mismo: el handler usa
    el `self.resultado` completo y `generar_expediente` separa por cliente.
    Con 3 clientes eran 3 botones identicos.
    """
    view = _vista_2_clientes(tmp_db)
    view._renderizar_tabs_resultado()

    # El paso final es el control global de la barra sticky...
    assert view.btn_expediente is view.btn_gen_exp
    # ...y ningun tab lo lleva (solo la secundaria Auditoria).
    for activo in view._result_controles_por_cliente.values():
        fila = activo["btn_row"]
        assert view.btn_gen_exp not in fila.controls
        assert len(fila.controls) == 1, "solo queda la acción secundaria"

    # Habilitarlo lo habilita una vez, sin depender del tab activo.
    view._set_btn_expediente(False)
    assert view.btn_gen_exp.disabled is True
    view._set_btn_expediente(True)
    assert view.btn_gen_exp.disabled is False


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
    # El panel interno nace visible=False: debe encenderse al pintar, si no
    # la card muestra solo el encabezado y la vista previa nunca se ve.
    assert view._result_panel.visible is True
    assert view._result_panel.content is not None
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


def test_placeholders_de_carga_son_distintos_por_tab(tmp_db):
    """Cada tab nace con su propio placeholder (no un control compartido)."""
    view = _vista_2_clientes(tmp_db)
    tabs = view._construir_tabs(["CLI_A", "CLI_B"])
    assert tabs.tabs[0].content is not tabs.tabs[1].content


def _textos(control):
    out = []
    pila = [control]
    while pila:
        c = pila.pop()
        if isinstance(c, ft.Text) and isinstance(c.value, str):
            out.append(c.value)
        for attr in ("controls", "content"):
            h = getattr(c, attr, None)
            if isinstance(h, ft.Control):
                pila.append(h)
            elif isinstance(h, (list, tuple)):
                pila.extend(x for x in h if isinstance(x, ft.Control))
    return out


def test_tab_sin_vista_muestra_aviso_en_vez_de_cargando(tmp_db):
    """Cliente sin vista (ocultar 0.00 lo vacio): el tab avisa, no queda en Cargando."""
    view = _vista_2_clientes(tmp_db)
    view._renderizar_tabs_resultado()
    view._mostrar_tab_vacio(view._resultado_clientes_order[0], "Sin filas xyz")
    contenido = view._result_tabs.tabs[0].content
    assert "Cargando" not in " ".join(_textos(contenido))
    assert any("Sin filas xyz" in t for t in _textos(contenido))


def test_rearmar_filtra_piezas_none(tmp_db):
    """Un contenido corto (piezas faltantes) no mete None al summary."""
    view = _vista_2_clientes(tmp_db)
    view._rearmar_result_summary({"content": ft.Column([ft.Text("solo")])})
    assert all(isinstance(c, ft.Control) for c in view._result_summary.controls)


def test_render_sin_leyenda_no_deja_none_en_el_arbol():
    """rebate_volumen no arma leyenda: el contenido no debe tener hijos None.

    Un None como hijo revienta la serializacion de Flet: el page.update()
    falla, la pantalla queda intacta y la vista previa se ve vacia.
    """
    from types import SimpleNamespace

    from src.ui.resultados_view import render_resultado

    df = pd.DataFrame(
        [
            {
                "LINEA": "GASEOSAS",
                "SOLES": 1000.0,
                "%_DEL_TOTAL": 50.0,
                "MONTO_NC": 100.0,
                "MONTO_NC_ND": 100.0,
            }
        ]
    )
    res = SimpleNamespace(
        dataframe=df,
        resumen={"total_nc": 100.0, "skus_afectados": 1},
        alertas=[],
        metricas={},
    )
    exp = SimpleNamespace(resultado=res, datos=None, contexto=None)
    out = render_resultado(exp, "rebate_volumen", None, "#ffffff", "#00ff00")
    assert out["content"] is not None
    assert all(isinstance(c, ft.Control) for c in out["content"].controls)


def test_selecciona_primer_tab_con_contenido(tmp_db, monkeypatch):
    """Si el primer tab queda vacio (ocultar 0.00), se selecciona el que si pinta."""
    view = _vista_2_clientes(tmp_db)
    primero, segundo = view._resultado_clientes_order
    real = view._obtener_vista_cliente
    monkeypatch.setattr(
        view, "_obtener_vista_cliente", lambda cid: None if cid == primero else real(cid)
    )

    view._renderizar_tabs_resultado()

    assert view._result_tabs.selected_index == 1
    assert view._result_cliente_activo == segundo
    # El tab vacio avisa con el conteo omitido, no queda en Cargando.
    textos = _textos(view._result_tabs.tabs[0].content)
    assert not any("Cargando" in t for t in textos)
    assert any("Sin filas" in t for t in textos)


def test_todos_vacios_queda_el_primero(tmp_db, monkeypatch):
    """Si ningun tab pinta, se queda el primero (comportamiento anterior)."""
    view = _vista_2_clientes(tmp_db)
    monkeypatch.setattr(view, "_obtener_vista_cliente", lambda cid: None)

    view._renderizar_tabs_resultado()

    assert view._result_tabs.selected_index == 0
    assert view._result_cliente_activo == view._resultado_clientes_order[0]
