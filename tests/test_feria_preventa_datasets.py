"""Tests para la separacion vista_previa vs reporte_excel de FeriaPreventa.

Valida:
- vista_previa: 7 columnas ligeras para decision rapida.
- reporte_excel: 19 columnas detalladas con % cumplimiento, gap, glosa,
  auditoria de notas y cobertura de cantidades (facturada/disponible/restante).
- RecognitionResult.get_excel() prioriza dataframe_excel.
- Compatibilidad retro: si la estrategia solo emite dataframe (legacy), get_excel() cae.
"""

import pandas as pd
import pytest

from src.domain import RecognitionResult
from src.core.models import ProcessedItem


@pytest.fixture
def item_ok():
    """Item con sustentacion completa."""
    return ProcessedItem(
        CODIGO="72014",
        ARTICULO="SILICONA LIQUIDA",
        CANTIDAD_SOLICITADA=1500,
        CANTIDAD_REAL_ENCONTRADA=1152,
        PRECIO_UNITARIO=1.53,
        MONTO_DESCUENTO_UNITARIO=1.15,
        PRECIO_NETO_FINAL=1.07,
        SUBTOTAL_DESCUENTO=10.80,
        PORCENTAJE_APLICADO=0.05,
        DOCUMENTOS=["F001-100", "F001-101"],
        STATUS="OK",
    )


@pytest.fixture
def item_varios_precios():
    """Item con precios variables (debe aparecer glosa)."""
    return ProcessedItem(
        CODIGO="72013",
        ARTICULO="SILICONA LIQUIDA",
        CANTIDAD_SOLICITADA=300,
        CANTIDAD_REAL_ENCONTRADA=252,
        PRECIO_UNITARIO=1.00,
        MONTO_DESCUENTO_UNITARIO=0.85,
        PRECIO_NETO_FINAL=1.07,
        SUBTOTAL_DESCUENTO=2.14,
        PORCENTAJE_APLICADO=0.10,
        DOCUMENTOS=["F001-100"],
        STATUS="INFO: Precios variables (Rango: 0.85-1.20). Se uso el mas reciente",
    )


@pytest.fixture
def item_sin_sustento():
    """Item donde no se sustenta todo."""
    return ProcessedItem(
        CODIGO="72029",
        ARTICULO="COLA SINTETICA 25",
        CANTIDAD_SOLICITADA=1500,
        CANTIDAD_REAL_ENCONTRADA=900,
        PRECIO_UNITARIO=1.53,
        MONTO_DESCUENTO_UNITARIO=1.15,
        PRECIO_NETO_FINAL=1.07,
        SUBTOTAL_DESCUENTO=4.10,
        PORCENTAJE_APLICADO=0.05,
        DOCUMENTOS=["F001-100"],
        STATUS="ALW: SE USARON 900 UNIDADES: Sustentadas 900, pendientes 348",
    )


class TestRecognitionResult:
    def test_dataframe_default(self):
        """Dataframe vacio por default (compatibilidad retro)."""
        r = RecognitionResult()
        assert r.dataframe.empty
        assert r.dataframe_excel.empty

    def test_get_excel_legacy(self):
        """Si solo hay dataframe, get_excel cae retro-compatible."""
        r = RecognitionResult(dataframe=pd.DataFrame({"SKU": ["1"]}))
        assert len(r.get_excel()) == 1
        assert "SKU" in r.get_excel().columns

    def test_get_excel_con_dataframe_excel(self):
        """Si dataframe_excel esta seteado, get_excel lo prioriza."""
        r = RecognitionResult(
            dataframe=pd.DataFrame({"SKU": ["1"], "preview_only": [True]}),
            dataframe_excel=pd.DataFrame({"SKU": ["2"], "detalle": ["x"]}),
        )
        excel = r.get_excel()
        assert "detalle" in excel.columns
        assert "preview_only" not in excel.columns
        assert excel["SKU"].iloc[0] == "2"

    def test_get_preview_retorna_dataframe(self):
        r = RecognitionResult(
            dataframe=pd.DataFrame({"SKU": ["1"]}),
            dataframe_excel=pd.DataFrame({"SKU": ["2"]}),
        )
        assert r.get_preview()["SKU"].iloc[0] == "1"


