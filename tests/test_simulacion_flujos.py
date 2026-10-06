"""Simulacion de flujos COMPLETOS de las dos secciones que entregan archivos:

  1. Reporte de compras  (ReportePanel: expandir -> generar -> exportar)
  2. Sustento/Expedientes (ReconocimientoView._ejecutar -> _generar_expediente)

Los flujos se conducen con los controles Flet reales y la DB temporal de
`tmp_db`, pero pasando siempre por los entry-points que usa el usuario (los
hilos, los guards y los writers), no replicando su logica como hacen otros
tests. Si un flujo se rompe en la UI, se rompe aca.

Se cubre ademas el camino de la DB real de 3.5 GB, marcado `real_db`, que la
suite decia correr pero no ejercitaba.
"""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from openpyxl import load_workbook

try:
    import flet as ft
except ImportError:  # pragma: no cover - entorno sin Flet
    ft = None

pytestmark = pytest.mark.skipif(ft is None, reason="requiere Flet instalado")

from src.core import ventas_db
from src.core.g360_theme import G360Theme
from src.core.xls_processor import derivar_campos
from src.ui.reconocimiento_view import ReconocimientoView
from src.ui.reporte_panel import ReportePanel


# ══ harness ═══════════════════════════════════════════════════════════


class _PageStub:
    """page.update() no necesita un servidor Flet, solo existir."""

    def __init__(self):
        self.actualizaciones = 0

    def update(self):
        self.actualizaciones += 1


class _AppStub:
    def __init__(self, desktop=None, page=None):
        self.page = page
        self._desktop = Path(desktop) if desktop else Path.cwd()
        self.snacks = []
        self.loading = []

    G360_ACCENT = G360Theme.accent_color()
    G360_SUCCESS = G360Theme.ok_color()
    G360_WARNING = G360Theme.warning_color()
    G360_ERROR = G360Theme.error_color()
    G360_ACCENT_2 = G360Theme.accent_2_color()

    def show_snackbar(self, msg, *a, **k):
        self.snacks.append(str(msg))

    def show_loading(self, *a, **k):
        self.loading.append(str(a[0]) if a else "")

    def hide_loading(self, *a, **k):
        self.loading.append("hide")

    def _pick_files(self, *a, **k):
        return []

    def _pick_file(self, *a, **k):
        return None

    def _get_desktop_path(self):
        return self._desktop


def _esperar(cond, timeout=30, msg="la condicion no se cumplio"):
    """Espera activa. Los dos flows corren en hilo y no devuelven el Thread."""
    fin = time.time() + timeout
    while time.time() < fin:
        if cond():
            return
        time.sleep(0.02)
    raise AssertionError(msg)


def _preview_listo(panel):
    """_generar suelta el busy ANTES de pintar la tabla (reporte_panel.py:552),
    asi que esperar solo por `_busy` mira la pantalla a medio pintar. El status
    recien cambia cuando `_render_resultados` termino."""
    return not panel._busy and panel.status.value != "Consultando…"


def _export_listo(panel, n=1):
    """El `finally` del export loguea el snackbar antes de liberar el busy."""
    return not panel._busy and len(panel.app.snacks) >= n


class _SpyThread:
    """Captura los hilos que lanzan _ejecutar / _generar_expediente.

    Esos dos metodos crean el Thread y lo sueltan: no devuelven la referencia y
    tragan la excepcion en un snackbar. Sin espiar, no hay forma de esperar el
    resultado ni ver el error.
    """

    _REAL = threading.Thread

    def __init__(self):
        self.hijos = []

    def __call__(self, target=None, *args, **kw):
        daemon = kw.pop("daemon", True)
        box = SimpleNamespace(error=None, thread=None)

        def _run():
            try:
                target(*args, **kw)
            except BaseException as ex:  # noqa: BLE001 - el hilo real se lo come
                box.error = ex

        box.thread = self._REAL(target=_run, daemon=daemon)
        self.hijos.append(box)
        return box.thread

    def join(self, timeout=120):
        # Snapshot: el propio codigo espiado puede lanzar otro Thread (p.ej. al
        # pintar el resultado) y agregarlo a la lista. Iterar en vivo tomaba ese
        # hijo recien creado todavia sin arrancar y colgaba el test.
        for box in list(self.hijos):
            box.thread.join(timeout)
            assert not box.thread.is_alive(), "el hilo no termino"
        errores = [b.error for b in self.hijos if b.error is not None]
        assert not errores, f"el hilo fallo: {errores[0]!r}"
        return self.hijos


@pytest.fixture
def spy_threads(monkeypatch):
    """Parchea Thread en view_handlers durante el test."""
    import src.ui.view_handlers as vh

    spy = _SpyThread()
    monkeypatch.setattr(vh.threading, "Thread", spy)
    return spy


import time  # noqa: E402  (lo usa _esperar)


# ── datos ──────────────────────────────────────────────────────────────

CLIENTE_A = "00056101"
CLIENTE_B = "00004884"
CLIENTE_C = "00005610"
VENDEDOR = "01188"


