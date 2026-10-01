"""Cantidad Determinada: precio de lista + cantidad exacta + FIFO/LIFO."""

import pandas as pd
import pytest

from src.domain import ExpedienteComercial, PipelineContext
from src.strategies.cantidad_determinada import CantidadDeterminadaStrategy


@pytest.fixture
def historial():
    return pd.DataFrame(
        {
            "CODIGO": ["A001", "A001", "A002", "A003"],
            "ARTICULO": ["Producto A", "Producto A", "Producto B", "Producto C"],
            "CANTIDAD": [100, 50, 200, 30],
            "SOLES": [350.0, 175.0, 600.0, 150.0],
            "TIPO_DOC": ["F", "F", "F", "F"],
            "SERIE": ["001", "001", "002", "003"],
            "NUMERO": ["100", "101", "200", "300"],
            "FECHA": pd.to_datetime(["2026-01-15", "2026-02-20", "2026-03-10", "2026-04-05"]),
            "COD_CLIENTE": ["00068414"] * 4,
            "DOC_CLIENTE": ["20560201011"] * 4,
        }
    )


@pytest.fixture
def lista():
    return pd.DataFrame({"SKU": ["A001", "A002"], "PRECIO_BASE": [3.09, 2.80]})


def _cant(**skus):
    return pd.DataFrame({"SKU": list(skus), "CANTIDAD": list(skus.values())})


def _run(historial, lista, cant, cfg):
    exp = ExpedienteComercial(
        nombre="T",
        familia="T",
        estrategia="CantidadDeterminada",
        datos=historial,
        contexto=PipelineContext(config=dict(cfg)),
    )
    exp.condiciones = [lista, cant]
    return CantidadDeterminadaStrategy().process(exp)


class TestCantidadDeterminada:
    def test_cantidad_exacta_fifo(self, historial, lista):
        res = _run(historial, lista, _cant(A001=50), {"sort_mode": "fecha_asc"})
        df = res.dataframe
        assert len(df) == 1
        assert float(df["CANTIDAD"].iloc[0]) == 50.0  # exacta, no 150
        assert df["FACTURA"].iloc[0] == "F001-100"  # FIFO: 1ª (ene)
        assert float(df["PRECIO_HIST"].iloc[0]) == 3.50
        assert float(df["PRECIO_NETO"].iloc[0]) == 3.09
        assert float(df["DIFERENCIA"].iloc[0]) == pytest.approx(0.41)
        assert float(df["MONTO_NC"].iloc[0]) == pytest.approx(20.50)
        assert "AL01" in str(df["ALERTA"].iloc[0])
        assert res.resumen["total_nc"] == pytest.approx(20.50)
        assert res.resumen["skus_afectados"] == 1
        assert not res.alertas  # sin exceso

    def test_lifo_consume_lo_mas_reciente(self, historial, lista):
        res = _run(historial, lista, _cant(A001=50), {"sort_mode": "fecha_desc"})
        df = res.dataframe
        assert len(df) == 1
        assert df["FACTURA"].iloc[0] == "F001-101"  # LIFO: 2ª (feb)

    def test_exceso_con_alerta_al09(self, historial, lista):
        res = _run(historial, lista, _cant(A001=500), {"sort_mode": "fecha_asc"})
        df = res.dataframe
        # Sustentadas: 100 + 50 (nunca más que lo facturado).
        assert float(df["CANTIDAD"].sum()) == 150.0
        al09 = [a for a in res.alertas if a.codigo == "AL09"]
        assert len(al09) == 1
        assert "AL09" in al09[0].mensaje and "500" in al09[0].mensaje
        # Monto solo sobre lo sustentado.
        assert res.resumen["total_nc"] == pytest.approx(0.41 * 150)

    def test_consolidado_con_exceso_en_fila(self, historial, lista):
        res = _run(
            historial,
            lista,
            _cant(A001=500),
            {"sort_mode": "fecha_asc", "modalidad": "consolidado"},
        )
        df = res.dataframe
        assert len(df) == 1  # una fila por SKU
        assert float(df["CANTIDAD"].iloc[0]) == 150.0
        assert "F001-100" in str(df["FACTURAS"].iloc[0])
        assert "F001-101" in str(df["FACTURAS"].iloc[0])
        assert float(df["MONTO_NC"].iloc[0]) == pytest.approx(61.50)
        alerta = str(df["ALERTA"].iloc[0])
        assert "AL09" in alerta and "500" in alerta  # exceso en la fila

    def test_consolidado_sin_exceso(self, historial, lista):
        res = _run(
            historial,
            lista,
            _cant(A001=60, A002=10),
            {"sort_mode": "fecha_asc", "modalidad": "consolidado"},
        )
        df = res.dataframe
        assert sorted(df["SKU"].tolist()) == ["A001", "A002"]
        assert "AL09" not in str(df["ALERTA"].tolist())
        # 60 × 0.41 + 10 × (3.00 − 2.80) = 26.60
        assert res.resumen["total_nc"] == pytest.approx(26.60)

    def test_reparto_por_facturas_fifo(self, historial, lista):
        res = _run(historial, lista, _cant(A001=120), {"sort_mode": "fecha_asc"})
        df = res.dataframe
        # 100 de la 1ª + 20 de la 2ª.
        assert sorted(df["FACTURA"].tolist()) == ["F001-100", "F001-101"]
        assert float(df["CANTIDAD"].sum()) == 120.0

    def test_sku_sin_en_lista_no_alcanza(self, historial, lista):
        res = _run(historial, lista, _cant(A003=10), {"sort_mode": "fecha_asc"})
        assert res.dataframe.empty
        # A003 no está en la lista: historial∩lista no lo trae.
        assert any(a.tipo == "warning" and "Sin facturas" in a.mensaje for a in res.alertas)

    def test_faltan_insumos(self, historial, lista):
        exp = ExpedienteComercial(
            nombre="T",
            familia="T",
            estrategia="CantidadDeterminada",
            datos=historial,
            contexto=PipelineContext(config={}),
        )
        exp.condiciones = [lista]  # sin cantidad
        res = CantidadDeterminadaStrategy().process(exp)
        assert res.dataframe.empty
        assert res.alertas[0].tipo == "error"
        assert "cantidad" in res.alertas[0].mensaje.lower()

        exp.condiciones = []  # sin lista
        res = CantidadDeterminadaStrategy().process(exp)
        assert "lista" in res.alertas[0].mensaje.lower()

    def test_cantidad_cero_o_invalida(self, historial, lista):
        res = _run(
            historial,
            lista,
            pd.DataFrame({"SKU": ["A001"], "CANTIDAD": [0]}),
            {"sort_mode": "fecha_asc"},
        )
        assert res.dataframe.empty
        assert res.alertas[0].tipo == "error"

    def test_historial_vacio(self, lista):
        res = _run(pd.DataFrame(), lista, _cant(A001=10), {})
        assert res.dataframe.empty
        assert "Historial" in res.alertas[0].mensaje


