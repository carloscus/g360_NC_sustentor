"""VRS shares exact note adjustments but respects its requested quantity."""

import pandas as pd

from src.core.nc_reconciliation import reconciliar_notas
from src.domain import ExpedienteComercial, PipelineContext
from src.strategies.cantidad_determinada import CantidadDeterminadaStrategy


def _history_with_exact_fae():
    return pd.DataFrame(
        [
            {
                "CODIGO": "A1",
                "ARTICULO": "Producto A",
                "CANTIDAD": 100.0,
                "SOLES": 1000.0,
                "FECHA": pd.Timestamp("2026-01-01"),
                "TIPO_DOC": "F01",
                "SERIE": "001",
                "NUMERO": "10",
                "DOC_ID": "F001-10",
                "TIPO_CLASE": "factura",
                "FACTURA_REF": "",
            },
            {
                "CODIGO": "A1",
                "ARTICULO": "Producto A",
                "CANTIDAD": 0.0,
                "CANTIDAD_FAE": 100.0,
                "SOLES": -100.0,
                "FECHA": pd.Timestamp("2026-01-10"),
                "TIPO_DOC": "NCR",
                "SERIE": "N001",
                "NUMERO": "1",
                "DOC_ID": "N001-1",
                "TIPO_CLASE": "descuento",
                "FACTURA_REF": "F001-10",
            },
        ]
    ).fillna({"CANTIDAD_FAE": 0.0})


def _run(modalidad):
    hist = _history_with_exact_fae()
    rec = reconciliar_notas(
        hist,
        documentos={
            "facturas": [True, True],
            "nc": [True, True],
            "ndb": [True, False],
        },
        modalidad=modalidad,
    )
    exp = ExpedienteComercial(
        nombre="VRS",
        familia="precio",
        estrategia="CantidadDeterminada",
        datos=hist[hist.TIPO_CLASE == "factura"].copy(),
        contexto=PipelineContext(
            config={
                "modalidad": modalidad,
                "sort_mode": "fecha_asc",
                "reconciliacion_nc": rec,
            }
        ),
    )
    exp.condiciones = [
        pd.DataFrame(
            {
                "SKU": ["A1"],
                "CANTIDAD_NC": [50.0],
                "PRECIO_BASE": [8.0],
            }
        )
    ]
    return CantidadDeterminadaStrategy().process(exp)


def test_vrs_individual_uses_exact_fae_for_price_not_requested_qty():
    result = _run("individual")
    row = result.dataframe.iloc[0]
    # PU effective: (1000 - 100) / 100 = 9; requested 50 => 50 × (9 - 8).
    assert row["PRECIO_HIST"] == 9.0
    assert row["CANTIDAD"] == 50.0
    assert row["MONTO_NC"] == 50.0
    assert row["Cantidad Facturada"] == 100.0
    assert row["Cantidad Disponible"] == 100.0
    assert "APLICADA" in row["ALERTA"]
    assert "N001-1" in row["AUDITORIA_NC"]


def test_vrs_consolidated_shows_exact_fae_but_keeps_invoice_price():
    result = _run("consolidado")
    row = result.dataframe.iloc[0]
    assert row["PRECIO_HIST"] == 10.0
    assert row["CANTIDAD"] == 50.0
    assert row["MONTO_NC"] == 100.0
    assert "no aplicado" in row["ALERTA"]
    assert "N001-1" in row["AUDITORIA_NC"]


def test_vrs_exact_physical_return_removes_available_qty_but_warns_without_stopping():
    hist = _history_with_exact_fae()
    hist.loc[
        hist.TIPO_CLASE == "descuento",
        ["TIPO_DOC", "TIPO_CLASE", "CANTIDAD", "CANTIDAD_FAE", "SOLES"],
    ] = ["NCR", "devolucion", -100.0, 0.0, -1000.0]
    policy = {"facturas": [True, True], "nc": [True, True], "ndb": [True, False]}
    rec = reconciliar_notas(hist, documentos=policy, modalidad="individual")
    exp = ExpedienteComercial(
        nombre="VRS",
        familia="precio",
        estrategia="CantidadDeterminada",
        datos=hist[hist.TIPO_CLASE == "factura"].copy(),
        contexto=PipelineContext(
            config={
                "modalidad": "individual",
                "sort_mode": "fecha_asc",
                "reconciliacion_nc": rec,
            }
        ),
    )
    exp.condiciones = [
        pd.DataFrame(
            {
                "SKU": ["A1"],
                "CANTIDAD_NC": [50.0],
                "PRECIO_BASE": [8.0],
            }
        )
    ]
    result = CantidadDeterminadaStrategy().process(exp)
    assert result.dataframe.empty
    assert any(a.codigo == "AL09" for a in result.alertas)
    assert any(a.codigo == "AL12" and "cantidad reducida" in a.mensaje for a in result.alertas)