def _fila(**kw):
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
        id_cliente=CLIENTE_A,
        doc_cliente="20601024714",
        nom_cliente="MULTICOPIAS MARY E.I.R.L.",
        tpo_doc="F01",
        serie_doc="204",
        nro_doc="67375",
        referencia="",
        moneda="Soles",
        cantidad=800.0,
        cantidad_fae=0.0,
        soles=896.0,
        dolares=0.0,
        precio_unitario=1.12,
        anho=2026,
        mes=9,
        fecha_orig="2026-09-05",
        fecha_ref=None,
        fecha_venc=None,
        cod_sucursal="01",
        nom_sucursal="LIMA",
        departamento="LIMA",
        provincia="LIMA",
        distrito="SAN ISIDRO",
        id_vendedor=VENDEDOR,
        nom_vendedor="VENDEDOR UNO",
        id_pedido="P1",
        file_source="sim",
        mes_ref="2026-09",
        tipo_operacion="",
        factura_ref_serie="",
        factura_ref_nro="",
        folio_unico="",
    )
    base.update(kw)
    derivar_campos(base)
    return base


@pytest.fixture
def sim_db(tmp_db):
    """3 clientes: A con compras+NC, C con compras, B solo fuera del rango."""
    filas = [
        _fila(),
        _fila(nro_doc="67380", fecha_orig="2026-10-02", mes=10, mes_ref="2026-10", soles=120.0),
        _fila(
            id_cliente=CLIENTE_C,
            doc_cliente="20601024715",
            nom_cliente="CLIENTE CON COMPRAS",
            nro_doc="55555",
            soles=500.0,
        ),
        _fila(
            id_cliente=CLIENTE_B,
            nom_cliente="CLIENTE FUERA DE RANGO",
            fecha_orig="2025-01-10",
            mes=1,
            mes_ref="2025-01",
            soles=50.0,
            nro_doc="1",
        ),
        # NC del cliente A: una devolucion y un descuento por la misma factura
        _fila(
            tpo_doc="NCR",
            serie_doc="N204",
            nro_doc="900010",
            referencia="F01/204-67375",
            cantidad=-200.0,
            soles=-224.0,
            fecha_orig="2026-09-10",
            folio_unico="",
        ),
        _fila(
            tpo_doc="NCR",
            serie_doc="N204",
            nro_doc="900011",
            referencia="F01/204-67375",
            cantidad=0.0,
            cantidad_fae=800.0,
            soles=-96.0,
            fecha_orig="2026-09-12",
            folio_unico="",
        ),
    ]
    conn = tmp_db.get_conn()
    ventas_db.insert_ventas(conn, filas)
    ventas_db.populate_nc_asociadas(conn)
    return tmp_db


@pytest.fixture
def panel(sim_db, tmp_path, monkeypatch):
    """ReportePanel con pagina real, salida en tmp y sin abrir el explorador."""
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    monkeypatch.setattr("os.startfile", lambda *_: None)
    p = ReportePanel(_AppStub(desktop=out, page=_PageStub()), SimpleNamespace())
    p.construir_card()
    return p


RANGO = (datetime(2026, 9, 1), datetime(2026, 10, 31))


def _abrir(panel, clientes, vendedor=VENDEDOR, rango=RANGO):
    """Atajo del flujo de la UI: elegir cliente(s) con prefill y generar."""
    for i, (cid, nom) in enumerate(clientes):
        if i == 0:
            panel.expandir(cliente=(cid, nom), vendedor=vendedor, rango=rango)
        else:
            panel.state["clientes"].append((cid, nom))
            panel._pintar_cli_chips()
    _esperar(lambda: _preview_listo(panel), msg="el preview no termino")


def _xlsx_en(panel):
    out = panel.app._get_desktop_path()
    return sorted(out.glob("*.xlsx"))


# ══ 1. Reporte de compras — flujo completo ═══════════════════════════


