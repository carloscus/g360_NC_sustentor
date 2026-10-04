"""Simula escenarios de la Card DB + API para verificar funcionamiento,
actualizaci�n, reemplazo y reconexi�n.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# Se ejercita contra el Flet real, que es lo que corre en produccion.
# Antes se ponia `sys.modules["flet"] = MagicMock()` a nivel de modulo: ese parche
# es global y nunca se restauraba, asi que contaminaba a todo modulo importado
# DESPUES. De ahi venian los fallos por orden de recoleccion y el cuelgue de la
# suite completa. Si Flet no esta disponible el archivo se salta, en vez de
# envenenar el resto de la corrida.
try:
    import flet as ft
except ImportError:  # pragma: no cover - entorno sin Flet
    ft = None

pytestmark = pytest.mark.skipif(ft is None, reason="requiere Flet instalado")

from src.core import ventas_db
from src.ui.view_panels import _ViewPanels
from src.core.xls_processor import derivar_campos


@pytest.fixture(autouse=True)
def _clear_card_cache():
    ventas_db.invalidate_card_info_cache()
    yield
    ventas_db.invalidate_card_info_cache()


def _row(fecha: str, nro: str, mes_ref: str, vendedor: str = "178", sku: str = "02211") -> dict:
    v = {
        "id_articulo": sku,
        "original_sku": sku,
        "nom_articulo": "GASEOSA 3L",
        "id_linea": "01",
        "nom_linea": "GASEOSAS",
        "id_grupo": "01",
        "nom_grupo": "G",
        "id_tipo": "01",
        "nom_tipo": "T",
        "id_familia": "01",
        "nom_familia": "F",
        "id_cliente": "00004884",
        "doc_cliente": "20100047218",
        "nom_cliente": "CLIENTE DEMO",
        "tpo_doc": "F01",
        "serie_doc": "001",
        "nro_doc": nro,
        "referencia": "",
        "moneda": "Soles",
        "cantidad": 1.0,
        "cantidad_fae": 0.0,
        "soles": 10.0,
        "dolares": 0.0,
        "precio_unitario": 10.0,
        "anho": int(fecha[:4]),
        "mes": int(fecha[5:7]),
        "fecha_orig": fecha,
        "fecha_ref": None,
        "fecha_venc": None,
        "cod_sucursal": "01",
        "nom_sucursal": "LIMA",
        "departamento": "LIMA",
        "provincia": "LIMA",
        "distrito": "LIMA",
        "id_vendedor": vendedor,
        "nom_vendedor": "VENDEDOR",
        "id_pedido": "P1",
        "ord_compra": "",
        "file_source": "sim",
        "mes_ref": mes_ref,
        "tipo_operacion": "venta",
        "factura_ref_serie": "",
        "factura_ref_nro": "",
        "folio_unico": "",
        "id_ubigeo": "",
        "estado_linea": "",
        "canal_distribucion": "",
        "id_guia": "",
        "nom_condicion_pago": "",
        "division": "",
        "fec_cargo": "",
    }
    derivar_campos(v)
    return v


def _insert(conn: sqlite3.Connection, rows: list[dict]) -> None:
    ventas_db.insert_ventas(conn, rows)


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS ventas (
            id_articulo TEXT, original_sku TEXT, nom_articulo TEXT,
            id_linea TEXT, nom_linea TEXT, id_grupo TEXT, nom_grupo TEXT,
            id_tipo TEXT, nom_tipo TEXT, id_familia TEXT, nom_familia TEXT,
            id_cliente TEXT, doc_cliente TEXT, nom_cliente TEXT,
            tpo_doc TEXT, serie_doc TEXT, nro_doc TEXT, referencia TEXT,
            moneda TEXT, cantidad REAL, cantidad_fae REAL, soles REAL,
            dolares REAL, precio_unitario REAL, anho INTEGER, mes INTEGER,
            fecha_orig TEXT, fecha_ref TEXT, fecha_venc TEXT,
            cod_sucursal TEXT, nom_sucursal TEXT,
            departamento TEXT, provincia TEXT, distrito TEXT,
            id_vendedor TEXT, nom_vendedor TEXT, id_pedido TEXT,
            ord_compra TEXT, file_source TEXT, mes_ref TEXT,
            tipo_operacion TEXT, factura_ref_serie TEXT, factura_ref_nro TEXT,
            folio_unico TEXT, id_ubigeo TEXT, estado_linea TEXT,
            canal_distribucion TEXT, id_guia TEXT, nom_condicion_pago TEXT,
            division TEXT, fec_cargo TEXT
        );
        CREATE TABLE IF NOT EXISTS nc_asociadas (
            factura_doc_id TEXT, nc_doc_id TEXT, nc_tpo TEXT, nc_serie TEXT,
            nc_nro TEXT, fecha_orig TEXT, cantidad REAL, soles REAL
        );
        CREATE TABLE IF NOT EXISTS stats_cache (
            key TEXT PRIMARY KEY, value REAL, updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS day_state (
            dia TEXT PRIMARY KEY, ultima_captura TEXT, filas INTEGER,
            soles REAL, estado TEXT, cerrado_en TEXT
        );
        CREATE TABLE IF NOT EXISTS sync_log (
            tipo TEXT, estado TEXT, filas_subidas INTEGER,
            duracion_segundos REAL, error_message TEXT,
            started_at TEXT, finished_at TEXT
        );
        CREATE TABLE IF NOT EXISTS mes_checksums (
            mes TEXT PRIMARY KEY, checksum TEXT, updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tabla TEXT, operacion TEXT, registro_id TEXT,
            cambios TEXT, usuario TEXT, fecha TEXT
        );
        CREATE VIEW IF NOT EXISTS vw_dim_cliente AS
        SELECT DISTINCT id_cliente, doc_cliente, nom_cliente FROM ventas;
        CREATE VIEW IF NOT EXISTS vw_dim_articulo AS
        SELECT DISTINCT id_articulo, nom_articulo FROM ventas;
        CREATE VIEW IF NOT EXISTS vw_documento AS
        SELECT DISTINCT tpo_doc, serie_doc, nro_doc, folio_unico, fecha_orig
        FROM ventas;
        CREATE VIEW IF NOT EXISTS vw_devoluciones AS
        SELECT * FROM ventas WHERE tipo_operacion = 'devolucion';
        CREATE VIEW IF NOT EXISTS vw_facturas_disponibles AS
        SELECT * FROM ventas WHERE tpo_doc LIKE 'F01%';
        CREATE TABLE IF NOT EXISTS dim_vendedor (id_vendedor TEXT, nom_vendedor TEXT);
        CREATE TABLE IF NOT EXISTS dim_cliente (id_cliente TEXT, nom_cliente TEXT);
        CREATE TABLE IF NOT EXISTS dim_documento (tpo_doc TEXT, serie_doc TEXT, nro_doc TEXT);
        CREATE TABLE IF NOT EXISTS dim_linea (id_linea TEXT, nom_linea TEXT);
        CREATE TABLE IF NOT EXISTS dim_ruc (doc_cliente TEXT, nom_cliente TEXT);
        CREATE TABLE IF NOT EXISTS dim_articulo (id_articulo TEXT, nom_articulo TEXT);
        """
    )
    conn.commit()


