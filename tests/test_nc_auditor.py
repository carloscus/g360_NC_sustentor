"""Auditor NC/ND: regla exact-match por folio (DIRECTO vs informativo)."""

import pandas as pd
import pytest

from src.core.nc_auditor import CreditNoteAuditor


def _df(rows):
    return pd.DataFrame(rows)


def _factura(doc="F012-1", sku="SK1", qty=100.0, soles=1000.0):
    return dict(
        TIPO_CLASE="factura",
        CODIGO=sku,
        SOLES=soles,
        CANTIDAD=qty,
        DOC_ID=doc,
        FACTURA_REF="",
        TIPO_DOC="F01",
    )


def _ncr(doc, ref, sku, fae, soles, clase="descuento"):
    return dict(
        TIPO_CLASE=clase,
        CODIGO=sku,
        SOLES=-abs(soles),
        CANTIDAD=0.0,
        CANTIDAD_FAE=fae,
        DOC_ID=doc,
        FACTURA_REF=ref,
        TIPO_DOC="NCR",
    )


def _ndb(doc, ref, sku, soles):
    return dict(
        TIPO_CLASE="cargo",
        CODIGO=sku,
        SOLES=abs(soles),
        CANTIDAD=0.0,
        CANTIDAD_FAE=100.0,
        DOC_ID=doc,
        FACTURA_REF=ref,
        TIPO_DOC="NDB",
    )


@pytest.fixture
def base():
    return [_factura()]


def test_directo_exact_match(base):
    df = _df(base + [_ncr("N900-A1", "F012-1", "SK1", 100.0, 50.0)])
    alertas = CreditNoteAuditor().auditar(df)
    directos = [a for a in alertas if a.tipo == "match_directo"]
    assert len(directos) == 1
    a = directos[0]
    assert a.nivel_detalle == "DIRECTO"
    assert a.cant_factura == 100.0 and a.cant_nc == 100.0 and a.n_docs == 1


def test_dos_nc_suman_exact_match(base):
    df = _df(
        base
        + [
            _ncr("N900-A1", "F012-1", "SK1", 50.0, 25.0),
            _ncr("N900-A2", "F012-1", "SK1", 50.0, 25.0),
        ]
    )
    alertas = CreditNoteAuditor().auditar(df)
    # Cada folio suma 50 != 100 -> ambos informativos (agregado por folio)
    assert not [a for a in alertas if a.tipo == "match_directo"]
    assert len([a for a in alertas if a.tipo == "nc_informativa"]) == 2


def test_folio_split_mismo_doc_directo(base):
    # Mismo folio en 2 filas que suman 100 -> DIRECTO con n_docs=2
    r1 = _ncr("N900-A1", "F012-1", "SK1", 60.0, 30.0)
    r2 = _ncr("N900-A1", "F012-1", "SK1", 40.0, 20.0)
    df = _df(base + [r1, r2])
    alertas = CreditNoteAuditor().auditar(df)
    directos = [a for a in alertas if a.tipo == "match_directo"]
    assert len(directos) == 1
    assert directos[0].n_docs == 2
    assert directos[0].cant_nc == 100.0


def test_parcial_y_exceso_informativos(base):
    df = _df(
        base
        + [
            _ncr("N900-B1", "F012-1", "SK1", 50.0, 30.0),
            _ncr("N900-C1", "F012-1", "SK1", 150.0, 60.0),
        ]
    )
    alertas = CreditNoteAuditor().auditar(df)
    assert not [a for a in alertas if a.tipo == "match_directo"]
    inf = [a for a in alertas if a.tipo == "nc_informativa"]
    assert len(inf) == 2
    assert all("no afecta precio" in a.mensaje for a in inf)


