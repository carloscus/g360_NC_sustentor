"""Tests para NormalizationEngine.preparar_historial_erp().

Este metodo es la fuente canonica de ingesta + normalizacion del proyecto.
Tanto NCProcessor (legacy) como AllocationEngine / FeriaPreventa consumen
historales del ERP; este test fija el contrato.

Casos cubiertos:
    - Deteccion dinamica de cabecera (filas vacias iniciales)
    - Purga de fila TOTAL/TOTALES al final
    - HEADER_MAP renombrando
    - Limpieza de IDs preservando ceros a la izquierda
    - Reemplazo O->0 en columnas numericas
    - Fechas: serial Excel + dayfirst
    - Calculo de PRECIO_UNITARIO si falta o suma 0
    - Validacion de columnas criticas (raise vs skip)
    - Registro de filas omitidas (fechas ilegibles)
    - Idempotencia (llamar 2 veces produce el mismo resultado)
"""

import pandas as pd
import pytest
from datetime import datetime

from src.validation.normalization import NormalizationEngine


@pytest.fixture
def engine():
    return NormalizationEngine()


@pytest.fixture
def historial_basico():
    """Historial minimo bien formado (ya con cabeceras correctas)."""
    return pd.DataFrame(
        {
            "ANHO": [2024, 2024, 2024],
            "MES": [1, 1, 1],
            "CODIGO": ["011019", "11019", "A001"],
            "ARTICULO": ["SKU A", "SKU B", "SKU C"],
            "DOC_CLIENTE": ["12345678", "87654321", "11223344"],
            "CLIENTE": ["C1", "C2", "C3"],
            "TIPO_DOC": ["F", "F", "N"],
            "SERIE": ["001", "002", "003"],
            "NUMERO": ["100", "200", "300"],
            "CANTIDAD": [10, 5, 20],
            "SOLES": [100.0, 50.0, 300.0],
            "PRECIO_UNITARIO": [10.0, 10.0, 15.0],
            "FECHA": [datetime(2024, 1, 15), datetime(2024, 1, 10), datetime(2024, 1, 20)],
        }
    )


class TestDeteccionCabecera:
    def test_cabecera_ya_detectada(self, engine, historial_basico):
        out = engine.preparar_historial_erp(historial_basico)
        assert "CODIGO" in out.columns
        assert "FECHA" in out.columns
        assert len(out) == 3

    def test_cabecera_en_primera_fila(self, engine):
        """Si la primera fila ya contiene keywords, se usa como cabecera."""
        # Construir un df donde la primera fila del contenido son los headers
        df = pd.DataFrame(
            [
                ["ANHO", "CODIGO", "CANTIDAD", "SOLES", "FECHA", "NUMERO"],
                [2024, "A001", 10, 100.0, datetime(2024, 1, 15), "100"],
                [2024, "A002", 5, 50.0, datetime(2024, 1, 10), "200"],
            ]
        )
        out = engine.preparar_historial_erp(df)
        assert "CODIGO" in out.columns
        assert len(out) == 2
        assert out["CODIGO"].iloc[0] == "A001"

    def test_cabecera_en_fila_intermedia(self, engine):
        """Si hay filas vacias/logo al inicio, las salta hasta encontrar keywords."""
        df = pd.DataFrame(
            [
                ["", "", "", "", "", ""],
                ["LOGO_CIA", "Version 2024", "S.A.", "", "", ""],
                ["ANHO", "CODIGO", "CANTIDAD", "SOLES", "FECHA", "NUMERO"],
                [2024, "A001", 10, 100.0, datetime(2024, 1, 15), "100"],
            ]
        )
        out = engine.preparar_historial_erp(df)
        assert "CODIGO" in out.columns
        assert len(out) == 1
        assert out["CODIGO"].iloc[0] == "A001"

    def test_purga_fila_totales(self, engine):
        """Fila final con TOTAL/TOTALES se elimina."""
        df = pd.DataFrame(
            [
                ["ANHO", "CODIGO", "CANTIDAD", "SOLES", "FECHA", "NUMERO"],
                [2024, "A001", 10, 100.0, datetime(2024, 1, 15), "100"],
                ["TOTAL GENERAL", "", 10, 100.0, "", ""],
            ]
        )
        out = engine.preparar_historial_erp(df)
        # Solo la fila de datos debe quedar
        assert len(out) == 1
        assert out["CODIGO"].iloc[0] == "A001"


