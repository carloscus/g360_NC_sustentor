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


class TestSkuDuplicadoEnLista:
    """Un SKU dos veces en la lista multiplicaba las filas del historial.

    2 lineas x 2 precios = 4 filas con los montos duplicados. La lista es una
    lookup (un SKU, un precio): se queda el primero y se avisa para corregir
    el archivo. Caso real: NC 215-31630 de un solo SKU en 4 filas.
    """

    def _lista_dup(self):
        import pandas as pd

        return pd.DataFrame(
            [
                {"SKU": "X", "PRECIO_BASE": 9.0},
                {"SKU": "X", "PRECIO_BASE": 9.0},
            ]
        )

    def _hist_2(self):
        import pandas as pd

        return pd.DataFrame(
            {
                "CODIGO": ["X", "X"],
                "ARTICULO": ["P", "P"],
                "CANTIDAD": [10, 20],
                "SOLES": [100.0, 200.0],
                "FECHA": pd.to_datetime(["2026-01-15"] * 2),
                "TIPO_DOC": ["F01"] * 2,
                "SERIE": ["201"] * 2,
                "NUMERO": ["1", "2"],
                "COD_CLIENTE": ["C1"] * 2,
                "DOC_CLIENTE": ["R1"] * 2,
                "CLIENTE": ["CLI"] * 2,
            }
        )

    def test_no_duplica_filas(self):
        from src.strategies.price_difference import PriceDifferenceStrategy

        st = PriceDifferenceStrategy()
        df, _, _ = st._procesar_comparar(
            self._hist_2(),
            self._lista_dup(),
            [],
            config={"modalidad": "individual", "sort_mode": "fecha_desc"},
        )
        assert len(df) == 2, "2 lineas x 1 precio = 2 filas, no 4"
        assert sorted(df["CANTIDAD"].tolist()) == [10, 20]

    def test_avisa_el_duplicado(self):
        from src.strategies.price_difference import PriceDifferenceStrategy

        st = PriceDifferenceStrategy()
        alertas: list = []
        st._procesar_comparar(
            self._hist_2(),
            self._lista_dup(),
            alertas,
            config={"modalidad": "individual", "sort_mode": "fecha_desc"},
        )
        textos = " ".join(getattr(a, "mensaje", "") for a in alertas).lower()
        assert "repetido" in textos and "x" in textos

    def test_sin_duplicados_no_hay_falsa_alarma(self):
        import pandas as pd

        from src.strategies.price_difference import PriceDifferenceStrategy

        st = PriceDifferenceStrategy()
        alertas: list = []
        lista = pd.DataFrame([{"SKU": "X", "PRECIO_BASE": 9.0}])
        st._procesar_comparar(
            self._hist_2(),
            lista,
            alertas,
            config={"modalidad": "individual", "sort_mode": "fecha_desc"},
        )
        textos = " ".join(getattr(a, "mensaje", "") for a in alertas).lower()
        assert "repetido" not in textos


