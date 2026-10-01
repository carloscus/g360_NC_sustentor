import pandas as pd
from src.core.utils import (
    _clean_value,
    format_id_name,
    format_doc_id,
    calcular_precio_unitario_df,
    cliente_visible,
    IGV_PERCENT,
    EXCEL_FMT_NUMBER,
    EXCEL_FMT_PCT,
)


class TestCleanValue:
    def test_none_returns_empty(self):
        assert _clean_value(None) == ""

    def test_nan_string_returns_empty(self):
        assert _clean_value("nan") == ""
        assert _clean_value("None") == ""

    def test_normal_string_stripped(self):
        assert _clean_value("  hello  ") == "hello"

    def test_non_printable_removed(self):
        assert _clean_value("he\x00llo") == "hello"


class TestFormatIdName:
    def test_both_present(self):
        assert format_id_name("001", "Producto A") == "001 - Producto A"

    def test_only_id(self):
        assert format_id_name("001", "") == "001"

    def test_only_name(self):
        assert format_id_name("", "Producto A") == "Producto A"

    def test_both_empty(self):
        assert format_id_name("", "") == ""


class TestFormatDocId:
    def test_full_doc(self):
        assert format_doc_id("F", "204", "51999") == "F204-51999"

    def test_serie_with_prefix(self):
        assert format_doc_id("F", "F204", "51999") == "F204-51999"

    def test_only_type(self):
        assert format_doc_id("F", "", "") == "F"

    def test_none_values(self):
        assert format_doc_id(None, None, None) == ""


class TestCalcularPrecioUnitarioDf:
    def test_basic_df(self):
        df = pd.DataFrame({"SOLES": [100, 200], "CANTIDAD": [10, 20]})
        result = calcular_precio_unitario_df(df)
        assert result["PRECIO_UNITARIO"].tolist() == [10.0, 10.0]

    def test_missing_columns(self):
        df = pd.DataFrame({"A": [1]})
        result = calcular_precio_unitario_df(df)
        assert "PRECIO_UNITARIO" not in result.columns

    def test_division_by_zero(self):
        df = pd.DataFrame({"SOLES": [100], "CANTIDAD": [0]})
        result = calcular_precio_unitario_df(df)
        assert result["PRECIO_UNITARIO"].iloc[0] == 0.0


class TestConstants:
    def test_igv_percent(self):
        assert IGV_PERCENT == 0.18

    def test_excel_formats(self):
        assert EXCEL_FMT_NUMBER == "#,##0.00"
        assert EXCEL_FMT_PCT == "0.00%"


class TestClienteVisible:
    """Convención: interno 8 dígitos, display SIEMPRE corto."""

    def test_id_corto_y_largo_muestran_lo_mismo(self):
        assert cliente_visible("68414") == "68414"
        assert cliente_visible("00068414") == "68414"
        assert cliente_visible("00056101") == "56101"
        assert cliente_visible("00002035") == "2035"

    def test_ruc_11_digitos_intacto(self):
        assert cliente_visible("20100047218") == "20100047218"

    def test_limites(self):
        assert cliente_visible("") == "0"
        assert cliente_visible(None) == "0"
        assert cliente_visible("0") == "0"

    def test_alias_del_reporte(self):
        from src.ui.reporte_compras import _c_visible

        assert _c_visible("00068414") == "68414"