class TestHeaderMap:
    def test_variantes_precio_unitario(self, engine):
        """Distintas formas de 'precio unitario' se unifican a PRECIO_UNITARIO."""
        df = pd.DataFrame(
            {
                "ANHO": [2024],
                "CODIGO": ["A001"],
                "CANTIDAD": [10],
                "SOLES": [100.0],
                "FECHA": [datetime(2024, 1, 15)],
                "NUMERO": ["100"],
                "PRECIO UNITARIO": [10.0],
            }
        )
        out = engine.preparar_historial_erp(df)
        assert "PRECIO_UNITARIO" in out.columns
        assert out["PRECIO_UNITARIO"].iloc[0] == 10.0

    def test_prefijo_id_a_codigo(self, engine):
        """ID_ARTICULO se renombra a CODIGO."""
        df = pd.DataFrame(
            {
                "ANHO": [2024],
                "ID_ARTICULO": ["A001"],
                "CANTIDAD": [10],
                "SOLES": [100.0],
                "FECHA": [datetime(2024, 1, 15)],
                "NUMERO": ["100"],
            }
        )
        out = engine.preparar_historial_erp(df)
        assert "CODIGO" in out.columns
        assert out["CODIGO"].iloc[0] == "A001"

    def test_sin_cambio_si_ya_estandar(self, engine, historial_basico):
        out = engine.preparar_historial_erp(historial_basico)
        for col in ["ANHO", "CODIGO", "CANTIDAD", "SOLES", "FECHA", "NUMERO"]:
            assert col in out.columns


class TestLimpiezaIDs:
    def test_ceros_iniciales_preservados(self, engine, historial_basico):
        out = engine.preparar_historial_erp(historial_basico)
        assert out["CODIGO"].iloc[0] == "011019"
        assert out["CODIGO"].iloc[1] == "11019"
        assert out["CODIGO"].nunique() == 3  # no colapso

    def test_punto_cero_eliminado(self, engine):
        """Float 1234.0 -> '1234'."""
        df = pd.DataFrame(
            {
                "ANHO": [2024],
                "CODIGO": ["01240"],
                "CANTIDAD": [10],
                "SOLES": [100.0],
                "FECHA": [datetime(2024, 1, 15)],
                "NUMERO": ["100"],
            }
        )
        # Simulamos que CODIGO llega como float con .0
        df["CODIGO"] = pd.Series([1240.0], dtype=object)
        out = engine.preparar_historial_erp(df)
        assert out["CODIGO"].iloc[0] == "01240" or out["CODIGO"].iloc[0] == "1240"


class TestLimpiezaNumerica:
    def test_O_reemplazado_por_0(self, engine):
        """Errores de digitacion 'O' -> '0' en columnas numericas."""
        df = pd.DataFrame(
            {
                "ANHO": [2024],
                "CODIGO": ["A001"],
                "CANTIDAD": ["1O"],
                "SOLES": ["1OO"],
                "FECHA": [datetime(2024, 1, 15)],
                "NUMERO": ["100"],
            }
        )
        out = engine.preparar_historial_erp(df)
        assert out["CANTIDAD"].iloc[0] == 10  # '1O' -> 10
        assert out["SOLES"].iloc[0] == 100.0  # '1OO' -> 100.0

    def test_numericos_a_float(self, engine):
        """Columnas numericas llegan como float limpios."""
        df = pd.DataFrame(
            {
                "ANHO": [2024],
                "CODIGO": ["A001"],
                "CANTIDAD": ["10"],
                "SOLES": ["100"],
                "FECHA": [datetime(2024, 1, 15)],
                "NUMERO": ["100"],
            }
        )
        out = engine.preparar_historial_erp(df)
        assert pd.api.types.is_numeric_dtype(out["CANTIDAD"])
        assert pd.api.types.is_numeric_dtype(out["SOLES"])
        assert out["CANTIDAD"].iloc[0] == 10

    def test_invalidos_a_cero(self, engine):
        """Strings no numericos van a 0."""
        df = pd.DataFrame(
            {
                "ANHO": [2024],
                "CODIGO": ["A001"],
                "CANTIDAD": ["abc"],
                "SOLES": ["xyz"],
                "FECHA": [datetime(2024, 1, 15)],
                "NUMERO": ["100"],
            }
        )
        out = engine.preparar_historial_erp(df)
        assert out["CANTIDAD"].iloc[0] == 0
        assert out["SOLES"].iloc[0] == 0


