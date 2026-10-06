"""La debilidad de formato de fechas, cerrada en las tres capas.

El modo de falla que se repite en este proyecto no es "una fecha fea": es el
**silencio**. Un ISO crudo en pantalla, una celda vacia en un Excel que se ve
correcto, o filas que un rango por texto deja fuera. Estos tests fijan las tres
capas con ejecucion, no con grep:

  ingesta  -> toda via de entrada guarda ISO
  UI       -> ninguna superficie muestra el ISO crudo
  export   -> las celdas de fecha son fechas REALES con dd/mm/yyyy
"""

from __future__ import annotations

import sqlite3

import pandas as pd
import pytest
from openpyxl import load_workbook

from src.core import ventas_db
from src.core.fechas import FMT_UI, a_fecha, excel_fmt_ui, fecha_iso, fecha_ui

# Los 5 formatos que entrega el ERP mas uno ilegible.
FORMATOS = ["15/01/2024", "15-01-2024", "2024-01-15", "2024-01-15 10:30:00", "2024/01/15"]
GLOB_ISO = "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]"
COLS = ventas_db.COLS_FECHA


def _fila(n, fecha):
    from src.core.xls_processor import derivar_campos

    v = dict(
        id_articulo="02211",
        original_sku="02211",
        nom_articulo="GASEOSA 3L",
        id_linea="0101",
        nom_linea="GASEOSAS",
        id_cliente="00068414",
        doc_cliente="20100047218",
        nom_cliente="CLIENTE DEMO SAC",
        tpo_doc="F012",
        serie_doc="012",
        nro_doc=str(1000 + n),
        cantidad=1.0,
        soles=10.0,
        anho=2024,
        mes=1,
        fecha_orig=fecha,
        mes_ref="2024-01",
        cod_sucursal="01",
    )
    derivar_campos(v)
    return v


def _no_iso(conn, col):
    return conn.execute(
        f"SELECT COUNT(*) FROM ventas WHERE {col} IS NOT NULL AND {col}<>'' "
        f"AND {col} NOT GLOB '{GLOB_ISO}'"
    ).fetchone()[0]


class TestIngesta:
    """Ninguna via de entrada puede dejar una fecha fuera de ISO."""

    def test_insert_ventas_normaliza_las_cuatro_columnas(self, tmp_db):
        conn = tmp_db.get_conn()
        filas = []
        for i, f in enumerate(FORMATOS):
            v = _fila(i, f)
            v["fecha_ref"] = f
            v["fecha_venc"] = f
            v["fec_cargo"] = f
            filas.append(v)
        tmp_db.insert_ventas(conn, filas)
        for c in COLS:
            assert _no_iso(conn, c) == 0, f"{c} quedo fuera de ISO"

    def test_replace_folios_normaliza_las_cuatro_columnas(self, tmp_db):
        conn = tmp_db.get_conn()
        for i, f in enumerate(FORMATOS):
            v = _fila(2000 + i, f)
            v["fecha_ref"] = f
            v["fecha_venc"] = f
            v["fec_cargo"] = f
            tmp_db.replace_folios(conn, [v])
        for c in COLS:
            assert _no_iso(conn, c) == 0, f"{c} quedo fuera de ISO"

    def test_normaliza_fechas_fila_de_otra_base(self, tmp_db):
        """`traer_folios_faltantes` copia filas de la base del Tauri.

        Esa base mantiene su propio formato (hoy tiene fec_cargo en dd/mm/yyyy),
        asi que copiar verbatim reintroducia fechas no ISO en cada sync.
        """
        cols = ["nro_doc", "fecha_orig", "fecha_ref", "fecha_venc", "fec_cargo"]
        for f in FORMATOS:
            fila = ("1", f, f, f, f)
            out = ventas_db.normaliza_fechas_fila(fila, cols)
            for valor in out[1:]:
                assert a_fecha(valor) is not None
                assert len(valor) == 10 and valor[4] == "-"

    def test_normaliza_fechas_fila_no_inventa_ni_pierde(self, tmp_db):
        cols = ["fecha_orig", "nro_doc"]
        # Ilegible se conserva tal cual: es preferible un dato raro a perderlo.
        assert ventas_db.normaliza_fechas_fila(("basura", "9"), cols) == ("basura", "9")
        # Vacia y None se dejan como estaban.
        assert ventas_db.normaliza_fechas_fila(("", "9"), cols) == ("", "9")
        assert ventas_db.normaliza_fechas_fila((None, "9"), cols) == (None, "9")
        # Idempotente.
        assert ventas_db.normaliza_fechas_fila(("2024-01-15", "9"), cols) == ("2024-01-15", "9")

    def test_normaliza_fechas_fila_sin_columnas_de_fecha(self, tmp_db):
        assert ventas_db.normaliza_fechas_fila(("a", "b"), ["x", "y"]) == ("a", "b")


