import pytest
import pandas as pd
import numpy as np
from src.domain import ExpedienteComercial, RecognitionResult, PipelineContext
from src.pipeline import CatalogoCargador
from src.strategies.price_difference import PriceDifferenceStrategy
from src.strategies.promotion_bonus import PromotionBonusStrategy
from src.strategies.volume_rebate import VolumeRebateStrategy
from src.strategies.cancel_invoice import CancelInvoiceStrategy
from src.validation.engine import ValidationEngine, BusinessValidator
from src.validation.normalization import NormalizationEngine


@pytest.fixture
def historial_sample():
    return pd.DataFrame(
        {
            "CODIGO": ["A001", "A001", "A002", "A003"],
            "ARTICULO": ["Producto A", "Producto A", "Producto B", "Producto C"],
            "CANTIDAD": [100, 50, 200, 30],
            "SOLES": [350.00, 175.00, 600.00, 150.00],
            "TIPO_DOC": ["F", "F", "F", "F"],
            "SERIE": ["001", "001", "002", "003"],
            "NUMERO": ["100", "101", "200", "300"],
            "FECHA": pd.to_datetime(["2026-01-15", "2026-02-20", "2026-03-10", "2026-04-05"]),
        }
    )


@pytest.fixture
def lista_precios():
    return pd.DataFrame(
        {
            "SKU": ["A001", "A002"],
            "PRECIO_BASE": [3.09, 2.80],
        }
    )


@pytest.fixture
def lista_descuentos():
    return pd.DataFrame(
        {
            "SKU": ["A001"],
            "PRECIO_BASE": [5.20],
            "DESC1": [0.25],
            "DESC2": [0.04],
            "DESC3": [0.10],
            "DESC4": [0.08],
            "DESC5": [0.02],
            "PRECIO_NETO": [
                3.04
            ],  # 5.20 * (1-0.25) * (1-0.04) * (1-0.10) * (1-0.08) * (1-0.02) = 3.038
            "PRESENTACION": [1],
        }
    )


class TestCatalogo:
    def test_carga_procesos(self):
        c = CatalogoCargador()
        ps = c.listar_procesos()
        assert len(ps) == 9  # CDT→VRS + DF (devolucion_fisica)

    def test_obtener_diferencia_precio(self):
        c = CatalogoCargador()
        p = c.obtener_proceso("diferencia_precio")
        assert p["strategy"] == "PriceDifference"
        assert "PRECIO_BASE" in p["template"]["columnas_requeridas"]

    def test_obtener_schema(self):
        c = CatalogoCargador()
        s = c.obtener_schema("bonificacion_promocion")
        assert "PRECIO_UNITARIO" in s["columnas_requeridas"]


class TestValidationEngine:
    def test_historial_valido(self, historial_sample):
        ve = ValidationEngine()
        errores = ve.validar(historial_sample)
        assert len(errores) == 0

    def test_faltan_columnas(self):
        df = pd.DataFrame({"SOLES": [100]})
        ve = ValidationEngine()
        errores = ve.validar(df)
        assert len(errores) > 0

    def test_df_vacio(self):
        ve = ValidationEngine()
        errores = ve.validar(pd.DataFrame())
        assert len(errores) > 0


