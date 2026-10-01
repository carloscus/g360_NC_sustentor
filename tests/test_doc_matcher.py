import pytest
import pandas as pd
from datetime import datetime
from src.core.doc_matcher import seleccionar_mejor_documento


@pytest.fixture
def historial_con_varias_facturas():
    """Historial de un solo cliente con 3 facturas de distinta cobertura."""
    return pd.DataFrame(
        {
            "CODIGO": ["A", "A", "A", "B", "C", "D", "E"],
            "CANTIDAD": [10, 5, 3, 8, 6, 4, 2],
            "SOLES": [100, 50, 30, 120, 90, 60, 30],
            "PRECIO_UNITARIO": [10.0, 10.0, 10.0, 15.0, 15.0, 15.0, 15.0],
            "TIPO_DOC": ["F", "F", "F", "F", "F", "F", "F"],
            "SERIE": ["001", "001", "002", "002", "001", "002", "001"],
            "NUMERO": ["100", "101", "200", "200", "102", "201", "103"],
            "FECHA": [
                datetime(2024, 6, 1),
                datetime(2024, 6, 5),
                datetime(2024, 7, 1),
                datetime(2024, 7, 1),
                datetime(2024, 5, 1),
                datetime(2024, 7, 10),
                datetime(2024, 4, 1),
            ],
            "CLIENTE": ["X", "X", "X", "X", "X", "X", "X"],
            "COD_CLIENTE": ["C001"] * 7,
        }
    )


@pytest.fixture
def requerimiento_tres_skus():
    """Req con 3 SKUs (A, B, C) y montos que suman S/ 210 de sustento."""
    return pd.DataFrame(
        {
            "CODIGO": ["A", "B", "C"],
            "CANTIDAD_NC": [10, 5, 4],
            "PORCENTAJE_DESC": [0.10, 0.05, 0.0],
        }
    )


