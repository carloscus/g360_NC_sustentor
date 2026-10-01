"""Tests para la funcion split_doc_id() y build_doc_full().

Caso de uso: consolidar la logica duplicada de TIPO+SERIE+NUMERO que existia
en 3 sitios (processor.py, inventory.py, normalization.py) con inconsistencias
mismas (algunas usan 'while' para limpiar prefijos, otras solo un 'if').
"""

import pandas as pd

from src.core.utils import split_doc_id, build_doc_full


class TestSplitDocIdScalar:
    """Verifica split_doc_id() con valores escalares."""

    def test_caso_simple(self):
        """('F', '001', '0457996') -> ('F', '001', '0457996') sin cambios."""
        t, s, n = split_doc_id("F", "001", "0457996")
        assert t == "F"
        assert s == "001"
        assert n == "0457996"

    def test_serie_con_prefijo_tipo(self):
        """Serie 'F001' con tipo 'F' -> serie limpia '001'."""
        t, s, n = split_doc_id("F", "F001", "0457996")
        assert t == "F"
        assert s == "001"
        assert n == "0457996"

    def test_serie_con_prefijo_duplicado_FF(self):
        """Serie 'FF001' (caso edge: ERP duplica tipo) -> '001'."""
        t, s, n = split_doc_id("F", "FF001", "0457996")
        assert t == "F"
        assert s == "001"
        assert n == "0457996"

    def test_nro_con_guion(self):
        """Nro 'F-0457996' con tipo 'F' -> '0457996'."""
        t, s, n = split_doc_id("F", "001", "F-0457996")
        assert t == "F"
        assert s == "001"
        assert n == "0457996"

    def test_tipo_lowercase_normalizado(self):
        """'nc' tipo lowercase -> 'N' mayuscula."""
        t, s, n = split_doc_id("nc", "001", "100")
        assert t == "N"
        assert s == "001"
        assert n == "100"

    def test_tipo_none_default_F(self):
        """Si tipo es None/empty, default a 'F'."""
        t, s, n = split_doc_id(None, "001", "100")
        assert t == "F"
        assert s == "001"
        assert n == "100"

    def test_serie_y_nro_vacios(self):
        """Todos vacios -> ('F', '', '') - solo tipo default."""
        t, s, n = split_doc_id("", "", "")
        assert t == "F"
        assert s == ""
        assert n == ""

    def test_valores_nan_string(self):
        """'nan' como string se trata como vacio."""
        t, s, n = split_doc_id("nan", "nan", "nan")
        assert t == "F"
        assert s == ""
        assert n == ""

    def test_solo_nro(self):
        """Sin serie, solo nro -> ('F', '', '0457996')."""
        t, s, n = split_doc_id("F", "", "0457996")
        assert t == "F"
        assert s == ""
        assert n == "0457996"

    def test_tipo_con_caracter_extra(self):
        """Tipo 'FAC' se trunca a 'F'."""
        t, s, n = split_doc_id("FAC", "001", "100")
        assert t == "F"
        assert s == "001"
        assert n == "100"

    def test_nro_con_doble_guion_solo_primero(self):
        """Nro '001-100-A' se queda con '100-A' (split solo la primera vez)."""
        t, s, n = split_doc_id("F", "001", "001-100-A")
        assert t == "F"
        # split('-', 1) deja solo la primera division: '100-A'
        assert s == "001"
        assert n == "100-A"

    def test_ceros_iniciales_preservados(self):
        """'011019' / '0457996' - los ceros a la izquierda se preservan."""
        t, s, n = split_doc_id("F", "001", "0457996")
        assert n == "0457996"