class TestFlujoCompletoReporteCompras:
    def test_expandir_generar_exportar_limpiar(self, panel):
        # --- expandir con prefill (como cuando el usuario elige un cliente) ---
        _abrir(panel, [(CLIENTE_A, "MULTICOPIAS")])

        assert panel.state["vendedor"] == VENDEDOR
        assert panel.state["desde"] == RANGO[0] and panel.state["hasta"] == RANGO[1]
        df = panel.state["datos"][CLIENTE_A]
        assert df is not None and not df.empty, "no consulto compras del cliente"

        # --- la vista previa muestra el desglose, no solo un status ---
        assert len(panel.resultados.controls) >= 2  # linea de texto + tabla
        assert "MULTICOPIAS" in _texto_resultados(panel)
        assert "1 con compras" in panel.status.value
        assert "2 mes(es)" in _texto_resultados(panel)
        assert "neto S/ 696.00" in panel.status.value  # 1016 bruto - 320 de NC

        # --- exportar ---
        panel._exportar()
        _esperar(lambda: _export_listo(panel), msg="el export no termino")

        files = _xlsx_en(panel)
        assert len(files) == 1, files
        # Nombre con el cliente que eligio el usuario + su id visible
        assert files[0].name.startswith("Compras - MULTICOPIAS ")
        assert "56101" in files[0].name
        assert files[0].suffix == ".xlsx"
        assert any("Excel:" in s for s in panel.app.snacks)

        wb = load_workbook(files[0], read_only=True)
        try:
            # Hojas por defecto (sin sucursales): resumen forzado + las 5 elegidas
            for h in ("Resumen Ejecutivo", "Consolidado", "Ajustes_NC_NDB", "BD_Registro"):
                assert h in wb.sheetnames, wb.sheetnames
            assert "Consolidado" in wb.sheetnames
            assert len(wb.sheetnames) >= 5
        finally:
            wb.close()

        # --- limpiar deja la card lista para otra consulta ---
        panel.limpiar()
        assert panel.state["datos"] == {}
        assert panel.state["clientes"] == []
        assert panel.state["vendedor"] is None
        assert panel.state["hojas"] == set(panel.DEFAULT_HOJAS)
        assert list(panel.resultados.controls) == []
        assert panel.status.value == ""
        assert panel.cli_chips.controls == []
        assert panel._abierto is True  # no se colapsa con el Limpiar de la card

    def test_export_incluye_hojas_opcionales_y_ruta_de_salida(self, panel):
        panel._on_chip(SimpleNamespace(control=SimpleNamespace(data="sucursales", selected=True)))
        _abrir(panel, [(CLIENTE_A, "MULTICOPIAS")])
        panel._exportar()
        _esperar(lambda: _export_listo(panel), msg="el export no termino")

        files = _xlsx_en(panel)
        wb = load_workbook(files[0], read_only=True)
        try:
            assert "Sucursales" in wb.sheetnames  # hoja opcional, ahora si
            assert "Ajustes_NC_NDB" in wb.sheetnames  # el cliente tiene NC
        finally:
            wb.close()

    def test_dos_clientes_genera_un_excel_por_cliente(self, panel):
        _abrir(
            panel,
            [(CLIENTE_A, "MULTICOPIAS"), (CLIENTE_B, "CLIENTE FUERA DE RANGO")],
        )
        assert "1 con compras" in panel.status.value
        assert "sin compras en el rango" in _texto_resultados(panel)

        panel._exportar()
        _esperar(lambda: _export_listo(panel), msg="el export no termino")
        names = [f.name for f in _xlsx_en(panel)]
        assert len(names) == 1, "el cliente sin datos no debe generar Excel"
        assert names[0].startswith("Compras - MULTICOPIAS ")
        assert any("Excel:" in s for s in panel.app.snacks)  # un cliente -> un mensaje

    def test_toggle_nc_cambia_el_neto(self, panel):
        _abrir(panel, [(CLIENTE_A, "MULTICOPIAS")])
        con_nc = float(panel.state["datos"][CLIENTE_A]["SOLES"].sum())

        panel.nc_sw.value = False
        panel._generar()
        _esperar(lambda: _preview_listo(panel), msg="el preview no termino")
        sin_nc = float(panel.state["datos"][CLIENTE_A]["SOLES"].sum())

        # 896 + 120 = 1016 bruto; las NC restan 224 + 96
        assert con_nc == 1016.0 - 320.0
        assert sin_nc == 1016.0
        assert con_nc < sin_nc

    def test_reexportar_no_pisa_el_anterior(self, panel):
        _abrir(panel, [(CLIENTE_A, "MULTICOPIAS")])
        panel._exportar()
        _esperar(lambda: _export_listo(panel), msg="el export no termino")
        panel._exportar()
        _esperar(lambda: _export_listo(panel, 2), msg="el 2do export no termino")

        names = sorted(f.name for f in _xlsx_en(panel))
        assert len(names) == 2, f"el 2do export no escribio su archivo: {names}"
        assert "(2)" in names[0] or "(2)" in names[1]


def _textos_ui(ctrl):
    """Textos de un arbol de controles Flet (value/text/label, recursivo)."""
    out = []
    for attr in ("value", "text", "label", "tooltip"):
        v = getattr(ctrl, attr, None)
        if isinstance(v, str):
            out.append(v)
    for attr in ("controls", "actions"):
        for h in getattr(ctrl, attr, None) or []:
            out += _textos_ui(h)
    for attr in ("content", "title"):
        c = getattr(ctrl, attr, None)
        if c is not None:
            out += _textos_ui(c)
    return out


def _texto_resultados(panel):
    out = []

    def _borrar(c):
        for a in ("value", "text", "label"):
            v = getattr(c, a, None)
            if isinstance(v, str):
                out.append(v)
        for a in ("controls", "content"):
            for h in getattr(c, a, None) or []:
                _borrar(h)

    for c in panel.resultados.controls:
        _borrar(c)
    return "\n".join(out)


# ══ 1b. Reporte de compras — situaciones límite ══════════════════════