class TestNormalizationEngine:
    def test_normalizar_historial(self, historial_sample):
        norm = NormalizationEngine()
        df = norm.normalizar_historial(historial_sample)
        assert "PRECIO_UNITARIO" in df.columns
        assert df["PRECIO_UNITARIO"].iloc[0] == pytest.approx(3.5, 0.01)

    def test_normalizar_condicion(self, lista_precios):
        norm = NormalizationEngine()
        df = norm.normalizar_condicion(lista_precios)
        assert "SKU" in df.columns
        assert df["PRECIO_BASE"].dtype in (float, np.float64)

    def test_validar_schema_ok(self, lista_precios):
        schema = {"columnas_requeridas": ["SKU", "PRECIO_BASE"]}
        norm = NormalizationEngine(schema)
        errores = norm.validar_schema(lista_precios)
        assert len(errores) == 0

    def test_validar_schema_falta_columna(self):
        df = pd.DataFrame({"SKU": ["A001"]})
        schema = {"columnas_requeridas": ["SKU", "PRECIO_BASE"]}
        norm = NormalizationEngine(schema)
        errores = norm.validar_schema(df)
        assert len(errores) == 1

    def test_cadena_descuentos(self, lista_descuentos):
        schema = {
            "columnas_descuento": {"pattern": "^DESC\\d+$"},
            "validation": {"tolerancia_precio_neto": 0.01},
        }
        norm = NormalizationEngine(schema)
        df = norm.aplicar_cadena_descuentos(lista_descuentos)
        assert "PRECIO_CALCULADO" in df.columns
        assert df["PRECIO_CALCULADO"].iloc[0] == pytest.approx(3.038, 0.001)
        assert df["_NETO_OK"].iloc[0] == True

    def test_normalizar_desc01_sin_underscore(self):
        """desc01 (sin underscore, zero-padded) debe normalizarse a DESC1"""
        schema = {"columnas_descuento": {"pattern": "^DESC\\d+$"}}
        df = pd.DataFrame(
            {
                "SKU": ["A001"],
                "PRECIO_BASE": [5.20],
                "desc01": [0.25],
                "desc02": [0.04],
            }
        )
        norm = NormalizationEngine(schema)
        df_norm = norm.normalizar_condicion(df)
        assert "DESC1" in df_norm.columns
        assert "DESC2" in df_norm.columns
        assert df_norm["DESC1"].iloc[0] == 0.25

    def test_normalizar_precios_columna(self):
        """PRECIOS debe mapearse a PRECIO_BASE via header_map"""
        schema = {
            "header_map": {"PRECIOS": "PRECIO_BASE"},
            "columnas_requeridas": ["SKU", "PRECIO_BASE"],
        }
        df = pd.DataFrame(
            {
                "SKU": ["A001"],
                "precios": [5.14],
            }
        )
        norm = NormalizationEngine(schema)
        df_norm = norm.normalizar_condicion(df)
        assert "PRECIO_BASE" in df_norm.columns
        assert df_norm["PRECIO_BASE"].iloc[0] == 5.14

    def test_normalizar_formato_usuario_completo(self):
        """Formato completo: SKU, precios, desc01..desc08"""
        schema = {
            "header_map": {"PRECIOS": "PRECIO_BASE"},
            "columnas_descuento": {"pattern": "^DESC\\d+$"},
            "columnas_requeridas": ["SKU", "PRECIO_BASE"],
        }
        df = pd.DataFrame(
            {
                "SKU": ["03108"],
                "precios": [5.14],
                "desc01": [0.25],
                "desc02": [0.04],
                "desc03": [0.04],
                "desc04": [0.07],
                "desc05": [0.02],
                "desc06": [0],
                "desc07": [0],
                "desc08": [0],
            }
        )
        norm = NormalizationEngine(schema)
        df_norm = norm.normalizar_condicion(df)
        assert "PRECIO_BASE" in df_norm.columns
        assert df_norm["PRECIO_BASE"].iloc[0] == 5.14
        assert "DESC1" in df_norm.columns
        assert "DESC2" in df_norm.columns
        assert "DESC8" in df_norm.columns
        assert df_norm["DESC1"].iloc[0] == 0.25

    def test_normalizar_condicion_preserva_sku_leading_zeros(self):
        """SKU con ceros a la izquierda debe preservarse como string (03108 != 3108)"""
        schema = {
            "header_map": {"PRECIOS": "PRECIO_BASE"},
            "columnas_requeridas": ["SKU", "PRECIO_BASE"],
        }
        df = pd.DataFrame(
            {
                "SKU": ["03108", "03157"],
                "precios": [5.14, 5.14],
            }
        )
        norm = NormalizationEngine(schema)
        df_norm = norm.normalizar_condicion(df)
        assert str(df_norm["SKU"].iloc[0]) == "03108"
        assert str(df_norm["SKU"].iloc[1]) == "03157"
        assert df_norm["PRECIO_BASE"].iloc[0] == 5.14

    def test_cadena_descuentos_desc01_renombrado(self):
        """Cadena de descuentos funciona tras normalizar desc01..desc08"""
        schema = {
            "columnas_descuento": {"pattern": "^DESC\\d+$"},
            "validation": {"tolerancia_precio_neto": 0.01},
        }
        df = pd.DataFrame(
            {
                "SKU": ["03108"],
                "PRECIO_BASE": [5.14],
                "DESC1": [0.25],
                "DESC2": [0.04],
                "DESC3": [0.04],
                "DESC4": [0.07],
                "DESC5": [0.02],
                "DESC6": [0],
                "DESC7": [0],
                "DESC8": [0],
            }
        )
        norm = NormalizationEngine(schema)
        df_calc = norm.aplicar_cadena_descuentos(df)
        expected = 5.14 * (1 - 0.25) * (1 - 0.04) * (1 - 0.04) * (1 - 0.07) * (1 - 0.02)
        assert df_calc["PRECIO_CALCULADO"].iloc[0] == pytest.approx(expected, 0.001)