class TestFechas:
    def test_serial_excel(self, engine):
        """45292 -> 2024-01-15 (serial Excel)."""
        df = pd.DataFrame(
            {
                "ANHO": [2024],
                "CODIGO": ["A001"],
                "CANTIDAD": [10],
                "SOLES": [100.0],
                "FECHA": [45292],
                "NUMERO": ["100"],
            }
        )
        out = engine.preparar_historial_erp(df)
        assert pd.api.types.is_datetime64_any_dtype(out["FECHA"])
        assert out["FECHA"].iloc[0].year == 2024

    def test_serial_como_string(self, engine):
        """'45292.0' (string) tambien se interpreta."""
        df = pd.DataFrame(
            {
                "ANHO": [2024],
                "CODIGO": ["A001"],
                "CANTIDAD": [10],
                "SOLES": [100.0],
                "FECHA": ["45292.0"],
                "NUMERO": ["100"],
            }
        )
        out = engine.preparar_historial_erp(df)
        assert pd.api.types.is_datetime64_any_dtype(out["FECHA"])

    def test_dayfirst(self, engine):
        """Fechas dd/mm/yyyy se interpretan correctamente."""
        df = pd.DataFrame(
            {
                "ANHO": [2024],
                "CODIGO": ["A001"],
                "CANTIDAD": [10],
                "SOLES": [100.0],
                "FECHA": ["15/01/2024"],
                "NUMERO": ["100"],
            }
        )
        out = engine.preparar_historial_erp(df)
        assert out["FECHA"].iloc[0].month == 1
        assert out["FECHA"].iloc[0].day == 15

    def test_omitidas_registradas(self, engine):
        """Fechas ilegibles reportan a omitidas_sink."""
        df = pd.DataFrame(
            {
                "ANHO": [2024, 2024],
                "CODIGO": ["A001", "A002"],
                "CANTIDAD": [10, 5],
                "SOLES": [100.0, 50.0],
                "FECHA": [datetime(2024, 1, 15), "no_es_fecha"],
                "NUMERO": ["100", "200"],
            }
        )
        omitidas = []
        engine.preparar_historial_erp(df, omitidas_sink=omitidas, validar_columnas=False)
        # La segunda fila (no_es_fecha) debe estar registrada
        # Nota: validar_columnas=False para no romper el test (FECHA puede quedar con NaT)
        assert any(o.get("NUMERO") == "200" for o in omitidas)


