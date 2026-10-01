import pandas as pd
from src.domain import RecognitionResult
from src.strategies.price_difference import PriceDifferenceStrategy
from src.ui.expediente_service import _extract_cliente_id


def _hist_con_cliente():
    return pd.DataFrame(
        {
            "CODIGO": ["A001", "A001", "A002"],
            "ARTICULO": ["Prod A", "Prod A", "Prod B"],
            "CANTIDAD": [100, 200, 50],
            "SOLES": [340.00, 680.00, 125.00],
            "FECHA": pd.to_datetime(["2026-01-15", "2026-02-20", "2026-03-10"]),
            "TIPO_DOC": ["F01", "F01", "F01"],
            "SERIE": ["201", "201", "201"],
            "NUMERO": ["100", "100", "200"],
            "COD_CLIENTE": ["00068426", "00068426", "00068426"],
            "DOC_CLIENTE": ["206039928", "206039928", "206039928"],
            "CLIENTE": ["DISTRIBUIDORA LOS ANGELES"] * 3,
        }
    )


class TestAsignacionClienteContext:
    def test_cantidad_en_lista_se_ignora(self):
        """DC: una eventual columna CANTIDAD en la lista se ignora (las
        cantidades siempre se toman de las facturas) con alerta informativa,
        y el cálculo sigue en modo comparar normal con contexto de cliente."""
        hist = _hist_con_cliente()
        lista = pd.DataFrame(
            [
                {"SKU": "A001", "PRECIO_BASE": 3.0, "CANTIDAD": 150},
                {"SKU": "A002", "PRECIO_BASE": 2.0, "CANTIDAD": 50},
            ]
        )
        st = PriceDifferenceStrategy()
        alertas = []
        df_res, _, df_full = st._procesar_comparar(hist, lista, alertas)
        assert any("CANTIDAD" in a.mensaje and "ignora" in a.mensaje for a in alertas)
        # Comparar normal por línea: A001 en 2 líneas + A002 = 3 filas
        assert len(df_res) == 3
        assert "COD_CLIENTE" in df_res.columns
        assert "DOC_CLIENTE" in df_res.columns
        assert set(df_res["COD_CLIENTE"].dropna().astype(str)) == {"00068426"}

    def test_asignacion_sin_cantidad_mantiene_cod_cliente(self):
        """Modo comparar normal ya incluye las columnas de cliente."""
        hist = _hist_con_cliente()
        lista = pd.DataFrame(
            [
                {"SKU": "A001", "PRECIO_BASE": 3.0},
                {"SKU": "A002", "PRECIO_BASE": 2.0},
            ]
        )
        st = PriceDifferenceStrategy()
        df_res, alertas, df_full = st._procesar_comparar(hist, lista, [])
        assert "COD_CLIENTE" in df_res.columns
        assert set(df_res["COD_CLIENTE"].dropna().astype(str)) == {"00068426"}


class TestExtractClienteId:
    def test_desde_dataframe(self):
        res = RecognitionResult(
            dataframe=pd.DataFrame({"COD_CLIENTE": ["00068426"]}),
            resumen={},
        )
        assert _extract_cliente_id(res) == "00068426"

    def test_fallback_dataframe_excel(self):
        res = RecognitionResult(
            dataframe=pd.DataFrame({"SKU": ["A001"]}),
            dataframe_excel=pd.DataFrame({"COD_CLIENTE": ["00068426"], "SKU": ["A001"]}),
            resumen={},
        )
        assert _extract_cliente_id(res) == "00068426"

    def test_fallback_historial_por_cliente_value(self):
        """Sin columnas de cliente en el resultado (asignación legacy), el id
        del cliente se resuelve desde el historial / el valor del filtro."""
        hist = _hist_con_cliente()
        res = RecognitionResult(
            dataframe=pd.DataFrame({"SKU": ["A001"], "MONTO_NC": [10.0]}),
            resumen={},
        )
        assert _extract_cliente_id(res, df_historial=hist, cliente_value="00068426") == "00068426"

    def test_fallback_por_nombre_cliente(self):
        hist = _hist_con_cliente()
        res = RecognitionResult(dataframe=pd.DataFrame({"SKU": ["A001"]}), resumen={})
        assert _extract_cliente_id(res, df_historial=hist, cliente_value="") == "00068426"

    def test_vacio_sin_fallbacks(self):
        res = RecognitionResult(dataframe=pd.DataFrame({"SKU": ["A001"]}), resumen={})
        assert _extract_cliente_id(res) == ""