def test_consolidado_una_sola_alerta(base):
    # Un mismo folio de feria: 4 lineas con SKUs inexistentes en la factura
    rows = base + [_ncr("N900-X1", "F012-1", f"OTRO{i}", 10.0, 5.0) for i in range(4)]
    df = _df(rows)
    alertas = CreditNoteAuditor().auditar(df)
    con = [a for a in alertas if a.tipo == "nc_consolidada"]
    assert len(con) == 1
    assert "4" in con[0].mensaje
    assert not [a for a in alertas if a.tipo == "sku_no_en_factura"]


def test_sku_inexistente_folio_simple_es_error(base):
    df = _df(base + [_ncr("N900-Z1", "F012-1", "FANTASMA", 10.0, 5.0)])
    alertas = CreditNoteAuditor().auditar(df)
    assert len([a for a in alertas if a.tipo == "sku_no_en_factura"]) == 1


def test_ndb_directa_aumenta(base):
    df = _df(base + [_ndb("N900-D1", "F012-1", "SK1", 20.0)])
    alertas = CreditNoteAuditor().auditar(df)
    directos = [a for a in alertas if a.tipo == "match_directo"]
    assert len(directos) == 1
    assert "aumenta" in directos[0].mensaje
    assert directos[0].nivel_detalle == "DIRECTO"


def test_ndb_usa_fae_exacta_como_evidencia_de_cantidad(base):
    row = _ndb("N900-D1", "F012-1", "SK1", 20.0)
    row["CANTIDAD_FAE"] = 25.0
    alertas = CreditNoteAuditor().auditar(_df(base + [row]))
    assert not [a for a in alertas if a.tipo == "match_directo"]
    assert any(a.tipo == "nc_informativa" and a.cant_nc == 25.0 for a in alertas)


def test_check_usar_off_or_consolidated_keeps_exact_note_informational(base):
    df = _df(base + [_ncr("N900-A1", "F012-1", "SK1", 100.0, 50.0)])
    policy = {
        "facturas": {"mostrar": True, "usar": True},
        "nc": {"mostrar": True, "usar": False},
        "ndb": {"mostrar": True, "usar": False},
    }
    for mode, docs in (
        ("individual", policy),
        ("consolidado", {**policy, "nc": {"mostrar": True, "usar": True}}),
    ):
        alerts = CreditNoteAuditor().auditar(df, documentos_historial=docs, modalidad=mode)
        assert not [a for a in alerts if a.tipo == "match_directo"]
        info = [a for a in alerts if a.tipo == "nc_informativa"]
        assert len(info) == 1
        assert "no aplicad" in info[0].mensaje


def test_ndb_sin_sku_general(base):
    r = _ndb("N900-D2", "F012-1", "", 20.0)
    df = _df(base + [r])
    alertas = CreditNoteAuditor().auditar(df)
    assert len([a for a in alertas if a.tipo == "nc_general"]) == 1


def test_consolidado_audita_notas_de_todas_las_facturas_lista():
    hist = _df(
        [
            _factura("F012-1", "SK1", 100, 1000),
            _factura("F013-2", "SK1", 80, 960),
            _ncr("N900-S2", "F013-2", "SK1", 80, 40),
        ]
    )
    resultado = pd.DataFrame([{"SKU": "SK1", "FACTURA": "F012-1", "FACTURAS": "F012-1, F013-2"}])
    alertas = CreditNoteAuditor().auditar(hist, resultado)
    assert any(a.factura_id == "F013-2" and a.nc_id == "N900-S2" for a in alertas)


def test_sin_referencia(base):
    r = _ncr("N900-S1", "", "SK1", 10.0, 5.0)
    df = _df(base + [r])
    alertas = CreditNoteAuditor().auditar(df)
    assert len([a for a in alertas if a.tipo == "sin_referencia"]) == 1