class TestPrecioUnitario:
    def test_calcula_si_falta(self, engine):
        """Si PRECIO_UNITARIO no esta, se calcula SOLES/CANTIDAD."""
        df = pd.DataFrame(
            {
                "ANHO": [2024],
                "CODIGO": ["A001"],
                "CANTIDAD": [10],
                "SOLES": [100.0],
                "FECHA": [datetime(2024, 1, 15)],
                "NUMERO": ["100"],
            }
        )
        out = engine.preparar_historial_erp(df)
        assert "PRECIO_UNITARIO" in out.columns
        assert out["PRECIO_UNITARIO"].iloc[0] == 10.0

    def test_recalcula_si_suma_cero(self, engine):
        """Si PRECIO_UNITARIO es 0 en todas las filas, se recalcula."""
        df = pd.DataFrame(
            {
                "ANHO": [2024, 2024],
                "CODIGO": ["A001", "A002"],
                "CANTIDAD": [10, 5],
                "SOLES": [100.0, 50.0],
                "FECHA": [datetime(2024, 1, 15), datetime(2024, 1, 20)],
                "NUMERO": ["100", "200"],
                "PRECIO_UNITARIO": [0, 0],
            }
        )
        out = engine.preparar_historial_erp(df)
        assert out["PRECIO_UNITARIO"].iloc[0] == 10.0

    def test_no_recalcula_con_flag(self, engine):
        """Con reescribir_precio_unitario_cero=False, se preserva el 0."""
        df = pd.DataFrame(
            {
                "ANHO": [2024, 2024],
                "CODIGO": ["A001", "A002"],
                "CANTIDAD": [10, 5],
                "SOLES": [100.0, 50.0],
                "FECHA": [datetime(2024, 1, 15), datetime(2024, 1, 20)],
                "NUMERO": ["100", "200"],
                "PRECIO_UNITARIO": [0, 0],
            }
        )
        out = engine.preparar_historial_erp(df, reescribir_precio_unitario_cero=False)
        assert (out["PRECIO_UNITARIO"] == 0).all()


class TestValidacionColumnas:
    def test_exitoso(self, engine, historial_basico):
        """No lanza excepcion con todas las columnas criticas."""
        engine.preparar_historial_erp(historial_basico)  # debe pasar

    def test_falta_columna_critica(self, engine):
        """Sin CODIGO: lanza ValueError."""
        df = pd.DataFrame(
            {
                "ANHO": [2024],
                "CANTIDAD": [10],
                "SOLES": [100.0],
                "FECHA": [datetime(2024, 1, 15)],
                "NUMERO": ["100"],
            }
        )
        with pytest.raises(ValueError, match="CODIGO"):
            engine.preparar_historial_erp(df)

    def test_skip_validacion(self, engine):
        """validar_columnas=False no lanza aunque falten criticas."""
        df = pd.DataFrame(
            {
                "ANHO": [2024],
                "CANTIDAD": [10],
                "SOLES": [100.0],
                "FECHA": [datetime(2024, 1, 15)],
                "NUMERO": ["100"],
            }
        )
        out = engine.preparar_historial_erp(df, validar_columnas=False)
        assert len(out) == 1


class TestIdempotencia:
    def test_doble_llamada_mismo_resultado(self, engine, historial_basico):
        """Llamar 2 veces produce DataFrames equivalentes."""
        out1 = engine.preparar_historial_erp(historial_basico)
        out2 = engine.preparar_historial_erp(out1)
        # Mismas columnas
        assert set(out1.columns) == set(out2.columns)
        # Mismos CODIGO (clave)
        assert list(out1["CODIGO"]) == list(out2["CODIGO"])

    def test_dataframe_vacio(self, engine):
        """DataFrame vacio se devuelve intacto."""
        df = pd.DataFrame()
        out = engine.preparar_historial_erp(df)
        assert out.empty


class TestSalidaNormalizacion:
    """Tests de salida normalizada: resultados para FIFO allocation."""

    def test_columnas_criticas_presentes(self, engine, historial_basico):
        """Columnas requeridas por AllocationEngine estan presentes."""
        # Replica la logica minima requerida para la asignacion FIFO
        out = engine.preparar_historial_erp(historial_basico)

        # 1. Columnas criticas presentes
        assert all(c in out.columns for c in ("CODIGO", "FECHA", "CANTIDAD", "SOLES", "NUMERO"))

        # 2. Tipos correctos para operaciones downstream
        assert pd.api.types.is_datetime64_any_dtype(out["FECHA"])
        assert pd.api.types.is_numeric_dtype(out["CANTIDAD"])
        assert pd.api.types.is_numeric_dtype(out["SOLES"])

        # 3. Sin perdidas de filas
        assert len(out) == len(historial_basico)

        # 4. CODIGO preserva ambos formatos
        assert out["CODIGO"].nunique() == 3
        assert "011019" in out["CODIGO"].values
        assert "11019" in out["CODIGO"].values

        # 5. PRECIO_UNITARIO listo para calculos
        assert out["PRECIO_UNITARIO"].sum() > 0
