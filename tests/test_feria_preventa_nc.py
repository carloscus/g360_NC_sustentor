"""FPE: individual vs consolidado con la misma política de NC/NDB que DC/DO/VRS.

Cubre la convención compartida de la familia de precio:
- el cálculo va sobre las facturas (las NC/NDB se reconcilian aparte);
- la nota exacta habilitada se APLICA solo en individual; en consolidado se
  detalla en la fila (ALERTA/AUDITORÍA) sin ajustar ni alertar en el panel;
- la cobertura (facturado / disponible / % restante) se repite por fila del
  mismo SKU, como en VRS;
- consolidado agrupa por SKU con precio por MODA y el detalle en la auditoría.
"""

import pandas as pd
import pytest

from src.core.nc_reconciliation import reconciliar_notas
from src.domain import ExpedienteComercial, PipelineContext
from src.strategies.feria_preventa import FeriaPreventaStrategy
from src.ui.config_builder import build_config, build_datos_exp


def _historial():
    """3 facturas de S1 (LIFO) + 1 de S2, con DOC_ID estilo ERP."""
    return pd.DataFrame(
        [
            {
                "CODIGO": "S1",
                "ARTICULO": "Prod 1",
                "LINEA": "L01",
                "CANTIDAD": 50.0,
                "SOLES": 170.0,
                "FECHA": pd.Timestamp("2026-01-05"),
                "TIPO_DOC": "F",
                "SERIE": "F201",
                "NUMERO": "100",
                "DOC_ID": "FF201-100",
                "COD_CLIENTE": "1",
                "DOC_CLIENTE": "206039928",
                "TIPO_CLASE": "factura",
                "FACTURA_REF": "",
                "CANTIDAD_FAE": 0.0,
                "TIPO_NOTA": "",
                "CATEGORIA": "",
            },
            {
                "CODIGO": "S1",
                "ARTICULO": "Prod 1",
                "LINEA": "L01",
                "CANTIDAD": 30.0,
                "SOLES": 102.0,
                "FECHA": pd.Timestamp("2026-01-10"),
                "TIPO_DOC": "F",
                "SERIE": "F201",
                "NUMERO": "101",
                "DOC_ID": "FF201-101",
                "COD_CLIENTE": "1",
                "DOC_CLIENTE": "206039928",
                "TIPO_CLASE": "factura",
                "FACTURA_REF": "",
                "CANTIDAD_FAE": 0.0,
                "TIPO_NOTA": "",
                "CATEGORIA": "",
            },
            {
                "CODIGO": "S1",
                "ARTICULO": "Prod 1",
                "LINEA": "L01",
                "CANTIDAD": 20.0,
                "SOLES": 68.0,
                "FECHA": pd.Timestamp("2026-01-20"),
                "TIPO_DOC": "F",
                "SERIE": "F202",
                "NUMERO": "100",
                "DOC_ID": "FF202-100",
                "COD_CLIENTE": "1",
                "DOC_CLIENTE": "206039928",
                "TIPO_CLASE": "factura",
                "FACTURA_REF": "",
                "CANTIDAD_FAE": 0.0,
                "TIPO_NOTA": "",
                "CATEGORIA": "",
            },
            {
                "CODIGO": "S2",
                "ARTICULO": "Prod 2",
                "LINEA": "L02",
                "CANTIDAD": 40.0,
                "SOLES": 160.0,
                "FECHA": pd.Timestamp("2026-01-08"),
                "TIPO_DOC": "F",
                "SERIE": "F201",
                "NUMERO": "100",
                "DOC_ID": "FF201-100",
                "COD_CLIENTE": "1",
                "DOC_CLIENTE": "206039928",
                "TIPO_CLASE": "factura",
                "FACTURA_REF": "",
                "CANTIDAD_FAE": 0.0,
                "TIPO_NOTA": "",
                "CATEGORIA": "",
            },
        ]
    )