class TestPriceDifference:
    def test_precio_fijo(self, historial_sample, lista_precios):
        exp = ExpedienteComercial(
            nombre="Test",
            estrategia="PriceDifference",
            datos=historial_sample,
            condiciones=[lista_precios],
            contexto=PipelineContext(),
        )
        strategy = PriceDifferenceStrategy()
        result = strategy.process(exp)
        assert result is not None
        assert result.resumen["skus_afectados"] == 2  # A001 y A002
        assert not result.dataframe.empty

    def test_precio_fijo_alerta_diferencia_positiva(self, historial_sample):
        # Precio historial mayor que precio lista (cliente pagó de más)
        lista = pd.DataFrame({"SKU": ["A001"], "PRECIO_BASE": [3.00]})
        exp = ExpedienteComercial(
            nombre="Test",
            estrategia="PriceDifference",
            datos=historial_sample,
            condiciones=[lista],
            contexto=PipelineContext(),
        )
        strategy = PriceDifferenceStrategy()
        result = strategy.process(exp)
        alertas_info = [a for a in result.alertas if a.tipo == "info"]
        assert len(alertas_info) > 0

    def test_con_descuentos(self, historial_sample, lista_descuentos):
        exp = ExpedienteComercial(
            nombre="Test",
            estrategia="PriceDifference",
            variante="discount_chain",
            datos=historial_sample,
            condiciones=[lista_descuentos],
            contexto=PipelineContext(),
        )
        strategy = PriceDifferenceStrategy()
        result = strategy.process(exp)
        assert result is not None
        assert result.resumen["skus_afectados"] >= 1

    def test_sin_coincidencias(self, historial_sample):
        lista = pd.DataFrame({"SKU": ["X999"], "PRECIO_BASE": [10.00]})
        exp = ExpedienteComercial(
            nombre="Test",
            estrategia="PriceDifference",
            datos=historial_sample,
            condiciones=[lista],
            contexto=PipelineContext(),
        )
        strategy = PriceDifferenceStrategy()
        result = strategy.process(exp)
        assert result.dataframe.empty or len(result.alertas) > 0

    def test_historial_vacio(self):
        exp = ExpedienteComercial(
            nombre="Test",
            estrategia="PriceDifference",
            datos=pd.DataFrame(),
            condiciones=[pd.DataFrame({"SKU": ["A001"], "PRECIO_CORRECTO": [3.00]})],
            contexto=PipelineContext(),
        )
        strategy = PriceDifferenceStrategy()
        result = strategy.process(exp)
        assert result.resumen["total_nc"] == 0

    def test_tol_redondeo_dentro_umbral(self):
        """Dif unitaria 0.01 ≤ 0.01 y total 1.00 ≤ 1.00 → AL11 (redondeo).
        PRECIO_HIST=5.21, PRECIO_NETO=5.20 => dif_u=0.01, cant=100 => total=1.00."""
        hist = pd.DataFrame(
            {
                "CODIGO": ["S1"],
                "ARTICULO": ["X"],
                "CANTIDAD": [100],
                "SOLES": [521.00],
                "TIPO_DOC": ["F"],
                "SERIE": ["01"],
                "NUMERO": ["1"],
            }
        )
        lista = pd.DataFrame({"SKU": ["S1"], "PRECIO_BASE": [5.20]})
        exp = ExpedienteComercial(
            nombre="TolRedondo",
            estrategia="PriceDifference",
            datos=hist,
            condiciones=[lista],
            contexto=PipelineContext(),
        )
        result = PriceDifferenceStrategy().process(exp)
        alertas_sku = [a for a in result.alertas if a.sku == "S1"]
        assert alertas_sku, "sin alertas para S1"
        # Tambien verificamos la columna ALERTA en el dataframe
        alerta_df = result.dataframe[result.dataframe["SKU"] == "S1"]["ALERTA"].iloc[0]
        assert "AL11" in alerta_df, f"Esperado AL11 en df, obtenido: {alerta_df}"

    def test_tol_redondeo_total_superado(self):
        """Dif unitaria 0.004 ≤ 0.01 pero × 500 unid = 2.0 > 1.00 → AL01."""
        hist = pd.DataFrame(
            {
                "CODIGO": ["S1"],
                "ARTICULO": ["X"],
                "CANTIDAD": [500],
                "SOLES": [2602.00],
                "TIPO_DOC": ["F"],
                "SERIE": ["01"],
                "NUMERO": ["1"],
            }
        )
        # PRECIO_HIST = 2602/500 = 5.204, PRECIO_NETO = 5.20, dif = 0.004
        lista = pd.DataFrame({"SKU": ["S1"], "PRECIO_BASE": [5.20]})
        exp = ExpedienteComercial(
            nombre="TolTotal",
            estrategia="PriceDifference",
            datos=hist,
            condiciones=[lista],
            contexto=PipelineContext(),
        )
        result = PriceDifferenceStrategy().process(exp)
        alerta_df = result.dataframe[result.dataframe["SKU"] == "S1"]["ALERTA"].iloc[0]
        assert "AL01" in alerta_df, f"Esperado AL01 (total superado), obtenido: {alerta_df}"

    def test_tol_redondeo_unitario_superado(self):
        """Dif unitaria 0.05 > 0.01 → AL01 sin importar cantidad."""
        hist = pd.DataFrame(
            {
                "CODIGO": ["S1"],
                "ARTICULO": ["X"],
                "CANTIDAD": [10],
                "SOLES": [52.50],
                "TIPO_DOC": ["F"],
                "SERIE": ["01"],
                "NUMERO": ["1"],
            }
        )
        # PRECIO_HIST = 5.25, PRECIO_NETO = 5.20, dif = 0.05 > 0.01
        lista = pd.DataFrame({"SKU": ["S1"], "PRECIO_BASE": [5.20]})
        exp = ExpedienteComercial(
            nombre="TolUnit",
            estrategia="PriceDifference",
            datos=hist,
            condiciones=[lista],
            contexto=PipelineContext(),
        )
        result = PriceDifferenceStrategy().process(exp)
        alerta_df = result.dataframe[result.dataframe["SKU"] == "S1"]["ALERTA"].iloc[0]
        assert "AL01" in alerta_df, f"Esperado AL01 (unitaria superada), obtenido: {alerta_df}"

    def test_tol_redondeo_proporcional_alto_volumen(self):
        """Unidad ok (0.004 ≤ 0.01) pero volumen alto hace total > 1.00 → AL01."""
        # dif_unitaria ≈ 0.004366; para que total > 1.00 necesitamos > 229 unid
        hist = pd.DataFrame(
            {
                "CODIGO": ["S1"],
                "ARTICULO": ["X"],
                "CANTIDAD": [300],
                "SOLES": [1501.31],
                "TIPO_DOC": ["F"],
                "SERIE": ["01"],
                "NUMERO": ["1"],
            }
        )
        # PRECIO_HIST = 1501.31/300 = 5.004366..., PRECIO_NETO = 5.00, dif = 0.004366
        lista = pd.DataFrame({"SKU": ["S1"], "PRECIO_BASE": [5.00]})
        exp = ExpedienteComercial(
            nombre="TolVol",
            estrategia="PriceDifference",
            datos=hist,
            condiciones=[lista],
            contexto=PipelineContext(),
        )
        result = PriceDifferenceStrategy().process(exp)
        alerta_df = result.dataframe[result.dataframe["SKU"] == "S1"]["ALERTA"].iloc[0]
        # dif_total = 0.004366 * 300 = 1.3098 > 1.00
        assert "AL01" in alerta_df, f"Esperado AL01 (volumen alto), obtenido: {alerta_df}"


