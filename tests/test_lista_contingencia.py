"""Lector de contingencia: plantilla primero, posicional solo si hace falta.

La plantilla oficial es el camino normal. Esto cubre archivos de proveedor
que no la cumplen (caso real: VINIFAN 2027): sin encabezados, con filas de
categoria entre los datos, filas vacias, y repartidos en varias hojas con
distinto layout. La regla verificada fila por fila dice que una fila es dato
si el precio es numerico y el codigo no esta vacio.
"""

from pathlib import Path

import pandas as pd
import pytest

from src.core.lista_contingencia import (
    _parece_lista,
    leer_contingencia,
    leer_lista_precios,
)
from src.core.utils import read_erp_file


def _xlsx_proveedor(p: Path) -> Path:
    """Replica el formato del caso real: 2 hojas, sin headers, con huecos."""
    f = p / "proveedor.xlsx"
    with pd.ExcelWriter(f, engine="openpyxl") as w:
        # Hoja 1: sin encabezados, categoria + vacias entre datos.
        pd.DataFrame(
            [
                ["FORROS VINIFAN", None, None, None],
                ["02217", 10.25, 0.25, 0.05],
                ["02216", 5.12, 0.25, 0.05],
                [None, None, None, None],
                ["02213", 9.14, 0.25, 0.05],
                [None, None, None, None],
            ]
        ).to_excel(w, sheet_name="VINIFAN", header=False, index=False)
        # Hoja 2: CON encabezados en la fila 5 (como BLISTERIA).
        pd.DataFrame(
            [
                ["LISTA DE PRECIOS", None, None, None],
                [None, None, None, None],
                ["N ITEM", "CODIGO", "PRECIO DE LISTA", "DESCUENTO 01"],
                ["BLISTERIA X", None, None, None],
                [1, "79103", 1.98, 0.25],
                [2, "79104", 2.50, 0.10],
            ]
        ).to_excel(w, sheet_name="BLISTERIA", header=False, index=False)
    return f


def _xlsx_plantilla(p: Path) -> Path:
    f = p / "plantilla.xlsx"
    pd.DataFrame(
        [
            {"CODIGO_SKU": "A1", "PRECIO_BASE": 4.0},
            {"CODIGO_SKU": "A2", "PRECIO_BASE": 5.0},
        ]
    ).to_excel(f, index=False)
    return f


class TestDespachador:
    def test_la_plantilla_no_pasa_por_contingencia(self, tmp_path):
        df, origen = leer_lista_precios(_xlsx_plantilla(tmp_path))
        assert origen == "plantilla"
        assert len(df) == 2

    def test_el_proveedor_usa_contingencia(self, tmp_path):
        df, origen = leer_lista_precios(_xlsx_proveedor(tmp_path))
        assert origen == "contingencia"
        # 3 de VINIFAN + 2 de BLISTERIA, sin categorias ni vacias.
        assert len(df) == 5
        assert set(df["CODIGO_SKU"].astype(str)) == {"02217", "02216", "02213", "79103", "79104"}

    def test_parece_lista_rechaza_columnas_basura(self, tmp_path):
        """El caso de hoy: 'FORROS VINIFAN' como nombre de columna."""
        crudo = read_erp_file(_xlsx_proveedor(tmp_path))
        assert list(crudo.columns)[0] == "FORROS VINIFAN"
        assert _parece_lista(crudo) is False

    def test_parece_lista_acepta_la_plantilla(self, tmp_path):
        assert _parece_lista(read_erp_file(_xlsx_plantilla(tmp_path))) is True


class TestContingencia:
    def test_concatena_las_hojas(self, tmp_path):
        df = leer_contingencia(_xlsx_proveedor(tmp_path))
        assert len(df) == 5

    def test_mapea_por_posicion_sin_headers(self, tmp_path):
        df = leer_contingencia(_xlsx_proveedor(tmp_path))
        fila = df[df["CODIGO_SKU"].astype(str) == "02217"].iloc[0]
        assert float(fila["PRECIO_BASE"]) == 10.25
        assert float(fila["DESC1"]) == 0.25
        assert float(fila["DESC2"]) == 0.05

    def test_detecta_headers_donde_los_hay(self, tmp_path):
        df = leer_contingencia(_xlsx_proveedor(tmp_path))
        fila = df[df["CODIGO_SKU"].astype(str) == "79103"].iloc[0]
        assert float(fila["PRECIO_BASE"]) == 1.98
        assert float(fila["DESC1"]) == 0.25

    def test_sin_sku_utiles_devuelve_vacio(self, tmp_path):
        f = tmp_path / "vacio.xlsx"
        pd.DataFrame([["SOLO", "TEXTO"], ["CATEGORIA", None]]).to_excel(
            f, header=False, index=False
        )
        assert leer_contingencia(f).empty

    def test_archivo_inexistente_levanta(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            leer_contingencia(tmp_path / "noexiste.xlsx")


class TestPuntaAPunta:
    def test_la_contingencia_entra_al_motor(self, tmp_path):
        """El DataFrame canonico tiene que servirle a `_procesar_comparar`."""
        from src.strategies.price_difference import PriceDifferenceStrategy

        df_lista, origen = leer_lista_precios(_xlsx_proveedor(tmp_path))
        assert origen == "contingencia"
        lista = df_lista.rename(columns={"CODIGO_SKU": "SKU"})

        hist = pd.DataFrame(
            {
                "CODIGO": ["02217", "79103", "ZZZ"],
                "ARTICULO": ["A", "B", "C"],
                "CANTIDAD": [10, 100, 5],
                "SOLES": [100.0, 180.0, 50.0],
                "FECHA": pd.to_datetime(["2026-01-15"] * 3),
                "TIPO_DOC": ["F01"] * 3,
                "SERIE": ["201"] * 3,
                "NUMERO": ["1", "2", "3"],
                "COD_CLIENTE": ["C1"] * 3,
                "DOC_CLIENTE": ["R1"] * 3,
                "CLIENTE": ["CLI"] * 3,
            }
        )
        df_res, _, _ = PriceDifferenceStrategy()._procesar_comparar(
            hist, lista, [], config={"modalidad": "individual", "sort_mode": "fecha_desc"}
        )
        assert sorted(df_res["SKU"].astype(str).unique()) == ["02217", "79103"]
        assert (df_res["MONTO_NC"].astype(float) > 0).any()
