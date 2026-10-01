"""Humo por tipo: todos los reportes DOCX+XLSX generan sin errores.

Cubre los 8 tipos activos con datos mínimos representativos: verifica que
el Excel (tablas + pie PROCESO + pie FACTURAS donde aplica) y el DOCX
(secciones + frase de cierre) salgan completos.
"""

import docx
import pandas as pd
import pytest
from openpyxl import load_workbook

from src.domain import RecognitionResult
from src.render.docx_renderer import DocxInformeRenderer
from src.render.excel_renderer import ExcelRenderer


def _df(tipo):
    base = {
        "SKU": ["S1"],
        "ARTICULO": ["Prod 1"],
        "CANTIDAD": [100],
        "FACTURA": ["F201-100"],
        "ALERTA": ["OK"],
        "CLIENTE": ["CLIENTE X"],
        "DOC_CLIENTE": ["206039928"],
    }
    extra = {
        "diferencia_precio": {
            "SOLES": [340.00],
            "PRECIO_HIST": [3.40],
            "PRECIO_BASE": [3.00],
            "PRECIO_NETO": [3.00],
            "DIFERENCIA": [0.40],
            "MONTO_NC": [40.00],
        },
        "diferencia_cantidad": {
            "SOLES": [340.00],
            "PRECIO_HIST": [3.40],
            "PRECIO_BASE": [3.00],
            "PRECIO_NETO": [3.00],
            "DIFERENCIA": [0.40],
            "MONTO_NC": [40.00],
        },
        "descuento_precio": {
            "SOLES": [340.00],
            "PRECIO_HIST": [3.40],
            "LINEA": ["L01"],
            "%_DESCUENTO": [0.05],
            "PRECIO_NETO": [3.23],
            "DIFERENCIA": [0.17],
            "MONTO_NC": [17.00],
        },
        "descuento_factura": {
            "PRECIO_UNITARIO": [3.40],
            "%_DESCUENTO": [0.05],
            "MONTO_NC": [17.00],
        },
        "bonificacion_promocion": {
            "FACTURAS": ["F201-100"],
            "CICLOS": [12],
            "BONIFICACION": [8],
            "PRECIO_UNITARIO": [3.40],
            "MONTO_NC": [27.20],
        },
        "rebate_volumen": {
            "LINEA": ["L01"],
            "SKUS": ["S1"],
            "MONTO_BASE": [1000.00],
            "%_DEL_TOTAL": [100.0],
            "%_REBATE": [0.05],
            "MONTO_NC": [50.00],
        },
        "anular_factura": {
            "PRECIO_HIST": [3.40],
            "MONTO_FACTURA": [340.00],
            "MONTO_NC": [340.00],
        },
        "feria_preventa": {
            "LINEA": ["L01"],
            "Cant. Solicitada": [120],
            "Cant. Sustentada": [100],
            "P.U. Hist.": [3.40],
            "P.U. Result.": [3.23],
            "Desc. Unit. (S/)": [0.17],
            "Tot. Sustento (S/)": [323.00],
            "Subtotal NC (S/)": [17.00],
            "FACTURAS": ["F201-100"],
            "Cant. Facturada": [100],
            "Cant. Disponible": [100],
            "% Stock Restante": [120.0],
            "Glosa": ["OK"],
        },
        "diferencia_stock": {
            "Cantidad Facturada": [100],
            "Stock Cliente": [60],
            "Stock Sustentado": [60],
            "% Stock Restante": [60.0],
            "PRECIO_HIST": [3.40],
            "Precio Lista": [3.00],
            "Precio Neto": [3.00],
            "Dif. Unitaria": [0.40],
            "MONTO_NC": [24.00],
        },
    }
    d = dict(base)
    d.update(extra[tipo])
    return pd.DataFrame(d)


def _res(df, tipo, total):
    return RecognitionResult(
        dataframe=df,
        dataframe_excel=df.copy(),
        resumen={
            "total_nc": total,
            "skus_afectados": 1,
            "doc_ref": "F201-101" if tipo == "rebate_volumen" else "F201-100",
        },
    )