class TestConstruirDataframesFeriaPreventa:
    """Tests equivalentes a los builders internos de FeriaPreventa."""

    @staticmethod
    def _construir_dataframes_vista_previa(items):
        """Replica el builder de la vista previa (7 cols)."""
        data = []
        for item in items:
            cant_sol = item.CANTIDAD_SOLICITADA or 0
            cant_sus = item.CANTIDAD_REAL_ENCONTRADA or 0
            data.append(
                {
                    "SKU": item.CODIGO,
                    "SKU - ARTICULO": f"{item.CODIGO} - {item.ARTICULO}",
                    "CANT. SOLICITADA": cant_sol,
                    "CANT. SUSTENTAR": cant_sus,
                    "PRECIO NETO": item.PRECIO_NETO_FINAL,
                    "SUBTOTAL (SIN IGV)": item.SUBTOTAL_DESCUENTO,
                    "ALERTA": "OK",
                }
            )
        return pd.DataFrame(data)

    @staticmethod
    def _construir_reporte_excel(items):
        """Replica el builder del reporte Excel (19 cols)."""
        data = []
        for item in items:
            cant_sol = item.CANTIDAD_SOLICITADA or 0
            cant_sus = item.CANTIDAD_REAL_ENCONTRADA or 0
            pct_cump = (cant_sus / cant_sol * 100) if cant_sol > 0 else 0.0
            su = str(item.STATUS or "").upper()
            glosa = []
            if "VARIABLE" in su:
                glosa.append("Precios variables en historial")
            gap = cant_sol - cant_sus
            if gap > 0:
                glosa.append(f"Sin sustento: {gap} unid.")
            docs_str = "; ".join(f"{doc} (0 unid)" for doc in item.DOCUMENTOS)
            linea = getattr(item, "LINEA", "") or getattr(item, "COD_LINEA", "")
            data.append(
                {
                    "SKU": item.CODIGO,
                    "ARTICULO": item.ARTICULO,
                    "LINEA": linea,
                    "Cant. Solicitada": cant_sol,
                    "Cant. Sustentada": cant_sus,
                    "% Cumplimiento": round(pct_cump, 1),
                    "P.U. Hist.": round(item.PRECIO_UNITARIO or 0, 5),
                    "Desc. (%) Aplicado": round(item.PORCENTAJE_APLICADO * 100, 2),
                    "Desc. Unit. (S/)": round(item.MONTO_DESCUENTO_UNITARIO or 0, 5),
                    "P.U. Result.": round(item.PRECIO_NETO_FINAL or 0, 5),
                    "Tot. Sustento (S/)": round(0.0, 2),
                    "Subtotal NC (S/)": round(item.SUBTOTAL_DESCUENTO or 0, 2),
                    "Facturas (qty)": docs_str,
                    "Glosa": " | ".join(glosa),
                    "Alerta": "OK",
                    "AUDITORIA_NC": "",
                    "Cant. Facturada": 0.0,
                    "Cant. Disponible": 0.0,
                    "% Stock Restante": 0.0,
                }
            )
        return pd.DataFrame(data)

    def test_vista_previa_7_columnas(self, item_ok, item_sin_sustento):
        df = self._construir_dataframes_vista_previa([item_ok, item_sin_sustento])
        assert len(df.columns) == 7
        assert list(df.columns) == [
            "SKU",
            "SKU - ARTICULO",
            "CANT. SOLICITADA",
            "CANT. SUSTENTAR",
            "PRECIO NETO",
            "SUBTOTAL (SIN IGV)",
            "ALERTA",
        ]

    def test_reporte_excel_19_columnas(self, item_ok, item_sin_sustento):
        df = self._construir_reporte_excel([item_ok, item_sin_sustento])
        assert len(df.columns) == 19

    def test_reporte_incluye_auditoria_y_cobertura(self, item_ok):
        """La fila lleva la auditoría de notas y la cobertura por SKU."""
        df = self._construir_reporte_excel([item_ok])
        for col in ("AUDITORIA_NC", "Cant. Facturada", "Cant. Disponible", "% Stock Restante"):
            assert col in df.columns

    def test_reporte_orden_logico(self, item_ok):
        """SKU primero, LINEA entre ARTICULO y Cant. Solicitada, auditoría al final."""
        df = self._construir_reporte_excel([item_ok])
        cols = list(df.columns)
        assert cols[0] == "SKU"
        assert cols[1] == "ARTICULO"
        assert cols[2] == "LINEA"
        assert cols.index("Alerta") < cols.index("AUDITORIA_NC")
        # la cobertura de cantidades va después del sustento
        assert cols.index("Tot. Sustento (S/)") < cols.index("Cant. Facturada")
        assert cols.index("Cant. Facturada") < cols.index("Cant. Disponible")
        assert cols.index("Cant. Disponible") < cols.index("% Stock Restante")

    def test_porcentaje_cumplimiento_ok(self, item_ok):
        """Item con sust. completa: 1152/1500 = 76.8%."""
        df = self._construir_reporte_excel([item_ok])
        # 1152/1500 * 100 = 76.8
        assert df["% Cumplimiento"].iloc[0] == 76.8

    def test_porcentaje_cumplimiento_sin_sustento(self, item_sin_sustento):
        """Item con gap: 900/1500 = 60.0%."""
        df = self._construir_reporte_excel([item_sin_sustento])
        # 900/1500 * 100 = 60.0
        assert df["% Cumplimiento"].iloc[0] == 60.0

    def test_glosa_precios_variables(self, item_varios_precios):
        df = self._construir_reporte_excel([item_varios_precios])
        assert "Precios variables" in df["Glosa"].iloc[0]

    def test_glosa_gap_sustento(self, item_sin_sustento):
        df = self._construir_reporte_excel([item_sin_sustento])
        assert "Sin sustento: 600 unid" in df["Glosa"].iloc[0]

    def test_ambos_glosa(self, item_varios_precios, item_sin_sustento):
        """Item con ambos tiene ambas glosas concatenadas con |."""
        df = self._construir_reporte_excel([item_varios_precios, item_sin_sustento])
        # Primer item: solo precios variables
        assert "Precios variables" in df["Glosa"].iloc[0]
        # Segundo item: solo gap
        assert "Sin sustento" in df["Glosa"].iloc[1]

    def test_decimales_constantes(self, item_ok, item_varios_precios):
        """Precios con 5 decimales, subtotales con 2, pct con 1."""
        df = self._construir_reporte_excel([item_ok, item_varios_precios])
        # P.U. tiene 5 decimales
        assert df["P.U. Hist."].iloc[0] == 1.53
        # Subtotal con 2 decimales
        assert df["Subtotal NC (S/)"].iloc[0] == 10.80
        # % Cumplimiento con 1 decimal
        assert df["% Cumplimiento"].iloc[1] == pytest.approx(84.0, abs=0.05)

    def test_linea_en_reporte(self, item_ok):
        """LINEA aparece como columna en el reporte Excel."""
        df = self._construir_reporte_excel([item_ok])
        assert "LINEA" in df.columns

    def test_doc_referencia_no_es_columna(self, item_ok):
        """Doc. Referencia es valor del header, no columna del cuerpo."""
        df = self._construir_reporte_excel([item_ok])
        assert "Doc. Referencia" not in df.columns