def _nota_fae_exacta():
    """NC con FAE exacta contra FF201-100 / S1: 50 u, -S/ 85."""
    return pd.DataFrame(
        [
            {
                "CODIGO": "S1",
                "ARTICULO": "Prod 1",
                "LINEA": "L01",
                "CANTIDAD": 0.0,
                "SOLES": -85.0,
                "FECHA": pd.Timestamp("2026-01-25"),
                "TIPO_DOC": "NC",
                "SERIE": "F900",
                "NUMERO": "1",
                "DOC_ID": "FF900-1",
                "TIPO_NOTA": "FAE",
                "CATEGORIA": "valor",
                "FACTURA_REF": "FF201-100",
                "DOC_NOTA": "FF900-1",
                "CANTIDAD_FAE": 50.0,
                "COD_CLIENTE": "1",
                "DOC_CLIENTE": "206039928",
                "TIPO_CLASE": "nc",
                "LINEA_REF": "",
            }
        ]
    )


def _requerimiento(tmp_path):
    df = pd.DataFrame(
        {"CODIGO": ["S1", "S2"], "CANTIDAD_NC": [80, 60], "PORCENTAJE_DESC": [0.05, 0.10]}
    )
    path = tmp_path / "requerimiento.xlsx"
    df.to_excel(path, index=False)
    return str(path)


def _run(tmp_path, modalidad, con_nota=True, usar_nc=True, forzar=True):
    hist = _historial()
    full = pd.concat([hist, _nota_fae_exacta()], ignore_index=True) if con_nota else hist
    documentos = {
        "facturas": {"mostrar": True, "usar": True},
        "nc": {"mostrar": True, "usar": usar_nc},
        "ndb": {"mostrar": True, "usar": False},
    }
    rec = reconciliar_notas(full, documentos=documentos, modalidad=modalidad)
    cfg = {
        "ruta_requerimientos": [_requerimiento(tmp_path)],
        "modalidad": modalidad,
        "documentos_historial": documentos,
        "reconciliacion_nc": rec,
        "sort_mode": "fecha_desc",
        "forzar_cantidad": forzar,
    }
    exp = ExpedienteComercial(
        nombre="FPE",
        familia="precio",
        estrategia="FeriaPreventa",
        datos=hist.copy(),
        contexto=PipelineContext(config=cfg),
    )
    return FeriaPreventaStrategy().process(exp)


class TestFpeModalidadIndividual:
    def test_una_fila_por_factura_x_sku(self, tmp_path):
        df = _run(tmp_path, "individual").get_excel()
        # S1 se sustenta con 3 facturas (LIFO) -> 3 filas; S2 con 1.
        assert len(df) == 4
        s1 = df[df["SKU"] == "S1"]
        assert s1["Cant. Solicitada"].tolist() == [80, 80, 80]
        assert sorted(s1["Facturas (qty)"]) == [
            "F201-100 (30 unid)",
            "F201-101 (30 unid)",
            "F202-100 (20 unid)",
        ]

    def test_cantidades_de_fila_suman_lo_solicitado(self, tmp_path):
        df = _run(tmp_path, "individual").get_excel()
        for sku, g in df.groupby("SKU"):
            assert g["Cant. Sustentada"].sum() == pytest.approx(g["Cant. Solicitada"].iloc[0])

    def test_nota_exacta_aplica_al_precio_atendido(self, tmp_path):
        df = _run(tmp_path, "individual").get_excel()
        fila_nota = df[df["Facturas (qty)"] == "F201-100 (30 unid)"].iloc[0]
        # 170 - 85 = 85 sobre 50 u -> P.U. efectivo 1.70 (era 3.40)
        assert fila_nota["Tot. Sustento (S/)"] == pytest.approx(30 * 1.70, abs=0.01)
        assert "APLICADA" in fila_nota["Alerta"]
        assert "NF900-1" in fila_nota["AUDITORIA_NC"]
        assert any(a.codigo == "AL12" for a in _run(tmp_path, "individual").alertas)

    def test_cobertura_por_sku_repetida_en_cada_fila(self, tmp_path):
        df = _run(tmp_path, "individual").get_excel()
        assert df["Cant. Facturada"].tolist() == [100.0, 100.0, 100.0, 40.0]
        assert df["Cant. Disponible"].tolist() == [100.0, 100.0, 100.0, 40.0]
        # % restante = solicitado / disponible (convención VRS): 80/100 y 60/40
        assert df["% Stock Restante"].tolist() == [80.0, 80.0, 80.0, 150.0]

    def test_sin_facturas_comprometidas_en_consolidado_de_resumen(self, tmp_path):
        res = _run(tmp_path, "individual")
        assert "documentos_unicos" not in res.resumen