def test_prefijos_columna_auditoria(base):
    df = _df(
        base
        + [
            _ncr("N900-A1", "F012-1", "SK1", 100.0, 50.0),
            _ncr("N900-B1", "F012-1", "SK1", 50.0, 30.0),
        ]
    )
    alertas = CreditNoteAuditor().auditar(df)
    res = pd.DataFrame([{"FACTURA": "F012-1", "SKU": "SK1"}])
    col = CreditNoteAuditor.build_audit_column(res, alertas)
    assert "[SKU]" in col.iloc[0] and "[INF]" in col.iloc[0]


def test_combinar_auditoria_mantiene_calculo_y_aplica_el_mismo_resultado():
    historial = _df(
        [
            _factura("F012-1", "SK1", 100, 1000),
            _ncr("N900-A1", "F012-1", "SK1", 100, 50),
        ]
    )
    nc_alertas = CreditNoteAuditor().auditar(
        historial, pd.DataFrame([{"FACTURA": "F012-1", "SKU": "SK1"}])
    )
    preview = pd.DataFrame(
        [{"FACTURA": "F012-1", "SKU": "SK1", "AUDITORIA_NC": "Precio efectivo S/ 9.50"}]
    )
    excel = preview.copy()
    CreditNoteAuditor.combinar_auditoria(preview, nc_alertas)
    CreditNoteAuditor.combinar_auditoria(excel, nc_alertas)
    assert preview.loc[0, "AUDITORIA_NC"] == excel.loc[0, "AUDITORIA_NC"]
    assert "Precio efectivo" in excel.loc[0, "AUDITORIA_NC"]
    assert "[SKU]" in excel.loc[0, "AUDITORIA_NC"]


def test_columna_no_duplica_entre_skus(base):
    # Misma factura, dos SKUs: la alerta de SK1 no debe pintarse en la fila SK2
    df = _df(base + [_ncr("N900-A1", "F012-1", "SK1", 100.0, 50.0)])
    alertas = CreditNoteAuditor().auditar(df)
    res = pd.DataFrame(
        [
            {"FACTURA": "F012-1", "SKU": "SK1"},
            {"FACTURA": "F012-1", "SKU": "SK2"},
        ]
    )
    col = CreditNoteAuditor.build_audit_column(res, alertas)
    assert "SK1" in col.iloc[0]
    assert col.iloc[1] == ""


def test_columna_tolera_sku_numerico(base):
    # pandas coerce 09009 -> '9009.0'; ambos lados deben normalizarse
    df = _df(base + [_ncr("N900-A1", "F012-1", "09009", 100.0, 50.0)])
    alertas = CreditNoteAuditor().auditar(df)
    res = pd.DataFrame([{"FACTURA": "F012-1", "SKU": 9009.0}])
    col = CreditNoteAuditor.build_audit_column(res, alertas)
    assert "SK1" not in col.iloc[0]
    assert "09009" in col.iloc[0]


def test_alerta_sin_sku_no_va_a_filas(base):
    # La consolidada (sin SKU) solo existe en el panel global, no por fila
    rows = base + [_ncr("N900-X1", "F012-1", f"OTRO{i}", 10.0, 5.0) for i in range(4)]
    df = _df(rows)
    alertas = CreditNoteAuditor().auditar(df)
    res = pd.DataFrame(
        [{"FACTURA": "F012-1", "SKU": "OTRO0"}, {"FACTURA": "F012-1", "SKU": "OTRO1"}]
    )
    col = CreditNoteAuditor.build_audit_column(res, alertas)
    assert all(c == "" for c in col)


def test_exceso_descuento_supera_valor_linea(base):
    # Descuento directo por 100 uds == 100 pero S/ por encima del valor de la linea
    df = _df(base + [_ncr("N900-A1", "F012-1", "SK1", 100.0, 1500.0)])
    alertas = CreditNoteAuditor().auditar(df)
    directos = [a for a in alertas if a.tipo == "match_directo"]
    assert len(directos) == 1
    assert "ADVERTENCIA" in directos[0].mensaje
    assert "supera el valor de la linea" in directos[0].mensaje