class TestDocxResumenConsistente:
    def test_resumen_economico_usa_el_dataframe(self, tmp_path):
        """El RESUMEN ECONÓMICO del informe deriva del mismo df/redondeo que la
        hoja Cálculo, aunque resumen['total_nc'] difiera."""
        from src.render.docx_renderer import DocxInformeRenderer
        import docx

        df = pd.DataFrame(
            {
                "SKU": ["A001", "A002"],
                "ARTICULO": ["P", "Q"],
                "CANTIDAD": [1, 1],
                "MONTO_NC": [49.99, 50.01],
                "FACTURA": ["F201-100", "F201-100"],
            }
        )
        resultado = RecognitionResult(
            dataframe=df,
            resumen={"total_nc": 250.00, "skus_afectados": 2, "doc_ref": "F201-100"},
        )
        out = tmp_path / "Informe.docx"
        DocxInformeRenderer().generar(
            resultado,
            ruta_salida=str(out),
            datos_adicionales={
                "tipo_operacion": "DC",
                "cliente": "CLIENTE X",
                "representante": "VENDEDOR",
                "numero_referencia": "F201-100",
                "evidencias": {},
            },
            nombre_archivo_excel="Calculo.xlsx",
        )
        txt = "\n".join(p.text for p in docx.Document(str(out)).paragraphs)
        txt += "\n" + "\n".join(
            c.text for t in docx.Document(str(out)).tables for r in t.rows for c in r.cells
        )
        assert "S/ 100.00" in txt
        assert "S/ 250.00" not in txt