class TestFpeModalidadConsolidado:
    def test_una_fila_por_sku(self, tmp_path):
        df = _run(tmp_path, "consolidado").get_excel()
        assert list(df["SKU"]) == ["S1", "S2"]
        assert len(df) == 2

    def test_precio_por_moda_y_detalle_en_auditoria(self, tmp_path):
        df = _run(tmp_path, "consolidado").get_excel()
        fila = df[df["SKU"] == "S1"].iloc[0]
        assert fila["P.U. Hist."] == 3.40  # precio de las 3 facturas
        assert "moda S/ 3.40000" in fila["AUDITORIA_NC"]
        assert "Cortes:" in fila["AUDITORIA_NC"]
        assert "F201-100 30u (3.40)" in fila["AUDITORIA_NC"]

    def test_consolidado_moda_con_precios_variables_por_factura(self, tmp_path):
        """Con precios distintos entre facturas: moda + rango, y el corte real."""
        hist = _historial()
        hist.loc[hist["DOC_ID"] == "FF202-100", "SOLES"] = 120.0  # 6.00/u
        documentos = {
            "facturas": {"mostrar": True, "usar": True},
            "nc": {"mostrar": True, "usar": True},
            "ndb": {"mostrar": True, "usar": False},
        }
        cfg = {
            "ruta_requerimientos": [_requerimiento(tmp_path)],
            "modalidad": "consolidado",
            "documentos_historial": documentos,
            "reconciliacion_nc": {},
            "sort_mode": "fecha_desc",
            "forzar_cantidad": True,
        }
        res = FeriaPreventaStrategy().process(
            ExpedienteComercial(
                nombre="FPE",
                familia="precio",
                estrategia="FeriaPreventa",
                datos=hist,
                contexto=PipelineContext(config=cfg),
            )
        )
        fila = res.get_excel()
        fila = fila[fila["SKU"] == "S1"].iloc[0]
        assert fila["P.U. Hist."] == 3.40  # 3.40 aparece en 2 líneas
        assert "rango S/ 3.40000" in fila["AUDITORIA_NC"]
        assert "F202-100 20u (6.00)" in fila["AUDITORIA_NC"]

    def test_montos_por_suma_exacta(self, tmp_path):
        df = _run(tmp_path, "consolidado").get_excel()
        fila = df[df["SKU"] == "S1"].iloc[0]
        assert fila["Cant. Sustentada"] == 80
        assert fila["Subtotal NC (S/)"] == pytest.approx(80 * 0.05 * 3.40, abs=0.01)
        # el soporte real sigue siendo el de las facturas (272 = 68+102+102)
        assert fila["Tot. Sustento (S/)"] == pytest.approx(272.0, abs=0.01)

    def test_nota_exacta_solo_se_detalla_no_ajusta(self, tmp_path):
        res = _run(tmp_path, "consolidado")
        fila = res.get_excel().iloc[0]
        assert "no aplicado" in fila["Alerta"]
        assert "NF900-1" in fila["AUDITORIA_NC"]
        # sin ajuste: el precio sigue siendo el de la factura
        assert fila["P.U. Hist."] == 3.40
        assert not any(a.codigo == "AL12" for a in res.alertas)

    def test_documentos_comprometidos_solo_en_consolidado(self, tmp_path):
        res = _run(tmp_path, "consolidado")
        assert res.resumen["documentos_unicos"] == ["F201-100", "F201-101", "F202-100"]
        assert res.resumen["titulo_documentos"] == "FACTURAS COMPROMETIDAS"


