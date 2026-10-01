"""Shared Mostrar/Usar policy captured from the UI and applied consistently."""

from types import SimpleNamespace

import pandas as pd

from src.ui.catalog import HistorialConfig
from src.ui.config_builder import build_config
from src.ui.expediente_service import _filtrar_documentos_historico
from src.ui.view_helpers import _ViewHelpers


def test_live_history_checks_are_captured_not_static_defaults():
    view = _ViewHelpers.__new__(_ViewHelpers)
    view._historico_config = HistorialConfig()
    view.historico_incluir_ctrls = {
        "facturas": SimpleNamespace(value=True),
        "nc": SimpleNamespace(value=False),
        "ndb": SimpleNamespace(value=True),
    }
    view.historico_calc_ctrls = {
        "facturas": SimpleNamespace(value=True),
        "nc": SimpleNamespace(value=True),
        "ndb": SimpleNamespace(value=False),
    }

    snapshot = view._leer_historico_config()
    assert snapshot.as_dict() == {
        "facturas": [True, True],
        "nc": [False, True],
        "ndb": [True, False],
    }
    cfg = build_config("diferencia_precio", {"historico_config": snapshot.as_dict()})
    assert cfg["documentos_historial"] == {
        "facturas": {"mostrar": True, "usar": True},
        "nc": {"mostrar": False, "usar": True},
        "ndb": {"mostrar": True, "usar": False},
    }


def test_history_export_obeys_mostrar_independently_from_usar():
    df = pd.DataFrame(
        {
            "TIPO_CLASE": ["factura", "descuento", "devolucion", "cargo"],
            "TIPO_DOC": ["F01", "NCR", "NCR", "NDB"],
            "NUMERO": ["1", "2", "3", "4"],
        }
    )
    policy = HistorialConfig(facturas=(True, True), nc=(False, True), ndb=(True, False))
    visible = _filtrar_documentos_historico(df, policy)
    # NC is used in calculation but hidden from this historical listing;
    # NDB is shown but not used.
    assert visible["NUMERO"].tolist() == ["1", "4"]