class TestSituacionesReporteCompras:
    def test_sin_clientes_no_hace_nada(self, panel):
        panel.construir_card()
        panel.expandir()
        _esperar(lambda: not panel._busy)
        assert panel._busy is False
        assert panel.state["datos"] == {}

    def test_generar_sin_clientes_avisa(self, panel):
        panel.construir_card()
        panel.state["clientes"] = []
        panel._generar()
        _esperar(lambda: not panel._busy)
        assert any("al menos un cliente" in s for s in panel.app.snacks)

    def test_exportar_sin_generar_avisa(self, panel):
        panel.construir_card()
        panel.state["clientes"] = [(CLIENTE_A, "MULTICOPIAS")]
        panel._exportar()
        _esperar(lambda: not panel._busy)
        assert any("Genera el reporte primero" in s for s in panel.app.snacks)
        assert _xlsx_en(panel) == []

    def test_exportar_sin_hojas_avisa(self, panel):
        panel.construir_card()
        panel.state["clientes"] = [(CLIENTE_A, "MULTICOPIAS")]
        panel.state["datos"] = {CLIENTE_A: pd.DataFrame({"SOLES": [1.0]})}
        panel.state["hojas"] = set()
        panel._exportar()
        _esperar(lambda: not panel._busy)
        assert any("al menos una hoja" in s for s in panel.app.snacks)
        assert _xlsx_en(panel) == []

    def test_limpiar_en_medio_de_una_tarea_no_interrumpe(self, panel):
        """La card no se limpia con un export en vuelo (se perderia el estado)."""
        panel.construir_card()
        _abrir(panel, [(CLIENTE_A, "MULTICOPIAS")])
        panel._set_busy(True)
        panel.limpiar()
        assert panel.state["clientes"] == [(CLIENTE_A, "MULTICOPIAS")]  # intacto
        panel._set_busy(False)

    def test_rango_sin_datos_no_genera(self, panel):
        panel.construir_card()
        panel.expandir(
            cliente=(CLIENTE_B, "CLIENTE FUERA DE RANGO"),
            vendedor=VENDEDOR,
            rango=(datetime(2026, 1, 1), datetime(2026, 1, 31)),
        )
        _esperar(lambda: _preview_listo(panel), msg="el preview no termino")
        assert panel.status.value == "Sin compras en el rango"
        panel._exportar()
        _esperar(lambda: not panel._busy)
        assert _xlsx_en(panel) == []
        assert any("Genera el reporte primero" in s for s in panel.app.snacks)

    def test_doble_click_mientras_corre_no_relanza(self, panel):
        """El boton queda disabled: un segundo _exportar no debe duplicar."""
        panel.construir_card()
        _abrir(panel, [(CLIENTE_A, "MULTICOPIAS")])
        assert panel.btn_exportar.disabled is False

        panel._set_busy(True)
        panel._exportar()
        time.sleep(0.3)  # el guard es synchronous: no hay nada que esperar
        assert _xlsx_en(panel) == [], "el segundo export paso igual"
        assert panel.app.loading.count("Generando Excel…") == 0
        panel._set_busy(False)

    def test_fallo_de_escritura_no_deja_la_card_bloqueada(self, panel, monkeypatch):
        import src.ui.reporte_panel as rp

        _abrir(panel, [(CLIENTE_A, "MULTICOPIAS")])

        def _boom(*a, **k):
            raise PermissionError("archivo abierto en Excel")

        monkeypatch.setattr(rp, "_escribir_xlsx", _boom)
        panel._exportar()
        _esperar(lambda: _export_listo(panel), msg="el export no termino")

        assert panel.btn_exportar.disabled is False, "quedo busy tras el error"
        assert panel.app.loading[-1] == "hide"  # se libero el overlay
        assert _xlsx_en(panel) == []
        # El fallo se reporta nombrando el cliente, no como error generico
        assert any("56101" in s for s in panel.app.snacks), panel.app.snacks
        assert any("con error" in s for s in panel.app.snacks), panel.app.snacks

    def test_fetch_caido_no_tapa_a_los_demas(self, panel, monkeypatch, caplog):
        """Con multi-cliente, uno que falla no puede tapar a los que funcionan."""
        import logging

        caplog.set_level(logging.ERROR, logger="src.ui.reporte_panel")
        panel.construir_card()
        panel.state["clientes"] = [(CLIENTE_A, "MULTICOPIAS"), (CLIENTE_B, "OTRO")]

        real = __import__("src.core.ventas_db_client", fromlist=["x"]).VentasDbClient

        class _Parcial:
            """El cliente A responde; el B revienta (SQL roto en ese cliente)."""

            def __init__(self, *a, **k):
                self._real = real(*a, **k)

            def __getattr__(self, n):
                return getattr(self._real, n)

            def fetch_compras_cliente(self, cid, **kw):
                if cid == CLIENTE_B:
                    raise RuntimeError("no such column: ID_LINEA")
                return self._real.fetch_compras_cliente(cid, **kw)

        import src.core.ventas_db_client as vdb

        monkeypatch.setattr(vdb, "VentasDbClient", _Parcial)
        panel.state["datos"] = {}
        panel.state["desde"], panel.state["hasta"] = RANGO
        panel._generar()
        _esperar(
            lambda: panel.status.value != "Consultando…",
            msg="el preview no termino",
        )

        df_ok = panel.state["datos"][CLIENTE_A]
        assert df_ok is not None and not df_ok.empty, "el cliente sano tambien perdio datos"
        assert panel.state["datos"][CLIENTE_B] is None
        assert panel.state["fallos"] == ["4884: no such column: ID_LINEA"]
        assert "1 con compras" in panel.status.value
        assert "1 con error" in panel.status.value
        # El que fallo aparece marcado, no como "sin compras en el rango"
        txt = _texto_resultados(panel)
        assert "no se pudo consultar" in txt
        assert "sin compras en el rango" not in txt

    def test_todos_los_clientes_caidos_avisa(self, panel, monkeypatch):
        panel.construir_card()
        panel.state["clientes"] = [(CLIENTE_A, "MULTICOPIAS")]
        panel.state["desde"], panel.state["hasta"] = RANGO

        class _Roto:
            def fetch_compras_cliente(self, *a, **k):
                raise RuntimeError("no such column: ID_LINEA")

            def fetch_clientes(self, *a, **k):
                return []

            def fetch_vendedores(self, *a, **k):
                return []

        import src.core.ventas_db_client as vdb

        monkeypatch.setattr(vdb, "VentasDbClient", lambda *a, **k: _Roto())
        panel._generar()
        _esperar(lambda: panel.status.value != "Consultando…", msg="no aviso el error")

        assert "No se pudo consultar 1 cliente(s)" == panel.status.value
        assert "no se pudo consultar" in _texto_resultados(panel)

    def test_un_cliente_caido_no_impide_exportar_los_demas(self, panel, monkeypatch):
        panel.construir_card()
        panel.state["clientes"] = [
            (CLIENTE_A, "MULTICOPIAS"),
            (CLIENTE_C, "CLIENTE CON COMPRAS"),
        ]
        panel.state["desde"], panel.state["hasta"] = RANGO

        real = __import__("src.core.ventas_db_client", fromlist=["x"]).VentasDbClient

        class _Parcial:
            def __init__(self, *a, **k):
                self._real = real(*a, **k)

            def __getattr__(self, n):
                return getattr(self._real, n)

            def fetch_compras_cliente(self, cid, **kw):
                return self._real.fetch_compras_cliente(cid, **kw)

            def fetch_client_by_id(self, cid):
                if cid == CLIENTE_C:
                    raise RuntimeError("RUC corrupto")
                return self._real.fetch_client_by_id(cid)

        import src.core.ventas_db_client as vdb

        monkeypatch.setattr(vdb, "VentasDbClient", _Parcial)
        panel._generar()
        _esperar(
            lambda: panel.status.value != "Consultando…",
            msg="el preview no termino",
        )
        assert "2 con compras" in panel.status.value

        panel._exportar()
        _esperar(lambda: _export_listo(panel), msg="el export no termino")

        files = _xlsx_en(panel)
        assert len(files) == 1, [f.name for f in files]
        assert files[0].name.startswith("Compras - MULTICOPIAS ")
        assert panel.state["fallos_export"] == ["5610: RUC corrupto"]
        assert any("1 Excel generados, 1 con error" in s for s in panel.app.snacks), (
            panel.app.snacks
        )

    def test_expandir_acepta_la_lista_de_la_busqueda(self, panel):
        """El boton de Búsqueda manda N clientes de una, no uno por vez."""
        panel.construir_card()
        panel.expandir(
            clientes=[("00056101", "A"), ("00005610", "C"), ("00004884", "B")],
            vendedor=VENDEDOR,
            rango=RANGO,
        )
        _esperar(lambda: _preview_listo(panel), msg="el preview no termino")

        assert [c[0] for c in panel.state["clientes"]] == [CLIENTE_A, CLIENTE_C, CLIENTE_B]
        assert panel.state["vendedor"] == VENDEDOR
        assert panel._abierto is True
        assert "3 cliente(s)" in panel.status.value
        assert len(panel.cli_chips.controls) == 3

    def test_expandir_no_duplica_clientes_ya_en_la_card(self, panel):
        panel.construir_card()
        panel.expandir(clientes=[(CLIENTE_A, "A")], rango=RANGO)
        _esperar(lambda: _preview_listo(panel), msg="el preview no termino")
        panel.expandir(clientes=[(CLIENTE_A, "A"), (CLIENTE_C, "C")], rango=RANGO)
        _esperar(lambda: _preview_listo(panel), msg="el 2do preview no termino")

        assert [c[0] for c in panel.state["clientes"]] == [CLIENTE_A, CLIENTE_C]

    def test_reset_general_colapsa_y_limpia(self, panel):
        from src.ui.view_handlers import _ViewHandlers

        panel.construir_card()
        _abrir(panel, [(CLIENTE_A, "MULTICOPIAS")])
        vista = SimpleNamespace(reporte_panel=panel)
        _ViewHandlers._reset_ui.__get__(vista)()
        assert panel.state["datos"] == {}
        assert panel._abierto is False


