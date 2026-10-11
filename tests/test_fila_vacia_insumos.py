"""Filas vacias en el XLSX de insumos: no son dato y no deben matchear.

Una fila en blanco llega como NaN. Si SOLO la lista la trae, el inner merge
la ignora y no pasa nada visible. Pero si el historial TAMBIEN trae una, el
merge las casa (NaN con NaN normalizado al mismo valor) y nace una fila
fantasma en el reconocimiento con todo NaN, que llega hasta el Excel.

La respuesta es sacarlas antes del merge - de las DOS tablas - y avisarlo
con una alerta informativa.
"""

import numpy as np
import pandas as pd

from src.strategies.price_difference import PriceDifferenceStrategy


def _hist(extra_vacia=False):
    filas = {
        "CODIGO": ["S1", "S1", "S2", "S3"],
        "ARTICULO": ["Prod 1", "Prod 1", "Prod 2", "Prod 3"],
        "CANTIDAD": [100, 200, 50, 10],
        "SOLES": [340.00, 700.00, 125.00, 20.00],
        "FECHA": pd.to_datetime(["2026-01-15", "2026-02-20", "2026-01-15", "2026-02-20"]),
        "TIPO_DOC": ["F01"] * 4,
        "SERIE": ["201"] * 4,
        "NUMERO": ["100", "101", "100", "101"],
        "COD_CLIENTE": ["00068426"] * 4,
        "DOC_CLIENTE": ["206039928"] * 4,
        "CLIENTE": ["DISTRIBUIDORA LOS ANGELES"] * 4,
    }
    df = pd.DataFrame(filas)
    if extra_vacia:
        vacia = pd.DataFrame([{c: np.nan for c in df.columns} | {"FECHA": pd.NaT}])
        df = pd.concat([df, vacia], ignore_index=True)
    return df


def _lista(extra_vacia=True):
    filas = [
        {"SKU": "S1", "PRECIO_BASE": 3.00},
        {"SKU": "S2", "PRECIO_BASE": 2.00},
        {"SKU": "S3", "PRECIO_BASE": 2.00},
    ]
    if extra_vacia:
        # Entre S1 y S2, como queda un Enter pisado en el Excel.
        filas.insert(1, {"SKU": np.nan, "PRECIO_BASE": np.nan})
    return pd.DataFrame(filas)


def _comparar(hist, lista):
    st = PriceDifferenceStrategy()
    alertas: list = []
    df_res, _, _ = st._procesar_comparar(
        hist, lista, alertas, config={"modalidad": "individual", "sort_mode": "fecha_desc"}
    )
    return df_res, alertas


def _sin_sku(df):
    if df.empty:
        return df
    llave = df["SKU"].astype(str).str.strip().str.lower()
    return df[llave.isin(["", "nan", "none"])]


class TestFilaVaciaEnLista:
    def test_el_hueco_no_aparece_en_el_resultado(self):
        df_res, _ = _comparar(_hist(), _lista(extra_vacia=True))
        assert len(_sin_sku(df_res)) == 0
        assert sorted(df_res["SKU"].astype(str).unique()) == ["S1", "S2", "S3"]

    def test_el_hueco_no_cambia_los_montos(self):
        con_hueco, _ = _comparar(_hist(), _lista(extra_vacia=True))
        sin_hueco, _ = _comparar(_hist(), _lista(extra_vacia=False))
        assert con_hueco.reset_index(drop=True).equals(sin_hueco.reset_index(drop=True))

    def test_se_avisa_que_se_ignoro(self):
        _, alertas = _comparar(_hist(), _lista(extra_vacia=True))
        textos = " ".join(getattr(a, "mensaje", "") for a in alertas).lower()
        assert "sin sku" in textos or "fila" in textos, f"deberia avisar: {textos}"

    def test_sin_hueco_no_hay_falsa_alarma(self):
        _, alertas = _comparar(_hist(), _lista(extra_vacia=False))
        textos = " ".join(getattr(a, "mensaje", "") for a in alertas).lower()
        assert "sin sku" not in textos