def test_ndb_supera_valor_linea(base):
    df = _df(base + [_ndb("N900-D1", "F012-1", "SK1", 2000.0)])
    alertas = CreditNoteAuditor().auditar(df)
    directos = [a for a in alertas if a.tipo == "match_directo"]
    assert len(directos) == 1
    assert "ADVERTENCIA" in directos[0].mensaje
    assert "supera el valor de la linea" in directos[0].mensaje


def test_consolidado_supera_total_factura(base):
    # Feria: 4 SKUs inexistentes que suman mas que la factura
    rows = base + [_ncr("N900-X1", "F012-1", f"OTRO{i}", 10.0, 300.0) for i in range(4)]
    df = _df(rows)
    alertas = CreditNoteAuditor().auditar(df)
    con = [a for a in alertas if a.tipo == "nc_consolidada"]
    assert len(con) == 1
    assert "ADVERTENCIA" in con[0].mensaje
    assert "supera el total de la factura" in con[0].mensaje


# --- Filtro de devoluci\u00f3n 100% en config_builder (solo cantidades) ---


def _historial_con_notas():
    filas = [
        dict(
            TIPO_DOC="F01",
            SERIE="900",
            NUMERO="1",
            CODIGO="SK1",
            CANTIDAD=100.0,
            SOLES=1000.0,
            REFERENCIA="",
        ),
        dict(
            TIPO_DOC="F01",
            SERIE="900",
            NUMERO="1",
            CODIGO="SK2",
            CANTIDAD=50.0,
            SOLES=500.0,
            REFERENCIA="",
        ),
        # Devolucion 100% de SK1
        dict(
            TIPO_DOC="NCR",
            SERIE="N900",
            NUMERO="A1",
            CODIGO="SK1",
            CANTIDAD=100.0,
            SOLES=-1000.0,
            REFERENCIA="F01/900-1",
        ),
        # Descuento de valor puro sobre SK2 (CANTIDAD=0) que con la regla
        # exact afecta precio (FAE == 50): NO debe excluirse del analisis.
        dict(
            TIPO_DOC="NCR",
            SERIE="N900",
            NUMERO="B1",
            CODIGO="SK2",
            CANTIDAD=0.0,
            CANTIDAD_FAE=50.0,
            SOLES=-500.0,
            REFERENCIA="F01/900-1",
        ),
    ]
    return pd.DataFrame(filas)


def test_filter_pd_reconcilia_devoluciones_y_nc_sin_excluir_silenciosamente():
    from src.ui.config_builder import _filter_pd

    df_hist = _historial_con_notas()
    df_hist["DOC_ID"] = "F900-1"
    df_hist["TIPO_CLASE"] = ["factura", "factura", "devolucion", "descuento"]
    datos_exp = df_hist[df_hist["TIPO_DOC"] == "F01"].copy()
    datos_exp["TIPO_CLASE"] = "factura"
    config = {
        "_reconciliar_nc_factura_sku": True,
        "documentos_historial": {
            "facturas": {"mostrar": True, "usar": True},
            "nc": {"mostrar": True, "usar": False},
            "ndb": {"mostrar": True, "usar": False},
        },
    }
    ui = {"df_historial_full": df_hist}
    res = _filter_pd(datos_exp, config, ui)
    # El default del caso muestra NC pero no las usa: ambas facturas siguen
    # en el cálculo base y la nota queda en la conciliación para auditoría.
    skus = set(res["CODIGO"])
    assert skus == {"SK1", "SK2"}, skus
    reconciliacion = config["reconciliacion_nc"]
    assert reconciliacion[("F900-1", "SK1")]["qty_return"] == 0
    assert reconciliacion[("F900-1", "SK1")]["details"][0]["estado"] == "exacta_no_usada"
    assert reconciliacion[("F900-1", "SK2")]["details"][0]["estado"] == "exacta_no_usada"