class TestPromotionBonus:
    def test_12_plus_1(self, historial_sample):
        exp = ExpedienteComercial(
            nombre="Test Promo",
            estrategia="PromotionBonus",
            datos=historial_sample,
            contexto=PipelineContext(config={"mecanica": "12+1", "skus": ["A001"]}),
        )
        strategy = PromotionBonusStrategy()
        result = strategy.process(exp)
        assert result is not None
        # A001 tiene 150 unidades: 150/12 = 12.5 -> 12 ciclos -> 12 unidades gratis
        bonif = result.metricas.get("unidades_bonificadas", 0)
        assert bonif == 12

    def test_mecanica_invalida(self, historial_sample):
        exp = ExpedienteComercial(
            nombre="Test",
            estrategia="PromotionBonus",
            datos=historial_sample,
            contexto=PipelineContext(config={"mecanica": "invalida"}),
        )
        strategy = PromotionBonusStrategy()
        result = strategy.process(exp)
        assert len(result.alertas) > 0

    def test_historial_vacio(self):
        exp = ExpedienteComercial(
            nombre="Test",
            estrategia="PromotionBonus",
            datos=pd.DataFrame(),
            contexto=PipelineContext(config={"mecanica": "12+1"}),
        )
        strategy = PromotionBonusStrategy()
        result = strategy.process(exp)
        assert result.resumen["total_nc"] == 0


