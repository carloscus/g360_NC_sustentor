"""Tests para DataDictionary.get_id_name_pairs() + unique_* atajos.

Centran la logica de extraccion de pares (id, nombre) que se repetia en:
    - reconocimiento_view.py:_cargar_clientes_dropdown_sf/_ci (2 copias literales)
    - consolidated_view.py
    - main.py
"""

import pandas as pd
import pytest

from src.core.data_dictionary import DataDictionary


@pytest.fixture
def df_clientes():
    return pd.DataFrame(
        {
            "COD_CLIENTE": ["C01", "C02", "C03", "C01"],
            "CLIENTE": ["EMPRESA A", "EMPRESA B", "EMPRESA C", "EMPRESA A"],
            "OTRO": [1, 2, 3, 4],
        }
    )


@pytest.fixture
def df_skus():
    return pd.DataFrame(
        {
            "CODIGO": ["011019", "11019", "A001"],
            "ARTICULO": ["PROD X", "PROD Y", "PROD Z"],
        }
    )


@pytest.fixture
def df_solo_nombres():
    """ERP sin id_field, solo nombre."""
    return pd.DataFrame({"CLIENTE": ["A", "B", "C"]})


@pytest.fixture
def df_solo_ids():
    """ERP solo con CODIGO, sin ARTICULO."""
    return pd.DataFrame({"CODIGO": ["011019", "11019"]})


@pytest.fixture
def df_con_nulos():
    """Datos con vacios y NaN que deben filtrarse."""
    return pd.DataFrame(
        {
            "COD_CLIENTE": ["C01", "", None, "C03", "C01"],
            "CLIENTE": ["EMPRESA A", "", "EMPRESA X", None, "EMPRESA A"],
        }
    )


class TestGetIdNamePairs:
    def test_basic_tuplas(self, df_clientes):
        pairs = DataDictionary.get_id_name_pairs(
            df_clientes, id_field="COD_CLIENTE", name_field="CLIENTE"
        )
        assert isinstance(pairs, list)
        assert all(isinstance(p, tuple) and len(p) == 2 for p in pairs)
        # 3 unicos (C01, C02, C03) deduplication aplicada
        assert len(pairs) == 3

    def test_orden_alfabetico_por_nombre(self, df_clientes):
        pairs = DataDictionary.get_id_name_pairs(
            df_clientes, id_field="COD_CLIENTE", name_field="CLIENTE"
        )
        nombres = [n for _, n in pairs]
        # orden case-insensitive: EMPRESA A, B, C
        assert nombres == ["EMPRESA A", "EMPRESA B", "EMPRESA C"]

    def test_sin_orden(self, df_clientes):
        pairs = DataDictionary.get_id_name_pairs(
            df_clientes,
            id_field="COD_CLIENTE",
            name_field="CLIENTE",
            sort_by_name=False,
        )
        # El primer registro del df persiste
        assert pairs[0] == ("C01", "EMPRESA A")

    def test_ceros_iniciales_preservados(self, df_skus):
        """010819 y 10819 se mantienen como dos pares distintos."""
        pairs = DataDictionary.get_id_name_pairs(df_skus, id_field="CODIGO", name_field="ARTICULO")
        ids = [i for i, _ in pairs]
        assert "011019" in ids
        assert "11019" in ids
        assert len(pairs) == 3

    def test_solo_nombre(self, df_solo_nombres):
        """Sin id_field, devuelve (None, name)."""
        pairs = DataDictionary.get_id_name_pairs(
            df_solo_nombres, id_field="COD_CLIENTE", name_field="CLIENTE"
        )
        assert all(i is None for i, _ in pairs)
        assert sorted(n for _, n in pairs) == ["A", "B", "C"]

    def test_solo_ids(self, df_solo_ids):
        """Sin name_field, devuelve (id, id)."""
        pairs = DataDictionary.get_id_name_pairs(
            df_solo_ids, id_field="CODIGO", name_field="ARTICULO"
        )
        assert pairs == [("011019", "011019"), ("11019", "11019")]

    def test_dataframe_vacio(self):
        pairs = DataDictionary.get_id_name_pairs(pd.DataFrame(), id_field="X", name_field="Y")
        assert pairs == []

    def test_columnas_no_existentes(self, df_clientes):
        pairs = DataDictionary.get_id_name_pairs(
            df_clientes, id_field="NO_EXISTE_ID", name_field="NO_EXISTE_NAME"
        )
        assert pairs == []

    def test_nulos_filtrados(self, df_con_nulos):
        """NaN/None en id o name se filtran; validos se mantienen."""
        pairs = DataDictionary.get_id_name_pairs(
            df_con_nulos, id_field="COD_CLIENTE", name_field="CLIENTE"
        )
        actual = set(pairs)
        # Solo sobrevivio C01+EMPRESA A (la unica fila con ambos validos antes de dedup)
        assert actual == {("C01", "EMPRESA A")}

    def test_dict_en_lugar_de_tupla(self, df_clientes):
        """return_tuples=False -> devuelve lista de dicts."""
        result = DataDictionary.get_id_name_pairs(
            df_clientes,
            id_field="COD_CLIENTE",
            name_field="CLIENTE",
            return_tuples=False,
        )
        assert all(isinstance(x, dict) for x in result)
        assert all({"id", "name"} <= x.keys() for x in result)
        nombres = {x["name"] for x in result}
        assert "EMPRESA A" in nombres


class TestAtajos:
    def test_unique_clients(self, df_clientes):
        pairs = DataDictionary.unique_clients(df_clientes)
        assert len(pairs) == 3
        assert all(isinstance(p, tuple) for p in pairs)

    def test_unique_skus(self, df_skus):
        pairs = DataDictionary.unique_skus(df_skus)
        assert ("011019", "PROD X") in pairs
        assert ("11019", "PROD Y") in pairs

    def test_unique_vendors(self):
        df = pd.DataFrame(
            {
                "COD_VENDEDOR": ["V01", "V02"],
                "VENDEDOR": ["JUAN", "PEDRO"],
            }
        )
        pairs = DataDictionary.unique_vendors(df)
        assert pairs == [("V01", "JUAN"), ("V02", "PEDRO")]

    def test_unique_branches(self):
        df = pd.DataFrame(
            {
                "COD_SUCURSAL": ["S01", "S02"],
                "SUCURSAL": ["LIMA", "CALLAO"],
            }
        )
        pairs = DataDictionary.unique_branches(df)
        assert pairs == [("S02", "CALLAO"), ("S01", "LIMA")] or pairs == [
            ("S01", "LIMA"),
            ("S02", "CALLAO"),
        ]


class TestInferenciaLabel:
    """Confirma que la inferencia de label para format_id_name funciona."""

    def test_inferencia_cliente(self):
        from src.core.utils import _infer_label_field

        assert _infer_label_field("COD_CLIENTE") == "CLIENTE"

    def test_inferencia_sku(self):
        from src.core.utils import _infer_label_field

        assert _infer_label_field("CODIGO") == "SKU"

    def test_inferencia_vendedor(self):
        from src.core.utils import _infer_label_field

        assert _infer_label_field("COD_VENDEDOR") == "VENDEDOR"

    def test_inferencia_sucursal(self):
        from src.core.utils import _infer_label_field

        assert _infer_label_field("COD_SUCURSAL") == "SUCURSAL"

    def test_inferencia_unknown_retorna_id_field(self):
        from src.core.utils import _infer_label_field

        assert _infer_label_field("OTRO_CAMPO") == "OTRO_CAMPO"
