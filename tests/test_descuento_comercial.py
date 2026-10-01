# -*- coding: utf-8 -*-
"""Rutas de Descuento Comercial (DO): archivo por SKU, % global y filtro SKU.

Flujo esperado: historial × SKUs con descuento → por factura y SKU,
precio atendido × (1 - %) × cantidad.
"""

import pandas as pd

from src.domain import ExpedienteComercial, PipelineContext
from src.pipeline import Pipeline


def _historial():
    return pd.DataFrame(
        {
            "COD_CLIENTE": ["00068426"] * 4,
            "CLIENTE": ["CLIENTE X"] * 4,
            "TIPO_DOC": ["F01"] * 4,
            "SERIE": ["204"] * 4,
            "NUMERO": ["40260", "40260", "40261", "40261"],
            "FECHA": ["05/01/2026"] * 4,
            "CODIGO": ["02203", "02202", "02203", "02204"],
            "ARTICULO": ["PELOTA A", "FORRO B", "PELOTA A", "NET C"],
            "LINEA": ["01 PELOTAS", "02 FORROS", "01 PELOTAS", "03 NETS"],
            "CANTIDAD": [100, 50, 200, 30],
            "SOLES": [1000.0, 750.0, 2000.0, 450.0],
            "PRECIO_UNITARIO": [10.0, 15.0, 10.0, 15.0],
            "DOC_CLIENTE": ["206039928"] * 4,
        }
    )


def _ejecutar_do(datos, config, condiciones=None):
    exp = ExpedienteComercial(
        nombre="Descuento comercial",
        familia="Descuento comercial",
        estrategia="PriceDifference",
        variante="discount_period",
        datos=datos,
        contexto=PipelineContext(config=config, antecedentes="", observaciones=""),
    )
    exp.condiciones = condiciones or []
    return Pipeline().ejecutar(exp)


class TestDescuentoArchivo:
    def test_archivo_sku_con_pct(self):
        """02203×5%: 50+100; 02202×10%: 75 → total 225."""
        desc = pd.DataFrame({"CODIGO_SKU": ["02203", "02202"], "DESCUENTO_PORCENTAJE": [5, 10]})
        exp = _ejecutar_do(_historial(), {"descuento_pct": 0.0}, [desc])
        assert exp.resultado is not None
        assert round(float(exp.resultado.resumen["total_nc"]), 2) == 225.0
        assert exp.resultado.resumen["skus_afectados"] == 2


class TestDescuentoGlobal:
    def test_pct_en_porcentaje_no_fraccion(self):
        """5 (% campo UI) sobre 4200 vendido → 210, no 21000."""
        exp = _ejecutar_do(_historial(), {"descuento_pct": 5.0})
        assert round(float(exp.resultado.resumen["total_nc"]), 2) == 210.0

    def test_pct_fraccion_tambien_vale(self):
        exp = _ejecutar_do(_historial(), {"descuento_pct": 0.05})
        assert round(float(exp.resultado.resumen["total_nc"]), 2) == 210.0


class TestDescuentoFiltroSku:
    def test_filtro_con_pct_sin_archivo(self):
        """Mapa SKU→% funciona sin archivo en condiciones: mismo 225."""
        exp = _ejecutar_do(
            _historial(), {"descuento_pct": 0.0, "sku_filter": {"02203": 5.0, "02202": 10.0}}
        )
        assert round(float(exp.resultado.resumen["total_nc"]), 2) == 225.0

    def test_filtro_restringe_global(self):
        """Filtro a 02203 + global 10% → (1000+2000)×10% = 300."""
        exp = _ejecutar_do(_historial(), {"descuento_pct": 10.0, "sku_filter": {"02203": 0}})
        assert round(float(exp.resultado.resumen["total_nc"]), 2) == 300.0
        assert exp.resultado.resumen["skus_afectados"] == 1

    def test_filtro_sin_pct_y_sin_global_da_error_claro(self):
        exp = _ejecutar_do(_historial(), {"descuento_pct": 0.0, "sku_filter": {"02203": 0}})
        assert exp.resultado.dataframe.empty
        msgs = " ".join(str(a.mensaje) for a in exp.resultado.alertas)
        assert "sin %" in msgs.lower() or "descuento global" in msgs.lower()