class TestVolumeRebate:
    def test_rebate_con_meta_cumplida(self, historial_sample):
        exp = ExpedienteComercial(
            nombre="Test Rebate",
            estrategia="VolumeRebate",
            datos=historial_sample,
            contexto=PipelineContext(
                config={
                    "meta_monto": 100,
                    "porcentaje_rebate": 5,
                    "nivel_calculo": "sku",
                    "skus": ["A001"],
                }
            ),
        )
        strategy = VolumeRebateStrategy()
        result = strategy.process(exp)
        assert result is not None
        assert result.resumen["total_nc"] > 0

    def test_rebate_meta_no_cumplida(self, historial_sample):
        exp = ExpedienteComercial(
            nombre="Test Rebate",
            estrategia="VolumeRebate",
            datos=historial_sample,
            contexto=PipelineContext(
                config={
                    "meta_monto": 100000,
                    "porcentaje_rebate": 5,
                    "skus": ["A001"],
                }
            ),
        )
        strategy = VolumeRebateStrategy()
        result = strategy.process(exp)
        assert result.resumen["total_nc"] == 0


class TestCancelInvoice:
    def test_cancelar_factura(self, historial_sample):
        exp = ExpedienteComercial(
            nombre="Test Cancel",
            estrategia="CancelInvoice",
            datos=historial_sample,
            contexto=PipelineContext(config={"factura_id": "F001-100"}),
        )
        strategy = CancelInvoiceStrategy()
        result = strategy.process(exp)
        assert result is not None
        assert result.resumen["total_nc"] == pytest.approx(350.0, 0.01)

    def test_factura_no_encontrada(self, historial_sample):
        exp = ExpedienteComercial(
            nombre="Test Cancel",
            estrategia="CancelInvoice",
            datos=historial_sample,
            contexto=PipelineContext(config={"factura_id": "F999-999"}),
        )
        strategy = CancelInvoiceStrategy()
        result = strategy.process(exp)
        assert result.resumen["total_nc"] == 0

    def test_sin_factura_seleccionada(self, historial_sample):
        exp = ExpedienteComercial(
            nombre="Test Cancel",
            estrategia="CancelInvoice",
            datos=historial_sample,
            contexto=PipelineContext(config={}),
        )
        strategy = CancelInvoiceStrategy()
        result = strategy.process(exp)
        assert result.resumen["total_nc"] == 0


class TestBusinessValidator:
    def test_alerta_sku_sin_precio(self):
        df = pd.DataFrame(
            {"SKU": ["A001"], "PRECIO_LISTA": [0], "DIFERENCIA": [-1], "MONTO_NC": [-10]}
        )
        result = RecognitionResult(dataframe=df)
        exp = ExpedienteComercial(estrategia="PriceDifference", resultado=result)
        bv = BusinessValidator()
        alertas = bv.validar(exp)
        assert len(alertas) > 0

    def test_alerta_diferencia_positiva(self):
        df = pd.DataFrame(
            {"SKU": ["A001"], "PRECIO_LISTA": [3], "DIFERENCIA": [0.5], "MONTO_NC": [25]}
        )
        result = RecognitionResult(dataframe=df)
        exp = ExpedienteComercial(estrategia="PriceDifference", resultado=result)
        bv = BusinessValidator()
        alertas = bv.validar(exp)
        info_alertas = [a for a in alertas if a.tipo == "info"]
        assert len(info_alertas) > 0


