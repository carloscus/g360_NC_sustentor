"""Tests para normalización de IDs preservando ceros a la izquierda.

Caso crítico: '011019' y '11019' son SKUs distintos del ERP.
Estos tests validan que ninguna normalización los colapse.
"""

import pandas as pd

from src.core.utils import clean_id_column


class TestCleanIdColumn:
    """Verifica clean_id_column() sobre Series."""

    def test_preserva_cerinicial_distinto(self):
        """'011019' y '11019' deben permanecer como dos valores distintos."""
        s = pd.Series(["011019", "11019"])
        out = clean_id_column(s)
        assert out.iloc[0] == "011019"
        assert out.iloc[1] == "11019"
        assert out.nunique() == 2

    def test_preserva_multiples_cerosiniciales(self):
        """SKUs como '01240', '00456', '78' se mantienen con su largo original."""
        s = pd.Series(["01240", "00456", "78", "000123"])
        out = clean_id_column(s)
        assert out.tolist() == ["01240", "00456", "78", "000123"]

    def test_elimina_punto_cero_final(self):
        """Float serializado como '1234.0' -> '1234' (sin perder ceros iniciales)."""
        s = pd.Series(["011019.0", "11019.0", "00456.0"])
        out = clean_id_column(s)
        assert out.iloc[0] == "011019"
        assert out.iloc[1] == "11019"
        assert out.iloc[2] == "00456"

    def test_elimina_punto_cero_duplicado(self):
        """Caso '1234.0.0' -> '1234' (defensivo, no deberia entrar del ERP)."""
        s = pd.Series(["1234.0.0"])
        out = clean_id_column(s)
        assert out.iloc[0] == "1234"

    def test_convierte_nan_a_vacio(self):
        """'nan', 'NaN', 'None', '<NA>' -> '' (string vacio)."""
        s = pd.Series(["nan", "NaN", "None", "<NA>", "real_value"])
        out = clean_id_column(s)
        assert out.iloc[0] == ""
        assert out.iloc[1] == ""
        assert out.iloc[2] == ""
        assert out.iloc[3] == ""
        assert out.iloc[4] == "real_value"

    def test_input_es_int(self):
        """Entradas numericas se castean a str sin perder ceros (aqui no hay iniciales)."""
        s = pd.Series([11019, 1234, 456])
        out = clean_id_column(s)
        assert out.tolist() == ["11019", "1234", "456"]

    def test_input_es_float(self):
        """Float 1234.0 se convierte a '1234'.

        Nota: 0.0 -> '0' es un SKU valido, NO se trata como nulo.
        """
        s = pd.Series([1234.0, 11019.0, 0.0])
        out = clean_id_column(s)
        assert out.iloc[0] == "1234"
        assert out.iloc[1] == "11019"
        assert out.iloc[2] == "0"

    def test_input_con_espacios_y_bom(self):
        """Limpia espacios y BOM sin alterar ceros iniciales."""
        s = pd.Series(["\ufeff011019 ", " 11019", "   "])
        out = clean_id_column(s)
        assert out.iloc[0] == "011019"
        assert out.iloc[1] == "11019"
        assert out.iloc[2] == ""

    def test_dtype_se_mantiene_object(self):
        """dtype debe seguir siendo object (preserva '011019' != '11019')."""
        s = pd.Series(["011019", "11019"])
        out = clean_id_column(s)
        assert out.dtype == object

    def test_no_collapsing_en_merge(self):
        """Simulando merge: los IDs deben matchear exactamente."""
        df_left = pd.DataFrame({"CODIGO": ["011019", "11019"], "qty": [10, 5]})
        df_right = pd.DataFrame({"CODIGO": ["011019", "11019"], "price": [100, 50]})
        df_left["CODIGO"] = clean_id_column(df_left["CODIGO"])
        df_right["CODIGO"] = clean_id_column(df_right["CODIGO"])
        merged = df_left.merge(df_right, on="CODIGO")
        assert len(merged) == 2
        assert set(merged["CODIGO"]) == {"011019", "11019"}
