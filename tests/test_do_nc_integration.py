"""NC/NDB reconciliation semantics in the DO (descuento comercial) engine."""

import pandas as pd

from src.core.nc_reconciliation import reconciliar_notas
from src.domain import ExpedienteComercial, PipelineContext
from src.pipeline import Pipeline
from src.strategies.price_difference import PriceDifferenceStrategy
from src.ui.config_builder import build_config, build_datos_exp


def _history(note_rows=()):
    rows = [
        {
            "CODIGO": "00123",
            "ARTICULO": "Producto X",
            "CANTIDAD": 100.0,
            "SOLES": 1000.0,
            "FECHA": pd.Timestamp("2026-01-15"),
            "TIPO_DOC": "F01",
            "SERIE": "001",
            "NUMERO": "10",
            "DOC_ID": "F001-10",
            "TIPO_CLASE": "factura",
            "FACTURA_REF": "",
            "CANTIDAD_FAE": 0.0,
        }
    ]
    for i, n in enumerate(note_rows, 1):
        rows.append(
            {
                "CODIGO": n.get("sku", "00123"),
                "ARTICULO": "Producto X",
                "CANTIDAD": n.get("cantidad", 0.0),
                "CANTIDAD_FAE": n.get("fae", 0.0),
                "SOLES": n.get("soles", -100.0),
                "FECHA": pd.Timestamp("2026-01-20"),
                "TIPO_DOC": n.get("tipo", "NCR"),
                "SERIE": "N900",
                "NUMERO": str(i),
                "DOC_ID": f"NCRN900-{i}",
                "TIPO_CLASE": n.get("clase", "descuento"),
                "FACTURA_REF": n.get("ref", "F001-10"),
                "REFERENCIA": "F01/001-10",
            }
        )
    return pd.DataFrame(rows)


def _run(hist, modalidad="individual", use_nc=True):
    options = {
        "facturas": {"mostrar": True, "usar": True},
        "nc": {"mostrar": True, "usar": use_nc},
        "ndb": {"mostrar": True, "usar": True},
    }
    rec = reconciliar_notas(hist, documentos=options, modalidad=modalidad)
    facts = hist[hist.TIPO_CLASE == "factura"].copy()
    desc = pd.DataFrame({"CODIGO": ["00123"], "DESCUENTO": [0.10]})
    result, row_alerts = PriceDifferenceStrategy()._procesar_descuento_simple(
        facts, desc, [], config={"modalidad": modalidad, "reconciliacion_nc": rec}
    )
    return result, row_alerts


def test_exact_fae_note_adjusts_price_before_discount():
    result, row_alerts = _run(_history([{"fae": 100, "soles": -100}]))
    row = result.iloc[0]
    assert row["PRECIO_HIST"] == 9.0
    assert row["PRECIO_HIST_FACTURA"] == 10.0
    assert row["SOLES"] == 1000.0
    assert row["SOLES_CALCULO"] == 900.0
    assert row["CANTIDAD"] == 100
    assert row["MONTO_NC"] == 90.0
    assert "APLICADA" in row["ALERTA"]
    assert "N900-1" in row["AUDITORIA_NC"]
    assert "DESC 10.00%" in row["AUDITORIA_NC"]
    assert any(a.codigo == "AL12" and "APLICADA" in a.mensaje for a in row_alerts)


def test_exact_return_reduces_eligible_qty_to_zero():
    result, row_alerts = _run(_history([{"clase": "devolucion", "cantidad": -100, "soles": -1000}]))
    row = result.iloc[0]
    assert row["CANTIDAD_FACTURADA"] == 100
    assert row["CANTIDAD"] == 0
    assert row["PRECIO_HIST"] == 10.0
    assert row["MONTO_NC"] == 0
    assert "cantidad reducida" in row["ALERTA"]
    assert "N900-1" in row["AUDITORIA_NC"]
    assert not any(a.codigo == "AL01" for a in row_alerts)


def test_partial_note_does_not_apply_but_is_alerted():
    result, row_alerts = _run(_history([{"fae": 40, "soles": -40}]))
    row = result.iloc[0]
    assert row["SOLES_CALCULO"] == 1000
    assert row["CANTIDAD"] == 100
    assert row["MONTO_NC"] == 100
    assert "REVISIÓN MANUAL" in row["ALERTA"]
    assert any(a.codigo == "AL12" for a in row_alerts)