class TestBordesCDT:
    """Regresiones de la ronda de verificacion (YAML + render + %)."""

    @staticmethod
    def _run(historial, lista, cant, cfg):
        exp = ExpedienteComercial(
            nombre="T",
            familia="T",
            estrategia="CantidadDeterminada",
            datos=historial,
            contexto=PipelineContext(config=dict(cfg)),
        )
        exp.condiciones = [lista, cant]
        return CantidadDeterminadaStrategy().process(exp)

    def test_consolidado_prorrea_total_factura(self, historial, lista):
        """120u de lineas de 350 y 175: el total debe ser 120 x 3.50 = 420,
        no 525 (la suma de las lineas COMPLETAS)."""
        res = self._run(
            historial,
            lista,
            _cant(A001=120),
            {"sort_mode": "fecha_asc", "modalidad": "consolidado"},
        )
        fila = res.dataframe.iloc[0]
        assert float(fila["CANTIDAD"]) == 120.0
        assert float(fila["TOTAL_FACTURA_EXACTO"]) == pytest.approx(420.00)
        assert float(fila["SOLES"]) == pytest.approx(420.00)
        # El monto de NC no cambia con el prorrateo.
        assert res.resumen["total_nc"] == pytest.approx(49.20)

    def test_al09_visible_en_fila_individual(self, historial, lista):
        """El exceso salia solo en el panel de alertas, no en la fila."""
        res = self._run(historial, lista, _cant(A001=500), {"sort_mode": "fecha_asc"})
        alertas_fila = res.dataframe["ALERTA"].astype(str)
        assert alertas_fila.str.contains("AL09").any()
        assert alertas_fila.str.contains("500").any()

    def test_lista_con_espacios_cruza_con_el_historial(self, historial):
        """' A001 ' en la lista no debe dar 'Sin facturas'."""
        lista = pd.DataFrame({"SKU": [" A001 "], "PRECIO_BASE": [3.09]})
        res = self._run(historial, lista, _cant(A001=50), {"sort_mode": "fecha_asc"})
        assert not res.dataframe.empty
        assert float(res.dataframe["MONTO_NC"].sum()) == pytest.approx(20.50)

    def test_desc_con_porcentaje_texto_no_revienta(self, historial):
        """'5%' provocaba TypeError: int - str dentro de la cadena."""
        lista = pd.DataFrame({"SKU": ["A001"], "PRECIO_BASE": [4.00], "DESC1": ["5%"]})
        res = self._run(historial, lista, _cant(A001=50), {"sort_mode": "fecha_asc"})
        assert not res.dataframe.empty
        assert float(res.dataframe["PRECIO_NETO"].iloc[0]) == pytest.approx(3.80)
        assert (res.dataframe["PRECIO_NETO"] > 0).all()