class TestCalculoDocxTotalesCuadran:
    """Los totales del Calculo.xlsx (recalculados como en Excel) deben cuadrar
    con el RESUMEN ECONÓMICO del docx. Reproduce las tres filas del caso real
    EXP-DC-68426-204-40260-20260913 (SKUs 02203/02202/02204)."""

    @staticmethod
    def _df_caso_real():
        return pd.DataFrame(
            [
                {
                    "SKU": "02203",
                    "ARTICULO": "FORRO N VINIFAN A4 CRISTAL 25",
                    "CANTIDAD": 4000,
                    "SOLES": 13878.44,
                    "PRECIO_HIST": 3.46961,
                    "PRECIO_BASE": 5.12,
                    "DESCUENTO_COMPUESTO": 0.3494,
                    "PRECIO_NETO": 3.33082,
                    "DIFERENCIA": 0.13879,
                    "MONTO_NC": 555.16,
                    "FACTURA": "F204-40260",
                    "ALERTA": "AL01 - Diferencia positiva (SKU 02203)",
                },
                {
                    "SKU": "02202",
                    "ARTICULO": "FORRO N VINIFAN OFICIO CRISTAL 25",
                    "CANTIDAD": 2500,
                    "SOLES": 17364.99,
                    "PRECIO_HIST": 6.946,
                    "PRECIO_BASE": 10.25,
                    "DESCUENTO_COMPUESTO": 0.3494,
                    "PRECIO_NETO": 6.66816,
                    "DIFERENCIA": 0.27784,
                    "MONTO_NC": 694.60,
                    "FACTURA": "F204-40260",
                    "ALERTA": "AL01 - Diferencia positiva (SKU 02202)",
                },
                {
                    "SKU": "02204",
                    "ARTICULO": "FORRO N VINIFANCITO CRISTAL 25-26",
                    "CANTIDAD": 1500,
                    "SOLES": 2602.21,
                    "PRECIO_HIST": 1.73481,
                    "PRECIO_BASE": 2.56,
                    "DESCUENTO_COMPUESTO": 0.3494,
                    "PRECIO_NETO": 1.66541,
                    "DIFERENCIA": 0.06940,
                    "MONTO_NC": 104.10,
                    "FACTURA": "F204-40260",
                    "ALERTA": "AL01 - Diferencia positiva (SKU 02204)",
                },
            ]
        )

    def test_neto_congelado_y_totales_cuadran_con_docx(self, tmp_path):
        from src.render.excel_renderer import ExcelRenderer
        from src.render.docx_renderer import DocxInformeRenderer
        from src.domain import RecognitionResult
        from openpyxl import load_workbook
        import re
        import docx

        df = self._df_caso_real()
        total_df = round(float(df["MONTO_NC"].sum()), 2)
        assert total_df == 1353.86

        resultado = RecognitionResult(
            dataframe=df[["SKU", "MONTO_NC"]],
            dataframe_excel=df,
            resumen={"total_nc": total_df, "skus_afectados": 3, "doc_ref": "F204-40260"},
        )

        xls = tmp_path / "Calculo.xlsx"
        ExcelRenderer().generar(
            resultado,
            str(xls),
            tipo="diferencia_precio",
            cliente="CLIENTE X",
            doc_ref="F204-40260",
            ruc="206039928",
        )
        wb = load_workbook(str(xls))
        ws = wb[wb.sheetnames[0]]

        netos_esperados = {22: 3.33082, 23: 6.66816, 24: 1.66541}
        # Localizar columnas por encabezado (la posición cambia cuando se
        # inyectan columnas DESC o PRECIO HIST. EFECTIVO).
        hdr_row = next(
            r
            for r in range(1, 40)
            if ws.cell(row=r, column=1).value == "N°"
            and ws.cell(row=r, column=2).value == "FACTURA"
            and any(ws.cell(row=r, column=c).value == "PRECIO NETO" for c in range(1, 30))
        )
        col_neto = next(
            c for c in range(1, 30) if ws.cell(row=hdr_row, column=c).value == "PRECIO NETO"
        )
        col_monto = next(c for c in range(1, 30) if ws.cell(row=hdr_row, column=c).value == "MONTO")
        for r, neto in netos_esperados.items():
            v = ws.cell(row=r, column=col_neto).value  # PRECIO NETO (tabla2)
            assert isinstance(v, (int, float)), f"neto fila {r} no es estático: {v!r}"
            assert abs(v - neto) < 1e-9

        # Evaluar las fórmulas como lo haría Excel (refs → valores, ROUND/MAX/SUM)
        def split_top(s):
            out, depth, cur = [], 0, ""
            for ch in s:
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                if ch == "," and depth == 0:
                    out.append(cur.strip())
                    cur = ""
                else:
                    cur += ch
            if cur.strip():
                out.append(cur.strip())
            return out

        def resolve(ref):
            r = ref.replace("$", "")
            val = ws[r].value
            if isinstance(val, (int, float)):
                return float(val)
            if isinstance(val, str) and val.startswith("="):
                return simulate(val[1:])
            raise TypeError(f"no resuelve {ref}: {val!r}")

        def expand_range(part):
            p = part.replace("$", "")
            if ":" not in p:
                return [p]
            from openpyxl.utils import column_index_from_string, get_column_letter

            m = re.fullmatch(r"([A-Z]+)(\d+):([A-Z]+)(\d+)", p)
            c1, r1, c2, r2 = m.groups()
            cols = range(column_index_from_string(c1), column_index_from_string(c2) + 1)
            return [
                f"{get_column_letter(ci)}{rr}" for rr in range(int(r1), int(r2) + 1) for ci in cols
            ]

        def simulate(expr):
            expr = expr.strip()
            m = re.fullmatch(r"(ROUND|MAX|SUM)\((.*)\)", expr, re.S)
            if m:
                name, args = m.groups()
                parts = split_top(args)
                if name == "SUM":
                    total = 0.0
                    for p in parts:
                        for ref in expand_range(p):
                            total += resolve(ref)
                    return total
                if name == "MAX":
                    return max(simulate(p) for p in parts)
                if name == "ROUND":
                    return round(simulate(parts[0]), int(parts[1]))
            sub = re.sub(
                r"\$?[A-Z]{1,3}\$?\d+",
                lambda mo: repr(resolve(mo.group(0))),
                expr,
            )
            return float(eval(sub, {"__builtins__": {}}))

        from openpyxl.utils import get_column_letter as _gcl

        monto_letter = _gcl(col_monto)
        montos = [resolve(f"{monto_letter}{r}") for r in (22, 23, 24)]
        assert montos == [555.16, 694.60, 104.10]
        assert round(resolve("G4"), 2) == total_df  # SUBTOTAL live = 1353.86

        # El docx muestra el mismo total
        out = tmp_path / "Informe.docx"
        DocxInformeRenderer().generar(
            resultado,
            ruta_salida=str(out),
            datos_adicionales={
                "tipo_operacion": "DC",
                "cliente": "CLIENTE X",
                "numero_referencia": "F204-40260",
                "evidencias": {},
            },
            nombre_archivo_excel="Calculo.xlsx",
        )
        txt = "\n".join(p.text for p in docx.Document(str(out)).paragraphs)
        txt += "\n" + "\n".join(
            c.text for t in docx.Document(str(out)).tables for r in t.rows for c in r.cells
        )
        assert f"{total_df:,.2f}" in txt


