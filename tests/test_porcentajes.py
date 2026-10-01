"""Convención canónica de porcentajes en toda la app: 0.05 = 5%.

El archivo guarda la fraccion y la mascara Excel '0.00%' la muestra como
porcentaje. Antes de unificar convivian tres convenciones y dos daban
montos equivocados en silencio:

  * CMV (descuento_factura): `pct / 100` sin guarda -> un 0.05 capturado
    daba 100x menos de NC (S/ 0.50 en vez de S/ 50.00).
  * FPE (allocation) y doc_matcher: corte `>= 0.5` -> cualquier descuento
    de 50% o mas se partia a la mitad otra vez (50% -> 0.5%).

Estos tests bloquean el helper unico, su interpretacion de puntos de
porcentaje (sin alerta cuando aplican bien; AL04 solo si exceden 100%)
y las rutas de las 6 estrategias con %.
"""

import pandas as pd
import pytest

from src.core.utils import normalizar_porcentaje, normalizar_porcentaje_serie
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
            "CANTIDAD": [100, 50, 200, 30],
            "SOLES": [1000.0, 750.0, 2000.0, 450.0],
            "PRECIO_UNITARIO": [10.0, 15.0, 10.0, 15.0],
            "DOC_CLIENTE": ["206039928"] * 4,
        }
    )


def _lista(pct_desc1):
    return pd.DataFrame(
        {"SKU": ["02203", "02202"], "PRECIO_BASE": [9.0, 14.0], "DESC1": [pct_desc1, 0.0]}
    )


class TestHelperEscalar:
    def test_acepta_los_tres_formatos_sin_alerta(self):
        for entrada in ("5%", "5.00%", "5 %", 0.05, "0.05"):
            frac, cod = normalizar_porcentaje(entrada)
            assert frac == pytest.approx(0.05), entrada
            assert cod is None, entrada

    def test_puntos_de_porcentaje_interpretados_sin_alerta(self):
        """5 = 5% se interpreta y aplica bien: sin alerta."""
        assert normalizar_porcentaje(5.0) == (pytest.approx(0.05), None)
        assert normalizar_porcentaje(25.0) == (pytest.approx(0.25), None)
        assert normalizar_porcentaje(100) == (pytest.approx(1.0), None)

    def test_al04_solo_si_excede_100_tras_interpretar(self):
        """150 puntos -> 1.50: excede 100% de verdad, mantiene AL04."""
        assert normalizar_porcentaje(150) == (pytest.approx(1.5), "AL04")
        assert normalizar_porcentaje("150%") == (pytest.approx(1.5), "AL04")

    def test_rango_valido_sin_alerta(self):
        for entrada in (0, 0.0, 0.05, 0.5, 1.0, "0", "1"):
            frac, cod = normalizar_porcentaje(entrada)
            assert cod is None, entrada
            assert 0.0 <= frac <= 1.0, entrada

    def test_negativo_y_texto_ilegible(self):
        assert normalizar_porcentaje(-0.2) == (0.0, "AL04")
        assert normalizar_porcentaje("0,05") == (0.0, "AL10")  # coma decimal
        assert normalizar_porcentaje("abc") == (0.0, "AL10")

    def test_vacio_y_nulos_sin_alerta(self):
        for entrada in ("", None, float("nan"), "nan", "none"):
            assert normalizar_porcentaje(entrada) == (0.0, None), entrada

    def test_es_idempotente(self):
        """Aplicarlo dos veces no cambia la fraccion (ya normalizada)."""
        una = normalizar_porcentaje(5.0)
        dos = normalizar_porcentaje(una[0])
        assert dos[0] == pytest.approx(0.05)
        assert dos[1] is None  # la segunda pasada no alerta

    def test_serie_vectorizada_calza_con_escalar(self):
        entradas = ["5%", 0.05, 5.0, 0, "", "0,05", 1.0, -0.2, "25%"]
        fracciones, codigos = normalizar_porcentaje_serie(pd.Series(entradas))
        for i, entrada in enumerate(entradas):
            esp_frac, esp_cod = normalizar_porcentaje(entrada)
            assert fracciones.iloc[i] == pytest.approx(esp_frac), entrada
            assert codigos.iloc[i] == esp_cod, entrada