class TestRegistroCadena:
    """La cadena nueva está registrada en cada capa."""

    def test_pipeline_y_mapa(self):
        from src.pipeline import Pipeline

        assert "CantidadDeterminada" in Pipeline.STRATEGIES
        assert Pipeline()._resolver_key_por_estrategia("CantidadDeterminada") == "diferencia_stock"

    def test_catalogo_ui(self):
        """CDT se fusionó en VRS: el caso canónico es VRS y CDT no existe."""
        from src.ui.catalog import CATALOGO, ORDEN_CASOS

        assert "CDT" not in CATALOGO
        assert "CDT" not in ORDEN_CASOS
        c = CATALOGO["VRS"]
        assert c.legacy_types == ("diferencia_stock", "diferencia_cantidad")
        assert "lista_precios" in c.insumos and "cantidad" in c.insumos
        assert c.strategy == "diferencia_stock"

    def test_alias_oculto_en_config(self):
        """`diferencia_cantidad` sigue resolviendo al motor sin ser un caso."""
        from src.ui.reconocimiento_config import ESTRATEGIA_POR_TIPO

        assert ESTRATEGIA_POR_TIPO["diferencia_cantidad"] == ("CantidadDeterminada", "")
        assert ESTRATEGIA_POR_TIPO["diferencia_stock"] == ("CantidadDeterminada", "")

    def test_alias_en_catalog_loader(self):
        """El loader resuelve el legacy type contra el schema canónico VRS."""
        from src.core.catalog_loader import CatalogLoader

        schema = CatalogLoader().obtener_schema("diferencia_cantidad")
        assert schema
        assert "CODIGO" in schema.get("columnas_requeridas", [])

    def test_namings_y_config(self):
        from src.ui.reconocimiento_config import ESTRATEGIA_POR_TIPO, NAMING, TIPO_CONFIG

        key = "diferencia_cantidad"
        assert ESTRATEGIA_POR_TIPO[key] == ("CantidadDeterminada", "")
        assert NAMING[key]["titulo"] == "NC por Cantidad Determinada"
        assert TIPO_CONFIG[key]["necesita_lista"] is True

    def test_config_builder_rama(self):
        from src.ui.config_builder import build_config

        cfg = build_config(
            "diferencia_cantidad", {"sort_mode_dc": "fecha_desc", "modalidad": "consolidado"}
        )
        assert cfg["sort_mode"] == "fecha_desc"
        assert cfg["modalidad"] == "consolidado"

    def test_config_builder_default_fifo(self):
        from src.ui.config_builder import build_config

        cfg = build_config("diferencia_cantidad", {})
        assert cfg["sort_mode"] == "fecha_asc"  # FIFO por defecto
        assert cfg["modalidad"] == "individual"

    def test_pipeline_end_to_end(self, historial, lista):
        """Cadena completa (normalización → cálculo → reglas)."""
        from src.pipeline import Pipeline

        exp = ExpedienteComercial(
            nombre="T",
            familia="T",
            estrategia="CantidadDeterminada",
            datos=historial,
            contexto=PipelineContext(config={"sort_mode": "fecha_asc", "modalidad": "individual"}),
        )
        exp.condiciones = [lista, _cant(A001=120)]
        exp = Pipeline().ejecutar(exp)
        assert exp.resultado is not None
        df = exp.resultado.dataframe
        assert len(df) == 2  # FIFO: 100 + 20
        assert "SKU" in df.columns  # convención del render
        assert float(df["CANTIDAD"].sum()) == 120.0
        assert exp.resultado.resumen["total_nc"] == pytest.approx(0.41 * 120)

    def test_consolidado_doc_ref_principal(self, historial, lista):
        """P0.3: doc_ref = factura de mayor SOLES (naming del expediente)."""
        res = _run(
            historial,
            lista,
            _cant(A001=50, A002=50),
            {"sort_mode": "fecha_asc", "modalidad": "consolidado"},
        )
        # A001→F001-100 (50×3.50=175) vs A002→F002-200 (50×3.00=150).
        assert res.resumen["doc_ref"] == "F001-100"

    def test_individual_sin_doc_ref_global(self, historial, lista):
        """En individual el doc_ref lo fija el split por factura."""
        res = _run(
            historial, lista, _cant(A001=50), {"sort_mode": "fecha_asc", "modalidad": "individual"}
        )
        assert "doc_ref" not in res.resumen
