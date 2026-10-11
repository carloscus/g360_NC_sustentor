"""Regresion: ejecutar DC con 2 clientes y NC no debe reventar.

El flujo real de la app carga el historial desde SQLite, donde las columnas de
notas traen `NC_ASOCIADAS`. Con notas presentes, `_enriquecer_nc_nd` aplicaba
`bool()` a una Serie en vez de al `.any()`, y pandas levantaba "truth value of
a Series is ambiguous". Como `_ejecutar_tarea` envuelve todo en un try/except
generico, el fallo solo se veia como el snackbar "Ocurrio un problema en
cargar el detalle del cliente" y no se pintaba ninguna vista previa.
"""

import sys
from pathlib import Path

import flet as ft
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core import ventas_db
from src.core.ventas_db_client import VentasDbClient
from src.core.xls_processor import derivar_campos
from src.ui.reconocimiento_view import ReconocimientoView

from tests.conftest import *  # noqa: F401,F403  (fixtures: tmp_db)
from tests.test_render_parentdata import _AppStub  # noqa: E402

CLIENTES = [
    ("00056101", "20601024714", "CLIENTE ALFA S.A."),
    ("00005610", "20601024715", "CLIENTE BETA E.I.R.L."),
]
CLIENTES_IDS = sorted(c for c, _r, _n in CLIENTES)


def _fila(cid, ruc, nom, tpo, serie, nro, soles, cantidad, fecha, **extra):
    base = dict(
        id_articulo="014850",
        original_sku="014850",
        nom_articulo="PELOTA PVC #5",
        id_linea="0101",
        nom_linea="GASEOSAS",
        id_grupo="01",
        nom_grupo="G",
        id_tipo="01",
        nom_tipo="T",
        id_familia="01",
        nom_familia="F",
        id_cliente=cid,
        doc_cliente=ruc,
        nom_cliente=nom,
        tpo_doc=tpo,
        serie_doc=serie,
        nro_doc=nro,
        referencia="",
        moneda="Soles",
        cantidad=cantidad,
        cantidad_fae=0.0,
        soles=soles,
        dolares=0.0,
        precio_unitario=5.0,
        anho=2026,
        mes=9,
        fecha_orig=fecha,
        fecha_ref=None,
        fecha_venc=None,
        cod_sucursal="01",
        nom_sucursal="LIMA",
        departamento="LIMA",
        provincia="LIMA",
        distrito="SAN ISIDRO",
        id_vendedor="01188",
        nom_vendedor="VEND",
        id_pedido="P1",
        file_source="t",
        mes_ref="2026-09",
        tipo_operacion="",
        factura_ref_serie="",
        factura_ref_nro="",
        folio_unico="",
    )
    base.update(extra)
    return base


def _poblar_2_clientes_con_nc():
    """2 clientes x 2 facturas + 1 NC de devolucion cada uno."""
    filas = []
    n = 67370
    for cid, ruc, nom in CLIENTES:
        primera = None
        for _ in range(2):
            n += 1
            filas.append(_fila(cid, ruc, nom, "F01", "204", str(n), 500.0, 100.0, "2026-09-05"))
            primera = n
        filas.append(
            _fila(
                cid,
                ruc,
                nom,
                "NCR",
                "N204",
                f"9{primera}",
                -250.0,
                -50.0,
                "2026-09-20",
                referencia=f"F01/204-{primera}",
                factura_ref_serie="204",
                factura_ref_nro=str(primera),
            )
        )
    rows = []
    for d in filas:
        d = dict(d)
        derivar_campos(d)
        rows.append(d)
    conn = ventas_db.get_conn()
    ventas_db.insert_ventas(conn, rows)
    ventas_db.populate_nc_asociadas(conn)


def _historial_real() -> pd.DataFrame:
    cli = VentasDbClient()
    partes = [
        cli.fetch_historial(id_cliente=cid, fecha_desde="2026-09-01", fecha_hasta="2026-09-30")
        for cid, _ruc, _nom in CLIENTES
    ]
    return pd.concat(partes, ignore_index=True)


def _vista_con_datos(tmp_db, tmp_path, vista):
    view = ReconocimientoView(vista)
    view.build()
    view.tipo_actual = "DC"
    view.tipo_dropdown.value = "DC"
    view.df_historial = _historial_real()
    lista = tmp_path / "lista.csv"
    lista.write_text("CODIGO_SKU,PRECIO_BASE\n014850,4.20\n", encoding="utf-8")
    view.lista_path = str(lista)
    return view


def test_dc_2_clientes_con_nc_no_muestra_error(tmp_db, tmp_path):
    """El flujo completo no debe emitir el snackbar de error."""
    _poblar_2_clientes_con_nc()
    app = _AppStub()
    errores = []
    app.show_snackbar = lambda msg, *a, **k: errores.append(msg)

    view = _vista_con_datos(tmp_db, tmp_path, app)
    view._ejecutar_tarea()

    assert errores == [], f"la app mostro un error: {errores}"
    assert view.resultado is not None
    assert view.resultado.resultado is not None
    assert not view.resultado.resultado.dataframe.empty
    assert view.resultados_container.visible is True
    # El panel interno nace visible=False: debe encenderse al pintar.
    assert view._result_panel.visible is True
    assert view._result_panel.content is not None


def test_dc_2_clientes_agrupa_en_2_tabs(tmp_db, tmp_path):
    """Con 2 clientes deben quedar 2 grupos y la vista multi-cliente."""
    _poblar_2_clientes_con_nc()
    app = _AppStub()
    app.show_snackbar = lambda msg, *a, **k: None

    view = _vista_con_datos(tmp_db, tmp_path, app)
    view._ejecutar_tarea()

    assert sorted(view._resultado_clientes_order) == sorted(c for c, _r, _n in CLIENTES)

    # La rama multi-cliente tambien debe encender el panel interno.
    assert view._result_panel.visible is True

    # Un set por cliente, mas el global (clave None) del panel unico.
    sets = view._result_controles_por_cliente
    assert set(sets) == {None, *CLIENTES_IDS}

    # Todos los tabs se pintan desde el arranque: ninguno queda en el
    # placeholder de carga, y cada uno con su tabla y su boton habilitado.
    for cid in CLIENTES_IDS:
        activo = sets[cid]
        assert len(activo["tabla"].rows) > 0, f"tabla vacia para {cid}"
        assert activo["btn_row"].controls[0].disabled is False
    for tab in view._result_tabs.tabs:
        assert not isinstance(tab.content, ft.Column) or tab.content.controls, tab.text

    # El tab visible es el primero, y los handlers apuntan a el.
    assert view._result_tabs.selected_index == 0
    assert view._result_cliente_activo == CLIENTES_IDS[0]
    assert view.btn_expediente.disabled is False
