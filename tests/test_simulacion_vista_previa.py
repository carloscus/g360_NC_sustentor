"""Simulaciones de la vista previa de resultados (4 escenarios).

Cada escenario ejecuta el flujo real de la vista (pipeline -> agrupar ->
pintar) y audita el arbol de controles con invariantes de PINTADO, es decir,
condiciones necesarias para que el cliente Flet maquete la pantalla:

  1. sin None como hijo en listas `controls` (Flet no serializa None y el
     `page.update()` falla dejando la pantalla intacta, sin error en Python);
  2. sin `expand=True` con padre directo no-Flex (Row/Column). Fuera de un
     Flex el expand no resuelve tamano y el area puede quedar en blanco;
  3. si hay tabs: area de alto finito + scroll interno por tab (ft.Tabs es
     un viewport; con alto infinito no maqueta);
  4. panel interno visible con contenido, y tab seleccionado con contenido.

E1: DC individual, 1 cliente (panel unico).
E2: DC individual, 2 clientes con contenido (2 tabs).
E3: DC individual, 2 clientes, ocultar 0.00 vacia el 1ro (replica caso real:
    el 1ro avisa con conteo y se selecciona el 2do).
E4: DC individual, 3 clientes (3 tabs, un set por cliente).
"""

import sys
from pathlib import Path

import flet as ft
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ui.reconocimiento_view import ReconocimientoView

from tests.conftest import *  # noqa: F401,F403  (fixtures: tmp_db)
from tests.test_render_parentdata import _AppStub  # noqa: E402

CLI_A = "00056101"
CLI_B = "00005610"
CLI_C = "00080436"