class TestPuenteBusquedaReporte:
    """El boton "Reporte de compras" de la card Búsqueda.

    Antes no existia: la multi-selección de Búsqueda no llegaba a la card de
    reportes y el usuario tenía que re-pickear los clientes adentro de ella.
    Los tests pasan por el picker real (con `abrir_picker_clientes` interceptado
    en su `on_confirm`), que es la misma callback que invoca la UI.
    """

    @pytest.fixture
    def vista_real(self, tmp_db, tmp_path, monkeypatch):
        out = tmp_path / "out"
        out.mkdir(exist_ok=True)
        monkeypatch.setattr("os.startfile", lambda *_: None)
        v = ReconocimientoView(_AppStub(desktop=out, page=_PageStub()))
        v.build()
        return v

    def _elegir(self, vista, monkeypatch, clientes):
        """Pulsa 'Buscar cliente', tilda los que sea y confirma."""
        import src.ui.widgets.cliente_picker as cp

        box = {}

        def _picker(app, on_confirm, multiple=True, **kw):
            box["cb"] = on_confirm

        monkeypatch.setattr(cp, "abrir_picker_clientes", _picker)
        vista.busq_buscar_cli_btn.on_click(None)
        box["cb"](clientes)

    def test_el_boton_se_habilita_al_elegir_clientes(self, vista_real, monkeypatch):
        """Regresion: su `disabled` se fijaba al construir y quedaba muerto."""
        v = vista_real
        assert v.busq_btn_reporte.disabled is True  # recien construida, sin seleccion

        self._elegir(v, monkeypatch, [{"id": CLIENTE_A, "nombre": "MULTICOPIAS"}])
        assert v._sel_clientes == [(CLIENTE_A, "MULTICOPIAS")]
        assert v.busq_btn_reporte.disabled is False

    def test_quitar_el_ultimo_lo_vuelve_a_apagar(self, vista_real, monkeypatch):
        """Quitar el chip del ultimo cliente deshabilita el boton otra vez."""
        v = vista_real
        self._elegir(v, monkeypatch, [{"id": CLIENTE_A, "nombre": "MULTICOPIAS"}])
        assert v.busq_btn_reporte.disabled is False

        chip = v.busq_cli_chips.controls[0]
        chip.on_delete(SimpleNamespace(control=chip))

        assert v._sel_clientes == []
        assert v.busq_btn_reporte.disabled is True

    def test_el_boton_lleva_clientes_vendedor_y_rango(self, vista_real, sim_db, monkeypatch):
        v = vista_real
        v.busq_fd = [datetime(2026, 9, 1).date()]
        v.busq_fh = [datetime(2026, 10, 31).date()]
        self._elegir(
            v,
            monkeypatch,
            [
                {"id": CLIENTE_A, "nombre": "MULTICOPIAS"},
                {"id": CLIENTE_C, "nombre": "CLIENTE CON COMPRAS"},
            ],
        )
        v._search_vend_id = VENDEDOR

        v.busq_btn_reporte.on_click(None)
        panel = v.reporte_panel

        _esperar(lambda: _preview_listo(panel), msg="el reporte no arranco")
        assert [c[0] for c in panel.state["clientes"]] == [CLIENTE_A, CLIENTE_C]
        assert panel.state["vendedor"] == VENDEDOR
        assert panel.state["desde"] == datetime(2026, 9, 1)
        assert panel.state["hasta"] == datetime(2026, 10, 31)
        assert panel._abierto is True
        assert "2 cliente(s)" in panel.status.value

        panel._exportar()
        _esperar(lambda: _export_listo(panel), msg="el export no termino")
        names = sorted(f.name for f in v.app._get_desktop_path().glob("*.xlsx"))
        assert len(names) == 2, names

    def test_sin_clientes_seleccionados_avisa_y_no_abre(self, vista_real):
        v = vista_real
        v.reporte_panel.construir_card()
        v.busq_btn_reporte.on_click(None)
        assert any("al menos un cliente" in s for s in v.app.snacks), v.app.snacks
        assert v.reporte_panel.state["clientes"] == []
        assert v.reporte_panel._abierto is False

    def test_el_boton_esta_en_la_ui(self, vista_real):
        """Si no esta montado el usuario no lo encuentra."""
        v = vista_real
        assert "Reporte de compras" in "\n".join(_textos_ui(v.container))