class TestFilaVaciaEnFeriaYDescuento:
    """La misma higiene de DC aplica a FPE y al motor legacy de descuento."""

    def _hist_con_vacia(self):
        hist = pd.DataFrame(
            {
                "CODIGO": ["S1", "S2"],
                "ARTICULO": ["P1", "P2"],
                "LINEA": ["L01", "L02"],
                "CANTIDAD": [50.0, 40.0],
                "SOLES": [170.0, 160.0],
                "FECHA": pd.to_datetime(["2026-01-05", "2026-01-08"]),
                "TIPO_DOC": ["F", "F"],
                "SERIE": ["F201", "F201"],
                "NUMERO": ["100", "100"],
                "DOC_ID": ["FF201-100", "FF201-100"],
                "COD_CLIENTE": ["1", "1"],
                "DOC_CLIENTE": ["206039928"] * 2,
                "TIPO_CLASE": ["factura", "factura"],
                "FACTURA_REF": ["", ""],
                "CANTIDAD_FAE": [0.0, 0.0],
                "TIPO_NOTA": ["", ""],
                "CATEGORIA": ["", ""],
            }
        )
        vacia = pd.DataFrame([{c: np.nan for c in hist.columns} | {"FECHA": pd.NaT}])
        return pd.concat([hist, vacia], ignore_index=True)

    def test_feria_avisa_filas_sin_sku(self, tmp_path):
        from src.domain import ExpedienteComercial, PipelineContext
        from src.strategies.feria_preventa import FeriaPreventaStrategy

        req = tmp_path / "req.xlsx"
        pd.DataFrame(
            {"CODIGO": ["S1", "S2"], "CANTIDAD_NC": [10, 10], "PORCENTAJE_DESC": [0.05, 0.05]}
        ).to_excel(req, index=False)
        cfg = {
            "ruta_requerimientos": [str(req)],
            "modalidad": "individual",
            "sort_mode": "fecha_desc",
            "forzar_cantidad": True,
            "documentos_historial": {
                "facturas": {"mostrar": True, "usar": True},
                "nc": {"mostrar": True, "usar": False},
                "ndb": {"mostrar": True, "usar": False},
            },
        }
        exp = ExpedienteComercial(
            nombre="FPE",
            familia="precio",
            estrategia="FeriaPreventa",
            datos=self._hist_con_vacia(),
            contexto=PipelineContext(config=cfg),
        )
        res = FeriaPreventaStrategy().process(exp)
        textos = " ".join(a.mensaje.lower() for a in res.alertas)
        assert "sin sku" in textos

    def test_descuento_avisa_filas_sin_sku(self):
        from src.domain import ExpedienteComercial, PipelineContext
        from src.strategies.descuento_factura import DescuentoFacturaStrategy

        df = pd.DataFrame(
            {
                "CODIGO": ["S1", np.nan, "S2"],
                "CANTIDAD": [10.0, 5.0, 20.0],
                "PRECIO_UNITARIO": [34.0, 10.0, 8.0],
                "FECHA": pd.to_datetime(["2026-01-05"] * 3),
                "TIPO_DOC": ["F01"] * 3,
                "SERIE": ["201"] * 3,
                "NUMERO": ["100"] * 3,
                "CLIENTE": ["CLI"] * 3,
            }
        )
        exp = ExpedienteComercial(
            nombre="DF",
            familia="precio",
            estrategia="DescuentoFactura",
            datos=df,
            contexto=PipelineContext(config={"descuento_pct": 0.05}),
        )
        res = DescuentoFacturaStrategy().process(exp)
        textos = " ".join(a.mensaje.lower() for a in res.alertas)
        assert "sin sku" in textos
        # La fila vacia no entra al calculo: 2 SKUs x su descuento.
        assert len(res.get_excel()) == 2

    def test_descuento_sin_vacias_no_avisa(self):
        from src.domain import ExpedienteComercial, PipelineContext
        from src.strategies.descuento_factura import DescuentoFacturaStrategy

        df = pd.DataFrame(
            {
                "CODIGO": ["S1", "S2"],
                "CANTIDAD": [10.0, 20.0],
                "PRECIO_UNITARIO": [34.0, 8.0],
                "FECHA": pd.to_datetime(["2026-01-05"] * 2),
                "TIPO_DOC": ["F01"] * 2,
                "SERIE": ["201"] * 2,
                "NUMERO": ["100"] * 2,
                "CLIENTE": ["CLI"] * 2,
            }
        )
        exp = ExpedienteComercial(
            nombre="DF",
            familia="precio",
            estrategia="DescuentoFactura",
            datos=df,
            contexto=PipelineContext(config={"descuento_pct": 0.05}),
        )
        res = DescuentoFacturaStrategy().process(exp)
        textos = " ".join(a.mensaje.lower() for a in res.alertas)
        assert "sin sku" not in textos