class TestPipeline:
    def test_pipeline_diferencia_precio(self, historial_sample, lista_precios):
        from src.pipeline import Pipeline

        pipeline = Pipeline()
        exp = ExpedienteComercial(
            nombre="Pipeline Test",
            estrategia="PriceDifference",
            datos=historial_sample,
            condiciones=[lista_precios],
            contexto=PipelineContext(config={"modalidad": "por_sku_periodo"}),
        )
        result = pipeline.ejecutar(exp)
        assert result is not None
        assert result.resultado is not None


from src.ui.expediente_service import _clonar_sin_diferencia
from types import SimpleNamespace


class TestClonarSinDiferencia:
    """El filtro omitir_sin_diferencia debe excluir:
    - |DIFERENCIA| <= 0.001 (coincidencia exacta, AL02)
    - ALERTA == "AL11" (redondeo acumulable dentro de tolerancia)
    """

    def _make_res(self, diferencias, alertas, cantidad=1):
        import pandas as pd
        from src.domain import RecognitionResult

        datos = pd.DataFrame(
            {
                "SKU": ["S1"] * len(diferencias),
                "CANTIDAD": [cantidad] * len(diferencias),
                "SOLES": [5.20 * cantidad] * len(diferencias),
                "PRECIO_HIST": [5.20] * len(diferencias),
                "PRECIO_NETO": [5.20] * len(diferencias),
                "DIFERENCIA": diferencias,
                "MONTO_NC": [max(0.0, d) * cantidad for d in diferencias],
                "FACTURA": ["F01/012-100"] * len(diferencias),
                "ALERTA": alertas,
                "DOC_CLIENTE": ["20603839928"] * len(diferencias),
            }
        )
        res = RecognitionResult(dataframe=datos)
        res.resumen = {"total_nc": 0.0, "skus_afectados": 0}
        return res

    def test_excluye_al02_coincidencia_exacta(self):
        res = self._make_res([0.0005], ["AL02 - Precios coinciden"])
        exp = SimpleNamespace(resultado=res, datos=res.dataframe, contexto={})
        sub = _clonar_sin_diferencia(exp)
        # Todas las filas filtradas -> retorna None
        assert sub is None

    def test_excluye_al11_redondeo_acumulable(self):
        res = self._make_res(
            [0.004], ["AL11 - Redondeo acumulable (unit. S/ 0.004, total S/ 0.00)"]
        )
        exp = SimpleNamespace(resultado=res, datos=res.dataframe, contexto={})
        sub = _clonar_sin_diferencia(exp)
        # Todas las filas filtradas -> retorna None
        assert sub is None

    def test_mantiene_al01_diferencia_real(self):
        res = self._make_res([0.05], ["AL01 - Diferencia positiva S/ 0.05"])
        exp = SimpleNamespace(resultado=res, datos=res.dataframe, contexto={})
        sub = _clonar_sin_diferencia(exp)
        assert sub is not None
        assert not sub.resultado.dataframe.empty
        assert len(sub.resultado.dataframe) == 1
        assert "AL01" in sub.resultado.dataframe["ALERTA"].iloc[0]

    def test_mix_al01_y_al11_mantiene_solo_al01(self):
        res = self._make_res(
            [0.05, 0.004],
            ["AL01 - Diferencia positiva S/ 0.05", "AL11 - Redondeo acumulable"],
        )
        exp = SimpleNamespace(resultado=res, datos=res.dataframe, contexto={})
        sub = _clonar_sin_diferencia(exp)
        assert sub is not None
        assert len(sub.resultado.dataframe) == 1
        assert "AL01" in sub.resultado.dataframe["ALERTA"].iloc[0]

    def test_devuelve_none_si_todo_se_filtra(self):
        res = self._make_res([0.0001, 0.003], ["AL02", "AL11"])
        exp = SimpleNamespace(resultado=res, datos=res.dataframe, contexto={})
        sub = _clonar_sin_diferencia(exp)
        assert sub is None

    def test_excluye_diferencia_negativa_monto_cero(self):
        """Diferencia negativa (-0.021) genera MONTO_NC=0 por clip -> se excluye."""
        res = self._make_res([-0.02128], ["AL02 - Diferencia negativa, sin NC"])
        exp = SimpleNamespace(resultado=res, datos=res.dataframe, contexto={})
        sub = _clonar_sin_diferencia(exp)
        assert sub is None, "Se debio filtrar la fila con MONTO_NC=0"

    def test_mantiene_al01_con_monto_real(self):
        """AL01 con MONTO_NC > 0 se mantiene aunque haya diferencia negativa en otra fila."""
        res = self._make_res(
            [-0.02128, 0.05],
            ["AL02 - Diferencia negativa", "AL01 - Diferencia positiva S/ 0.05"],
        )
        exp = SimpleNamespace(resultado=res, datos=res.dataframe, contexto={})
        sub = _clonar_sin_diferencia(exp)
        assert sub is not None
        assert len(sub.resultado.dataframe) == 1