class TestFpeCheckUsar:
    def test_sin_check_usar_la_nota_no_se_aplica(self, tmp_path):
        """Mismo criterio que DC: la nota no ajusta y queda como observación."""
        res = _run(tmp_path, "individual", usar_nc=False)
        fila = res.get_excel()
        fila = fila[fila["Facturas (qty)"] == "F201-100 (30 unid)"].iloc[0]
        assert fila["Tot. Sustento (S/)"] == pytest.approx(30 * 3.40, abs=0.01)
        assert "APLICADA" not in fila["Alerta"]
        assert "check Usar desactivado" in fila["Alerta"]

    def test_sin_notas_no_hay_auditoria_de_notas(self, tmp_path):
        df = _run(tmp_path, "individual", con_nota=False).get_excel()
        assert df["AUDITORIA_NC"].fillna("").str.strip().eq("").all()


class TestFpeCoberturaYAlertas:
    def test_faltante_de_sustento_por_fuera_de_la_factura(self, tmp_path):
        """Sin forzar cantidad, lo reconocido es el soporte real (AL09)."""
        res = _run(tmp_path, "individual", con_nota=False, forzar=False)
        df = res.get_excel()
        s2 = df[df["SKU"] == "S2"]
        assert s2["Cant. Sustentada"].sum() == 40.0
        assert any(a.codigo == "AL09" for a in res.alertas)
        assert any("Sin sustento" in g for g in df["Glosa"])

    def test_al09_reporta_lo_realmente_asignado(self, tmp_path):
        res = _run(tmp_path, "individual", con_nota=False, forzar=False)
        al09 = next(a for a in res.alertas if a.codigo == "AL09")
        assert "Solo 40 unid. de 60" in al09.mensaje


class TestFpeConfigBuilder:
    def test_build_config_expone_modalidad(self):
        for modalidad in ("individual", "consolidado"):
            cfg = build_config(
                "feria_preventa",
                {
                    "modalidad": modalidad,
                    "requerimientos_paths": [],
                    "sort_mode": "fecha_desc",
                    "forzar_cantidad": True,
                },
            )
            assert cfg["modalidad"] == modalidad

    def test_build_config_modalidad_por_defecto_individual(self):
        cfg = build_config("feria_preventa", {"requerimientos_paths": []})
        assert cfg["modalidad"] == "individual"

    def test_datos_exp_solo_facturas_y_reconcilia_por_factura_sku(self, tmp_path):
        hist = pd.concat([_historial(), _nota_fae_exacta()], ignore_index=True)
        documentos = {
            "facturas": {"mostrar": True, "usar": True},
            "nc": {"mostrar": True, "usar": True},
            "ndb": {"mostrar": True, "usar": False},
        }
        cfg = build_config(
            "feria_preventa",
            {
                "modalidad": "individual",
                "requerimientos_paths": [],
                "sort_mode": "fecha_desc",
                "forzar_cantidad": True,
                "historico_config": documentos,
                "df_historial_full": hist,
            },
        )
        datos = build_datos_exp("feria_preventa", hist, cfg, {"df_historial_full": hist})
        # la nota no entra como stock asignable
        assert set(datos["TIPO_CLASE"]) == {"factura"}
        assert cfg["_reconciliar_nc_factura_sku"] is True
        assert ("1", "FF201-100", "S1") in cfg["reconciliacion_nc"]