class TestSplitDocIdSeries:
    """Verifica split_doc_id() con pd.Series (vectorizado)."""

    def test_serie_basica(self):
        """Aplica split_doc_id a columnas de un DataFrame."""
        df = pd.DataFrame(
            {
                "TIPO_DOC": ["F", "F", "N"],
                "SERIE": ["001", "F002", "001"],
                "NUMERO": ["100", "200", "300"],
            }
        )
        t, s, n = split_doc_id(df["TIPO_DOC"], df["SERIE"], df["NUMERO"])
        assert list(t) == ["F", "F", "N"]
        assert list(s) == ["001", "002", "001"]
        assert list(n) == ["100", "200", "300"]

    def test_serie_con_nulos(self):
        """None y 'nan' en la serie se tratan como vacio."""
        df = pd.DataFrame(
            {
                "TIPO_DOC": [None, "F", "nan"],
                "SERIE": ["001", None, "001"],
                "NUMERO": ["100", "200", None],
            }
        )
        t, s, n = split_doc_id(df["TIPO_DOC"], df["SERIE"], df["NUMERO"])
        assert list(t) == ["F", "F", "F"]  # None/nan -> default 'F'
        assert list(s) == ["001", "", "001"]
        assert list(n) == ["100", "200", ""]

    def test_series_broadcasting(self):
        """Un escalar puede broadcastearse a una serie."""
        df = pd.DataFrame(
            {
                "SERIE": ["001", "002", "003"],
                "NUMERO": ["100", "200", "300"],
            }
        )
        t, s, n = split_doc_id("F", df["SERIE"], df["NUMERO"])
        assert list(t) == ["F", "F", "F"]
        assert list(s) == ["001", "002", "003"]
        assert list(n) == ["100", "200", "300"]

    def test_dtypes_se_mantienen(self):
        """Las Series retornadas siguen siendo Series."""
        t, s, n = split_doc_id(
            pd.Series(["F", "N"]), pd.Series(["001", "002"]), pd.Series(["100", "200"])
        )
        assert isinstance(t, pd.Series)
        assert isinstance(s, pd.Series)
        assert isinstance(n, pd.Series)


class TestBuildDocFull:
    """Verifica build_doc_full() que reconstruye el DOC_ID."""

    def test_tipo_serie_nro(self):
        """El caso canonico: F + 001 + 0457996 -> F001-0457996."""
        out = build_doc_full("F", "001", "0457996")
        assert out == "F001-0457996"

    def test_solo_tipo_y_serie(self):
        """Sin nro: F + 001 -> F001."""
        out = build_doc_full("F", "001", "")
        assert out == "F001"

    def test_solo_tipo_y_nro(self):
        """Sin serie: F + '' + 0457996 -> F-0457996."""
        out = build_doc_full("F", "", "0457996")
        assert out == "F-0457996"

    def test_solo_tipo(self):
        """Solo tipo: F + '' + '' -> F."""
        out = build_doc_full("F", "", "")
        assert out == "F"

    def test_tipo_none_default_F(self):
        """Tipo None -> 'F' default."""
        out = build_doc_full(None, "", "")
        assert out == "F"

    def test_tipo_normalizado_a_upper(self):
        """Tipo lowercase -> uppercase y truncado a 1 letra."""
        out = build_doc_full("nc", "001", "100")
        assert out == "N001-100"


class TestCasosReales:
    """Casos basados en datos reales del ERP."""

    def test_factura_peruana_completa(self):
        """F + 001 + 0457996 -> F001-0457996."""
        t, s, n = split_doc_id("F", "001", "0457996")
        assert build_doc_full(t, s, n) == "F001-0457996"

    def test_nota_credito_con_prefijo(self):
        """NC + 001 + 100 -> N001-100."""
        t, s, n = split_doc_id("NC", "001", "100")
        assert build_doc_full(t, s, n) == "N001-100"

    def test_caso_raro_F001_extrano(self):
        """Serie 'F001' + tipo 'F' + nro '100' -> F001-100."""
        t, s, n = split_doc_id("F", "F001", "100")
        assert build_doc_full(t, s, n) == "F001-100"

    def test_serie_negativa_o_guion_inicial(self):
        """Serie '-001' -> trimada a '001'."""
        t, s, n = split_doc_id("F", "-001", "100")
        assert s == "001"

    def test_idempotencia_split(self):
        """split_doc_id es idempotente: split(split(x)) == split(x)."""
        original = ("F", "F001", "100")
        t1, s1, n1 = split_doc_id(*original)
        t2, s2, n2 = split_doc_id(t1, s1, n1)
        assert (t1, s1, n1) == (t2, s2, n2)