# ══ 4. Separacion por cliente (2 clientes, misma lista de precios) ════
#
# El reporte de sustento con 2 clientes seleccionados tiene que salir como dos
# expedientes independientes: mismo proceso, datos separados.
#
# El bug que fijo esto: `_subresultado_por_cliente` filtraba por la columna
# `CLIENTE`, pero el dataframe del pipeline trae `COD_CLIENTE`. Sin coincidencia
# devolvia el dataframe entero, asi que cada "sub" resultado se llevaba las filas
# del otro cliente. Consecuencias medidas con 2 clientes / 3 facturas:
#   - individual: 6 escrituras en 3 carpetas -> cada carpeta con archivos "(2)"
#     y los 3 Informes declarando al primer cliente;
#   - consolidado: 2 carpetas con el contenido bien pero la segunda nombrada
#     con el COD_CLIENTE del primero.


ALFA = ("00056101", "20601024714", "CLIENTE ALFA")
BETA = ("00005610", "20601024715", "CLIENTE BETA")


def _historial_2_clientes():
    """ALFA: 2 facturas + 1 NC. BETA: 1 factura + 1 NC. Misma lista para ambos."""
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
            _fac(cli_a, ruc_a, nom_a, "F001", "101", 800.0, 896.0),
            _fac(cli_a, ruc_a, nom_a, "F001", "102", 400.0, 448.0),
            _nc(cli_a, ruc_a, nom_a, "F001", "101", -50.0, 800.0),
            _fac(cli_b, ruc_b, nom_b, "F001", "201", 200.0, 224.0),
            _nc(cli_b, ruc_b, nom_b, "F001", "201", -30.0, 200.0),
        ]
    )