def _docx_texto(path):
    doc = docx.Document(str(path))
    pars = [p.text for p in doc.paragraphs if p.text.strip()]
    tabs = [c.text for t in doc.tables for r in t.rows for c in r.cells]
    return pars, tabs


TIPOS = [
    "diferencia_precio",
    "diferencia_cantidad",
    "descuento_precio",
    "descuento_factura",
    "bonificacion_promocion",
    "rebate_volumen",
    "anular_factura",
    "feria_preventa",
    "diferencia_stock",
]
TOTALES = {
    "diferencia_precio": 40.00,
    "diferencia_cantidad": 40.00,
    "descuento_precio": 17.00,
    "descuento_factura": 17.00,
    "bonificacion_promocion": 27.20,
    "rebate_volumen": 50.00,
    "anular_factura": 340.00,
    "feria_preventa": 17.00,
    "diferencia_stock": 24.00,
}


class TestHumoExcel:
    def _generar(self, tmp_path, tipo, **kw):
        df = _df(tipo)
        out = tmp_path / f"{tipo}_Calculo.xlsx"
        ExcelRenderer().generar(
            _res(df, tipo, TOTALES[tipo]),
            str(out),
            tipo=tipo,
            cliente="CLIENTE X",
            doc_ref="F201-100",
            ruc="206039928",
            modalidad=kw.get("modalidad", "individual"),
        )
        wb = load_workbook(str(out))
        ws = wb[wb.sheetnames[0]]
        return [c.value for row in ws.iter_rows() for c in row if isinstance(c.value, str)]

    def test_todos_generan_tabla(self, tmp_path):
        for tipo in TIPOS:
            textos = self._generar(tmp_path, tipo)
            assert "SKU" in textos or "LÍNEA" in textos, tipo
            assert "PROCESO APLICADO" not in textos, tipo

    def test_columna_a_ancho_15(self, tmp_path):
        from openpyxl import load_workbook
        from src.render.excel_renderer import ExcelRenderer

        df = _df("diferencia_precio")
        out = tmp_path / "C.xlsx"
        ExcelRenderer().generar(
            _res(df, "diferencia_precio", 40.00),
            str(out),
            tipo="diferencia_precio",
            cliente="C",
            doc_ref="F201-100",
        )
        wb = load_workbook(str(out))
        ws = wb[wb.sheetnames[0]]
        assert ws.column_dimensions["A"].width == 15
        assert ws.page_margins.left == 0.8
        assert ws.page_margins.right == 0.8

    def test_facturas_comprometidas_solo_con_resumen(self, tmp_path):
        from src.domain import RecognitionResult

        df = _df("diferencia_precio")
        res = RecognitionResult(
            dataframe=df,
            dataframe_excel=df.copy(),
            resumen={
                "total_nc": 40.00,
                "skus_afectados": 1,
                "doc_ref": "F201-100",
                "documentos_unicos": ["F201-100"],
                "titulo_documentos": "FACTURAS COMPROMETIDAS",
            },
        )
        out = tmp_path / "C.xlsx"
        ExcelRenderer().generar(
            res,
            str(out),
            tipo="diferencia_precio",
            cliente="C",
            doc_ref="F201-100",
            modalidad="consolidado",
        )
        wb = load_workbook(str(out))
        ws = wb[wb.sheetnames[0]]
        textos = [c.value for row in ws.iter_rows() for c in row if isinstance(c.value, str)]
        assert "FACTURAS COMPROMETIDAS: F201-100" in textos

    @pytest.mark.parametrize("tipo", ["diferencia_precio", "diferencia_cantidad"])
    def test_consolidado_usa_columna_facturas(self, tmp_path, tipo):
        """El swap FACTURA -> FACTURAS solo estaba activo para DC.

        En consolidado cada fila agrupa varias facturas: sin el swap la
        columna mostraba unico documento y se perdian los demas.
        """
        df = _df(tipo)
        df["FACTURAS"] = ["F201-100, F201-101"]
        out = tmp_path / f"{tipo}_conso.xlsx"
        ExcelRenderer().generar(
            _res(df, tipo, TOTALES[tipo]),
            str(out),
            tipo=tipo,
            cliente="C",
            doc_ref="F201-100",
            modalidad="consolidado",
        )
        wb = load_workbook(str(out))
        ws = wb[wb.sheetnames[0]]
        textos = [c.value for row in ws.iter_rows() for c in row if isinstance(c.value, str)]
        assert "FACTURAS" in textos, f"{tipo}: no se escribio FACTURAS"
        assert "F201-100, F201-101" in textos, f"{tipo}: se perdio el listado"

    def test_do_consolidado_usa_columna_facturas(self, tmp_path):
        """DO comparte el swap aunque su tabla sea una lista plana de columnas."""
        from src.render.excel_renderer import columnas_consolidadas_dc, COLUMNAS_POR_TIPO

        col_defs = columnas_consolidadas_dc(COLUMNAS_POR_TIPO["descuento_precio"])
        headers = [c["header"] for c in col_defs]
        assert "FACTURAS" in headers and "FACTURA" not in headers
        # la lista original no se muta
        assert [c["header"] for c in COLUMNAS_POR_TIPO["descuento_precio"]][1] == "FACTURA"

    def test_feria_preventa_escribe_cobertura_de_cantidades(self, tmp_path):
        """FPE reporta facturado / disponible / % restante por SKU (como VRS)."""
        df = _df("feria_preventa")
        out = tmp_path / "FPE.xlsx"
        ExcelRenderer().generar(
            _res(df, "feria_preventa", 17.00),
            str(out),
            tipo="feria_preventa",
            cliente="C",
            doc_ref="F201-100",
        )
        wb = load_workbook(str(out))
        ws = wb[wb.sheetnames[0]]
        grid = [[c.value for c in row] for row in ws.iter_rows()]
        head = next(i for i, r in enumerate(grid) if "CANT. SUST." in r)
        headers = grid[head]
        assert "CANT. FACTURADA" in headers
        assert "CANT. DISPONIBLE" in headers
        assert "% STOCK REST." in headers
        fila = grid[head + 1]
        assert fila[headers.index("CANT. FACTURADA")] == 100
        assert fila[headers.index("% STOCK REST.")] == 120.0