class TestHistoricoReclamos:
    def _hist(self):
        return pd.DataFrame(
            {
                "FECHA": ["15/01/2026", "20/02/2026", "10/03/2026"],
                "TIPO_DOC": ["F01", "F01", "F01"],
                "SERIE": ["201", "201", "201"],
                "NUMERO": ["100", "100", "101"],
                "CODIGO": ["S1", "S2", "S1"],
                "ARTICULO": ["P1", "P2", "P1"],
                "CANTIDAD": [10, 5, 7],
                "PRECIO_UNITARIO": [3.5, 2.0, 3.5],
                "SOLES": [35.0, 10.0, 24.5],
                "DOC_CLIENTE": ["206039928"] * 3,
            }
        )

    def test_resalta_reclamados_y_leyenda(self, tmp_path):
        from openpyxl import load_workbook
        from src.render.audit_renderer import generar_historico_clasico

        out = generar_historico_clasico(
            df_historial=self._hist(),
            cliente_nombre="C",
            cliente_ruc="R",
            tipo_operacion="diferencia_precio",
            expediente_id="1",
            ruta_salida=tmp_path / "Historico.xlsx",
            reclamados={("F201-100", "S1")},
        )
        wb = load_workbook(str(out))
        ws = wb["Historico"]
        # Fila 13 = S1/F100 reclamada; fila 14 = S2 no reclamada
        assert str(ws.cell(row=13, column=8).fill.start_color.rgb).endswith("FFF2CC")
        assert not str(ws.cell(row=14, column=8).fill.start_color.rgb).endswith("FFF2CC")
        nota = ws.cell(row=ws.max_row, column=1).value
        assert "amarillo" in nota and "Cálculo" in nota

    def test_resumen_cuenta_documentos_distintos(self, tmp_path):
        from openpyxl import load_workbook
        from src.render.audit_renderer import generar_historico_clasico

        out = generar_historico_clasico(
            df_historial=self._hist(),
            cliente_nombre="C",
            cliente_ruc="R",
            tipo_operacion="diferencia_precio",
            expediente_id="1",
            ruta_salida=tmp_path / "Historico.xlsx",
        )
        wb = load_workbook(str(out))
        ws = wb["Historico"]
        # 3 líneas de 2 documentos distintos (100 ×2, 101 ×1)
        assert "2 factura(s)" in ws.cell(row=11, column=1).value
        assert ws.column_dimensions["A"].width == 15
        assert ws.page_margins.left == 0.8
        assert ws.page_margins.right == 0.8

    def test_reclamados_de_df(self):
        from src.ui.expediente_service import _reclamados_de_df

        df = pd.DataFrame(
            [
                {"SKU": "S1", "FACTURA": "F201-101", "FACTURAS": "F201-100, F201-101"},
                {"SKU": "S2", "FACTURA": "F201-100", "FACTURAS": "F201-100"},
            ]
        )
        assert _reclamados_de_df(df) == {("F201-101", "S1"), ("F201-100", "S1"), ("F201-100", "S2")}


class TestHistoricoClasicoLayout:
    def test_logo_y_titulo_en_col_a_datos_en_col_b(self, tmp_path):
        """El reporte clásico: logo + título en columna A y datos desde la B."""
        from src.render.audit_renderer import generar_historico_clasico
        from openpyxl import load_workbook

        df = pd.DataFrame(
            {
                "FECHA": ["15/01/2026"],
                "TIPO_DOC": ["F01"],
                "SERIE": ["201"],
                "NUMERO": ["100"],
                "CODIGO": ["A001"],
                "ARTICULO": ["P"],
                "CANTIDAD": [10],
                "PRECIO_UNITARIO": [3.5],
                "SOLES": [35.0],
                "DOC_CLIENTE": ["206039928"],
            }
        )
        out = generar_historico_clasico(
            df_historial=df,
            cliente_nombre="CLIENTE X",
            cliente_ruc="206039928",
            tipo_operacion="diferencia_precio",
            expediente_id="68426-204-40260",
            ruta_salida=tmp_path / "Historico.xlsx",
        )
        wb = load_workbook(str(out))
        ws = wb["Historico"]

        hdr_row = 12
        assert ws["B1"].value == "REPORTE DE PRECIOS — SEGMENTO HISTÓRICO DEL ERP"
        assert "B1:J1" in [str(m) for m in ws.merged_cells.ranges]
        assert (
            ws.cell(row=3, column=1).value == "CLIENTE:"
        )  # metadatos suben a la fila 3 (sin filas vacías)
        assert ws.cell(row=hdr_row, column=1).value == "FECHA EMISION"  # tabla inicia en col A
        assert ws.cell(row=hdr_row, column=10).value == "RUC CLIENTE"
        assert ws.cell(row=hdr_row + 1, column=1).value is not None

        total_row = hdr_row + 1 + len(df)
        assert "TOTAL DE 1 REGISTRO" in str(ws.cell(row=total_row, column=6).value)

        assert ws._images and ws._images[0].anchor._from.col == 0
        assert ws._images[0].anchor._from.row == 0  # logo anclado en A1 (filas 1-2)
        ext = ws._images[
            0
        ].anchor.ext  # tamaño renderizado en EMU (68×76 px @96 dpi = 68*9525 × 76*9525)
        assert (ext.cx, ext.cy) == (68 * 9525, 76 * 9525)  # 1.80 × 2.00 cm