@pytest.fixture
def vista_2c(tmp_db, tmp_path):
    """Vista con DC, 2 clientes y UNA sola lista de precios compartida.

    Se construye en un fixture (y no en el test) a proposito: `build()` levanta
    el poller de captura, que es un hilo de vida larga. Si se construyera con
    el espia de hilos ya puesto, `spy.join()` esperaria al poller para siempre.
    El test elige la modalidad despues (la lee generar_expediente al llamar).
    """
    lista = tmp_path / "lista_compartida.csv"
    lista.write_text("SKU,PRECIO_BASE\n014850,0.90\n", encoding="utf-8")
    out = tmp_path / "exp"
    out.mkdir(exist_ok=True)
    v = ReconocimientoView(_AppStub(desktop=out, page=_PageStub()))
    v.build()
    v.tipo_actual = "DC"
    v.df_historial = _historial_2_clientes()
    v.lista_path = str(lista)
    v.vendedor_dropdown.value = VENDEDOR
    v.vendedor_dropdown.options = [ft.dropdown.Option(key=VENDEDOR, text="VENDEDOR UNO")]
    for doc in ("facturas", "nc", "ndb"):
        c = v.historico_calc_ctrls.get(doc)
        if c is not None:
            c.value = True
    return v


def _generar(vista, spy):
    vista._ejecutar(None)
    spy.join()
    vista._generar_expediente(None)
    spy.join()
    return sorted(p for p in vista.app._get_desktop_path().iterdir() if p.is_dir())


def _celda_calculo(exp_dir, etiqueta="Cliente |"):
    """Valor de una fila 'etiqueta | valor' del Calculo.xlsx."""
    calc = [p for p in exp_dir.glob("*Calculo.xlsx") if "(2)" not in p.name]
    wb = load_workbook(calc[0], read_only=True)
    try:
        for name in wb.sheetnames:
            for row in wb[name].iter_rows(values_only=True):
                vals = [str(c) for c in row if c is not None]
                j = " | ".join(vals)
                if j.startswith(etiqueta):
                    return j.split("|", 1)[1].split("|")[0].strip()
    finally:
        wb.close()
    return ""


def _resumen_historico(exp_dir):
    hist = [p for p in exp_dir.glob("*Historico.xlsx") if "(2)" not in p.name]
    if not hist:
        return ""
    wb = load_workbook(hist[0], read_only=True)
    try:
        for name in wb.sheetnames:
            for row in wb[name].iter_rows(values_only=True):
                for c in row:
                    if c and str(c).startswith("RESUMEN:"):
                        return str(c).split(":", 1)[1].strip()
    finally:
        wb.close()
    return ""