def test_excess_note_raises_al13_without_changing_calc():
    result, row_alerts = _run(_history([{"fae": 120, "soles": -120}]))
    row = result.iloc[0]
    assert row["SOLES_CALCULO"] == 1000
    assert row["MONTO_NC"] == 100
    assert "AL13" in row["ALERTA"]
    assert "no aplicado" in row["ALERTA"]
    assert any(a.codigo == "AL13" for a in row_alerts)


def test_exact_note_without_usar_is_shown_but_not_applied():
    result, _alerts = _run(_history([{"fae": 100, "soles": -100}]), use_nc=False)
    row = result.iloc[0]
    assert row["SOLES_CALCULO"] == 1000
    assert row["PRECIO_HIST"] == 10.0
    assert row["MONTO_NC"] == 100
    assert "Usar desactivado" in row["ALERTA"]
    assert "N900-1" in row["AUDITORIA_NC"]


def test_consolidated_note_is_detailed_but_not_applied():
    result, row_alerts = _run(_history([{"fae": 100, "soles": -100}]), modalidad="consolidado")
    row = result.iloc[0]
    assert row["SOLES_CALCULO"] == 1000
    assert row["CANTIDAD"] == 100
    assert row["MONTO_NC"] == 100
    assert "no aplicado" in row["ALERTA"]
    assert "N900-1" in row["AUDITORIA_NC"]
    assert not any(a.codigo == "AL12" for a in row_alerts)


def test_global_discount_mode_applies_note_adjustment():
    hist = _history([{"fae": 100, "soles": -100}])
    rec = reconciliar_notas(
        hist,
        documentos={
            "facturas": {"mostrar": True, "usar": True},
            "nc": {"mostrar": True, "usar": True},
            "ndb": {"mostrar": True, "usar": True},
        },
        modalidad="individual",
    )
    facts = hist[hist.TIPO_CLASE == "factura"].copy()
    result, row_alerts = PriceDifferenceStrategy()._procesar_descuento_global(
        facts, 0.10, [], config={"modalidad": "individual", "reconciliacion_nc": rec}
    )
    row = result.iloc[0]
    assert row["PRECIO_HIST"] == 9.0
    assert row["MONTO_NC"] == 90.0
    assert "APLICADA" in row["ALERTA"]
    assert "N900-1" in row["AUDITORIA_NC"]
    assert any(a.codigo == "AL12" for a in row_alerts)


def test_pipeline_do_uses_live_history_checks_for_exact_note():
    hist = _history([{"fae": 100, "soles": -100}])
    policy = {
        "facturas": [True, True],
        "nc": [True, True],
        "ndb": [True, False],
    }
    ui = {
        "modalidad": "individual",
        "historico_config": policy,
        "cliente_pb": "CLIENTE X",
        "df_historial_full": hist,
    }
    config = build_config("descuento_precio", ui)
    data = build_datos_exp("descuento_precio", hist, config, ui)
    assert config.get("_reconciliar_nc_factura_sku") is True
    assert config.get("reconciliacion_nc")
    assert config.get("modalidad") == "individual"

    exp = ExpedienteComercial(
        nombre="Descuento comercial",
        familia="precio",
        estrategia="PriceDifference",
        variante="discount_period",
        datos=data,
        contexto=PipelineContext(config=config),
    )
    exp.condiciones = [pd.DataFrame({"CODIGO": ["00123"], "DESCUENTO": [0.10]})]
    exp = Pipeline().ejecutar(exp)
    assert exp.resultado is not None
    row = exp.resultado.dataframe.iloc[0]
    assert row["SOLES_CALCULO"] == 900.0
    assert row["PRECIO_HIST"] == 9.0
    assert row["MONTO_NC"] == 90.0
    assert "APLICADA" in row["ALERTA"]
    assert "N900-1" in row["AUDITORIA_NC"]


def test_build_config_keeps_do_modalidad_from_ui():
    cfg = build_config("descuento_precio", {"modalidad": "consolidado"})
    assert cfg["modalidad"] == "consolidado"
    cfg = build_config("descuento_precio", {})
    assert cfg["modalidad"] == "individual"
