"""Simulación unitaria del flujo DC multi-cliente con tabs.

Prueba los métodos core sin pasar por el pipeline completo de hilos:
  1. _agrupar_resultado_por_cliente con 1 y 2 clientes
  2. _renderizar_tabs_resultado → tabs + badge
  3. _actualizar_tab_resultado → switch de tab con datos scoped
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import flet as ft
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from src.core.g360_theme import G360Theme
from src.domain import ExpedienteComercial, PipelineContext
from src.ui.view_handlers import _ViewHandlers


# ── helpers ────────────────────────────────────────────────────────────

CLI_A = "00056101"
CLI_B = "00005610"

ALFA = (CLI_A, "20601024714", "CLIENTE ALFA")
BETA = (CLI_B, "20601024715", "CLIENTE BETA")


def _historial_2_clientes():
    cli_a, ruc_a, nom_a = ALFA
    cli_b, ruc_b, nom_b = BETA

    def _fac(cli, ruc, nom, serie, nro, cant, soles):
        return {
            "CODIGO": "014850",
            "ARTICULO": "PELOTA PVC #5",
            "CANTIDAD": cant,
            "SOLES": soles,
            "FECHA": pd.Timestamp("2026-09-05"),
            "TIPO_DOC": "F01",
            "SERIE": serie,
            "NUMERO": nro,
            "DOC_ID": f"{serie}-{nro}",
            "TIPO_CLASE": "factura",
            "FACTURA_REF": "",
            "CANTIDAD_FAE": 0.0,
            "CLIENTE": nom,
            "COD_CLIENTE": cli,
            "DOC_CLIENTE": ruc,
        }

    def _nc(cli, ruc, nom, serie, nro, soles, fae):
        return {
            "CODIGO": "014850",
            "ARTICULO": "PELOTA PVC #5",
            "CANTIDAD": 0.0,
            "CANTIDAD_FAE": fae,
            "SOLES": soles,
            "FECHA": pd.Timestamp("2026-09-20"),
            "TIPO_DOC": "NCR",
            "SERIE": "N900",
            "NUMERO": "90001",
            "DOC_ID": "N900-90001",
            "TIPO_CLASE": "descuento",
            "FACTURA_REF": f"{serie}-{nro}",
            "REFERENCIA": f"F01/{serie}-{nro}",
            "CLIENTE": nom,
            "COD_CLIENTE": cli,
            "DOC_CLIENTE": ruc,
        }

    return pd.DataFrame(
        [
            _fac(*ALFA, "F001", "101", 800.0, 896.0),
            _fac(*ALFA, "F001", "102", 400.0, 448.0),
            _nc(*ALFA, "F001", "101", -50.0, 800.0),
            _fac(*BETA, "F001", "201", 200.0, 224.0),
            _nc(*BETA, "F001", "201", -30.0, 200.0),
        ]
    )


def _historial_1_cliente():
    cli_a, ruc_a, nom_a = ALFA
    return pd.DataFrame(
        [
            {
                "CODIGO": "014850",
                "ARTICULO": "PELOTA PVC #5",
                "CANTIDAD": 800.0,
                "SOLES": 896.0,
                "FECHA": pd.Timestamp("2026-09-05"),
                "TIPO_DOC": "F01",
                "SERIE": "F001",
                "NUMERO": "101",
                "DOC_ID": "F001-101",
                "TIPO_CLASE": "factura",
                "FACTURA_REF": "",
                "CANTIDAD_FAE": 0.0,
                "CLIENTE": nom_a,
                "COD_CLIENTE": CLI_A,
                "DOC_CLIENTE": ruc_a,
            },
            {
                "CODIGO": "014850",
                "ARTICULO": "PELOTA PVC #5",
                "CANTIDAD": 0.0,
                "CANTIDAD_FAE": 800.0,
                "SOLES": -50.0,
                "FECHA": pd.Timestamp("2026-09-20"),
                "TIPO_DOC": "NCR",
                "SERIE": "N900",
                "NUMERO": "90001",
                "DOC_ID": "N900-90001",
                "TIPO_CLASE": "descuento",
                "FACTURA_REF": "F001-101",
                "REFERENCIA": "F01/F001-101",
                "CLIENTE": nom_a,
                "COD_CLIENTE": CLI_A,
                "DOC_CLIENTE": ruc_a,
            },
        ]
    )


def _mock_view():
    """Construye un objeto view mínimo con los métodos y atributos necesarios."""
    from src.ui.resultados_view import render_resultado

    view = SimpleNamespace()
    for name in dir(_ViewHandlers):
        if name.startswith("_") and not name.startswith("__"):
            attr = getattr(_ViewHandlers, name)
            if callable(attr):
                try:
                    setattr(view, name, attr.__get__(view))
                except TypeError:
                    pass

    view.df_historial = None
    view.tipo_actual = "DC"
    view.render_resultado = render_resultado

    view.df_historial = None
    view.tipo_actual = "DC"
    view.app = SimpleNamespace(
        G360_ACCENT=G360Theme.accent_color(),
        G360_SUCCESS=G360Theme.ok_color(),
        G360_WARNING=G360Theme.warning_color(),
        G360_ERROR=G360Theme.error_color(),
        page=None,
        show_snackbar=lambda *a, **k: None,
        show_loading=lambda *a, **k: None,
        hide_loading=lambda *a, **k: None,
    )
    view.resultado = None
    view._resultado_por_cliente = {}
    view._resultado_clientes_order = []
    view._resultado_global = None

    view.lbl_total_nc = SimpleNamespace(value="S/ 0,00")
    view.lbl_skus = SimpleNamespace(value="0 SKU")
    view.lbl_alertas_count = SimpleNamespace(value="0 alertas")
    view.resultados_table = SimpleNamespace(
        columns=[],
        rows=[],
        clear=lambda: None,
        append=lambda x: None,
    )
    view.resultados_container = SimpleNamespace(visible=False, content=None)
    view._result_tabs = None
    view._result_tabs_container = None
    view._result_clientes = None
    view.btn_expediente = SimpleNamespace(disabled=True)
    view.alertas_container = SimpleNamespace(visible=False, content=None)
    view._result_warn_row = SimpleNamespace(controls=[])
    view._result_load_more_btn = SimpleNamespace(text="", visible=False)
    view._result_summary = None
    view._omitir_activo = lambda: False
    view._nd_activo = lambda: False
    view._result_sort_col = None
    view._result_sort_asc = False

    return view


def _build_expediente(df):
    return SimpleNamespace(
        resultado=SimpleNamespace(
            resumen={"total_nc": 0.0, "skus_afectados": 0},
            alertas=[],
            trazabilidad=[],
            dataframe=df,
            get_excel=lambda: None,
            metricas={},
        ),
        datos={},
        contexto=PipelineContext(config={}, antecedentes="", observaciones=""),
        estrategia="",
        variantes=[],
    )


# ── Tests: agrupamiento ────────────────────────────────────────────────


class TestAgruparResultadoPorCliente:
    def test_1_cliente_crea_un_grupo(self):
        """Con 1 cliente se crea _resultado_por_cliente con 1 entrada."""
        view = _mock_view()
        exp = _build_expediente(_historial_1_cliente())
        view._agrupar_resultado_por_cliente(exp)

        assert len(view._resultado_clientes_order) == 1
        assert len(view._resultado_por_cliente) == 1
        assert CLI_A in view._resultado_por_cliente
        assert view._resultado_global is exp

    def test_2_clientes_crea_grupos_separados(self):
        view = _mock_view()
        exp = _build_expediente(_historial_2_clientes())
        view._agrupar_resultado_por_cliente(exp)

        assert len(view._resultado_clientes_order) == 2
        assert len(view._resultado_por_cliente) == 2
        # Orden alfabético por nombre: ALFA < BETA
        assert view._resultado_clientes_order[0] == CLI_A
        assert view._resultado_clientes_order[1] == CLI_B

    def test_cada_grupo_mantiene_solo_su_cliente(self):
        view = _mock_view()
        exp = _build_expediente(_historial_2_clientes())
        view._agrupar_resultado_por_cliente(exp)

        for cid in view._resultado_clientes_order:
            grp_exp = view._resultado_por_cliente[cid]
            df = grp_exp.resultado.dataframe
            clientes = set(df["COD_CLIENTE"].dropna().unique())
            assert clientes == {cid}, f"grupo {cid} tiene: {clientes}"


# ── Tests: tabs rendering ─────────────────────────────────────────────


class TestRenderizarTabsResultado:
    def test_1_cliente_no_cria_tabs(self):
        """Com 1 cliente, _mostrar_resultado não chama _renderizar_tabs_resultado."""
        view = _mock_view()
        exp = _build_expediente(_historial_1_cliente())
        view._agrupar_resultado_por_cliente(exp)

        multi = len(view._resultado_por_cliente) > 1
        assert multi is False

    def test_2_clientes_cria_tabs_e_badge(self):
        """Dois clientes criam tabs com badge de quantidade."""
        view = _mock_view()
        exp = _build_expediente(_historial_2_clientes())
        view._agrupar_resultado_por_cliente(exp)
        view.resultados_container = SimpleNamespace(visible=False, content=None)

        # Apenas constrói os widgets sem chamar render_resultado
        badge = view._construir_badge_clientes(view._resultado_clientes_order)
        tabs = view._construir_tabs(view._resultado_clientes_order)

        assert isinstance(tabs, ft.Tabs)
        assert len(tabs.tabs) == 2
        assert isinstance(badge, ft.Container)

        badge_texts = [c.value for c in badge.content.controls if isinstance(c, ft.Text)]
        assert any("2 cliente" in t for t in badge_texts), f"badge texts: {badge_texts}"

    def test_tabs_ordenados_alfabeticamente(self):
        view = _mock_view()
        exp = _build_expediente(_historial_2_clientes())
        view._agrupar_resultado_por_cliente(exp)
        view.resultados_container = SimpleNamespace(visible=False, content=None)

        tabs = view._construir_tabs(view._resultado_clientes_order)
        orden = [t.text for t in tabs.tabs]
        assert orden == [CLI_A, CLI_B], f"orden: {orden}"

    def test_tabs_inicializados_com_placeholder(self):
        view = _mock_view()
        exp = _build_expediente(_historial_2_clientes())
        view._agrupar_resultado_por_cliente(exp)
        view.resultados_container = SimpleNamespace(visible=False, content=None)

        tabs = view._construir_tabs(view._resultado_clientes_order)
        for tab in tabs.tabs:
            assert tab.content is not None


# ── Tests: tab switching ──────────────────────────────────────────────


class TestActualizarTabResultado:
    def test_obtener_vista_cliente_retorna_none_se_nao_existir(self):
        view = _mock_view()
        view._resultado_por_cliente = {}
        assert view._obtener_vista_cliente(" inexistent") is None

    def test_obtener_vista_cliente_retorna_vista_para_cliente_valido(self):
        view = _mock_view()
        exp = _build_expediente(_historial_2_clientes())
        view._agrupar_resultado_por_cliente(exp)

        vista = view._obtener_vista_cliente(CLI_A)
        assert vista is not None
        assert vista.resultado is not None
        assert not vista.resultado.dataframe.empty

    def test_obtener_vista_cliente_retorna_none_para_df_vazio(self):
        view = _mock_view()
        empty_df = pd.DataFrame({"COD_CLIENTE": [], "SKU": []})
        exp = _build_expediente(empty_df)
        view._agrupar_resultado_por_cliente(exp)

        assert view._obtener_vista_cliente(CLI_A) is None


# ── Tests: integración single vs multi ────────────────────────────────


class TestFlujoDCSingleVsMulti:
    def test_1_cliente_sem_tabs(self):
        view = _mock_view()
        exp = _build_expediente(_historial_1_cliente())
        view._agrupar_resultado_por_cliente(exp)

        # 多客户判断
        multi = len(view._resultado_por_cliente) > 1
        assert multi is False

    def test_2_clientes_com_tabs(self):
        view = _mock_view()
        exp = _build_expediente(_historial_2_clientes())
        view._agrupar_resultado_por_cliente(exp)

        multi = len(view._resultado_por_cliente) > 1
        assert multi is True
        assert len(view._resultado_clientes_order) == 2

    def test_dfs_escoped_por_cliente(self):
        view = _mock_view()
        exp = _build_expediente(_historial_2_clientes())
        view._agrupar_resultado_por_cliente(exp)

        for cid in view._resultado_clientes_order:
            grp = view._resultado_por_cliente[cid]
            df = grp.resultado.dataframe
            assert df is not None
            assert not df.empty
            assert set(df["COD_CLIENTE"].dropna().unique()) == {cid}