class TestFpePipelineCompleto:
    """Flujo real de la app: config → datos → Pipeline → reporte."""

    def _pipeline(self, tmp_path, modalidad):
        from src.pipeline import Pipeline

        hist = pd.concat([_historial(), _nota_fae_exacta()], ignore_index=True)
        documentos = {
            "facturas": {"mostrar": True, "usar": True},
            "nc": {"mostrar": True, "usar": True},
            "ndb": {"mostrar": True, "usar": False},
        }
        ui = {
            "modalidad": modalidad,
            "requerimientos_paths": [_requerimiento(tmp_path)],
            "sort_mode": "fecha_desc",
            "forzar_cantidad": True,
            "historico_config": documentos,
            "df_historial_full": hist,
        }
        config = build_config("feria_preventa", ui)
        datos = build_datos_exp("feria_preventa", hist, config, ui)
        exp = ExpedienteComercial(
            nombre="Feria / Preventa",
            familia="precio",
            estrategia="FeriaPreventa",
            datos=datos,
            contexto=PipelineContext(config=config),
        )
        return Pipeline().ejecutar(exp)

    def test_individual_por_factura(self, tmp_path):
        exp = self._pipeline(tmp_path, "individual")
        df = exp.resultado.get_excel()
        assert sorted(set(df["SKU"])) == ["S1", "S2"]
        assert len(df) == 4  # 3 facturas de S1 + 1 de S2
        assert "Cant. Facturada" in df.columns

    def test_consolidado_por_sku(self, tmp_path):
        exp = self._pipeline(tmp_path, "consolidado")
        df = exp.resultado.get_excel()
        assert list(df["SKU"]) == ["S1", "S2"]
        assert exp.resultado.resumen["documentos_unicos"] == ["F201-100", "F201-101", "F202-100"]


class TestFpeFacturaYExpediente:
    """Columna FACTURA (P0.2): el expediente individual ya no cae a consolidado.

    FPE devuelve dataframe = vista previa por SKU (sin FACTURA) y
    dataframe_excel = detalle por factura×SKU; el split lee el detalle.
    """

    def test_excel_individual_trae_factura_por_fila(self, tmp_path):
        df = _run(tmp_path, "individual").get_excel()
        assert "FACTURA" in df.columns
        assert sorted(df["FACTURA"].dropna().unique()) == ["F201-100", "F201-101", "F202-100"]

    def test_excel_consolidado_trae_factura_principal(self, tmp_path):
        df = _run(tmp_path, "consolidado").get_excel()
        assert "FACTURA" in df.columns
        assert df["FACTURA"].astype(str).str.strip().ne("").all()

    def test_expediente_individual_se_divide_por_factura(self, tmp_path, monkeypatch):
        from types import SimpleNamespace
        from src.ui import expediente_service as es

        res = self._res(tmp_path)
        assert "FACTURA" not in res.dataframe.columns  # vista previa por SKU
        exp_ns = SimpleNamespace(resultado=res, datos=pd.DataFrame(), contexto=None)
        llamadas = []
        monkeypatch.setattr(
            es, "_generar_uno", lambda **kw: llamadas.append(kw["modalidad"]) or tmp_path
        )
        dirs = es._generar_individual(
            exp_ns, "feria_preventa", None, pd.DataFrame(), ["1"], "", "", "", "", tmp_path
        )
        # 3 facturas únicas → 3 expedientes individuales, ninguno consolidado.
        assert len(dirs) == 3
        assert set(llamadas) == {"individual"}

    def test_vista_previa_se_recorta_por_skus_de_la_factura(self, tmp_path):
        from types import SimpleNamespace
        from src.ui import expediente_service as es

        sub = es._subresultado_por_cliente(
            SimpleNamespace(resultado=self._res(tmp_path), datos=pd.DataFrame(), contexto=None), "1"
        )
        sub_fac = es._subresultado_por_factura(sub, "F202-100")
        # F202-100 solo trae S1: la vista previa del Informe no muestra S2.
        assert set(sub_fac.resultado.dataframe["SKU"]) == {"S1"}

    def _res(self, tmp_path):
        exp = TestFpePipelineCompleto()._pipeline(tmp_path, "individual")
        assert exp.resultado is not None
        return exp.resultado