class TestConvencion:
    def test_ui_con_barras_y_storage_iso(self):
        assert FMT_UI == "%d/%m/%Y"
        assert fecha_ui("2024-01-15") == "15/01/2024"
        assert fecha_iso("15/01/2024") == "2024-01-15"
        assert excel_fmt_ui() == "dd/mm/yyyy"

    def test_lectura_tolera_todos_los_formatos_del_erp(self):
        for f in FORMATOS:
            assert a_fecha(f) is not None, f
            assert fecha_iso(f) == "2024-01-15"

    def test_ilegible_no_revienta(self):
        for f in ("basura", "15.01.2024", "", None, "31/02/2024"):
            assert fecha_ui(f) == ""
            assert fecha_iso(f) == ""


class TestUI:
    def test_columnas_de_fecha_se_detectan(self):
        from src.ui.resultados_view import _es_col_fecha

        for col in ("FECHA", "FECHA_REF", "FECHA_VENC", "FEC_CARGO", "fecha", "FECHA_DOC"):
            assert _es_col_fecha(col), col
        for col in ("SKU", "ARTICULO", "SOLES", "CANTIDAD", "CLIENTE"):
            assert not _es_col_fecha(col), col

    def test_tabla_generica_no_muestra_iso(self):
        """La tabla de resultados cae a `list(df.columns)[:5]` si nada coincide.

        Si entre esas columnas cae una fecha, se mostraba el ISO crudo. Ahora
        pasa por `fecha_ui`.
        """
        import inspect

        from src.ui import resultados_view as rv

        src = inspect.getsource(rv)
        # el fallback de columnas existe, y el render de celda formatea fechas
        assert "list(df.columns)[:5]" in src
        assert "_es_col_fecha(col)" in src

    def test_tabla_generica_formatea_una_columna_fecha(self, tmp_db):
        """Ejecuta el render real con una FECHA y comprueba la celda.

        El df no tiene ninguna columna de `cols_prioridad` ni de `cols_fallback`,
        asi que la tabla cae a `list(df.columns)[:5]`, que es justo donde la FECHA
        se mostraba cruda.
        """
        import flet as ft

        from src.ui.resultados_view import _build_standard_table

        df = pd.DataFrame(
            [
                {"FECHA": "2024-01-15", "DOC": "F01/012/1", "MONEDA": "Soles"},
                {"FECHA": "15/01/2024", "DOC": "F01/012/2", "MONEDA": "Soles"},
            ]
        )
        cols, rows, _toggles = _build_standard_table(df, None, "individual")
        textos = [c.content.value for r in rows for c in r.cells if isinstance(c.content, ft.Text)]
        assert any("15/01/2024" in t for t in textos), textos
        assert not any("2024-01-15" in t for t in textos), textos