def _fac(cod, nom, nro, sku="014850", cantidad=100.0, soles=500.0):
    return {
        "CODIGO": sku,
        "ARTICULO": f"ART {sku}",
        "CANTIDAD": cantidad,
        "SOLES": soles,
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


def _exp_dc(hist, precios: dict):
    """Ejecuta el pipeline DC con una lista de precios dada."""
    from src.domain import ExpedienteComercial, PipelineContext
    from src.pipeline import Pipeline

    lista = pd.DataFrame(
        {"CODIGO_SKU": list(precios), "PRECIO_BASE": [precios[k] for k in precios]}
    )
    exp = ExpedienteComercial(
        nombre="DC",
        familia="precio",
        estrategia="PriceDifference",
        datos=hist,
        contexto=PipelineContext(config={"sort_mode": "fecha_desc", "modalidad": "individual"}),
    )
    exp.condiciones = [lista]
    return Pipeline().ejecutar(exp), hist


def _vista_con_resultado(hist, exp):
    view = ReconocimientoView(_AppStub())
    view.build()
    view.df_historial = hist
    view.resultado = exp
    view.tipo_actual = "DC"
    return view


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


def _resumen_de_tab(tab):
    """Baja Tab -> Container -> Column(scroll) -> [resumen] (None si es aviso)."""
    contenido = getattr(tab.content, "content", None)
    if (
        isinstance(contenido, ft.Column)
        and contenido.scroll is not None
        and len(contenido.controls) == 1
        and isinstance(contenido.controls[0], ft.Column)
    ):
        return contenido.controls[0]
    return None


def _auditar_subarbol(raiz):
    """Recorre con padres y devuelve hallazgos (lista vacia = todo bien)."""
    hallazgos = []
    padres = {id(raiz): None}
    pila = [raiz]
    orden = []
    while pila:
        c = pila.pop()
        orden.append(c)
        for attr in ("controls", "content"):
            h = getattr(c, attr, None)
            hijos = h if isinstance(h, list) else ([h] if h is not None else [])
            for x in hijos:
                if x is None:
                    hallazgos.append(f"None como hijo en {type(c).__name__}.{attr}")
                elif isinstance(x, ft.Control):
                    padres[id(x)] = c
                    pila.append(x)
    for c in orden:
        if getattr(c, "expand", False):
            p = padres.get(id(c))
            if not isinstance(p, (ft.Row, ft.Column)):
                hallazgos.append(f"expand=True con padre {type(p).__name__} en {type(c).__name__}")
    return hallazgos


def _auditar_resultados(view):
    """Invariantes de pintado sobre la card de resultados. Devuelve hallazgos."""
    hallazgos = []
    cont = view.resultados_container
    panel = view._result_panel
    if not cont.visible:
        hallazgos.append("contenedor externo invisible")
    if not panel.visible:
        hallazgos.append("panel interno invisible")
    if panel.content is None:
        hallazgos.append("panel interno sin contenido")
        return hallazgos
    hallazgos.extend(_auditar_subarbol(cont))
    tabs = getattr(view, "_result_tabs", None)
    n_tabs = len(tabs.tabs) if tabs is not None and getattr(tabs, "tabs", None) else 0
    if n_tabs:
        area = view._result_tabs_container.controls[1]
        if not (isinstance(area, ft.Container) and (area.height or 0) > 0):
            hallazgos.append("area de tabs sin altura acotada")
        for i, tab in enumerate(tabs.tabs):
            resumen = _resumen_de_tab(tab)
            if resumen is None:
                # Tab con aviso (vacio legitimo): no debe ser el Cargando eterno.
                if "Cargando" in " ".join(_textos(tab.content)):
                    hallazgos.append(f"tab[{i}] atascado en Cargando")
                continue
            contenido = tab.content.content
            if contenido.scroll is None:
                hallazgos.append(f"tab[{i}] sin scroll interno")
            if len(resumen.controls) <= 5:
                hallazgos.append(f"tab[{i}] resumen con pocos hijos")
        sel = tabs.selected_index
        if _resumen_de_tab(tabs.tabs[sel]) is None:
            hallazgos.append(f"tab seleccionado [{sel}] sin contenido")
    else:
        resumen = getattr(view, "_result_summary", None)
        n = len(getattr(resumen, "controls", []) or [])
        if n <= 5:
            hallazgos.append(f"panel unico con {n} hijos (vacio?)")
        if len(view.resultados_table.rows) == 0:
            hallazgos.append("tabla del panel unico sin filas")
    return hallazgos


def test_E1_panel_unico_1_cliente(tmp_db):
    hist = pd.DataFrame(
        [
            _fac(CLI_A, "CLIENTE ALFA", "101"),
            _fac(CLI_A, "CLIENTE ALFA", "102"),
        ]
    )
    exp, hist = _exp_dc(hist, {"014850": 4.20})
    view = _vista_con_resultado(hist, exp)
    view._mostrar_resultado_post_ejecucion(exp)
    assert _auditar_resultados(view) == []


def test_E2_multi_2_clientes_con_contenido(tmp_db):
    hist = pd.DataFrame(
        [
            _fac(CLI_A, "CLIENTE ALFA", "101"),
            _fac(CLI_A, "CLIENTE ALFA", "102"),
            _fac(CLI_B, "CLIENTE BETA", "201"),
        ]
    )
    exp, hist = _exp_dc(hist, {"014850": 4.20})
    view = _vista_con_resultado(hist, exp)
    view._mostrar_resultado_post_ejecucion(exp)
    assert _auditar_resultados(view) == []
    assert view._result_tabs.selected_index == 0


def test_E3_ocultar_cero_vacia_primer_tab(tmp_db):
    """Replica el caso real: ocultar 0.00 filtra todo el 1er cliente."""
    hist = pd.DataFrame(
        [
            _fac(CLI_A, "CLIENTE ALFA", "101", sku="014850"),  # coincide -> se oculta
            _fac(CLI_B, "CLIENTE BETA", "201", sku="014851"),  # difiere -> queda
        ]
    )
    exp, hist = _exp_dc(hist, {"014850": 5.00, "014851": 4.20})
    view = _vista_con_resultado(hist, exp)
    view.chk_ocultar_cero.value = True
    view._mostrar_resultado_post_ejecucion(exp)
    assert _auditar_resultados(view) == []
    # El 1ro (coincide todo) avisa con el conteo omitido y se selecciona el 2do.
    orden = view._resultado_clientes_order
    assert view._result_tabs.selected_index == orden.index(CLI_B)
    assert view._result_cliente_activo == CLI_B
    textos = _textos(view._result_tabs.tabs[orden.index(CLI_A)].content)
    assert any("omitida" in t for t in textos)


def test_E4_multi_3_clientes(tmp_db):
    hist = pd.DataFrame(
        [
            _fac(CLI_A, "CLIENTE ALFA", "101"),
            _fac(CLI_B, "CLIENTE BETA", "201"),
            _fac(CLI_C, "CLIENTE GAMMA", "301"),
        ]
    )
    exp, hist = _exp_dc(hist, {"014850": 4.20})
    view = _vista_con_resultado(hist, exp)
    view._mostrar_resultado_post_ejecucion(exp)
    assert _auditar_resultados(view) == []
    assert len(view._result_tabs.tabs) == 3
    assert set(view._result_controles_por_cliente) >= {CLI_A, CLI_B, CLI_C}


def test_ocultar_cero_activado_por_defecto(tmp_db):
    """El check nace prendido: por defecto no se muestran ni generan 0.00."""
    from tests.test_render_parentdata import _AppStub

    view = ReconocimientoView(_AppStub())
    view.build()
    assert view.chk_ocultar_cero.value is True


def test_ir_a_reporte_cambia_de_vista(tmp_db):
    """El boton del header alterna a la vista Reporte (sin pagina tambien)."""
    from tests.test_render_parentdata import _AppStub

    view = ReconocimientoView(_AppStub())
    view.build()
    assert view._vista_actual == "sustento"
    assert view.ir_a_reporte() is True
    assert view._vista_actual == "reporte"
    assert view.container.content is view._raiz_reporte()
    assert view.reporte_panel._abierto is True
    assert view.mostrar_sustento() is True
    assert view._vista_actual == "sustento"
    # La raiz de sustento incluye la barra sticky: montar solo el scroll
    # dejaba sin boton a Ejecutar / Generar expediente.
    assert view.container.content is view._raiz_sustento
    assert view._sticky_bar in view.container.content.controls


def _claves(raiz):
    out = []
    pila = [raiz]
    while pila:
        c = pila.pop()
        if getattr(c, "key", None):
            out.append(c.key)
        for attr in ("controls", "content"):
            h = getattr(c, attr, None)
            if isinstance(h, ft.Control):
                pila.append(h)
            elif isinstance(h, (list, tuple)):
                pila.extend(x for x in h if isinstance(x, ft.Control))
    return out


def test_zonas_separadas(tmp_db):
    """La card de reporte vive en su vista, no en el layout de sustento.

    (El texto "Reporte de compras" si aparece en sustento: es el boton
    puente de Busqueda que manda la seleccion al reporte.)
    """
    from tests.test_render_parentdata import _AppStub

    view = ReconocimientoView(_AppStub())
    view.build()
    assert "reportes_card" not in _claves(view._workflow_scroll)
    assert "Resultado del reconocimiento" in _textos(view._workflow_scroll)
    raiz = view._raiz_reporte()
    assert "reportes_card" in _claves(raiz)
    assert any("Volver al sustento" in t for t in _textos(raiz))