class TestHumoDocx:
    def _generar(self, tmp_path, tipo, **kw):
        df = _df(tipo)
        out = tmp_path / f"{tipo}_Informe.docx"
        datos = {
            "tipo_operacion": "DC",
            "tipo_calculo": tipo,
            "cliente": "CLIENTE X",
            "representante": "V",
            "numero_referencia": "F201-100",
            "evidencias": {},
            "modalidad": kw.get("modalidad", "individual"),
            "periodo": "01/01/2026 al 31/01/2026",
            "descripcion": "caso de prueba",
            "observaciones": "sin observaciones",
        }
        DocxInformeRenderer().generar(
            _res(df, tipo, TOTALES[tipo]),
            ruta_salida=str(out),
            datos_adicionales=datos,
            nombre_archivo_excel="C.xlsx",
        )
        return _docx_texto(out)

    def test_todos_generan_secciones(self, tmp_path):
        for tipo in TIPOS:
            pars, _ = self._generar(tmp_path, tipo)
            for sec in (
                "1. DATOS GENERALES",
                "2. RESULTADO ECON",
                "3. ANTECEDENTES",
                "4. OBSERVACIONES",
                "5. DOCUMENTOS",
            ):
                assert any(sec in p for p in pars), (tipo, sec)
            assert not any("PROCESO APLICADO" in p for p in pars), tipo

    def test_frase_cierre_todos(self, tmp_path):
        claves = {
            "diferencia_precio": "histórico",
            "diferencia_cantidad": "cantidad exacta",
            "descuento_precio": "omitidos",
            "descuento_factura": "omitidos",
            "bonificacion_promocion": "bonificación",
            "rebate_volumen": "rebate",
            "anular_factura": "anula",
            "feria_preventa": "feria",
            "diferencia_stock": "SKU",
        }
        for tipo in TIPOS:
            pars, _ = self._generar(tmp_path, tipo)
            assert any("S/" in p and claves[tipo] in p for p in pars), tipo