class TestRenderOpcionesCalculo:
    """La vista de resultados debe reflejar las OPCIONES DE CÁLCULO:

    - Con marcar_nd, las filas con diferencia negativa (MONTO_NC=0) se
      clasifican "nd" (GENERA ND) en vez de "alarma" (SIN NC).
    - render_resultado con marcar_nd=True devuelve conteos consistentes.
    """

    def _make_df(self):
        return pd.DataFrame(
            [
                {
                    "SKU": "A",
                    "ARTICULO": "Pos",
                    "CANTIDAD": 2,
                    "PRECIO_HIST": 2.94,
                    "PRECIO_BASE": 2.84,
                    "PRECIO_NETO": 2.84,
                    "DIFERENCIA": 0.10,
                    "MONTO_NC": 5.00,
                    "ALERTA": "AL01 - Diferencia positiva",
                    "NC_EXISTENTE": "",
                },
                {
                    "SKU": "B",
                    "ARTICULO": "Neg",
                    "CANTIDAD": 10,
                    "PRECIO_HIST": 2.50,
                    "PRECIO_BASE": 2.70,
                    "PRECIO_NETO": 2.70,
                    "DIFERENCIA": -0.20,
                    "MONTO_NC": 0.00,
                    "ALERTA": "AL02 - Diferencia negativa",
                    "NC_EXISTENTE": "",
                },
                {
                    "SKU": "C",
                    "ARTICULO": "Coin",
                    "CANTIDAD": 5,
                    "PRECIO_HIST": 2.50,
                    "PRECIO_BASE": 2.50,
                    "PRECIO_NETO": 2.50,
                    "DIFERENCIA": 0.0005,
                    "MONTO_NC": 0.00,
                    "ALERTA": "AL02 - Precios coinciden, sin NC",
                    "NC_EXISTENTE": "",
                },
            ]
        )

    def test_status_sin_marcar_nd(self):
        from src.ui.resultados_view import _build_standard_table

        _, _, sc = _build_standard_table(
            self._make_df(), pd.DataFrame(), "descuento_precio", marcar_nd=False
        )
        assert sc["n_genera"] == 1
        assert sc["n_alarma"] == 1
        assert sc["n_nd"] == 0
        assert sc["n_coincide"] == 1

    def test_status_con_marcar_nd(self):
        from src.ui.resultados_view import _build_standard_table

        _, _, sc = _build_standard_table(
            self._make_df(), pd.DataFrame(), "descuento_precio", marcar_nd=True
        )
        assert sc["n_genera"] == 1
        assert sc["n_alarma"] == 0
        assert sc["n_nd"] == 1
        assert sc["n_coincide"] == 1

    def test_render_resultado_marcar_nd(self):
        from src.ui.resultados_view import render_resultado
        from src.domain import RecognitionResult

        df = self._make_df()
        res = RecognitionResult(dataframe=df.copy(), dataframe_excel=df.copy())
        res.resumen = {"total_nc": 5.0, "skus_afectados": 1}
        res.alertas = []
        res.metricas = {}
        exp = SimpleNamespace(resultado=res, datos=df.copy(), contexto={"config": {}})
        r = render_resultado(
            exp, "descuento_precio", df.copy(), "#123456", "#34d399", marcar_nd=True
        )
        assert r["total_nc"] == "S/ 5.00"
        assert len(r["table_rows"]) == 3
        assert r["status_counts"]["n_nd"] == 1
