import pandas as pd
from src.core.detector import (
    detectar_notas_en_historial,
    resumen_notas_por_factura,
    obtener_notas_de_factura,
    separar_inventario,
    _es_documento_nota,
    _es_factura,
    _parsear_referencia_factura,
)


class TestClasificacion:
    def test_es_nota_credito(self):
        assert _es_documento_nota("NC")
        assert _es_documento_nota("NC01")
        assert _es_documento_nota("NOTA CREDITO")

    def test_es_nota_debito(self):
        assert _es_documento_nota("NDB")
        assert _es_documento_nota("ND")

    def test_no_es_nota(self):
        assert not _es_documento_nota("F")
        assert not _es_documento_nota("F001")
        assert not _es_documento_nota("B")
        assert not _es_documento_nota("")

    def test_es_factura(self):
        assert _es_factura("F")
        assert _es_factura("F001")
        assert _es_factura("B")

    def test_no_es_factura(self):
        assert not _es_factura("NC")
        assert not _es_factura("NDB")


class TestParsearReferencia:
    def test_formato_factura_completo(self):
        assert _parsear_referencia_factura("F026/001-1234567") == "F001-1234567"

    def test_formato_simple(self):
        assert _parsear_referencia_factura("F001-100") == "F001-100"

    def test_vacio(self):
        assert _parsear_referencia_factura("") == ""
        assert _parsear_referencia_factura(None) == ""


class TestDetectarNotas:
    def test_sin_notas(self):
        df = pd.DataFrame(
            {
                "TIPO_DOC": ["F", "F"],
                "SERIE": ["001", "002"],
                "NUMERO": ["100", "200"],
                "CODIGO": ["A001", "A002"],
                "CANTIDAD": [10, 20],
                "SOLES": [100, 200],
                "REFERENCIA": ["", ""],
            }
        )
        result = detectar_notas_en_historial(df)
        assert result.empty

    def test_con_notas(self):
        df = pd.DataFrame(
            {
                "TIPO_DOC": ["F", "NC", "NDB"],
                "SERIE": ["001", "001", "002"],
                "NUMERO": ["100", "50", "30"],
                "CODIGO": ["A001", "A001", "A002"],
                "ARTICULO": ["Art A", "Art A", "Art B"],
                "CANTIDAD": [10, -5, 3],
                "SOLES": [100, -50, 30],
                "REFERENCIA": ["", "F001-100", "F002-200"],
            }
        )
        result = detectar_notas_en_historial(df)
        assert len(result) == 2
        assert "DOC_NOTA" in result.columns
        assert "FACTURA_REF" in result.columns


class TestSepararInventario:
    def test_separacion_correcta(self):
        df = pd.DataFrame(
            {
                "TIPO_DOC": ["F", "F", "NC", "NDB"],
                "SERIE": ["001", "002", "001", "002"],
                "NUMERO": ["100", "200", "50", "30"],
                "CODIGO": ["A001", "A002", "A001", "A002"],
                "CANTIDAD": [10, 20, -5, 3],
                "SOLES": [100, 200, -50, 30],
                "REFERENCIA": ["", "", "F001-100", "F002-200"],
            }
        )
        result = separar_inventario(df)
        assert len(result["facturas"]) == 2
        assert len(result["notas"]) == 2
        assert "F001-100" in result["resumen_notas"]

    def test_inventario_vacio(self):
        result = separar_inventario(pd.DataFrame())
        assert result["facturas"].empty
        assert result["notas"].empty


class TestObtenerNotasDeFactura:
    def test_filtra_por_factura(self):
        df = pd.DataFrame(
            {
                "DOC_NOTA": ["NC01", "NC02"],
                "FACTURA_REF": ["F001-100", "F002-200"],
                "CODIGO": ["A001", "A002"],
                "CANTIDAD": [5, 3],
                "SOLES": [50, 30],
            }
        )
        result = obtener_notas_de_factura(df, "F001-100")
        assert len(result) == 1
        assert result.iloc[0]["DOC_NOTA"] == "NC01"


class TestResumenNotas:
    def test_resumen_agrupa_por_factura(self):
        df = pd.DataFrame(
            {
                "DOC_NOTA": ["NC01", "NC02"],
                "FACTURA_REF": ["F001-100", "F001-100"],
                "CODIGO": ["A001", "A002"],
                "ARTICULO": ["Art A", "Art B"],
                "CANTIDAD": [5.0, 3.0],
                "SOLES": [50.0, 30.0],
            }
        )
        resumen = resumen_notas_por_factura(df)
        assert "F001-100" in resumen
        assert resumen["F001-100"]["total_notas"] == 2
        assert resumen["F001-100"]["total_soles"] == 80.0
        assert "A001" in resumen["F001-100"]["skus"]
