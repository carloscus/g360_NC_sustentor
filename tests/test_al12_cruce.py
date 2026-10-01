"""Cruce NC/NDB por (factura, SKU): AL12 con cantidades FAE."""

import pandas as pd

from src.core.detector import (
    detectar_notas_en_historial,
    resumen_notas_por_factura,
)
from src.domain import generar_texto_alerta
from src.ui.view_handlers import expandir_facturas


def _nota(tipo="NCR", serie="900", nro="1", ref="F201-100", sku="S1", cant=0, fae=15, soles=-50.0):
    return {
        "TIPO_DOC": tipo,
        "SERIE": serie,
        "NUMERO": nro,
        "REFERENCIA": ref,
        "CODIGO": sku,
        "ARTICULO": "P1",
        "CANTIDAD": cant,
        "CANTIDAD_FAE": fae,
        "SOLES": soles,
    }


class TestDetectorFae:
    def test_fae_en_output(self):
        notas = detectar_notas_en_historial(pd.DataFrame([_nota()]))
        assert notas["CANTIDAD_FAE"].iloc[0] == 15.0
        assert notas["DOC_NOTA"].iloc[0] == "N900-1"

    def test_resumen_docs_con_fae(self):
        notas = detectar_notas_en_historial(pd.DataFrame([_nota()]))
        r = resumen_notas_por_factura(notas)
        docs = r["F201-100"]["skus"]["S1"]["docs"]
        assert docs == [{"doc": "N900-1", "tipo": "NC", "qty": 15.0, "fae": True}]

    def test_resumen_cantidad_sin_fae(self):
        notas = detectar_notas_en_historial(pd.DataFrame([_nota(cant=8, fae=0)]))
        r = resumen_notas_por_factura(notas)
        docs = r["F201-100"]["skus"]["S1"]["docs"]
        assert docs == [{"doc": "N900-1", "tipo": "NC", "qty": 8.0, "fae": False}]

    def test_resumen_ndb(self):
        notas = detectar_notas_en_historial(
            pd.DataFrame([_nota(tipo="NDB", cant=3, fae=0, soles=30.0)])
        )
        r = resumen_notas_por_factura(notas)
        docs = r["F201-100"]["skus"]["S1"]["docs"]
        assert docs[0]["tipo"] == "NDB"


class TestTextoAL12:
    def test_nc_con_fae(self):
        t = generar_texto_alerta(
            "AL12",
            factura="F204-40260",
            docs=[{"doc": "215-2025", "tipo": "NC", "qty": 15, "fae": True}],
            sku="016757",
        )
        assert t == (
            "AL12 - Factura F204-40260 cuenta con NC 215-2025 x 15 unid (FAE) (SKU 016757)"
        )

    def test_ndb_sin_fae(self):
        t = generar_texto_alerta(
            "AL12",
            factura="F204-100",
            docs=[{"doc": "N900-1", "tipo": "NDB", "qty": 3, "fae": False}],
            sku="S1",
        )
        assert t == ("AL12 - Factura F204-100 cuenta con NDB N900-1 x 3 unid (SKU S1)")

    def test_multiples_docs(self):
        t = generar_texto_alerta(
            "AL12",
            factura="F204-100",
            sku="S1",
            docs=[
                {"doc": "N1", "tipo": "NC", "qty": 5, "fae": True},
                {"doc": "N2", "tipo": "NDB", "qty": 2, "fae": False},
            ],
        )
        assert t == (
            "AL12 - Factura F204-100 cuenta con NC N1 x 5 unid (FAE), NDB N2 x 2 unid (SKU S1)"
        )

    def test_omite_doc_vacio(self):
        t = generar_texto_alerta(
            "AL12",
            factura="F1",
            sku="S1",
            docs=[
                {"doc": "", "tipo": "NC", "qty": 1, "fae": False},
                {"doc": "N1", "tipo": "NC", "qty": 1, "fae": False},
            ],
        )
        assert "N1 x 1 unid" in t


class TestExpandirFacturas:
    def test_unico(self):
        assert expandir_facturas("F201-100") == ["F201-100"]

    def test_lista_comas(self):
        assert expandir_facturas("F201-100, F201-101") == ["F201-100", "F201-101"]

    def test_punto_coma_y_sufijo(self):
        assert expandir_facturas("F201-100 (5 unid); F201-101") == ["F201-100", "F201-101"]

    def test_numero_suelto_con_mapa(self):
        assert expandir_facturas("100, 101", {"100": ["F204-100"], "101": ["F204-101"]}) == [
            "F204-100",
            "F204-101",
        ]

    def test_vacio(self):
        assert expandir_facturas("") == []
        assert expandir_facturas("nan") == []