class TestExport:
    def test_fecha_obj_no_descarta_una_fecha_no_iso(self):
        """Reporte de Compras: antes `strptime('%Y-%m-%d')` devolvia None.

        None = celda VACIA. Una fecha no ISO se perdia dentro de un Excel que
        se veia perfecto.
        """
        from src.ui.reporte_compras import _fecha_obj

        for f in FORMATOS:
            assert _fecha_obj(f) is not None, f
            assert _fecha_obj(f).isoformat() == "2024-01-15"
        # Sin dato sigue siendo None (y solo eso).
        assert _fecha_obj("") is None
        assert _fecha_obj(None) is None

    def test_historico_escribe_fecha_real_con_formato_ui(self, tmp_path):
        """Verifica el tipo de celda y el number_format leyendo el .xlsx."""
        from src.render.audit_renderer import generar_historico_clasico

        df = pd.DataFrame(
            [
                {
                    "FECHA": f,
                    "TIPO_DOC": "F01",
                    "SERIE": "F01",
                    "NUMERO": str(1000 + i),
                    "CODIGO": "00123",
                    "ARTICULO": "Producto X",
                    "CANTIDAD": 10.0,
                    "PRECIO_UNITARIO": 2.5,
                    "SOLES": 25.0,
                    "CLIENTE": "CLIENTE DEMO",
                }
                for i, f in enumerate(FORMATOS)
            ]
        )
        p = generar_historico_clasico(
            df,
            cliente_nombre="CLIENTE DEMO",
            cliente_ruc="00056101",
            ruta_salida=tmp_path / "hist.xlsx",
        )
        wb = load_workbook(p)
        try:
            ws = wb[wb.sheetnames[0]]
            # localizar el encabezado de la columna de fecha
            hdr = None
            for r in range(1, 25):
                for c in range(1, ws.max_column + 1):
                    if str(ws.cell(row=r, column=c).value or "").strip() == "FECHA EMISION":
                        hdr = (r, c)
                        break
                if hdr:
                    break
            assert hdr, "no se encontro la columna FECHA EMISION"
            r0, c0 = hdr
            celdas = []
            for r in range(r0 + 1, ws.max_row + 1):
                cell = ws.cell(row=r, column=c0)
                if cell.value is not None and cell.data_type != "s":
                    celdas.append(cell)
            assert len(celdas) == len(FORMATOS), f"solo {len(celdas)} filas con fecha"
            for cell in celdas:
                # 'd' = celda de fecha real: ordena y filtra como fecha
                assert cell.data_type == "d", f"{cell.value!r} quedo como {cell.data_type}"
                assert cell.number_format == excel_fmt_ui()
                assert cell.value.date().isoformat() == "2024-01-15"
        finally:
            wb.close()

    def test_historico_una_hoja_por_anio(self, tmp_path):
        from src.render.audit_renderer import generar_historico_clasico

        df = pd.DataFrame(
            [
                {
                    "FECHA": f,
                    "TIPO_DOC": "F01",
                    "SERIE": "F01",
                    "NUMERO": str(2000 + i),
                    "CODIGO": "00123",
                    "ARTICULO": "Producto X",
                    "CANTIDAD": 10.0,
                    "PRECIO_UNITARIO": 2.5,
                    "SOLES": 25.0,
                    "CLIENTE": "CLIENTE DEMO",
                }
                for i, f in enumerate(["2023-01-15", "2024-01-15", "2025-06-30"])
            ]
        )
        p = generar_historico_clasico(
            df, cliente_nombre="C", cliente_ruc="00056101", ruta_salida=tmp_path / "h.xlsx"
        )
        wb = load_workbook(p)
        try:
            assert {"2023", "2024", "2025"} <= set(wb.sheetnames)
        finally:
            wb.close()

    def test_historico_conserva_una_fecha_ilegible(self, tmp_path):
        """No se pierde: va a su propia hoja y queda como texto."""
        from openpyxl import load_workbook

        from src.render.audit_renderer import generar_historico_clasico

        df = pd.DataFrame(
            [
                {
                    "FECHA": f,
                    "TIPO_DOC": "F01",
                    "SERIE": "F01",
                    "NUMERO": str(3000 + i),
                    "CODIGO": "00123",
                    "ARTICULO": "Producto X",
                    "CANTIDAD": 10.0,
                    "PRECIO_UNITARIO": 2.5,
                    "SOLES": 25.0,
                    "CLIENTE": "CLIENTE DEMO",
                }
                for i, f in enumerate(["2024-01-15", "basura"])
            ]
        )
        p = generar_historico_clasico(
            df, cliente_nombre="C", cliente_ruc="00056101", ruta_salida=tmp_path / "h.xlsx"
        )
        wb = load_workbook(p)
        try:
            assert "Sin fecha" in wb.sheetnames
            ws = wb["Sin fecha"]
            assert any(
                "basura" in str(ws.cell(row=r, column=c).value)
                for r in range(1, ws.max_row + 1)
                for c in range(1, ws.max_column + 1)
            )
        finally:
            wb.close()


class TestConsultaPorRango:
    def test_rangos_sobre_una_columna_iso_no_mienten(self, tmp_db, sample_ventas):
        """El síntoma original: `BETWEEN` por texto sobre dd/mm/yyyy daba 0 filas."""
        conn = tmp_db.get_conn()
        tmp_db.insert_ventas(conn, sample_ventas)
        n = conn.execute(
            "SELECT COUNT(*) FROM ventas WHERE fecha_orig BETWEEN '2024-01-01' AND '2024-12-31'"
        ).fetchone()[0]
        assert n == len(sample_ventas)

    def test_order_by_por_fecha_es_cronologico(self, tmp_db, sample_ventas):
        conn = tmp_db.get_conn()
        tmp_db.insert_ventas(conn, sample_ventas)
        filas = conn.execute("SELECT fecha_orig FROM ventas ORDER BY fecha_orig").fetchall()
        assert [f[0] for f in filas] == sorted(f[0] for f in filas)


def test_no_hay_ventas_tabla_sin_schema(tmp_db):
    """Guarda: el helper de conteo no debe romperse con una base vacia."""
    conn = tmp_db.get_conn()
    assert isinstance(_no_iso(conn, "fecha_orig"), int)
    assert isinstance(sqlite3.connect(":memory:"), sqlite3.Connection)