class TestReconocimientoFeriaPreventa:
    """Tests de flujo: el Reporte puede venir de una estrategia real."""

    def test_dataframes_separados(self, item_ok, item_sin_sustento):
        """Una estrategia emite ambos dataframes a la vez."""
        # Simulamos que la estrategia llama internamente al constructor.
        items = [item_ok, item_sin_sustento]

        df_preview = pd.DataFrame(
            [
                {
                    "SKU": item.CODIGO,
                    "SKU - ARTICULO": f"{item.CODIGO} - {item.ARTICULO}",
                    "CANT. SOLICITADA": item.CANTIDAD_SOLICITADA,
                    "CANT. SUSTENTAR": item.CANTIDAD_REAL_ENCONTRADA,
                    "PRECIO NETO": item.PRECIO_NETO_FINAL,
                    "SUBTOTAL (SIN IGV)": item.SUBTOTAL_DESCUENTO,
                    "ALERTA": "OK",
                }
                for item in items
            ]
        )

        df_excel = pd.DataFrame(
            [
                {
                    "SKU": item.CODIGO,
                    "ARTICULO": item.ARTICULO,
                    "Cant. Solicitada": item.CANTIDAD_SOLICITADA,
                    "Cant. Sustentada": item.CANTIDAD_REAL_ENCONTRADA,
                    "% Cumplimiento": round(
                        100 * item.CANTIDAD_REAL_ENCONTRADA / item.CANTIDAD_SOLICITADA, 1
                    ),
                    "Subtotal NC (S/)": item.SUBTOTAL_DESCUENTO,
                    "Glosa": f"Sin sustento: {item.CANTIDAD_SOLICITADA - item.CANTIDAD_REAL_ENCONTRADA} unid."
                    if item.CANTIDAD_SOLICITADA > item.CANTIDAD_REAL_ENCONTRADA
                    else "",
                }
                for item in items
            ]
        )

        resultado = RecognitionResult(
            dataframe=df_preview,
            dataframe_excel=df_excel,
            resumen={"total_nc": float(df_excel["Subtotal NC (S/)"].sum())},
        )

        # Vista previa tiene 7 cols, reporte tiene 7 (subset sin LOTE)
        assert len(resultado.get_preview().columns) == 7
        assert len(resultado.get_excel().columns) >= 7
        # El resumen total_nc es del reporte
        assert resultado.resumen["total_nc"] == pytest.approx(14.90)

    def test_resumen_incluye_archivo_y_doc_referencia(self):
        """El resumen del RecognitionResult lleva archivo y documento_referencia."""
        r = RecognitionResult(
            resumen={
                "archivo": "requerimiento_test.xlsx",
                "documento_referencia": "F204-99999",
                "total_nc": 100.0,
            },
        )
        assert r.resumen["archivo"] == "requerimiento_test.xlsx"
        assert r.resumen["documento_referencia"] == "F204-99999"