class TestSeleccionarMejorDocumento:
    def test_selecciona_factura_solvente_con_mas_skus(
        self, historial_con_varias_facturas, requerimiento_tres_skus
    ):
        """Factura F002-200 tiene SKUs A, B (2 coinciden) y SOLES=150 — debe ser elegida si es solvente."""
        doc = seleccionar_mejor_documento(historial_con_varias_facturas, requerimiento_tres_skus)
        assert doc == "F002-200"

    def test_retorna_vacio_si_historial_vacio(self, requerimiento_tres_skus):
        doc = seleccionar_mejor_documento(pd.DataFrame(), requerimiento_tres_skus)
        assert doc == ""

    def test_retorna_vacio_si_requerimiento_vacio(self, historial_con_varias_facturas):
        doc = seleccionar_mejor_documento(historial_con_varias_facturas, pd.DataFrame())
        assert doc == ""

    def test_retorna_vacio_si_requerimiento_sin_skus(self, historial_con_varias_facturas):
        df = pd.DataFrame({"CANTIDAD_NC": [1]})  # sin CODIGO
        doc = seleccionar_mejor_documento(historial_con_varias_facturas, df)
        assert doc == ""

    def test_factura_unica_coincide(self):
        """Un solo SKU en requerimiento, una sola factura con ese SKU."""
        hist = pd.DataFrame(
            {
                "CODIGO": ["X"],
                "CANTIDAD": [5],
                "SOLES": [100],
                "PRECIO_UNITARIO": [20.0],
                "TIPO_DOC": ["F"],
                "SERIE": ["A"],
                "NUMERO": ["1"],
                "FECHA": [datetime(2024, 1, 1)],
            }
        )
        req = pd.DataFrame({"CODIGO": ["X"], "CANTIDAD_NC": [2], "PORCENTAJE_DESC": [0.0]})
        doc = seleccionar_mejor_documento(hist, req)
        assert doc == "FA-1"

    def test_factura_con_mas_skus_gana(self):
        """Aunque la factura B tenga menor monto, gana por tener más SKUs."""
        hist = pd.DataFrame(
            {
                "CODIGO": ["A", "A", "B", "C"],
                "CANTIDAD": [5, 3, 4, 2],
                "SOLES": [50, 30, 100, 80],
                "PRECIO_UNITARIO": [10.0, 10.0, 25.0, 40.0],
                "TIPO_DOC": ["F", "F", "F", "F"],
                "SERIE": ["1", "1", "2", "3"],
                "NUMERO": ["1", "2", "1", "1"],
                "FECHA": [datetime(2024, 1, 1)] * 4,
            }
        )
        req = pd.DataFrame(
            {
                "CODIGO": ["A", "B", "C"],
                "CANTIDAD_NC": [1, 1, 1],
                "PORCENTAJE_DESC": [0, 0, 0],
            }
        )
        # F11 tiene solo A (score=1), F21 tiene B (score=1), F31 tiene C (score=1)
        # El monto requerido: (1*10)+(1*25)+(1*40)=75
        # F11=50 < 75, F21=100 >= 75, F31=80 >= 75
        # Entre F21 y F31, ambas score=1, gana F21 (100>80)
        doc = seleccionar_mejor_documento(hist, req)
        assert doc == "F2-1"

    def test_fallback_sin_solvente(self):
        """Ninguna factura cubre el monto — gana la de mayor score."""
        hist = pd.DataFrame(
            {
                "CODIGO": ["A", "A", "B"],
                "CANTIDAD": [1, 1, 1],
                "SOLES": [10, 10, 5],
                "PRECIO_UNITARIO": [10.0, 10.0, 5.0],
                "TIPO_DOC": ["F", "F", "F"],
                "SERIE": ["1", "1", "2"],
                "NUMERO": ["1", "2", "1"],
                "FECHA": [datetime(2024, 1, 1)] * 3,
            }
        )
        req = pd.DataFrame(
            {
                "CODIGO": ["A", "B"],
                "CANTIDAD_NC": [10, 10],
                "PORCENTAJE_DESC": [0, 0],
            }
        )
        # Monto req: 10*10 + 10*5 = 150
        # F11=10 < 150, F12=10 < 150, F21=5 < 150
        # Ninguna solvente → fallback: gana F11 (score=1, SOLES=10)
        doc = seleccionar_mejor_documento(hist, req)
        assert doc == "F1-1"

    def test_desempate_por_fecha_mas_reciente(self):
        """Mismo score y mismo monto, gana la más reciente."""
        hist = pd.DataFrame(
            {
                "CODIGO": ["A", "A"],
                "CANTIDAD": [5, 5],
                "SOLES": [100, 100],
                "PRECIO_UNITARIO": [20.0, 20.0],
                "TIPO_DOC": ["F", "F"],
                "SERIE": ["A", "A"],  # serie "A" → format_doc_id produce "FA-xxx"
                "NUMERO": ["100", "101"],
                "FECHA": [datetime(2024, 1, 1), datetime(2024, 6, 1)],
            }
        )
        req = pd.DataFrame(
            {
                "CODIGO": ["A"],
                "CANTIDAD_NC": [2],
                "PORCENTAJE_DESC": [0],
            }
        )
        # FA-100 y FA-101 tienen score=1, SOLES=100 >= 40 (solventes)
        # Gana la más reciente: FA-101
        doc = seleccionar_mejor_documento(hist, req)
        assert doc == "FA-101"

    def test_monto_requerido_con_descuento(self):
        """El monto requerido se calcula como cant * precio * (1-desc)."""
        hist = pd.DataFrame(
            {
                "CODIGO": ["A", "A"],
                "CANTIDAD": [5, 5],
                "SOLES": [200, 50],
                "PRECIO_UNITARIO": [40.0, 10.0],
                "TIPO_DOC": ["F", "F"],
                "SERIE": ["A", "A"],  # serie "A" → format_doc_id produce "FA-xxx"
                "NUMERO": ["100", "200"],
                "FECHA": [datetime(2024, 1, 1)] * 2,
            }
        )
        req = pd.DataFrame(
            {
                "CODIGO": ["A"],
                "CANTIDAD_NC": [10],
                "PORCENTAJE_DESC": [0.50],  # 50% descuento → monto = 10*40*0.5 = 200
            }
        )
        # FA-100: score=1, SOLES=200 >= 200 → solvente
        # FA-200: score=1, SOLES=50 < 200 → no solvente
        doc = seleccionar_mejor_documento(hist, req)
        assert doc == "FA-100"