class TestFilaVaciaEnAmbos:
    def test_vacio_no_matchea_vacio(self):
        """El bug: NaN con NaN casaba y nacia una fila fantasma con todo NaN."""
        df_res, _ = _comparar(_hist(extra_vacia=True), _lista(extra_vacia=True))
        assert len(_sin_sku(df_res)) == 0, "no debe haber fila fantasma"
        assert sorted(df_res["SKU"].astype(str).unique()) == ["S1", "S2", "S3"]

    def test_el_resultado_es_identico_sin_los_huecos(self):
        """Mismos SKU y mismos montos: el hueco no cambia el reconocimiento.

        Se comparan columnas de negocio, no dtypes: la fila NaN fuerza la
        columna CANTIDAD a float, y eso es un artefacto de pandas, no una
        diferencia en el resultado.
        """
        con_huecos, _ = _comparar(_hist(extra_vacia=True), _lista(extra_vacia=True))
        limpios, _ = _comparar(_hist(), _lista(extra_vacia=False))
        cols = ["SKU", "MONTO_NC"]
        a = con_huecos[cols].sort_values("SKU").reset_index(drop=True)
        b = limpios[cols].sort_values("SKU").reset_index(drop=True)
        assert a["SKU"].tolist() == b["SKU"].tolist()
        assert a["MONTO_NC"].astype(float).tolist() == b["MONTO_NC"].astype(float).tolist()

    def test_avisa_por_los_dos_lados(self):
        _, alertas = _comparar(_hist(extra_vacia=True), _lista(extra_vacia=True))
        textos = " ".join(getattr(a, "mensaje", "") for a in alertas).lower()
        assert "historial" in textos and "lista de precios" in textos

    def test_una_lista_toda_vacia_no_revienta(self):
        vacia = pd.DataFrame([{"SKU": np.nan, "PRECIO_BASE": np.nan}])
        df_res, alertas = _comparar(_hist(), vacia)
        # Todo es hueco: no hay con que mergear. No debe reventar ni fantasmear.
        assert len(_sin_sku(df_res)) == 0
class TestSubtituloEnPlantilla:
    """Pegar datos con subtitulos dentro de la plantilla no detiene el proceso.

    Una fila de categoria ("FORROS VINIFAN" con precio NaN) tiene SKU no
    vacio, asi que el filtro de huecos no la saca. Pero sin PRECIO_BASE no
    puede entrar al merge con sentido: si el historial trae el mismo texto,
    casan y nace una fila con MONTO_NC NaN.
    """

    def _plantilla(self):
        # Mismos SKU que `_hist()` (S1/S2), mas un hueco y un subtitulo.
        return pd.DataFrame(
            [
                {"SKU": "S1", "PRECIO_BASE": 3.00},
                {"SKU": np.nan, "PRECIO_BASE": np.nan},
                {"SKU": "FORROS VINIFAN", "PRECIO_BASE": np.nan},
                {"SKU": "S2", "PRECIO_BASE": 2.00},
            ]
        )

    def _hist_con_subtitulo(self):
        df = _hist()
        extra = pd.DataFrame(
            [
                {
                    "CODIGO": "FORROS VINIFAN",
                    "ARTICULO": "X",
                    "CANTIDAD": 5,
                    "SOLES": 50.0,
                    "FECHA": pd.Timestamp("2026-01-15"),
                    "TIPO_DOC": "F01",
                    "SERIE": "201",
                    "NUMERO": "9",
                    "COD_CLIENTE": "C1",
                    "DOC_CLIENTE": "R1",
                    "CLIENTE": "CLI",
                }
            ]
        )
        return pd.concat([df, extra], ignore_index=True)

    def test_el_subtitulo_no_entra(self):
        df_res, _ = _comparar(self._hist_con_subtitulo(), self._plantilla())
        assert "FORROS VINIFAN" not in df_res["SKU"].astype(str).tolist()
        assert sorted(df_res["SKU"].astype(str).unique()) == ["S1", "S2"]

    def test_sin_monto_nan_en_el_resultado(self):
        df_res, _ = _comparar(self._hist_con_subtitulo(), self._plantilla())
        assert df_res["MONTO_NC"].notna().all(), "ninguna fila puede quedar con monto NaN"

    def test_se_avisa_del_subtitulo(self):
        _, alertas = _comparar(self._hist_con_subtitulo(), self._plantilla())
        textos = " ".join(getattr(a, "mensaje", "") for a in alertas).lower()
        assert "sin precio" in textos