def _build_view() -> _ViewPanels:
    v = _ViewPanels.__new__(_ViewPanels)
    v.app = SimpleNamespace(
        page=MagicMock(),
        show_snackbar=MagicMock(),
        show_loading=MagicMock(),
        hide_loading=MagicMock(),
        G360_ACCENT="#3b82f6",
        G360_SUCCESS="#34d399",
        G360_WARNING="#fbbf24",
        G360_ERROR="#f87171",
    )
    return v


def _pintable(monkeypatch):
    """Deja que el refresh llegue hasta el final.

    En produccion la card se pinta sola al abrir, asi que `_refrescar_card_db`
    solo llama `update()` si sus controles ya estan en la pagina; con Flet real
    un control no montado lanza `AssertionError` y el refresh aborta en el primer
    `update()` (antes de asignar status y alerta). Sin pagina real no hay forma
    de montarlos, asi que se neutra `Control.update`; `monkeypatch` lo restaura
    al terminar el test y no contamina al resto de la corrida.
    """
    monkeypatch.setattr(ft.Control, "update", lambda self: None, raising=False)


class TestSimulacionesCardDB:
    def test_escenario_1_sin_db(self, monkeypatch, tmp_path):
        data_dir = tmp_path / "data"
        monkeypatch.setenv("G360_DATA_DIR", str(data_dir))
        ventas_db.reset_allowed_lines_cache()
        ventas_db.init_db()

        v = _build_view()
        _pintable(monkeypatch)
        v._construir_card_db(async_=False)
        v._refrescar_card_db(async_=False)

        assert callable(v._refrescar_card_db)
        assert getattr(v._refrescar_card_db, "__self__", None) is v

        info = ventas_db.db_card_info()
        if not info.get("exists"):
            assert "Sin base de datos local" in str(v.card_db_kpis.controls)
            assert v.card_db_status.value == "Primera vez · configura tu base de datos"
        else:
            assert "Datos:" in str(v.card_db_status.value) or v.card_db_status.value == ""

    def test_escenario_2_db_ok_y_actualizacion(self, monkeypatch, tmp_path):
        data_dir = tmp_path / "data"
        monkeypatch.setenv("G360_DATA_DIR", str(data_dir))
        ventas_db.reset_allowed_lines_cache()
        ventas_db.init_db()

        conn = ventas_db.get_conn()
        _init_schema(conn)

        base_date = date(2026, 9, 28)
        rows_old = [
            _row((base_date - timedelta(days=i)).isoformat(), str(i), "2026-09")
            for i in range(7, 0, -1)
        ]
        _insert(conn, rows_old)
        conn.close()

        ventas_db.invalidate_card_info_cache()
        info = ventas_db.db_card_info()
        assert info["exists"] is True
        assert info["filas"] == len(rows_old)

        v = _build_view()
        _pintable(monkeypatch)
        v._construir_card_db(async_=False)
        v._refrescar_card_db(async_=False)

        assert len(v.card_db_kpis.controls) == 1

        conn = ventas_db.get_conn()
        _insert(conn, [_row(base_date.isoformat(), "new1", "2026-09")])
        conn.close()
        ventas_db.invalidate_card_info_cache()
        v._refrescar_card_db(async_=False)

        conn = ventas_db.get_conn()
        try:
            count = conn.execute("SELECT COUNT(*) FROM ventas").fetchone()[0]
        finally:
            conn.close()
        assert count == len(rows_old) + 1

    def test_escenario_3_reemplazo_y_api(self, monkeypatch, tmp_path):
        data_dir = tmp_path / "data"
        monkeypatch.setenv("G360_DATA_DIR", str(data_dir))
        ventas_db.reset_allowed_lines_cache()

        ventas_db.init_db()
        conn = ventas_db.get_conn()
        _init_schema(conn)
        _insert(conn, [_row("2026-09-28", str(i), "2026-09") for i in range(10)])
        conn.close()

        conn = ventas_db.get_conn()
        try:
            count_old = conn.execute("SELECT COUNT(*) FROM ventas").fetchone()[0]
        finally:
            conn.close()
        assert count_old == 10

        v = _build_view()
        _pintable(monkeypatch)
        v._construir_card_db(async_=False)
        v._refrescar_card_db(async_=False)
        assert len(v.card_db_kpis.controls) >= 1

        backup = data_dir / "historial_backup.db"
        ventas_db.db_path().rename(backup)
        ventas_db.init_db()
        conn = ventas_db.get_conn()
        _init_schema(conn)
        _insert(conn, [_row("2026-09-29", str(i), "2026-09") for i in range(20)])
        conn.close()
        ventas_db.invalidate_card_info_cache()

        v._refrescar_card_db(async_=False)

        conn = ventas_db.get_conn()
        try:
            count_new = conn.execute("SELECT COUNT(*) FROM ventas").fetchone()[0]
            fmax = conn.execute("SELECT MAX(fecha_orig) FROM ventas").fetchone()[0]
        finally:
            conn.close()
        assert count_new == 20
        assert fmax == "2026-09-29"

        from src.core.api_robustness.state import publicar_health

        publicar_health(
            {
                "api_online": False,
                "desfase_horas": None,
                "url": "http://x",
                "error": "caido",
                "checked_at": 0.0,
            }
        )
        txt_off, _col_off = v._chip_snapshot_txt()
        assert txt_off == "offline"

        publicar_health(
            {
                "api_online": True,
                "desfase_horas": 0.5,
                "url": "http://x",
                "error": None,
                "checked_at": 0.0,
            }
        )
        txt_on, _col_on = v._chip_snapshot_txt()
        assert txt_on == "ok"

    def test_escenario_4_dias_desde_ultimo_y_huecos(self, monkeypatch, tmp_path):
        data_dir = tmp_path / "data"
        monkeypatch.setenv("G360_DATA_DIR", str(data_dir))
        ventas_db.reset_allowed_lines_cache()
        ventas_db.init_db()

        conn = ventas_db.get_conn()
        _init_schema(conn)
        rows = []
        for d in [f"2026-0{m}-15" for m in (1, 2, 3, 5)]:
            rows.append(_row(d, d.replace("-", ""), d[:7]))
        _insert(conn, rows)
        conn.close()

        ventas_db.invalidate_card_info_cache()
        info = ventas_db._cargar_card_info()
        assert info["dias_desde_ultimo"] > 1
        assert len(info["huecos"]) > 0

        v = _build_view()
        _pintable(monkeypatch)
        v._construir_card_db(async_=False)
        v._refrescar_card_db(async_=False)
        assert "Ausentes:" in str(v.card_db_alerta.value)