class TestDescuentoFacturaCMV:
    """NC = PRECIO_UNITARIO × fraccion × CANTIDAD (antes: × pct/100)."""

    def _ejecutar(self, desc_pct):
        exp = ExpedienteComercial(
            nombre="Descuento factura",
            familia="Descuento factura",
            estrategia="DescuentoFactura",
            datos=_historial(),
            contexto=PipelineContext(
                config={"descuento_pct": desc_pct, "factura_id": "F01-204-40260"}
            ),
        )
        return Pipeline().ejecutar(exp)

    def test_fraccion_correcta_no_se_divide_otra_vez(self):
        """0.05 (5%) sobre 380 unid: 210.00, no 2.10 (100x menos)."""
        exp = self._ejecutar(0.05)
        res = exp.resultado
        assert res is not None
        assert float(res.dataframe["MONTO_NC"].sum()) == pytest.approx(210.00)
        assert float(res.dataframe["%_DESCUENTO"].iloc[0]) == pytest.approx(0.05)
        # Captura canonica: no genera ninguna alerta.
        assert not [a for a in res.alertas if getattr(a, "codigo", "") in ("AL04", "AL10")]

    def test_puntos_de_porcentaje_mismo_monto(self):
        """5 (5%) -> el mismo monto y sin rastro AL04 (se aplica bien)."""
        exp = self._ejecutar(5)
        res = exp.resultado
        assert float(res.dataframe["MONTO_NC"].sum()) == pytest.approx(210.00)
        assert not [a for a in res.alertas if getattr(a, "codigo", "") == "AL04"]


class TestFPEYDocMatcher:
    """El corte anterior era `>= 0.5`: 50% -> 0.5% (100x menos)."""

    def test_corte_allocation_engine(self):
        from src.strategies.allocation.engine import _convertir_porcentaje

        assert _convertir_porcentaje(0.5) == pytest.approx(0.5)  # 50%
        assert _convertir_porcentaje(1.0) == pytest.approx(1.0)  # 100%
        assert _convertir_porcentaje(0.05) == pytest.approx(0.05)
        assert _convertir_porcentaje("5%") == pytest.approx(0.05)
        assert _convertir_porcentaje(5) == pytest.approx(0.05)  # puntos

    def test_corte_doc_matcher(self):
        from src.core.doc_matcher import _convertir_porcentaje

        assert _convertir_porcentaje(0.5) == pytest.approx(0.5)
        assert _convertir_porcentaje(5) == pytest.approx(0.05)

    def test_status_de_item_sin_alerta_con_puntos(self):
        from src.strategies.allocation.engine import _convertir_porcentaje_y_alerta

        frac, cod = _convertir_porcentaje_y_alerta(5)
        assert frac == pytest.approx(0.05)
        assert cod is None


class TestCadenaDC:
    """La cadena de descuentos ya no depende del formato de captura."""

    @staticmethod
    def _comparar(lista):
        exp = ExpedienteComercial(
            nombre="DC",
            familia="DC",
            estrategia="PriceDifference",
            datos=_historial(),
            contexto=PipelineContext(config={"sort_mode": "fecha_asc"}),
        )
        exp.condiciones = [lista]
        return Pipeline().ejecutar(exp)

    def test_desc_texto_sin_crash(self):
        """'5%' antes reventaba con TypeError: int - str."""
        exp = self._comparar(_lista("5%"))
        assert exp.resultado is not None and not exp.resultado.dataframe.empty
        neto = float(exp.resultado.dataframe["PRECIO_NETO"].iloc[0])
        assert neto == pytest.approx(9.0 * 0.95)  # 8.55

    def test_desc_numerico_no_da_precio_negativo(self):
        """5.0 antes daba PRECIO_NETO = -16.00 sin ninguna alerta."""
        exp = self._comparar(_lista(5.0))
        df = exp.resultado.dataframe
        assert float(df["PRECIO_NETO"].iloc[0]) == pytest.approx(8.55)
        assert (df["PRECIO_NETO"] > 0).all()

    def test_header_oficial_de_plantilla(self):
        """'PRECIO _LISTA' es un alias del header_map de la plantilla DC."""
        lista = pd.DataFrame({"SKU": ["02203"], "PRECIO _LISTA": [9.0]})
        exp = self._comparar(lista)
        assert exp.resultado is not None
        assert not exp.resultado.dataframe.empty


class TestRebateVolumen:
    def test_fraccion_tanto_como_puntos(self):
        for entrada in (5, 0.05, "5%"):
            exp = ExpedienteComercial(
                nombre="Rebate",
                familia="Rebate",
                estrategia="VolumeRebate",
                datos=_historial(),
                contexto=PipelineContext(
                    config={"meta_monto": 1.0, "porcentaje_rebate": entrada, "lineas": []}
                ),
            )
            exp = Pipeline().ejecutar(exp)
            assert exp.resultado is not None, entrada
            assert float(exp.resultado.dataframe["MONTO_NC"].sum()) > 0, entrada