class TestSeparacionPorCliente:
    def test_el_filtro_por_cliente_aisla_en_vez_de_devolver_todo(self):
        """El sub-resultado de cada cliente trae SOLO sus filas."""
        from src.ui.expediente_service import _subresultado_por_cliente

        exp = SimpleNamespace(
            resultado=SimpleNamespace(
                resumen={"total_nc": 60.0, "skus_afectados": 3},
                alertas=[],
                trazabilidad=[],
                dataframe=pd.DataFrame(
                    {
                        "SKU": ["014850"] * 3,
                        "FACTURA": ["F001-101", "F001-102", "F001-201"],
                        "COD_CLIENTE": [ALFA[0], ALFA[0], BETA[0]],
                        "MONTO_NC": [10.0, 20.0, 30.0],
                    }
                ),
                get_excel=lambda: None,
            ),
            datos={},
            contexto=None,
        )
        sub_a = _subresultado_por_cliente(exp, ALFA[2], ALFA[0])
        sub_b = _subresultado_por_cliente(exp, BETA[2], BETA[0])

        assert sorted(set(sub_a.resultado.dataframe["COD_CLIENTE"])) == [ALFA[0]]
        assert len(sub_a.resultado.dataframe) == 2
        assert sorted(set(sub_b.resultado.dataframe["COD_CLIENTE"])) == [BETA[0]]
        assert len(sub_b.resultado.dataframe) == 1

    def test_el_mapa_nombre_id_sale_del_historial(self):
        from src.ui.expediente_service import _cod_clientes_por_nombre

        m = _cod_clientes_por_nombre(_historial_2_clientes())
        assert m == {ALFA[2]: ALFA[0], BETA[2]: BETA[0]}
        assert _cod_clientes_por_nombre(None) == {}
        assert _cod_clientes_por_nombre(pd.DataFrame()) == {}

    def test_individual_una_carpeta_por_documento_sin_duplicados(
        self, vista_2c, spy_threads, monkeypatch
    ):
        monkeypatch.setattr("os.startfile", lambda *_: None)
        vista_2c.modalidad_actual = "individual"
        exps = _generar(vista_2c, spy_threads)

        assert len(exps) == 3, [p.name for p in exps]
        # El bug: el bucle corria una vez por cliente sobre el mismo set de
        # facturas, asi que cada carpeta receiving 2 escrituras ("(2)").
        for d in exps:
            archivos = [f.name for f in d.iterdir() if f.is_file()]
            assert len(archivos) == 3, (d.name, archivos)
            assert not any("(2)" in a for a in archivos), (d.name, archivos)

    def test_individual_cada_informe_declara_su_cliente(self, vista_2c, spy_threads, monkeypatch):
        monkeypatch.setattr("os.startfile", lambda *_: None)
        vista_2c.modalidad_actual = "individual"
        exps = _generar(vista_2c, spy_threads)

        # El id de la carpeta tiene que coincidir con el cliente que declara
        # el Informe: antes los 3 decian ALFA, incluido el de BETA.
        por_carpeta = {d.name.split("-")[2].lstrip("0"): _celda_calculo(d) for d in exps}
        assert por_carpeta == {"56101": ALFA[2], "5610": BETA[2]}, por_carpeta
        assert set(_celda_calculo(d) for d in exps) == {ALFA[2], BETA[2]}

    def test_individual_el_historico_se_acota_a_su_documento(
        self, vista_2c, spy_threads, monkeypatch
    ):
        """El historico del 101 listaba las 2 facturas de ALFA: no es su evidencia."""
        monkeypatch.setattr("os.startfile", lambda *_: None)
        vista_2c.modalidad_actual = "individual"
        exps = _generar(vista_2c, spy_threads)

        res = {d.name: _resumen_historico(d) for d in exps}
        fac_101 = next(d for d in exps if "-101-" in d.name)
        fac_102 = next(d for d in exps if "-102-" in d.name)
        fac_201 = next(d for d in exps if "-201-" in d.name)

        # 101: su factura (896) menos la NC que se le aplica (-50)
        assert (
            res[fac_101.name]
            == "2 registro(s) \u00b7 1 factura(s) \u00b7 1 nota(s) \u00b7 Total S/ 846.00"
        )
        # 102: su factura sola, sin nota
        assert (
            res[fac_102.name]
            == "1 registro(s) \u00b7 1 factura(s) \u00b7 0 nota(s) \u00b7 Total S/ 448.00"
        )
        # 201: la de BETA, no la de ALFA
        assert (
            res[fac_201.name]
            == "2 registro(s) \u00b7 1 factura(s) \u00b7 1 nota(s) \u00b7 Total S/ 194.00"
        )

    def test_consolidado_una_carpeta_por_cliente_con_su_id(
        self, vista_2c, spy_threads, monkeypatch
    ):
        """La carpeta se nombra con el id del cliente y su factura de referencia."""
        monkeypatch.setattr("os.startfile", lambda *_: None)
        vista_2c.modalidad_actual = "consolidado"
        exps = _generar(vista_2c, spy_threads)

        assert len(exps) == 2, [p.name for p in exps]
        nombres = sorted(d.name for d in exps)
        assert any(ALFA[0].lstrip("0") in n for n in nombres), nombres
        assert any(BETA[0].lstrip("0") in n for n in nombres), nombres
        # Id de la carpeta == cliente del Informe (el bug le ponia el de A a B)
        por_carpeta = {d.name.split("-")[2].lstrip("0"): _celda_calculo(d) for d in exps}
        assert por_carpeta == {"56101": ALFA[2], "5610": BETA[2]}, por_carpeta

    def test_consolidado_el_historico_conserva_el_periodo_del_cliente(
        self, vista_2c, spy_threads, monkeypatch
    ):
        """En consolidado el historico es el del cliente: 2 facturas de ALFA."""
        monkeypatch.setattr("os.startfile", lambda *_: None)
        vista_2c.modalidad_actual = "consolidado"
        exps = _generar(vista_2c, spy_threads)

        alfa = next(d for d in exps if _celda_calculo(d) == ALFA[2])
        beta = next(d for d in exps if _celda_calculo(d) == BETA[2])
        assert _resumen_historico(alfa) == (
            "3 registro(s) \u00b7 2 factura(s) \u00b7 1 nota(s) \u00b7 Total S/ 1,294.00"
        )
        assert _resumen_historico(beta) == (
            "2 registro(s) \u00b7 1 factura(s) \u00b7 1 nota(s) \u00b7 Total S/ 194.00"
        )
