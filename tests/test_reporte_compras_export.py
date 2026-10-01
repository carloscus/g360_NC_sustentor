"""Reporte de compras: paquete de 5 hojas (Resumen, Consolidado, Ajustes,
BD, Facturas con Detallado)."""

import re
from datetime import date

import pandas as pd
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.utils.cell import column_index_from_string, coordinate_to_tuple

from src.core import ventas_db
from src.core.ventas_db_client import VentasDbClient
from src.ui import reporte_compras as rc


def _filas():
    base = dict(
        id_articulo="014850",
        original_sku="014850",
        nom_articulo="PELOTA PVC #5",
        id_linea="0101",
        nom_linea="GASEOSAS",
        id_grupo="01",
        nom_grupo="G",
        id_tipo="01",
        nom_tipo="T",
        id_familia="01",
        nom_familia="F",
        id_cliente="00056101",
        doc_cliente="20601024714",
        nom_cliente="MULTICOPIAS MARY E.I.R.L.",
        tpo_doc="F01",
        serie_doc="204",
        nro_doc="67375",
        referencia="",
        moneda="Soles",
        cantidad=800.0,
        cantidad_fae=0.0,
        soles=896.0,
        dolares=0.0,
        precio_unitario=1.12,
        anho=2026,
        mes=9,
        fecha_orig="2026-09-05",
        fecha_ref=None,
        fecha_venc=None,
        cod_sucursal="01",
        nom_sucursal="LIMA",
        departamento="LIMA",
        provincia="LIMA",
        distrito="SAN ISIDRO",
        id_vendedor="01188",
        nom_vendedor="VEND",
        id_pedido="P1",
        file_source="t",
        mes_ref="2026-09",
        tipo_operacion="",
        factura_ref_serie="",
        factura_ref_nro="",
        folio_unico="",
    )
    f_oct = dict(
        base,
        serie_doc="204",
        nro_doc="67380",
        cantidad=100.0,
        soles=120.0,
        fecha_orig="2026-10-02",
        mes_ref="2026-10",
        cod_sucursal="02",
        nom_sucursal="AREQUIPA",
        id_pedido="P2",
    )
    dev = dict(
        base,
        tpo_doc="NCR",
        serie_doc="N204",
        nro_doc="900010",
        referencia="F01/204-67375",
        cantidad=-200.0,
        soles=-224.0,
        fecha_orig="2026-09-10",
        folio_unico="",
    )
    aju = dict(
        base,
        tpo_doc="NCR",
        serie_doc="N204",
        nro_doc="900011",
        referencia="F01/204-67375",
        cantidad=0.0,
        cantidad_fae=800.0,
        soles=-96.0,
        fecha_orig="2026-09-12",
        folio_unico="",
    )
    return base, f_oct, dev, aju


def _poblar(tmp_db):
    from src.core.xls_processor import derivar_campos

    rows = []
    for v in _filas():
        d = dict(v)
        derivar_campos(d)
        rows.append(d)
    conn = tmp_db.get_conn()
    ventas_db.insert_ventas(conn, rows)
    ventas_db.populate_nc_asociadas(conn)
    return rows


class TestFetchCompras:
    def test_soles_con_y_sin_nc(self, tmp_db):
        _poblar(tmp_db)
        cli = VentasDbClient()
        neto = cli.fetch_compras_cliente("00056101", incluir_nc=True)
        bruto = cli.fetch_compras_cliente("00056101", incluir_nc=False)
        assert round(float(neto["SOLES"].sum()), 2) == 896.0 + 120.0 - 224.0 - 96.0
        assert round(float(bruto["SOLES"].sum()), 2) == 896.0 + 120.0

    def test_unidades_con_y_sin_devoluciones(self, tmp_db):
        _poblar(tmp_db)
        cli = VentasDbClient()
        neto = cli.fetch_compras_cliente("00056101", incluir_nc=True)
        sin_dev = cli.fetch_compras_cliente("00056101", incluir_nc=True, excluir_devoluciones=True)
        assert float(neto["CANTIDAD"].sum()) == 800.0 + 100.0 - 200.0
        assert float(sin_dev["CANTIDAD"].sum()) == 800.0 + 100.0


class TestConsolidadoFetch:
    KW = dict(fecha_desde="2026-09-01", fecha_hasta="2026-10-31")

    def test_lineas_componentes(self, tmp_db):
        _poblar(tmp_db)
        df = VentasDbClient().fetch_lineas_resumen_cliente("00056101", **self.KW)
        assert list(df.columns) == [
            "COD_LINEA",
            "LINEA",
            "BRUTA",
            "DEV_S",
            "DESC_S",
            "NDB_S",
            "SOLES",
            "CANTIDAD",
            "N_DOCS",
            "N_SKUS",
        ]
        assert len(df) == 1
        r = df.iloc[0]
        assert r["COD_LINEA"] == "01"  # canónico (sufijo de '0101')
        assert (r["BRUTA"], r["DEV_S"], r["DESC_S"], r["NDB_S"], r["SOLES"]) == (
            1016.0,
            -224.0,
            -96.0,
            0.0,
            696.0,
        )
        assert r["CANTIDAD"] == 700.0
        assert (r["N_DOCS"], r["N_SKUS"]) == (4, 1)

    def test_skus_resumen(self, tmp_db):
        _poblar(tmp_db)
        df = VentasDbClient().fetch_skus_resumen_cliente("00056101", **self.KW)
        assert list(df.columns) == [
            "COD_SKU",
            "SKU",
            "COD_LINEA",
            "LINEA",
            "SOLES",
            "CANTIDAD",
            "N_DOCS",
        ]
        assert len(df) == 1
        r = df.iloc[0]
        assert r["COD_SKU"] == "014850"  # ceros a la izquierda intactos
        assert (r["SOLES"], r["CANTIDAD"], r["N_DOCS"]) == (696.0, 700.0, 4)

    def test_toggle_off_componentes_en_cero(self, tmp_db):
        _poblar(tmp_db)
        cli = VentasDbClient()
        lin = cli.fetch_lineas_resumen_cliente("00056101", incluir_nc=False, **self.KW)
        assert (lin["BRUTA"].iloc[0], lin["SOLES"].iloc[0]) == (1016.0, 1016.0)
        assert (lin["DEV_S"].iloc[0], lin["DESC_S"].iloc[0], lin["NDB_S"].iloc[0]) == (
            0.0,
            0.0,
            0.0,
        )
        hist = cli.fetch_historico_mensual("00056101", incluir_nc=False, **self.KW)
        assert round(float(hist["BRUTA"].sum()), 2) == 1016.0
        assert round(float(hist["NETA"].sum()), 2) == 1016.0
        assert (hist["DEV_S"].sum(), hist["DESC_S"].sum(), hist["NDB_S"].sum()) == (0.0, 0.0, 0.0)
        assert (hist["NC"].sum(), hist["NDB_DOCS"].sum()) == (0, 0)

    def test_sin_datos_vacio_con_columnas(self, tmp_db):
        cli = VentasDbClient()
        kw = dict(fecha_desde="2020-01-01", fecha_hasta="2020-01-31")
        assert list(cli.fetch_lineas_resumen_cliente("00000000", **kw).columns)[:3] == [
            "COD_LINEA",
            "LINEA",
            "BRUTA",
        ]
        assert list(cli.fetch_skus_resumen_cliente("00000000", **kw).columns)[:3] == [
            "COD_SKU",
            "SKU",
            "COD_LINEA",
        ]


class TestBDFetch:
    KW = dict(fecha_desde="2026-09-01", fecha_hasta="2026-10-31")

    def test_columnas_y_orden(self, tmp_db):
        _poblar(tmp_db)
        df = VentasDbClient().fetch_compras_lineas_cliente("00056101", **self.KW)
        assert list(df.columns) == [
            "MES_REF",
            "FECHA",
            "TPO_DOC",
            "DOC",
            "REF",
            "COD_LINEA",
            "LINEA",
            "SUCURSAL",
            "COD_SKU",
            "ARTICULO",
            "CANTIDAD",
            "PU",
            "SOLES",
            "OPERACION",
            "PEDIDO",
            "FAE",
            "CANT_DEV",
            "BRUTO",
            "AJUSTE",
        ]
        # fixture: factura sep, NC devolución, NC descuento, factura oct
        assert list(df["FECHA"]) == ["2026-09-05", "2026-09-10", "2026-09-12", "2026-10-02"]
        assert list(df["DOC"]) == ["F204-67375", "NN204-900010", "NN204-900011", "F204-67380"]
        assert list(df["PEDIDO"]) == ["P1", "P1", "P1", "P2"]

    def test_analiticas(self, tmp_db):
        _poblar(tmp_db)
        df = VentasDbClient().fetch_compras_lineas_cliente("00056101", **self.KW)
        fac = df[df["DOC"] == "F204-67375"].iloc[0]
        assert fac["CANT_DEV"] == 200.0  # devuelta contra esa línea
        assert (fac["BRUTO"], fac["AJUSTE"]) == (896.0, 0.0)
        dev = df[df["OPERACION"] == "devolucion"].iloc[0]
        assert dev["CANT_DEV"] == 0.0  # las notas no devuelven
        assert (dev["BRUTO"], dev["AJUSTE"]) == (0.0, -224.0)
        aju = df[df["OPERACION"] == "ajuste_valor"].iloc[0]
        assert aju["FAE"] == 800.0
        assert (aju["BRUTO"], aju["AJUSTE"]) == (0.0, -96.0)
        # Bruto + Ajuste = neto por fila
        assert ((df["BRUTO"] + df["AJUSTE"]).round(2) == df["SOLES"].round(2)).all()

    def test_sin_datos_retorna_columnas(self, tmp_db):
        df = VentasDbClient().fetch_compras_lineas_cliente(
            "00000000", fecha_desde="2020-01-01", fecha_hasta="2020-01-31"
        )
        assert df.empty and list(df.columns)[:4] == ["MES_REF", "FECHA", "TPO_DOC", "DOC"]

    def test_respeta_allowlist(self, tmp_db):
        TestLineasValidadas()._poblar(tmp_db)
        cli = VentasDbClient()
        con = cli.fetch_compras_lineas_cliente("00056101", solo_lineas_activas=True, **self.KW)
        assert "F204-67390" not in set(con["DOC"])  # línea inválida
        assert "F204-67375" in set(con["DOC"])  # línea válida

    def test_guarda_fisica_por_fila(self, tmp_db):
        """El descuento legacy con cantidad +175 no mueve unidades."""
        TestMetricasFisicasEconomicas()._poblar(tmp_db)
        df = VentasDbClient().fetch_compras_lineas_cliente("00056101")
        desc = df[df["OPERACION"] == "ajuste_valor"]
        assert len(desc) == 1
        assert float(desc["CANTIDAD"].iloc[0]) == 0.0  # +175 legado
        assert float(desc["SOLES"].iloc[0]) == -20.0
        assert (float(desc["BRUTO"].iloc[0]), float(desc["AJUSTE"].iloc[0])) == (0.0, -20.0)
        ndb = df[df["OPERACION"] == "nota_debito"]
        assert float(ndb["CANTIDAD"].iloc[0]) == 0.0  # +3 del NDB
        assert float(ndb["SOLES"].iloc[0]) == 30.0
        assert float(df["CANTIDAD"].sum()) == 90.0  # 100 − 10
        assert round(float(df["SOLES"].sum()), 2) == 910.0


class TestNotasSku:
    def _poblar_notas(self, tmp_db):
        from src.core.xls_processor import derivar_campos

        base = dict(
            id_articulo="014850",
            original_sku="014850",
            nom_articulo="PELOTA PVC #5",
            id_linea="0101",
            nom_linea="GASEOSAS",
            id_grupo="01",
            nom_grupo="G",
            id_tipo="01",
            nom_tipo="T",
            id_familia="01",
            nom_familia="F",
            id_cliente="00056101",
            doc_cliente="20601024714",
            nom_cliente="MULTICOPIAS MARY E.I.R.L.",
            tpo_doc="F01",
            serie_doc="204",
            nro_doc="67375",
            referencia="",
            moneda="Soles",
            cantidad=800.0,
            cantidad_fae=0.0,
            soles=896.0,
            dolares=0.0,
            precio_unitario=1.12,
            anho=2026,
            mes=9,
            fecha_orig="2026-09-05",
            fecha_ref=None,
            fecha_venc=None,
            cod_sucursal="01",
            nom_sucursal="LIMA",
            departamento="LIMA",
            provincia="LIMA",
            distrito="SAN ISIDRO",
            id_vendedor="01188",
            nom_vendedor="VEND",
            id_pedido="P1",
            file_source="t",
            mes_ref="2026-09",
            tipo_operacion="",
            factura_ref_serie="",
            factura_ref_nro="",
            folio_unico="",
        )
        otro_sku = dict(
            base, id_articulo="014851", nom_articulo="PELOTA PVC BOY", cantidad=100.0, soles=120.0
        )
        dev = dict(
            base,
            tpo_doc="NCR",
            serie_doc="N204",
            nro_doc="900010",
            referencia="F01/204-67375",
            cantidad=-200.0,
            cantidad_fae=-200.0,
            soles=-224.0,
            fecha_orig="2026-09-10",
            folio_unico="",
        )
        aju = dict(
            base,
            tpo_doc="NCR",
            serie_doc="N204",
            nro_doc="900011",
            referencia="F01/204-67375",
            cantidad=0.0,
            cantidad_fae=-12.0,
            soles=-96.0,
            fecha_orig="2026-09-12",
            folio_unico="",
        )
        ndb = dict(
            base,
            tpo_doc="NDB",
            serie_doc="N204",
            nro_doc="900012",
            referencia="F01/204-67375",
            cantidad=0.0,
            cantidad_fae=2.0,
            soles=50.0,
            fecha_orig="2026-09-15",
            folio_unico="",
        )
        flag = dict(
            base,
            tpo_doc="NCR",
            serie_doc="N204",
            nro_doc="900013",
            referencia="F01/204-67375",
            cantidad=0.0,
            cantidad_fae=-1.0,
            soles=-31.35,
            precio_unitario=31.35,
            fecha_orig="2026-09-18",
            folio_unico="",
        )
        zero = dict(
            base,
            tpo_doc="NCR",
            serie_doc="N204",
            nro_doc="900014",
            referencia="F01/204-67375",
            cantidad=0.0,
            cantidad_fae=0.0,
            soles=-10.0,
            fecha_orig="2026-09-19",
            folio_unico="",
        )
        # SKU que la factura no trae (flag "SKU no facturado" + sin base).
        sku_faltante = dict(
            base,
            id_articulo="014899",
            nom_articulo="INEXISTENTE",
            tpo_doc="NCR",
            serie_doc="N204",
            nro_doc="900015",
            referencia="F01/204-67375",
            cantidad=0.0,
            cantidad_fae=0.0,
            soles=-10.0,
            fecha_orig="2026-09-20",
            folio_unico="",
        )
        # Descuento parcial: FAE (100) != cant. fact. (800).
        aju_parcial = dict(
            base,
            tpo_doc="NCR",
            serie_doc="N204",
            nro_doc="900016",
            referencia="F01/204-67375",
            cantidad=0.0,
            cantidad_fae=100.0,
            soles=-50.0,
            fecha_orig="2026-09-21",
            folio_unico="",
        )
        # Huérfanas: sin referencia y con factura inexistente.
        huerf_sin_ref = dict(
            base,
            tpo_doc="NCR",
            serie_doc="N204",
            nro_doc="900017",
            referencia="",
            cantidad=0.0,
            cantidad_fae=0.0,
            soles=-30.0,
            fecha_orig="2026-09-22",
            folio_unico="",
        )
        huerf_fantasma = dict(
            base,
            tpo_doc="NCR",
            serie_doc="N204",
            nro_doc="900018",
            referencia="F01/204-99999",
            cantidad=0.0,
            cantidad_fae=0.0,
            soles=-40.0,
            fecha_orig="2026-09-23",
            folio_unico="",
        )
        rows = []
        for v in (
            base,
            otro_sku,
            dev,
            aju,
            ndb,
            flag,
            zero,
            sku_faltante,
            aju_parcial,
            huerf_sin_ref,
            huerf_fantasma,
        ):
            d = dict(v)
            derivar_campos(d)
            rows.append(d)
        conn = tmp_db.get_conn()
        ventas_db.insert_ventas(conn, rows)
        ventas_db.populate_nc_asociadas(conn)
        return rows

    def test_fetch_notas_por_tipo(self, tmp_db):
        self._poblar_notas(tmp_db)
        df = VentasDbClient().fetch_notas_sku_cliente("00056101")
        # Bloque principal: 7 filas (las 2 huérfanas van aparte).
        assert len(df) == 7
        assert set(df["NC"]) == {
            "N204-900010",
            "N204-900011",
            "N204-900012",
            "N204-900013",
            "N204-900014",
            "N204-900015",
            "N204-900016",
        }
        dev = df[df["TIPO"] == "devolucion"].iloc[0]
        assert dev["FACTURA"] == "F204-67375"
        assert dev["CANT_FACT"] == 800.0
        assert dev["F_FACT"] == "2026-09-05"
        assert dev["CANT_NC"] == 200.0
        assert dev["SOLES_NC"] == -224.0  # signo nativo
        assert dev["SOLES_FACT"] == 896.0
        assert dev["PU_NC"] == 1.12  # precio real de la NC
        assert dev["PU_FACT"] == 1.12  # 896/800
        assert dev["FAE"] == 200.0  # en dev replica cantidad
        assert dev["SALDO"] == 600.0  # una sola vez por grupo
        desc = df[(df["TIPO"] == "ajuste_valor") & (df["NC"] == "N204-900011")].iloc[0]
        assert desc["SOLES_NC"] == -96.0
        assert desc["FAE"] == 12.0  # base FAE real
        flag = df[df["NC"] == "N204-900013"].iloc[0]
        assert flag["FAE"] == 1.0  # flag −1: base no desagregada
        assert flag["SOLES_NC"] == -31.35
        zero = df[df["NC"] == "N204-900014"].iloc[0]
        assert zero["FAE"] == 0.0
        ndb = df[df["TIPO"] == "nota_debito"].iloc[0]
        assert ndb["SOLES_NC"] == 50.0  # NDB en positivo
        assert ndb["FAE"] == 2.0
        # SKU no facturado: cantidades en cero y SOLES_FACT None, pero
        # F_FACT a nivel factura (la fecha existe aunque falte el SKU).
        falt = df[df["NC"] == "N204-900015"].iloc[0]
        assert falt["CANT_FACT"] == 0.0
        assert falt["F_FACT"] == "2026-09-05"
        assert falt["SOLES_FACT"] is None
        parcial = df[df["NC"] == "N204-900016"].iloc[0]
        assert parcial["FAE"] == 100.0  # != cant. fact. (800)
        # Saldo solo en la primera fila de cada grupo.
        assert df["SALDO"].dropna().tolist() == [600.0, 0.0]

    def test_particion_principal_huerfanas(self, tmp_db):
        self._poblar_notas(tmp_db)
        cli = VentasDbClient()
        kw = dict(fecha_desde="2026-09-01", fecha_hasta="2026-09-30")
        main = cli.fetch_notas_sku_cliente("00056101", **kw)
        huerf = cli.fetch_notas_huerfanas_cliente("00056101", **kw)
        assert len(huerf) == 2
        assert set(main["NC"]).isdisjoint(set(huerf["NC"]))
        assert set(huerf["NC"]) == {"N204-900017", "N204-900018"}
        sin_ref = huerf[huerf["NC"] == "N204-900017"].iloc[0]
        assert sin_ref["FACTURA"] == ""
        fantasma = huerf[huerf["NC"] == "N204-900018"].iloc[0]
        assert fantasma["FACTURA"] == "F204-99999"
        assert fantasma["SOLES_NC"] == -40.0

    def test_sin_notas_vacio(self, tmp_db):
        cli = VentasDbClient()
        assert cli.fetch_notas_sku_cliente("00056101").empty
        cols = list(cli.fetch_notas_huerfanas_cliente("00056101").columns)
        assert cols == [
            "FACTURA",
            "NC",
            "FECHA_DOC",
            "TIPO",
            "SKU",
            "ARTICULO",
            "CANT_NC",
            "SOLES_NC",
            "FAE",
        ]

    def test_saldo_historico_y_negativo(self, tmp_db):
        """Devolución pre-rango reduce el saldo (físico, sin fecha)."""
        from src.core.xls_processor import derivar_campos

        # insert_ventas es delete-then-insert por mes: la previa va antes.
        previa = dict(
            id_articulo="014850",
            original_sku="014850",
            nom_articulo="PELOTA PVC #5",
            id_linea="0101",
            nom_linea="GASEOSAS",
            id_grupo="01",
            nom_grupo="G",
            id_tipo="01",
            nom_tipo="T",
            id_familia="01",
            nom_familia="F",
            id_cliente="00056101",
            doc_cliente="20601024714",
            nom_cliente="MULTICOPIAS MARY E.I.R.L.",
            tpo_doc="NCR",
            serie_doc="N204",
            nro_doc="900001",
            referencia="F01/204-67375",
            moneda="Soles",
            cantidad=-900.0,
            cantidad_fae=-900.0,
            soles=-1008.0,
            dolares=0.0,
            precio_unitario=1.12,
            anho=2026,
            mes=8,
            fecha_orig="2026-08-15",
            fecha_ref=None,
            fecha_venc=None,
            cod_sucursal="01",
            nom_sucursal="LIMA",
            departamento="LIMA",
            provincia="LIMA",
            distrito="SAN ISIDRO",
            id_vendedor="01188",
            nom_vendedor="VEND",
            id_pedido="P1",
            file_source="t",
            mes_ref="2026-08",
            tipo_operacion="",
            factura_ref_serie="",
            factura_ref_nro="",
            folio_unico="",
        )
        derivar_campos(previa)
        ventas_db.insert_ventas(tmp_db.get_conn(), [previa])
        self._poblar_notas(tmp_db)
        df = VentasDbClient().fetch_notas_sku_cliente(
            "00056101", fecha_desde="2026-09-01", fecha_hasta="2026-09-30"
        )
        # La previa no entra al detalle (fuera de rango) pero sí al saldo.
        assert "N204-900001" not in set(df["NC"])
        saldos = df["SALDO"].dropna().tolist()
        assert saldos[0] == -300.0  # 800 − (200 en rango + 900 previa)
        assert 0.0 in saldos  # grupo sin devoluciones intacto

    def test_toggle_off_vacia_ambos(self, tmp_db):
        self._poblar_notas(tmp_db)
        cli = VentasDbClient()
        kw = dict(fecha_desde="2026-09-01", fecha_hasta="2026-09-30", incluir_nc=False)
        assert cli.fetch_notas_sku_cliente("00056101", **kw).empty
        assert cli.fetch_notas_huerfanas_cliente("00056101", **kw).empty


class TestDetalladoFetch:
    KW = dict(fecha_desde="2026-09-01", fecha_hasta="2026-10-31")

    def test_bruto_por_doc_sku(self, tmp_db):
        _poblar(tmp_db)
        df = VentasDbClient().fetch_facturas_sku_detalle_cliente("00056101", **self.KW)
        assert list(df.columns) == [
            "FECHA",
            "DOC",
            "PEDIDO",
            "OC",
            "SUCURSAL",
            "COD_LINEA",
            "LINEA",
            "SKU",
            "ARTICULO",
            "CANT",
            "BRUTO",
            "NC",
        ]
        assert len(df) == 2
        f1 = df[df["DOC"] == "F204-67375"].iloc[0]
        assert f1["FECHA"] == "2026-09-05"
        assert f1["PEDIDO"] == "P1"
        assert f1["SUCURSAL"] == "LIMA"
        assert (f1["CANT"], f1["BRUTO"]) == (800.0, 896.0)
        assert f1["SKU"] == "014850"
        # NC asociadas históricas en orden cronológico.
        assert f1["NC"] == ["N204-900010", "N204-900011"]
        f2 = df[df["DOC"] == "F204-67380"].iloc[0]
        assert f2["NC"] == []
        # Solo mundo bruto: las NC no aparecen como filas.
        assert set(df["DOC"]) == {"F204-67375", "F204-67380"}

    def test_doc_bruto_negativo(self, tmp_db):
        TestDescuentoEmbebido()._poblar(tmp_db)
        df = VentasDbClient().fetch_facturas_sku_detalle_cliente(
            "00056101", fecha_desde="2026-09-01", fecha_hasta="2026-09-30"
        )
        assert len(df) == 1
        assert df["BRUTO"].iloc[0] == -50.0  # neto negativo en la hoja

    def test_paridad_df(self, tmp_db):
        _poblar(tmp_db)
        cli = VentasDbClient()
        det = cli.fetch_facturas_sku_detalle_cliente("00056101", **self.KW)
        hist = cli.fetch_historico_mensual("00056101", **self.KW)
        dev = cli.fetch_notas_sku_cliente("00056101", **self.KW)
        assert round(float(det["BRUTO"].sum()), 2) == round(float(hist["BRUTA"].sum()), 2)
        neto = float(det["BRUTO"].sum()) + float(dev["SOLES_NC"].sum())
        assert round(neto, 2) == round(float(hist["NETA"].sum()), 2)

    def test_respeta_allowlist(self, tmp_db):
        TestLineasValidadas()._poblar(tmp_db)
        cli = VentasDbClient()
        kw = dict(fecha_desde="2026-09-01", fecha_hasta="2026-09-30")
        docs = set(
            cli.fetch_facturas_sku_detalle_cliente("00056101", solo_lineas_activas=True, **kw)[
                "DOC"
            ]
        )
        assert "F204-67390" not in docs
        assert "F204-67375" in docs


class TestComparativoFetch:
    KW = dict(fecha_desde="2026-09-01", fecha_hasta="2026-10-31")

    def test_mes_x_sku_columnas_y_guarda(self, tmp_db):
        _poblar(tmp_db)
        df = VentasDbClient().fetch_compras_sku_mes_cliente("00056101", **self.KW)
        assert list(df.columns) == [
            "MES_REF",
            "COD_SKU",
            "SKU",
            "COD_LINEA",
            "LINEA",
            "SOLES",
            "CANTIDAD",
        ]
        sep = df[df["MES_REF"] == "2026-09"].iloc[0]
        assert sep["COD_SKU"] == "014850"
        assert sep["COD_LINEA"] == "01"  # canónico sin ruido
        assert sep["CANTIDAD"] == 600.0  # 800 − 200 (guarda física)
        assert sep["SOLES"] == 576.0  # 896 − 224 − 96
        oct_ = df[df["MES_REF"] == "2026-10"].iloc[0]
        assert (oct_["CANTIDAD"], oct_["SOLES"]) == (100.0, 120.0)

    def test_toggle_off_bruta(self, tmp_db):
        _poblar(tmp_db)
        df = VentasDbClient().fetch_compras_sku_mes_cliente("00056101", incluir_nc=False, **self.KW)
        sep = df[df["MES_REF"] == "2026-09"].iloc[0]
        assert (sep["CANTIDAD"], sep["SOLES"]) == (800.0, 896.0)

    def test_fd_comparativo_retrocede_24m_al_enero(self):
        # Siempre arranca el 1 de enero del año (corte - 2) para mostrar
        # los 3 años completos (el más reciente parcialmente).
        assert rc._fd_comparativo("2026-09-01") == "2024-01-01"
        assert rc._fd_comparativo("2026-09-15") == "2024-01-01"
        assert rc._fd_comparativo("2026-01-01") == "2024-01-01"
        assert rc._fd_comparativo("2025-03-15") == "2023-01-01"
        assert rc._fd_comparativo("") == ""
        assert rc._fd_comparativo(None) == ""

    def test_meses_rango_y_parciales(self):
        assert rc._meses_rango("2026-06-01", "2026-09-26") == {"06", "07", "08", "09"}
        # Rango que cruza año.
        assert rc._meses_rango("2025-11-01", "2026-02-28") == {"11", "12", "01", "02"}
        assert rc._meses_rango("", "") == set()
        # Parciales: corte a mitad de mes (inicio o fin).
        assert rc._meses_parciales("2026-06-01", "2026-09-26") == {"09"}
        assert rc._meses_parciales("2026-06-15", "2026-09-30") == {"06"}
        assert rc._meses_parciales("2026-06-01", "2026-09-30") == set()
        assert rc._meses_parciales("", "") == set()


class TestComparativoHoja:
    """Bloques (mes × línea) y (mes × SKU) con dif/% vs año previo."""

    @staticmethod
    def _df_comp():
        return pd.DataFrame(
            [
                {
                    "MES_REF": "2025-06",
                    "COD_LINEA": "01",
                    "LINEA": "PELOTAS",
                    "SOLES": 1000.0,
                    "CANTIDAD": 90.0,
                },
                {
                    "MES_REF": "2026-06",
                    "COD_LINEA": "01",
                    "LINEA": "PELOTAS",
                    "SOLES": 1200.0,
                    "CANTIDAD": 100.0,
                },
                {
                    "MES_REF": "2026-06",
                    "COD_LINEA": "78",
                    "LINEA": "ARCHIVO",
                    "SOLES": 500.0,
                    "CANTIDAD": 40.0,
                },
            ]
        )

    @staticmethod
    def _df_sku():
        return pd.DataFrame(
            [
                {
                    "MES_REF": "2026-06",
                    "COD_SKU": "014850",
                    "SKU": "PELOTA PVC",
                    "COD_LINEA": "01",
                    "LINEA": "PELOTAS",
                    "SOLES": 1200.0,
                    "CANTIDAD": 100.0,
                },
                {
                    "MES_REF": "2025-06",
                    "COD_SKU": "014850",
                    "SKU": "PELOTA PVC",
                    "COD_LINEA": "01",
                    "LINEA": "PELOTAS",
                    "SOLES": 1000.0,
                    "CANTIDAD": 90.0,
                },
            ]
        )

    def _wb(self, tmp_path, df_sku=None):
        out = tmp_path / "C.xlsx"
        rc._escribir_xlsx(
            out,
            "56101",
            "M",
            df_comp_sku=self._df_sku() if df_sku is None else df_sku,
            hojas={"comparativo"},
        )
        return load_workbook(str(out))["Comparativo"]

    def test_geometria_grano_fusionado(self, tmp_path):
        ws = self._wb(tmp_path)
        # 2 años → 14 cols (5 identidad + 4 valores + 2 dif/% + Obs + 2 tend).
        assert [c.value for c in ws[2]] == [
            "Mes",
            "Código línea",
            "Línea",
            "Código SKU",
            "SKU",
            "Unid 2026",
            "Soles 2026",
            "dif 26-25",
            "% 26-25",
            "Unid 2025",
            "Soles 2025",
            "Obs.",
            "Tend. Soles",
            "Tend. Precio",
        ]
        # Una sola fila consolidada: mismo SKU en 2025 y 2026.
        f1 = [c.value for c in ws[3]]
        assert f1[0] == "06-2026"
        assert f1[1] == "01"
        assert f1[2] == "PELOTAS"
        assert f1[3] == "014850"
        assert f1[4] == "PELOTA PVC"
        assert f1[5:7] == [100.0, 1200.0]  # datos 2026
        assert f1[9:11] == [90.0, 1000.0]  # datos 2025
        assert f1[7] == '=IF(K3="","—",G3-K3)'  # dif fórmula con guarda
        # TOTAL COMPARABLE en fila 4, TOTAL DEL RANGO en fila 5.
        tot1 = [c.value for c in ws[4]]
        assert tot1[0] == "TOTAL COMPARABLE"
        tot2 = [c.value for c in ws[5]]
        assert tot2[0] == "TOTAL DEL RANGO"

    def test_una_tabla_y_cf(self, tmp_path):
        ws = self._wb(tmp_path)
        assert list(ws.tables) == ["ComparativoSKUs"]
        cfs = _cfs(ws)
        assert ("H3:H5", "lessThan") in cfs  # dif negativa en rojo
        assert ("I3:I5", "lessThan") in cfs  # % negativo en rojo
        assert ws.auto_filter.ref is None
        assert ws.freeze_panes is None

    def test_un_anio_sin_dif(self, tmp_db, tmp_path):
        """Rango de un solo año: sin columnas dif/%."""
        TestXlsx5Hojas()._todo(tmp_db, tmp_path)
        ws = load_workbook(str(tmp_path / "Compras.xlsx"))["Comparativo"]
        assert [c.value for c in ws[2]] == [
            "Mes",
            "Código línea",
            "Línea",
            "Código SKU",
            "SKU",
            "Unid 2026",
            "Soles 2026",
            "Obs.",
            "Tend. Soles",
            "Tend. Precio",
        ]
        assert ws.max_column == 10

    def test_mes_parcial_marcado(self, tmp_path):
        """Mes incompleto del rango: etiqueta '(parcial)' en la fila."""
        out = tmp_path / "P.xlsx"
        rc._escribir_xlsx(
            out,
            "56101",
            "M",
            df_comp_sku=pd.DataFrame(
                [
                    {
                        "MES_REF": "2025-09",
                        "COD_SKU": "014850",
                        "SKU": "PELOTA PVC",
                        "COD_LINEA": "01",
                        "LINEA": "PELOTAS",
                        "SOLES": 100.0,
                        "CANTIDAD": 9.0,
                    },
                    {
                        "MES_REF": "2026-09",
                        "COD_SKU": "014850",
                        "SKU": "PELOTA PVC",
                        "COD_LINEA": "01",
                        "LINEA": "PELOTAS",
                        "SOLES": 120.0,
                        "CANTIDAD": 10.0,
                    },
                    {
                        "MES_REF": "2026-08",
                        "COD_SKU": "014850",
                        "SKU": "PELOTA PVC",
                        "COD_LINEA": "01",
                        "LINEA": "PELOTAS",
                        "SOLES": 300.0,
                        "CANTIDAD": 30.0,
                    },
                    {
                        "MES_REF": "2025-08",
                        "COD_SKU": "014850",
                        "SKU": "PELOTA PVC",
                        "COD_LINEA": "01",
                        "LINEA": "PELOTAS",
                        "SOLES": 250.0,
                        "CANTIDAD": 25.0,
                    },
                ]
            ),
            rango_desde="2026-08-01",
            rango_hasta="2026-09-26",
            corte_hasta="2026-09-26",
            hojas={"comparativo"},
        )
        ws = load_workbook(str(out))["Comparativo"]
        etiquetas = []
        for r in range(3, ws.max_row + 1):
            v = ws.cell(row=r, column=1).value
            if v in ("TOTAL COMPARABLE", "TOTAL DEL RANGO"):
                break
            if v is not None:
                etiquetas.append(v)
        # Orden: mes ascendente; cada SKU consolida sus años en una sola fila.
        # Sept-2026 marcado (parcial) porque el rango corta el día 26.
        assert etiquetas == ["08-2026", "09-2026 (parcial)"]

    def test_paridad_neto_vs_bd(self, tmp_db, tmp_path):
        """Columna Soles del año = neto de BD (misma ventana)."""
        wb = TestXlsx5Hojas()._todo(tmp_db, tmp_path)
        ws = wb["Comparativo"]
        soles = 0.0
        for r in range(3, ws.max_row + 1):
            if ws.cell(row=r, column=1).value in ("TOTAL COMPARABLE", "TOTAL DEL RANGO", None):
                break
            v = ws.cell(row=r, column=7).value
            if isinstance(v, (int, float)):
                soles += v
        bd = wb["BD_Registro"]
        rt = next(r for r in range(1, bd.max_row + 1) if bd.cell(row=r, column=1).value == "TOTAL")
        assert round(soles, 2) == round(
            sum(bd.cell(row=r, column=12).value for r in range(2, rt)), 2
        )

    def test_sin_datos_no_escribe_libro(self, tmp_path):
        import pytest

        out = tmp_path / "V.xlsx"
        with pytest.raises(ValueError, match="sin datos"):
            rc._escribir_xlsx(out, "56101", "M", hojas={"comparativo"})

    def test_dos_totales_con_rango(self, tmp_path):
        """Rango con fd/fh: dos filas TOTAL y etiquetas MM-AAAA por año real."""
        out = tmp_path / "R.xlsx"
        rc._escribir_xlsx(
            out,
            "56101",
            "M",
            df_comp_sku=pd.DataFrame(
                [
                    {
                        "MES_REF": "2025-08",
                        "COD_SKU": "014850",
                        "SKU": "PELOTA PVC",
                        "COD_LINEA": "01",
                        "LINEA": "PELOTAS",
                        "SOLES": 200.0,
                        "CANTIDAD": 20.0,
                    },
                    {
                        "MES_REF": "2025-09",
                        "COD_SKU": "014850",
                        "SKU": "PELOTA PVC",
                        "COD_LINEA": "01",
                        "LINEA": "PELOTAS",
                        "SOLES": 300.0,
                        "CANTIDAD": 30.0,
                    },
                    {
                        "MES_REF": "2026-01",
                        "COD_SKU": "014850",
                        "SKU": "PELOTA PVC",
                        "COD_LINEA": "01",
                        "LINEA": "PELOTAS",
                        "SOLES": 150.0,
                        "CANTIDAD": 15.0,
                    },
                    {
                        "MES_REF": "2026-09",
                        "COD_SKU": "014850",
                        "SKU": "PELOTA PVC",
                        "COD_LINEA": "01",
                        "LINEA": "PELOTAS",
                        "SOLES": 400.0,
                        "CANTIDAD": 40.0,
                    },
                    {
                        "MES_REF": "2024-01",
                        "COD_SKU": "014850",
                        "SKU": "PELOTA PVC",
                        "COD_LINEA": "01",
                        "LINEA": "PELOTAS",
                        "SOLES": 100.0,
                        "CANTIDAD": 10.0,
                    },
                    {
                        "MES_REF": "2024-09",
                        "COD_SKU": "014850",
                        "SKU": "PELOTA PVC",
                        "COD_LINEA": "01",
                        "LINEA": "PELOTAS",
                        "SOLES": 180.0,
                        "CANTIDAD": 18.0,
                    },
                ]
            ),
            rango_desde="2025-08-01",
            rango_hasta="2026-09-30",
            corte_hasta="2026-09-30",
            hojas={"comparativo"},
        )
        ws = load_workbook(str(out))["Comparativo"]
        # Encabezado fila 2 (banner removido).
        assert [c.value for c in ws[2]] == [
            "Mes",
            "Código línea",
            "Línea",
            "Código SKU",
            "SKU",
            "Unid 2026",
            "Soles 2026",
            "dif 26-25",
            "% 26-25",
            "Unid 2025",
            "Soles 2025",
            "dif 25-24",
            "% 25-24",
            "Unid 2024",
            "Soles 2024",
            "Obs.",
            "Tend. Soles",
            "Tend. Precio",
        ]
        # Filas ordenadas: mes ascendente, año ascendente, neto descendente.
        datos = []
        for r in range(3, ws.max_row + 1):
            v = ws.cell(row=r, column=1).value
            if v in ("TOTAL COMPARABLE", "TOTAL DEL RANGO"):
                break
            if v is not None:
                datos.append(v)
        assert datos == ["01-2026", "08-2026", "09-2026"]
        # Dos filas TOTAL después de los datos (filas 6 y 7).
        tot1 = [c.value for c in ws[6]]
        tot2 = [c.value for c in ws[7]]
        assert tot1[0] == "TOTAL COMPARABLE"
        assert tot2[0] == "TOTAL DEL RANGO"
        # COMPARABLE: meses presentes en los 3 años (01 y 09) → dif válida.
        assert tot1[7] is not None and tot1[7].startswith("=")
        assert tot1[8] is not None and tot1[8].startswith("=")
        # DEL RANGO: 2024 sin meses en rango → dif = "—".
        assert tot2[7] == "—"
        assert tot2[8] == "—"


class TestLibroValidoExcel:
    """Reglas estrictas de Excel que openpyxl NO valida al guardar.

    Un libro que las rompe abre con 'recuperar contenido' (columnas
    duplicadas en una Tabla con 3+ años fue el caso real). Esta suite
    corre sobre el paquete completo y sobre un comparativo de 3 años.
    """

    @staticmethod
    def _errores(wb):
        from openpyxl.utils import range_boundaries

        errores = []
        vistos = {}
        for ws in wb.worksheets:
            # Ojo: TableList.items() devuelve strs; el objeto va por [].
            for nombre in ws.tables:
                tab = ws.tables[nombre]
                if nombre in vistos:
                    errores.append(f"tabla duplicada: {nombre}")
                vistos[nombre] = ws.title
                cols = [c.name for c in tab.tableColumns]
                if any(not isinstance(c, str) for c in cols):
                    errores.append(f"{ws.title}.{nombre}: encabezado no texto")
                if len(set(cols)) != len(cols):
                    errores.append(f"{ws.title}.{nombre}: columnas duplicadas {cols}")
                try:
                    min_c, min_r, max_c, max_r = range_boundaries(tab.ref)
                except Exception:
                    errores.append(f"{ws.title}.{nombre}: ref inválida {tab.ref}")
                    continue
                if max_r - min_r < 1:
                    errores.append(f"{ws.title}.{nombre}: sin filas de datos")
                for mr in ws.merged_cells.ranges:
                    (a, b, c_, d) = range_boundaries(str(mr))
                    if not (c_ < min_c or a > max_c or d < min_r or b > max_r):
                        errores.append(f"{ws.title}: merge {mr} solapa tabla {nombre}")
            if ws.tables and ws.auto_filter.ref:
                errores.append(f"{ws.title}: autofilter + tablas")
        return errores

    def test_tres_anios_columnas_unicas(self, tmp_path):
        df = pd.DataFrame(
            [
                {
                    "MES_REF": f"{y}-06",
                    "COD_SKU": "014850",
                    "SKU": "PELOTA PVC",
                    "COD_LINEA": "01",
                    "LINEA": "PELOTAS",
                    "SOLES": 1000.0 + i * 100,
                    "CANTIDAD": 90.0,
                }
                for i, y in enumerate(("2024", "2025", "2026"))
            ]
        )
        out = tmp_path / "T.xlsx"
        rc._escribir_xlsx(out, "56101", "M", df_comp_sku=df, hojas={"comparativo"})
        wb = load_workbook(str(out))
        ws = wb["Comparativo"]
        hdr = [c.value for c in ws[2]]
        assert hdr == [
            "Mes",
            "Código línea",
            "Línea",
            "Código SKU",
            "SKU",
            "Unid 2026",
            "Soles 2026",
            "dif 26-25",
            "% 26-25",
            "Unid 2025",
            "Soles 2025",
            "dif 25-24",
            "% 25-24",
            "Unid 2024",
            "Soles 2024",
            "Obs.",
            "Tend. Soles",
            "Tend. Precio",
        ]
        assert len(set(hdr)) == len(hdr)
        assert self._errores(wb) == []

    def test_paquete_completo_valido(self, tmp_db, tmp_path):
        wb = TestXlsx5Hojas()._todo(tmp_db, tmp_path)
        assert self._errores(wb) == []

    def test_sku_multi_linea_se_para_con_flag(self, tmp_path):
        """SKU bajo 2 líneas en un mes → filas separadas + flag en Obs."""
        out = tmp_path / "ML.xlsx"
        rc._escribir_xlsx(
            out,
            "56101",
            "M",
            df_comp_sku=pd.DataFrame(
                [
                    {
                        "MES_REF": "2026-06",
                        "COD_SKU": "9999",
                        "SKU": "GENÉRICO",
                        "COD_LINEA": "01",
                        "LINEA": "PELOTAS",
                        "SOLES": 500.0,
                        "CANTIDAD": 50.0,
                    },
                    {
                        "MES_REF": "2026-06",
                        "COD_SKU": "9999",
                        "SKU": "GENÉRICO",
                        "COD_LINEA": "21",
                        "LINEA": "REP. INDUSTRIALES",
                        "SOLES": 300.0,
                        "CANTIDAD": 30.0,
                    },
                    {
                        "MES_REF": "2025-06",
                        "COD_SKU": "9999",
                        "SKU": "GENÉRICO",
                        "COD_LINEA": "01",
                        "LINEA": "PELOTAS",
                        "SOLES": 400.0,
                        "CANTIDAD": 40.0,
                    },
                ]
            ),
            corte_hasta="2026-06-30",
            hojas={"comparativo"},
        )
        wb = load_workbook(str(out))
        ws = wb["Comparativo"]
        # Header fila 2.
        hdr = [c.value for c in ws[2]]
        assert hdr[-3] == "Obs."
        assert hdr[-2:] == ["Tend. Soles", "Tend. Precio"]
        # 2 filas consolidadas: línea 01 y línea 21 (mismo SKU en 2 líneas).
        datos = []
        for r in range(3, ws.max_row + 1):
            v = ws.cell(row=r, column=1).value
            if v in ("TOTAL COMPARABLE", "TOTAL DEL RANGO", None):
                break
            if v is not None:
                datos.append(
                    (
                        ws.cell(row=r, column=1).value,
                        ws.cell(row=r, column=4).value,
                        ws.cell(row=r, column=ws.max_column - 2).value,
                    )
                )
        # 2 filas, SKU 9999 aparece en las 2.
        assert len(datos) == 2
        cods = {d[1] for d in datos}
        flags = {d[2] for d in datos if d[2]}
        assert "9999" in cods
        assert "SKU en 2+ líneas" in flags
        # La fila 2026-linea 21 también tiene fuera del allowlist.
        assert any("Fuera del allowlist" in f for f in flags)
        # Una fila con línea fuera del allowlist → flag adicional.
        assert "Fuera del allowlist" in flags or "SKU en 2+ líneas" in flags

    def test_linea_fuera_allowlist_muestra_flag(self, tmp_path):
        """Línea fuera del allowlist aparece con flag 'Fuera del allowlist'."""
        out = tmp_path / "AL.xlsx"
        rc._escribir_xlsx(
            out,
            "56101",
            "M",
            df_comp_sku=pd.DataFrame(
                [
                    {
                        "MES_REF": "2026-06",
                        "COD_SKU": "014850",
                        "SKU": "PELOTA",
                        "COD_LINEA": "50",
                        "LINEA": "MATERIAL AUX",
                        "SOLES": 100.0,
                        "CANTIDAD": 10.0,
                    },
                    {
                        "MES_REF": "2026-06",
                        "COD_SKU": "014851",
                        "SKU": "BOLETA",
                        "COD_LINEA": "01",
                        "LINEA": "PELOTAS",
                        "SOLES": 200.0,
                        "CANTIDAD": 20.0,
                    },
                ]
            ),
            corte_hasta="2026-06-30",
            hojas={"comparativo"},
        )
        wb = load_workbook(str(out))
        ws = wb["Comparativo"]
        # Obs. es la antepenúltima columna; luego vienen los dos indicadores.
        obs_col = ws.max_column - 2
        flags = []
        for r in range(3, ws.max_row + 1):
            v = ws.cell(row=r, column=1).value
            if v in ("TOTAL COMPARABLE", "TOTAL DEL RANGO", None):
                break
            obs = ws.cell(row=r, column=obs_col).value
            if obs:
                flags.append(obs)
        assert "Fuera del allowlist" in flags
        assert "PELOTAS" not in flags


class TestTendenciasFacturacionYPrecio:
    """Indicadores separados: facturación (Soles) y precio promedio unitario.

    Como Soles = volumen × precio, son ejes independientes. La variación de
    unidades se descartó como indicador propio porque su flecha resultaba
    redundante con la de Soles; el precio sí aporta dirección propia.
    """

    def test_flecha_y_umbral_de_5_por_ciento(self):
        t = rc._tendencia_texto
        assert t(110, 100) == "↑ +10%"  # > +5% → alza
        assert t(90, 100) == "↓ -10%"  # < -5% → baja
        assert t(105, 100) == "→ +5%"  # borde superior = estable
        assert t(95, 100) == "→ -5%"  # borde inferior = estable
        assert t(100, 100) == "→ +0%"  # sin cambio
        assert t(101, 100) == "→ +1%"  # dentro de la banda plana

    def test_sin_base_comparable_devuelve_vacio(self):
        t = rc._tendencia_texto
        assert t(None, 100) == ""  # sin dato del año actual
        assert t(100, None) == ""  # sin dato del año previo
        assert t(None, None) == ""
        assert t(0, 0) == ""  # base no positiva
        assert t(100, 0) == ""  # división por cero evitada
        assert t(100, -50) == ""  # previo negativo: no interpretable
        assert t(True, 100) == ""  # bool no es cantidad válida

    def test_precios_redondean_a_por_ciento_entero(self):
        assert rc._tendencia_texto(1234.5, 1000) == "↑ +23%"
        assert rc._tendencia_texto(1000, 1234.5) == "↓ -19%"

    def test_precio_promedio_requiere_unidades_positivas(self):
        p = rc._precio_prom
        assert p(1000.0, 100.0) == 10.0
        assert p(1000.0, 0) is None  # sin volumen: promedio inestable
        assert p(1000.0, None) is None
        assert p(None, 100.0) is None
        assert p(1000.0, -50.0) is None  # unidades negativas
        assert p(True, 100.0) is None

    def _filas(self, tmp_path, filas):
        """Escribe el Comparativo y devuelve {código SKU: (soles, precio)}."""
        filas = [
            {
                "MES_REF": f"{y}-06",
                "COD_SKU": cod,
                "SKU": cod,
                "COD_LINEA": "01",
                "LINEA": "PELOTAS",
                "SOLES": soles,
                "CANTIDAD": unid,
            }
            for cod, por_anio in filas.items()
            for y, (soles, unid) in por_anio.items()
        ]
        out = tmp_path / "T.xlsx"
        rc._escribir_xlsx(
            out,
            "56101",
            "M",
            df_comp_sku=pd.DataFrame(filas),
            corte_hasta="2026-06-30",
            hojas={"comparativo"},
        )
        ws = load_workbook(str(out))["Comparativo"]
        hdr = [c.value for c in ws[2]]
        col_s = hdr.index("Tend. Soles") + 1
        col_p = hdr.index("Tend. Precio") + 1
        res = {}
        for r in range(3, ws.max_row + 1):
            v = ws.cell(row=r, column=1).value
            if v in ("TOTAL COMPARABLE", "TOTAL DEL RANGO", None):
                break
            if v is not None:
                res[ws.cell(row=r, column=4).value] = (
                    ws.cell(row=r, column=col_s).value,
                    ws.cell(row=r, column=col_p).value,
                )
        return res

    def test_crecimiento_por_volumen_con_precio_estable(self, tmp_path):
        """Más unidades al mismo precio: ventas ↑, precio →."""
        res = self._filas(tmp_path, {"0001": {"2025": (1000.0, 100.0), "2026": (1300.0, 130.0)}})
        assert res["0001"] == ("↑ +30%", "→ +0%")

    def test_crecimiento_por_precio_con_volumen_estable(self, tmp_path):
        """Mismas unidades a mayor precio: ventas ↑, precio ↑."""
        res = self._filas(tmp_path, {"0002": {"2025": (1000.0, 100.0), "2026": (1300.0, 100.0)}})
        assert res["0002"] == ("↑ +30%", "↑ +30%")

    def test_crecimiento_en_volumen_sostenido_con_descuento(self, tmp_path):
        """Soles ↑ con precio ↓: el crecimiento se compró con descuento."""
        res = self._filas(tmp_path, {"0003": {"2025": (1000.0, 100.0), "2026": (1872.0, 300.0)}})
        # precio prom. 10.00 -> 6.24 = -37.6%
        assert res["0003"] == ("↑ +87%", "↓ -38%")

    def test_caida_de_precio_con_volumen_estable(self, tmp_path):
        """Soles ↓ y precio ↓: la caída viene del precio, no del volumen."""
        res = self._filas(tmp_path, {"0004": {"2025": (1000.0, 100.0), "2026": (600.0, 100.0)}})
        assert res["0004"] == ("↓ -40%", "↓ -40%")

    def test_ventas_planas_por_precio_y_volumen_se_compensan(self, tmp_path):
        """Doble volumen a la mitad de precio: Soles →, precio ↓."""
        res = self._filas(tmp_path, {"0005": {"2025": (1000.0, 100.0), "2026": (1000.0, 200.0)}})
        assert res["0005"] == ("→ +0%", "↓ -50%")

    def test_indicadores_usan_el_mismo_par_de_anios(self, tmp_path):
        """Con 2024/2025/2026 se comparan 2026 vs 2025, no contra 2024."""
        res = self._filas(
            tmp_path,
            {"0006": {"2024": (100.0, 10.0), "2025": (1000.0, 100.0), "2026": (1200.0, 120.0)}},
        )
        assert res["0006"] == ("↑ +20%", "→ +0%")

    def test_sin_anio_previo_queda_vacio(self, tmp_path):
        """Un solo año con dato no admite tendencia."""
        res = self._filas(
            tmp_path,
            {
                "0007": {"2026": (1200.0, 120.0)},
                "0008": {"2025": (1000.0, 100.0), "2026": (1200.0, 120.0)},
            },
        )
        assert res["0007"] == (None, None)
        assert res["0008"] == ("↑ +20%", "→ +0%")

    def test_sin_unidades_no_hay_precio_promedio(self, tmp_path):
        """Con 0 unidades el promedio no existe: solo queda la facturación."""
        res = self._filas(tmp_path, {"0009": {"2025": (1000.0, 0.0), "2026": (1200.0, 0.0)}})
        assert res["0009"] == ("↑ +20%", None)

    def test_anio_intermedio_ausente_usa_el_disponible(self, tmp_path):
        """Si falta 2025, la comparación cae a 2024 (y queda señalizable)."""
        res = self._filas(tmp_path, {"0010": {"2024": (1000.0, 100.0), "2026": (1500.0, 150.0)}})
        assert res["0010"] == ("↑ +50%", "→ +0%")


class TestDescuentoEmbebido:
    """F/B con soles<0: bucket económico DESC (físico sin cambios)."""

    def _poblar(self, tmp_db):
        from src.core.xls_processor import derivar_campos

        base = dict(
            id_articulo="014850",
            original_sku="014850",
            nom_articulo="PELOTA PVC #5",
            id_linea="0101",
            nom_linea="GASEOSAS",
            id_grupo="01",
            nom_grupo="G",
            id_tipo="01",
            nom_tipo="T",
            id_familia="01",
            nom_familia="F",
            id_cliente="00056101",
            doc_cliente="20601024714",
            nom_cliente="MULTICOPIAS MARY E.I.R.L.",
            tpo_doc="F01",
            serie_doc="204",
            nro_doc="67375",
            referencia="",
            moneda="Soles",
            cantidad=5.0,
            cantidad_fae=0.0,
            soles=-50.0,
            dolares=0.0,
            precio_unitario=-10.0,
            anho=2026,
            mes=9,
            fecha_orig="2026-09-05",
            fecha_ref=None,
            fecha_venc=None,
            cod_sucursal="01",
            nom_sucursal="LIMA",
            departamento="LIMA",
            provincia="LIMA",
            distrito="SAN ISIDRO",
            id_vendedor="01188",
            nom_vendedor="VEND",
            id_pedido="P1",
            file_source="t",
            mes_ref="2026-09",
            tipo_operacion="",
            factura_ref_serie="",
            factura_ref_nro="",
            folio_unico="",
        )
        d = dict(base)
        derivar_campos(d)
        conn = tmp_db.get_conn()
        ventas_db.insert_ventas(conn, [d])

    def test_bucket_desc(self, tmp_db):
        self._poblar(tmp_db)
        kw = dict(fecha_desde="2026-09-01", fecha_hasta="2026-09-30")
        hist = VentasDbClient().fetch_historico_mensual("00056101", **kw)
        assert round(float(hist["BRUTA"].sum()), 2) == 0.0
        assert round(float(hist["DESC_S"].sum()), 2) == -50.0
        assert round(float(hist["NETA"].sum()), 2) == -50.0
        assert float(hist["UFACT"].sum()) == 5.0  # físico sin cambios
        assert rc.reconciliar_mensual(hist) == []
        lin = VentasDbClient().fetch_lineas_resumen_cliente("00056101", **kw)
        assert round(float(lin["BRUTA"].sum()), 2) == 0.0
        assert round(float(lin["DESC_S"].sum()), 2) == -50.0


class TestLineasValidadas:
    """El reporte respeta el allowlist (igual criterio que el sustento)."""

    def _poblar(self, tmp_db):
        from src.core.xls_processor import derivar_campos

        base, f_oct, dev, aju = _filas()
        # Factura de línea NO validada (sufijo '00' fuera del default)
        invalida = dict(
            base,
            id_linea="0100",
            nro_doc="67390",
            cantidad=100.0,
            soles=120.0,
            fecha_orig="2026-09-20",
            mes_ref="2026-09",
        )
        # NC de línea inválida que referencia una factura VÁLIDA (orphan-safe)
        nc_orphan = dict(dev, id_linea="0100", nro_doc="900020")
        rows = []
        for v in (base, f_oct, dev, aju, invalida, nc_orphan):
            d = dict(v)
            derivar_campos(d)
            rows.append(d)
        conn = tmp_db.get_conn()
        ventas_db.insert_ventas(conn, rows)
        ventas_db.populate_nc_asociadas(conn)

    def test_factura_linea_invalida_excluida(self, tmp_db):
        self._poblar(tmp_db)
        cli = VentasDbClient()
        kw = dict(fecha_desde="2026-09-01", fecha_hasta="2026-09-30")
        con = cli.fetch_compras_cliente("00056101", solo_lineas_activas=True, **kw)
        sin = cli.fetch_compras_cliente("00056101", solo_lineas_activas=False, **kw)
        # Septiembre: factura válida 896 − notas (224+96+224 huérfana) = 352.
        # Sin allowlist entra además la factura de línea 0100 (120) → 472.
        assert round(float(con["SOLES"].sum()), 2) == 352.00
        assert round(float(sin["SOLES"].sum()), 2) == 472.00

    def test_nc_orphan_se_conserva(self, tmp_db):
        self._poblar(tmp_db)
        cli = VentasDbClient()
        kw = dict(fecha_desde="2026-09-01", fecha_hasta="2026-09-30")
        df = cli.fetch_notas_sku_cliente("00056101", solo_lineas_activas=True, **kw)
        assert not df.empty
        assert set(df["FACTURA"]) == {"F204-67375"}
        # …y no cae al bloque de huérfanas (su factura existe).
        assert cli.fetch_notas_huerfanas_cliente("00056101", solo_lineas_activas=True, **kw).empty

    def test_lineas_resumen_respeta_allowlist(self, tmp_db):
        self._poblar(tmp_db)
        cli = VentasDbClient()
        kw = dict(fecha_desde="2026-09-01", fecha_hasta="2026-09-30")
        df = cli.fetch_lineas_resumen_cliente("00056101", solo_lineas_activas=True, **kw)
        # La factura de línea 0100 no entra; su NC huérfana sí (orphan-safe:
        # referencia una factura válida) y forma el grupo 00 (canónico).
        assert set(df["COD_LINEA"]) == {"01", "00"}
        orfa = df[df["COD_LINEA"] == "00"].iloc[0]
        assert round(float(orfa["SOLES"]), 2) == -224.0

    def test_linea_prefijada_se_fusiona(self, tmp_db):
        """'01' y '0101' son la misma línea comercial (PELOTAS)."""
        from src.core.xls_processor import derivar_campos

        base, f_oct, dev, aju = _filas()
        # Misma línea con código pelado (sin prefijo de sucursal).
        pelada = dict(
            base,
            id_linea="01",
            nro_doc="67390",
            cantidad=10.0,
            soles=12.0,
            fecha_orig="2026-09-20",
            mes_ref="2026-09",
        )
        rows = []
        for v in (base, f_oct, dev, aju, pelada):
            d = dict(v)
            derivar_campos(d)
            rows.append(d)
        conn = tmp_db.get_conn()
        ventas_db.insert_ventas(conn, rows)
        cli = VentasDbClient()
        kw = dict(fecha_desde="2026-09-01", fecha_hasta="2026-10-31")
        df = cli.fetch_lineas_resumen_cliente("00056101", **kw)
        assert set(df["COD_LINEA"]) == {"01"}  # un solo grupo
        r = df.iloc[0]
        assert round(float(r["SOLES"]), 2) == 696.0 + 12.0
        assert float(r["CANTIDAD"]) == 700.0 + 10.0

    def test_facturas_respeta_allowlist(self, tmp_db):
        self._poblar(tmp_db)
        cli = VentasDbClient()
        kw = dict(fecha_desde="2026-09-01", fecha_hasta="2026-09-30")
        docs = set(
            cli.fetch_facturas_sku_detalle_cliente("00056101", solo_lineas_activas=True, **kw)[
                "DOC"
            ]
        )
        assert "F204-67390" not in docs  # línea inválida
        assert "F204-67375" in docs  # línea válida


class TestFormato:
    def test_paquete_export_keys(self, tmp_db):
        _poblar(tmp_db)
        b = rc._paquete_export(VentasDbClient(), "00056101", "2026-09-01", "2026-10-31", True)
        assert set(b) == {
            "df_hist",
            "df_lineas",
            "df_skus",
            "df_comp_sku",
            "df_dev",
            "df_huerf",
            "df_hechos",
            "df_det",
            "df_suc_pareto",
            "df_suc_linea_mes",
            "df_suc_sku_mes",
        }
        assert b["df_hist"] is not None
        assert b["df_lineas"] is not None
        assert b["df_skus"] is not None
        assert b["df_comp_sku"] is not None
        assert b["df_dev"] is not None
        assert b["df_hechos"] is not None
        assert b["df_det"] is not None
        assert b["df_suc_pareto"] is not None
        assert b["df_suc_linea_mes"] is not None
        assert b["df_suc_sku_mes"] is not None
        assert b["df_huerf"] is None  # fixture sin huérfanas

    def test_analisis_sucursales_solo_se_lee_al_pedirlo(self, tmp_db):
        _poblar(tmp_db)
        b = rc._paquete_export(
            VentasDbClient(), "00056101", "2026-09-01", "2026-10-31", True, hojas={"resumen", "bd"}
        )
        assert b["df_suc_pareto"] is None
        assert b["df_suc_linea_mes"] is None
        assert b["df_suc_sku_mes"] is None

    def test_pareto_y_detalles_mensuales_concilian(self, tmp_db):
        _poblar(tmp_db)
        b = rc._paquete_export(
            VentasDbClient(),
            "00056101",
            "2026-09-01",
            "2026-10-31",
            True,
            hojas={"sucursales"},
            solo_lineas_activas=False,
        )
        p = b["df_suc_pareto"]
        lin = b["df_suc_linea_mes"]
        sku = b["df_suc_sku_mes"]
        assert p["BRUTA"].sum() == 1016.0
        assert p["SOLES"].sum() == 696.0
        assert lin["SOLES"].sum() == 696.0
        assert sku["SOLES"].sum() == 696.0
        assert p.iloc[0]["COD_SUCURSAL"] == "01"
        assert p.iloc[0]["DEV_S"] == -224.0
        assert p.iloc[0]["DESC_S"] == -96.0
        assert abs(float(p["PCT_BRUTA"].sum()) - 1.0) < 1e-9
        assert float(p["PCT_ACUMULADO"].iloc[-1]) == 1.0
        assert set(lin["MES_REF"]) == {"2026-09", "2026-10"}
        assert len(sku) == 2  # mismo SKU, dos sucursales/meses

    def test_paquete_export_sin_datos_no_explota(self, tmp_db):
        b = rc._paquete_export(VentasDbClient(), "00000000", "2020-01-01", "2020-01-31", True)
        assert b["df_hist"] is None
        assert b["df_det"] is None

    def test_paquete_export_solo_lecturas_pedidas(self, tmp_db):
        _poblar(tmp_db)
        b = rc._paquete_export(
            VentasDbClient(), "00056101", "2026-09-01", "2026-10-31", True, hojas={"resumen", "bd"}
        )
        assert b["df_hist"] is not None  # resumen
        assert b["df_hechos"] is not None  # bd
        assert b["df_lineas"] is None  # no pedido consolidado
        assert b["df_skus"] is None
        assert b["df_comp_sku"] is None  # no pedido comparativo
        assert b["df_dev"] is None
        assert b["df_huerf"] is None
        assert b["df_det"] is None

    def test_paquete_export_toggle_off(self, tmp_db):
        _poblar(tmp_db)
        b = rc._paquete_export(VentasDbClient(), "00056101", "2026-09-01", "2026-10-31", False)
        assert b["df_hist"] is not None  # bruta, componentes en cero
        assert b["df_dev"] is None
        assert b["df_huerf"] is None
        assert b["df_det"] is not None  # mundo bruto, sin toggle

    def test_mes_label_yyyymm(self):
        assert rc._mes_label("2026-09") == "2026-09"
        assert rc._mes_label("2026-9") == "2026-09"

    def test_fecha_corta(self):
        assert rc._fecha_corta("2026-09-05") == "05-09-2026"
        assert rc._fecha_corta("2026-09-05 10:00:00") == "05-09-2026"

    def test_motivo_etiqueta_cerrada(self):
        assert rc._motivo("devolucion") == "DEVOLUCIÓN"
        assert rc._motivo("ajuste_valor") == "DESCUENTO"
        assert rc._motivo("nota_debito") == "NDB"
        assert rc._motivo("raro") == "raro"


class TestXlsx5Hojas:
    KW = dict(fecha_desde="2026-09-01", fecha_hasta="2026-10-31")

    def _todo(self, tmp_db, tmp_path, incluir_nc=True):
        _poblar(tmp_db)
        cli = VentasDbClient()
        bundle = rc._paquete_export(
            cli, "00056101", self.KW["fecha_desde"], self.KW["fecha_hasta"], incluir_nc
        )
        out = tmp_path / "Compras.xlsx"
        rc._escribir_xlsx(
            out, "56101", "MULTICOPIAS", corte_hasta="2026-10-31", incluir_nc=incluir_nc, **bundle
        )
        return load_workbook(str(out))

    def test_orden_y_nombres(self, tmp_db, tmp_path):
        wb = self._todo(tmp_db, tmp_path)
        assert wb.sheetnames == [
            "Resumen Ejecutivo",
            "Consolidado",
            "Comparativo",
            "Sucursales",
            "Sucursal_Linea_Mes",
            "Sucursal_SKU_Mes",
            "Ajustes_NC_NDB",
            "BD_Registro",
            "Facturas",
        ]

    def test_hojas_seleccionadas_subconjunto(self, tmp_db, tmp_path):
        _poblar(tmp_db)
        cli = VentasDbClient()
        bundle = rc._paquete_export(
            cli,
            "00056101",
            self.KW["fecha_desde"],
            self.KW["fecha_hasta"],
            True,
            hojas={"resumen", "bd"},
        )
        out = tmp_path / "Sub.xlsx"
        rc._escribir_xlsx(
            out, "56101", "M", corte_hasta="2026-10-31", hojas={"resumen", "bd"}, **bundle
        )
        wb = load_workbook(str(out))
        assert wb.sheetnames == ["Resumen Ejecutivo", "BD_Registro"]

    def test_sin_datos_falla_claro(self, tmp_db, tmp_path):
        """Sin ninguna hoja con datos no se escribe un libro vacío."""
        import pytest

        _poblar(tmp_db)
        out = tmp_path / "Vacio.xlsx"
        with pytest.raises(ValueError, match="sin datos"):
            rc._escribir_xlsx(out, "56101", "M", df_hechos=None, hojas={"bd"})

    def test_recalcula_al_abrir(self, tmp_db, tmp_path):
        wb = self._todo(tmp_db, tmp_path)
        assert wb.calculation.fullCalcOnLoad is True


class TestXlsxSucursales:
    def test_tablas_planas_encabezados_unicos_fila_1(self, tmp_db, tmp_path):
        _poblar(tmp_db)
        cli = VentasDbClient()
        bundle = rc._paquete_export(
            cli,
            "00056101",
            "2026-09-01",
            "2026-10-31",
            True,
            hojas={"sucursales"},
            solo_lineas_activas=False,
        )
        out = tmp_path / "Sucursales.xlsx"
        rc._escribir_xlsx(out, "56101", "MULTICOPIAS", hojas={"sucursales"}, **bundle)
        wb = load_workbook(str(out), data_only=False)
        assert wb.sheetnames == ["Sucursales", "Sucursal_Linea_Mes", "Sucursal_SKU_Mes"]
        for ws in wb.worksheets:
            headers = [ws.cell(1, c).value for c in range(1, ws.max_column + 1)]
            assert all(isinstance(h, str) and h.strip() for h in headers)
            assert len(headers) == len(set(headers)), ws.title
            assert ws.freeze_panes == "A2"
            assert not ws.merged_cells.ranges
            assert len(ws.tables) == 1
            table = next(iter(ws.tables.values()))
            assert table.ref.startswith("A1:")
        assert wb["Sucursales"]["N2"].number_format == "0.0%"
        assert wb["Sucursales"]["O1"].value == "% Acumulado"
        assert wb["Sucursal_Linea_Mes"]["E1"].value == "Código línea"
        assert wb["Sucursal_SKU_Mes"]["E1"].value == "Código SKU"


def _cfs(ws):
    """Reglas condicionales como (rango, operador)."""
    out = []
    for cf in ws.conditional_formatting:
        for rl in cf.rules:
            out.append((str(cf.sqref), rl.operator))
    return out


class TestMergesTitulo:
    """A1 (título) combinado en Resumen; operativas usan print-header."""

    def test_a1_a2_combinadas(self, tmp_db, tmp_path):
        wb = TestXlsx5Hojas()._todo(tmp_db, tmp_path)
        # Solo Resumen conserva banner visible en filas 1-2.
        ws_res = wb["Resumen Ejecutivo"]
        merges = {str(r) for r in ws_res.merged_cells.ranges}
        assert "A1:P1" in merges
        assert "A2:P2" in merges
        assert "importes sin IGV" in str(ws_res["A2"].value)
        # Las operativas van directo a fila 1; el cliente en print-header.
        for hoja in ("Consolidado", "Comparativo", "Ajustes_NC_NDB", "BD_Registro", "Facturas"):
            ws = wb[hoja]
            merges = {str(r) for r in ws.merged_cells.ranges}
            # Sin merge A1:A2 (banner removido).
            assert not any(str(m).startswith("A1:") and str(m).endswith(":A2") for m in merges), (
                hoja
            )
            # Print-header presente.
            assert ws.oddHeader.left.text, f"{hoja}: sin oddHeader.left"
            assert ws.oddHeader.center.text, f"{hoja}: sin oddHeader.center"


class TestCondicionales:
    """Semaforización mínima: rojos = salidas/negativos, verde = NDB."""

    def test_resumen(self, tmp_db, tmp_path):
        _poblar(tmp_db)
        cli = VentasDbClient()
        kw = dict(fecha_desde="2026-09-01", fecha_hasta="2026-10-31")
        out = tmp_path / "R.xlsx"
        rc._escribir_xlsx(
            out,
            "56101",
            "M",
            df_hist=cli.fetch_historico_mensual("00056101", **kw),
            corte_hasta="2026-10-31",
            hojas={"resumen"},
        )
        cfs = _cfs(load_workbook(str(out))["Resumen Ejecutivo"])
        assert ("B6:B20", "lessThan") in cfs  # KPIs en rojo si < 0
        assert ("F24:F26", "lessThan") in cfs  # Venta neta mensual

    def test_consolidado(self, tmp_db, tmp_path):
        ws = TestConsolidadoHoja()._wb(tmp_db, tmp_path)["Consolidado"]
        cfs = _cfs(ws)
        assert ("F3:F4", "lessThan") in cfs  # Neto por línea
        assert ("D8:D9", "lessThan") in cfs  # Neto top SKUs

    def test_ajustes(self, tmp_db, tmp_path):
        ws = TestAjustesHoja()._wb(tmp_db, tmp_path)["Ajustes_NC_NDB"]
        cfs = _cfs(ws)
        assert ("Q9:Q16", "lessThan") in cfs  # S/ NC negativo
        assert ("Q9:Q16", "greaterThan") in cfs  # NDB positivo
        assert ("R9:R16", "lessThan") in cfs  # Saldo sobregirado

    def test_facturas_y_bd(self, tmp_db, tmp_path):
        wb = TestXlsx5Hojas()._todo(tmp_db, tmp_path)
        assert ("M3:M5", "lessThan") in _cfs(wb["Facturas"])
        assert ("L2:L6", "lessThan") in _cfs(wb["BD_Registro"])


class TestTablasNativas:
    """Cada bloque de detalle es una Tabla Excel (filtros + dinámicas)."""

    def test_resumen_mensual(self, tmp_db, tmp_path):
        _poblar(tmp_db)
        cli = VentasDbClient()
        kw = dict(fecha_desde="2026-09-01", fecha_hasta="2026-10-31")
        out = tmp_path / "R.xlsx"
        rc._escribir_xlsx(
            out,
            "56101",
            "M",
            df_hist=cli.fetch_historico_mensual("00056101", **kw),
            corte_hasta="2026-10-31",
            hojas={"resumen"},
        )
        ws = load_workbook(str(out))["Resumen Ejecutivo"]
        assert list(ws.tables) == ["ResumenMensual"]
        hdr = next(r for r in range(1, 40) if ws.cell(row=r, column=1).value == "Mes")
        rt = next(
            r for r in range(hdr, ws.max_row + 1) if ws.cell(row=r, column=1).value == "TOTAL"
        )
        assert ws.tables["ResumenMensual"].ref == f"A{hdr}:Q{rt - 1}"
        assert ws.auto_filter.ref is None  # la tabla filtra sola

    def test_ajustes(self, tmp_db, tmp_path):
        ws = TestAjustesHoja()._wb(tmp_db, tmp_path)["Ajustes_NC_NDB"]
        assert list(ws.tables) == ["AjustesDetalle", "AjustesHuerfanas"]
        assert ws.tables["AjustesDetalle"].ref == "A8:S15"
        assert ws.tables["AjustesHuerfanas"].ref == "A19:J21"
        assert ws.auto_filter.ref is None

    def test_facturas(self, tmp_db, tmp_path):
        ws = TestFacturasHoja()._wb(tmp_db, tmp_path)["Facturas"]
        assert list(ws.tables) == ["FacturasDetalle"]
        assert ws.tables["FacturasDetalle"].ref == "A2:O4"
        assert ws.auto_filter.ref is None


class TestResumenHoja:
    def _wb(self, tmp_db, tmp_path, incluir_nc=True, corte="2026-10-31", rango=None):
        _poblar(tmp_db)
        cli = VentasDbClient()
        kw = dict(fecha_desde="2026-09-01", fecha_hasta="2026-10-31")
        df_hist = cli.fetch_historico_mensual("00056101", incluir_nc=incluir_nc, **kw)
        out = tmp_path / "R.xlsx"
        extra = {"rango_desde": rango[0], "rango_hasta": rango[1]} if rango else {}
        rc._escribir_xlsx(
            out,
            "56101",
            "M",
            df_hist=df_hist,
            corte_hasta=corte,
            incluir_nc=incluir_nc,
            hojas={"resumen"},
            **extra,
        )
        return load_workbook(str(out))

    def _rt(self, ws):
        return next(
            r for r in range(5, ws.max_row + 1) if ws.cell(row=r, column=1).value == "TOTAL"
        )

    def test_kpis_enlazan_total_mensual(self, tmp_db, tmp_path):
        wb = self._wb(tmp_db, tmp_path)
        assert wb.sheetnames == ["Resumen Ejecutivo"]
        ws = wb["Resumen Ejecutivo"]
        assert ws["A1"].value == "COMPRAS NETAS — RESUMEN EJECUTIVO"
        assert "importes sin IGV" in str(ws["A2"].value)
        rt = self._rt(ws)
        vals = [c.value for c in ws["B"] if isinstance(c.value, str)]
        assert f"=F{rt}" in vals  # Venta neta → TOTAL
        assert f"=I{rt}" in vals  # Unidades netas
        assert not any("#REF!" in v for v in vals)
        assert "HISTORICO" not in " ".join(vals)
        origenes = [c.value for c in ws["C"] if isinstance(c.value, str)]
        assert f"F{rt}" in origenes
        # Último mes apunta a la última fila de datos.
        assert f"=A{rt - 1}" in vals
        # Tabla mensual con fórmulas vivas (fila del primer mes).
        r_sep = next(r for r in range(5, rt) if ws.cell(row=r, column=1).value == "2026-09")
        assert ws.cell(row=r_sep, column=6).value == (f"=SUM(B{r_sep}:E{r_sep})")
        assert ws.cell(row=r_sep, column=9).value == f"=G{r_sep}+H{r_sep}"
        assert ws.cell(row=rt, column=10).value == (f'=IF(G{rt}=0,"—",-H{rt}/G{rt})')
        textos = [c.value for r in ws.iter_rows() for c in r if isinstance(c.value, str)]
        assert any("Concilia" in t for t in textos)
        assert any("Incluir NC/ND: SÍ" in t for t in textos)

    def test_estado_parcial(self, tmp_db, tmp_path):
        wb = self._wb(tmp_db, tmp_path, corte="2026-09-15")
        ws = wb["Resumen Ejecutivo"]
        estados = {
            ws.cell(row=r, column=1).value: ws.cell(row=r, column=16).value
            for r in range(5, ws.max_row + 1)
            if ws.cell(row=r, column=1).value in ("2026-09", "2026-10")
        }
        assert estados == {"2026-09": "PARCIAL", "2026-10": "PARCIAL"}

    def test_modo_sin_nc(self, tmp_db, tmp_path):
        wb = self._wb(tmp_db, tmp_path, incluir_nc=False)
        ws = wb["Resumen Ejecutivo"]
        rt = self._rt(ws)
        fila = {
            ws.cell(row=r, column=1).value: ws.cell(row=r, column=2).value
            for r in range(1, ws.max_row + 1)
        }
        nota_modo = next(k for k in fila if isinstance(k, str) and "Incluir NC/ND" in k)
        assert "NO — solo facturas/boletas" in nota_modo
        # Estructura intacta: bruta y neta apuntan a su columna del TOTAL.
        assert fila["Venta bruta (S/)"] == f"=B{rt}"
        assert fila["Venta neta (S/)"] == f"=F{rt}"

    def test_nota_de_rango(self, tmp_db, tmp_path):
        wb = self._wb(tmp_db, tmp_path, rango=("2026-09-01", "2026-10-31"))
        ws = wb["Resumen Ejecutivo"]
        textos = [c.value for r in ws.iter_rows() for c in r if isinstance(c.value, str)]
        assert any("Rango: 01-09-2026 → 31-10-2026" in t for t in textos)

    def test_indice_con_hipervinculos_internos(self, tmp_db, tmp_path):
        wb = TestXlsx5Hojas()._todo(tmp_db, tmp_path)
        ws = wb["Resumen Ejecutivo"]
        links = {
            c.value: c.hyperlink.location
            for fila in ws.iter_rows(min_col=1, max_col=1)
            for c in fila
            if c.hyperlink
        }
        assert links["Consolidado"] == "'Consolidado'!A1"
        assert links["Ajustes_NC_NDB"] == "'Ajustes_NC_NDB'!A1"
        assert links["BD_Registro"] == "'BD_Registro'!A1"
        assert links["Facturas"] == "'Facturas'!A1"
        assert "Resumen Ejecutivo" not in links  # ni a sí misma


class TestConsolidadoHoja:
    def _wb(self, tmp_db, tmp_path):
        _poblar(tmp_db)
        cli = VentasDbClient()
        kw = dict(fecha_desde="2026-09-01", fecha_hasta="2026-10-31")
        out = tmp_path / "C.xlsx"
        rc._escribir_xlsx(
            out,
            "56101",
            "M",
            df_lineas=cli.fetch_lineas_resumen_cliente("00056101", **kw),
            df_skus=cli.fetch_skus_resumen_cliente("00056101", **kw),
            hojas={"consolidado"},
        )
        return load_workbook(str(out))

    def test_bloque_lineas(self, tmp_db, tmp_path):
        ws = self._wb(tmp_db, tmp_path)["Consolidado"]
        assert ws["A1"].value == "POR LÍNEA (S/)"
        assert [c.value for c in ws[2]] == [
            "Línea",
            "Bruta S/",
            "Dev. S/",
            "Desc. S/",
            "NDB S/",
            "Neto S/",
            "Unid.",
            "N° docs",
            "% neto",
        ]
        fila = [c.value for c in ws[3]]
        assert fila[0] == "01 - GASEOSAS"  # identidad línea sin artículo
        assert fila[1:5] == [1016.0, -224.0, -96.0, 0.0]
        assert fila[5] == "=SUM(B3:E3)"  # Neto con fórmula por fila
        assert fila[6:8] == [700.0, 4]
        assert fila[8] == 1.0  # % neto (única línea)
        total = [c.value for c in ws[4]]
        assert total[0] == "TOTAL"
        assert total[5] == "=SUBTOTAL(109,F3:F3)"
        assert total[8] == "=SUBTOTAL(109,I3:I3)"
        assert ws.freeze_panes is None
        assert ws.auto_filter.ref is None  # las tablas filtran solas
        assert list(ws.tables) == ["ConsolidadoLineas", "ConsolidadoSKUs"]
        assert ws.tables["ConsolidadoLineas"].ref == "A2:I3"
        assert ws.tables["ConsolidadoSKUs"].ref == "A7:F8"

    def test_bloque_top_skus(self, tmp_db, tmp_path):
        ws = self._wb(tmp_db, tmp_path)["Consolidado"]
        hdr = next(r for r in range(1, ws.max_row + 1) if ws.cell(row=r, column=1).value == "SKU")
        assert [ws.cell(row=hdr, column=c).value for c in range(1, 7)] == [
            "SKU",
            "Artículo",
            "Línea",
            "Neto S/",
            "Unid.",
            "% neto",
        ]
        fila = [ws.cell(row=hdr + 1, column=c).value for c in range(1, 7)]
        assert fila[:3] == ["014850", "PELOTA PVC #5", "01"]
        assert fila[3:5] == [696.0, 700.0]
        # Sin resto: sin fila "Otros", TOTAL directo.
        total = [ws.cell(row=hdr + 2, column=c).value for c in range(1, 7)]
        assert total[0] == "TOTAL"
        assert total[3] == f"=SUBTOTAL(109,D{hdr + 1}:D{hdr + 1})"

    def test_top_con_otros(self, tmp_path):
        skus = pd.DataFrame(
            [
                {
                    "COD_SKU": f"{i:06d}",
                    "SKU": f"ART {i}",
                    "COD_LINEA": "0101",
                    "LINEA": "GASEOSAS",
                    "SOLES": float(100 - i),
                    "CANTIDAD": float(10),
                    "N_DOCS": 1,
                }
                for i in range(17)
            ]
        )
        out = tmp_path / "Top.xlsx"
        rc._escribir_xlsx(out, "56101", "M", df_skus=skus, hojas={"consolidado"})
        ws = load_workbook(str(out))["Consolidado"]
        hdr = next(r for r in range(1, ws.max_row + 1) if ws.cell(row=r, column=1).value == "SKU")
        # 15 top + Otros (2) + TOTAL.
        assert ws.cell(row=hdr + 16, column=1).value == "Otros (2)"
        assert ws.cell(row=hdr + 16, column=4).value == 85.0 + 84.0
        assert ws.cell(row=hdr + 17, column=1).value == "TOTAL"
        assert ws.cell(row=hdr + 17, column=4).value == (f"=SUBTOTAL(109,D{hdr + 1}:D{hdr + 16})")
        assert ws.cell(row=hdr + 1, column=4).value == 100.0  # top primero


class TestAjustesHoja:
    def _wb(self, tmp_db, tmp_path):
        TestNotasSku()._poblar_notas(tmp_db)
        cli = VentasDbClient()
        kw = dict(fecha_desde="2026-09-01", fecha_hasta="2026-09-30")
        out = tmp_path / "A.xlsx"
        rc._escribir_xlsx(
            out,
            "56101",
            "M",
            df_dev=cli.fetch_notas_sku_cliente("00056101", **kw),
            df_huerf=cli.fetch_notas_huerfanas_cliente("00056101", **kw),
            hojas={"ajustes"},
        )
        return load_workbook(str(out))

    def _detalle(self, ws):
        hdr = next(
            r
            for r in range(1, ws.max_row + 1)
            if ws.cell(row=r, column=1).value == "Factura"
            and ws.cell(row=r, column=9).value == "Motivo"
        )
        filas = []
        r = hdr + 1
        while ws.cell(row=r, column=1).value not in ("TOTAL", None):
            filas.append([ws.cell(row=r, column=c).value for c in range(1, 20)])
            r += 1
        total = [ws.cell(row=r, column=c).value for c in range(1, 20)]
        return hdr, filas, total

    def test_detalle_motivo_y_signo(self, tmp_db, tmp_path):
        ws = self._wb(tmp_db, tmp_path)["Ajustes_NC_NDB"]
        assert ws["A1"].value == "TOTALES POR MOTIVO (S/)"
        hdr, filas, total = self._detalle(ws)
        assert [ws.cell(row=hdr, column=c).value for c in range(1, 20)] == [
            "Factura",
            "F. fact.",
            "SKU",
            "Artículo",
            "Cant. fact.",
            "S/ fact.",
            "NC",
            "F. doc",
            "Motivo",
            "Cant.",
            "Base FAE",
            "P.U. fact.",
            "P.U. NC",
            "Cobertura",
            "Afecta precio",
            "P.U. neto",
            "S/ NC",
            "Saldo",
            "Obs.",
        ]
        motivos = {f[6] for f in filas}
        assert motivos == {
            "N204-900010",
            "N204-900011",
            "N204-900012",
            "N204-900013",
            "N204-900014",
            "N204-900015",
            "N204-900016",
        }
        assert {f[8] for f in filas} == {"DEVOLUCIÓN", "DESCUENTO", "NDB"}
        por_nc = {f[6]: f for f in filas}
        assert por_nc["N204-900010"][16] == -224.0  # signo nativo
        assert por_nc["N204-900012"][16] == 50.0  # NDB positivo
        assert por_nc["N204-900010"][5] == 896.0  # S/ fact. del grupo
        assert por_nc["N204-900015"][5] == "—"  # SKU no facturado
        # Regla física: Cant. 0 en descuento/NDB (era la base FAE).
        assert por_nc["N204-900010"][9] == 200.0  # DEV conserva
        assert por_nc["N204-900011"][9] == 0  # DESC → 0
        assert por_nc["N204-900012"][9] == 0  # NDB → 0
        assert por_nc["N204-900013"][9] == 0  # flag −1 → 0
        # Base FAE visible para identificar el tipo de nota.
        assert por_nc["N204-900010"][10] == 200.0  # DEV replica cantidad
        assert por_nc["N204-900011"][10] == 12.0  # base del descuento
        assert por_nc["N204-900012"][10] == 2.0
        assert por_nc["N204-900014"][10] == 0.0  # sin base
        # Cobertura / Afecta precio / P.U. neto (solo DESCUENTO).
        assert por_nc["N204-900011"][13] == 0.015  # 12/800 puntual
        assert por_nc["N204-900011"][14] == "NO"
        assert por_nc["N204-900011"][15] == "—"
        assert por_nc["N204-900016"][13] == 0.125
        assert por_nc["N204-900016"][14] == "NO"
        assert por_nc["N204-900014"][13] == 0.0
        assert por_nc["N204-900014"][14] == "NO"
        assert por_nc["N204-900010"][13:16] == ["—", "—", "—"]  # DEV
        assert por_nc["N204-900012"][13:16] == ["—", "—", "—"]  # NDB
        assert total[0] == "TOTAL"
        r0 = hdr + 1
        rlast = r0 + len(filas) - 1
        assert total[16] == f"=SUBTOTAL(109,Q{r0}:Q{rlast})"
        assert total[10] == "—"  # las bases no se suman
        # Cant. fact. suma grupos distintos con notas (014851 no tiene).
        assert total[4] == 800.0

    def test_obs_flags(self, tmp_db, tmp_path):
        ws = self._wb(tmp_db, tmp_path)["Ajustes_NC_NDB"]
        _hdr, filas, _tot = self._detalle(ws)
        por_nc = {f[6]: f for f in filas}
        assert por_nc["N204-900015"][18] == "SKU no facturado; Revisar: sin base"
        assert por_nc["N204-900014"][18] == "Revisar: sin base"
        assert por_nc["N204-900016"][18] == "FAE ≠ cant. fact."
        assert por_nc["N204-900010"][18] in ("", None)
        # Alerta rosa en las celdas Obs. con flag.
        rosa = [
            c.fill.start_color.rgb
            for fila in ws.iter_rows()
            for c in fila
            if c.value
            in ("SKU no facturado; Revisar: sin base", "Revisar: sin base", "FAE ≠ cant. fact.")
        ]
        assert rosa and all(str(x).endswith("FFC7CE") for x in rosa)

    def test_saldo_negativo_flag(self, tmp_db, tmp_path):
        from src.core.xls_processor import derivar_campos

        previa = dict(TestNotasSku()._poblar_notas(tmp_db)[0])
        # Nota de devolución pre-rango que sobregira el grupo.
        dev_prev = dict(
            previa,
            tpo_doc="NCR",
            serie_doc="N204",
            nro_doc="900001",
            referencia="F01/204-67375",
            cantidad=-900.0,
            cantidad_fae=-900.0,
            soles=-1008.0,
            fecha_orig="2026-08-15",
            mes_ref="2026-08",
            folio_unico="",
        )
        derivar_campos(dev_prev)
        ventas_db.insert_ventas(tmp_db.get_conn(), [dev_prev])
        TestNotasSku()._poblar_notas(tmp_db)
        cli = VentasDbClient()
        kw = dict(fecha_desde="2026-09-01", fecha_hasta="2026-09-30")
        out = tmp_path / "SN.xlsx"
        rc._escribir_xlsx(
            out,
            "56101",
            "M",
            df_dev=cli.fetch_notas_sku_cliente("00056101", **kw),
            hojas={"ajustes"},
        )
        ws = load_workbook(str(out))["Ajustes_NC_NDB"]
        _hdr, filas, _tot = self._detalle(ws)
        por_nc = {f[6]: f for f in filas}
        assert por_nc["N204-900010"][17] == -300.0  # 800 − 200 − 900
        assert "Saldo negativo" in str(por_nc["N204-900010"][18])

    def test_descuento_total_precio_verificado(self, tmp_db, tmp_path):
        """FAE exacta a lo facturado: descuento total, precio actualizado."""
        from src.core.xls_processor import derivar_campos

        base = dict(
            id_articulo="014850",
            original_sku="014850",
            nom_articulo="PELOTA PVC #5",
            id_linea="0101",
            nom_linea="GASEOSAS",
            id_grupo="01",
            nom_grupo="G",
            id_tipo="01",
            nom_tipo="T",
            id_familia="01",
            nom_familia="F",
            id_cliente="00056101",
            doc_cliente="20601024714",
            nom_cliente="MULTICOPIAS MARY E.I.R.L.",
            tpo_doc="F01",
            serie_doc="204",
            nro_doc="67375",
            referencia="",
            moneda="Soles",
            cantidad=100.0,
            cantidad_fae=0.0,
            soles=1000.0,
            dolares=0.0,
            precio_unitario=10.0,
            anho=2026,
            mes=9,
            fecha_orig="2026-09-05",
            fecha_ref=None,
            fecha_venc=None,
            cod_sucursal="01",
            nom_sucursal="LIMA",
            departamento="LIMA",
            provincia="LIMA",
            distrito="SAN ISIDRO",
            id_vendedor="01188",
            nom_vendedor="VEND",
            id_pedido="P1",
            file_source="t",
            mes_ref="2026-09",
            tipo_operacion="",
            factura_ref_serie="",
            factura_ref_nro="",
            folio_unico="",
        )
        desc = dict(
            base,
            tpo_doc="NCR",
            serie_doc="N204",
            nro_doc="900010",
            referencia="F01/204-67375",
            cantidad=0.0,
            cantidad_fae=100.0,
            soles=-200.0,
            fecha_orig="2026-09-10",
            folio_unico="",
        )
        rows = []
        for v in (base, desc):
            d = dict(v)
            derivar_campos(d)
            rows.append(d)
        conn = tmp_db.get_conn()
        ventas_db.insert_ventas(conn, rows)
        cli = VentasDbClient()
        kw = dict(fecha_desde="2026-09-01", fecha_hasta="2026-09-30")
        df = cli.fetch_notas_sku_cliente("00056101", **kw)
        assert len(df) == 1
        out = tmp_path / "T.xlsx"
        rc._escribir_xlsx(out, "56101", "M", df_dev=df, hojas={"ajustes"})
        ws = load_workbook(str(out))["Ajustes_NC_NDB"]
        hdr = next(
            r for r in range(1, ws.max_row + 1) if ws.cell(row=r, column=1).value == "Factura"
        )
        f = [ws.cell(row=hdr + 1, column=c).value for c in range(1, 20)]
        assert f[6] == "N204-900010"
        assert f[13] == 1.0  # cobertura 100%
        assert f[14] == "SÍ"  # afecta precio
        assert f[15] == 8.0  # (1000-200)/100 verificado
        assert f[18] in ("", None)  # sin flags

    def test_total_identificable(self, tmp_db, tmp_path):
        ws = self._wb(tmp_db, tmp_path)["Ajustes_NC_NDB"]
        rt = next(r for r in range(5, ws.max_row + 1) if ws.cell(row=r, column=1).value == "TOTAL")
        c = ws.cell(row=rt, column=2)
        assert c.font.bold is True
        assert str(c.fill.start_color.rgb).endswith("E4EBF3")
        ws = self._wb(tmp_db, tmp_path)["Ajustes_NC_NDB"]
        sub = next(
            r for r in range(1, 12) if ws.cell(row=r, column=1).value == "TOTALES POR MOTIVO (S/)"
        )
        dev = ws.cell(row=sub + 2, column=2).value
        assert dev.startswith("=SUMIFS(Q")
        assert '"DEVOLUCIÓN"' in dev
        neto = ws.cell(row=sub + 5, column=2).value
        assert neto == f"=B{sub + 2}+B{sub + 3}+B{sub + 4}"
        assert ws.cell(row=sub + 5, column=1).value == "NETO AJUSTES"

    def test_bloque_huerfanas(self, tmp_db, tmp_path):
        ws = self._wb(tmp_db, tmp_path)["Ajustes_NC_NDB"]
        sub = next(
            r
            for r in range(1, ws.max_row + 1)
            if ws.cell(row=r, column=1).value == "NC SIN FACTURA (REVISAR)"
        )
        assert [ws.cell(row=sub + 1, column=c).value for c in range(1, 11)] == [
            "NC",
            "F. doc",
            "Motivo",
            "Factura ref.",
            "SKU",
            "Artículo",
            "Cant.",
            "Base FAE",
            "S/ NC",
            "Obs.",
        ]
        filas = {
            (ws.cell(row=r, column=1).value): [ws.cell(row=r, column=c).value for c in range(1, 11)]
            for r in (sub + 2, sub + 3)
        }
        assert filas["N204-900017"][9] == "Sin factura ref."
        assert filas["N204-900017"][3] == "—"
        assert filas["N204-900018"][9] == "Factura no encontrada"
        assert filas["N204-900018"][3] == "F204-99999"


class TestBDHoja:
    """BD_Registro: 19 columnas, analíticas y paridad con el resto."""

    KW = dict(fecha_desde="2026-09-01", fecha_hasta="2026-10-31")

    def _df(self, tmp_db, **extra):
        _poblar(tmp_db)
        kw = dict(self.KW, **extra)
        return VentasDbClient().fetch_compras_lineas_cliente("00056101", **kw)

    def _wb(self, tmp_db, tmp_path):
        return TestXlsx5Hojas()._todo(tmp_db, tmp_path)

    def _fila_total(self, ws):
        return next(
            r for r in range(5, ws.max_row + 1) if ws.cell(row=r, column=1).value == "TOTAL"
        )

    def test_hoja_bd_registro(self, tmp_db, tmp_path):
        ws = self._wb(tmp_db, tmp_path)["BD_Registro"]
        assert ws["A1"].value == "Documento"
        # Sin columna Nombre: Línea solo con el código canónico.
        assert [c.value for c in ws[1]] == [
            "Documento",
            "Fecha",
            "Mes",
            "Tipo",
            "Ref.",
            "Línea",
            "Sucursal",
            "SKU",
            "Artículo",
            "Cant.",
            "P.U.",
            "Neto (S/)",
            "Operación",
            "Pedido",
            "FAE",
            "Dev. (und.)",
            "Bruto (S/)",
            "Ajuste (S/)",
        ]
        assert ws.freeze_panes is None
        assert ws.auto_filter.ref is None  # la tabla filtra sola
        assert list(ws.tables) == ["BDRegistro"]
        t = ws.tables["BDRegistro"]
        rt = self._fila_total(ws)
        assert rt == 6  # 4 filas de datos (2-5)
        assert t.ref == f"A1:R{rt - 1}"  # TOTAL fuera de la tabla
        assert [c.name for c in t.tableColumns] == [c.value for c in ws[1]]
        assert t.tableStyleInfo.name == "TableStyleMedium2"
        assert ws.cell(row=rt, column=10).value == f"=SUBTOTAL(109,J2:J{rt - 1})"
        assert ws.cell(row=rt, column=12).value == f"=SUBTOTAL(109,L2:L{rt - 1})"
        assert ws.cell(row=rt, column=16).value == f"=SUBTOTAL(109,P2:P{rt - 1})"
        assert ws.cell(row=rt, column=17).value == f"=SUBTOTAL(109,Q2:Q{rt - 1})"
        assert ws.cell(row=rt, column=18).value == f"=SUBTOTAL(109,R2:R{rt - 1})"

    def test_fila_factura_con_analiticas(self, tmp_db, tmp_path):
        ws = self._wb(tmp_db, tmp_path)["BD_Registro"]
        fila = [c.value for c in ws[2]]
        assert fila[0] == "F204-67375"
        assert isinstance(fila[1], date) and str(fila[1])[:10] == "2026-09-05"
        assert fila[2] == "2026-09"
        assert fila[5] == "01"  # Línea solo código
        assert fila[13] == "P1"  # Pedido
        assert fila[15] == 200.0  # Dev. contra la línea
        assert fila[16] == 896.0 and fila[17] == 0.0  # Bruto/Ajuste
        assert ws["B2"].number_format == "dd-mm-yyyy"
        assert ws["J2"].number_format == "#,##0"
        assert ws["K2"].number_format == "#,##0.00000"
        assert ws["L2"].number_format == "#,##0.00"
        textos = [c.value for r in ws.iter_rows() for c in r if isinstance(c.value, str)]
        assert any("no pegar filas" in t for t in textos)

    def test_paridad_en_el_libro(self, tmp_db, tmp_path):
        wb = self._wb(tmp_db, tmp_path)
        ws = wb["BD_Registro"]
        rt = self._fila_total(ws)
        soles = sum(ws.cell(row=r, column=12).value for r in range(2, rt))
        cant = sum(ws.cell(row=r, column=10).value for r in range(2, rt))
        assert round(soles, 2) == 696.0
        assert round(cant, 2) == 700.0
        # Consolidado bloque A: valores de componentes.
        con = wb["Consolidado"]
        assert [con.cell(row=3, column=c).value for c in (2, 3, 4, 5)] == [
            1016.0,
            -224.0,
            -96.0,
            0.0,
        ]

    def test_export_solo_bd(self, tmp_db, tmp_path):
        df = self._df(tmp_db)
        out = tmp_path / "BD.xlsx"
        rc._escribir_xlsx(out, "56101", "M", df_hechos=df, hojas={"bd"})
        wb = load_workbook(str(out))
        assert wb.sheetnames == ["BD_Registro"]
        assert wb["BD_Registro"]["A1"].value == "Documento"


class TestFacturasHoja:
    """Tabla única por (factura, SKU) con NC asociadas e hipervínculos."""

    def _wb(self, tmp_db, tmp_path, solo_facturas=False):
        TestNotasSku()._poblar_notas(tmp_db)
        cli = VentasDbClient()
        kw = dict(fecha_desde="2026-09-01", fecha_hasta="2026-09-30")
        out = tmp_path / "F.xlsx"
        hojas = {"facturas"} if solo_facturas else {"ajustes", "facturas"}
        rc._escribir_xlsx(
            out,
            "56101",
            "M",
            df_dev=cli.fetch_notas_sku_cliente("00056101", **kw),
            df_huerf=cli.fetch_notas_huerfanas_cliente("00056101", **kw),
            df_det=cli.fetch_facturas_sku_detalle_cliente("00056101", **kw),
            hojas=hojas,
        )
        wb = load_workbook(str(out))
        if solo_facturas:
            assert wb.sheetnames == ["Facturas"]
        return wb

    def test_tabla_unica(self, tmp_db, tmp_path):
        wb = self._wb(tmp_db, tmp_path)
        assert wb.sheetnames == ["Ajustes_NC_NDB", "Facturas"]
        ws = wb["Facturas"]
        assert ws["A1"].value == "DETALLADO POR (FACTURA, SKU)"
        assert [c.value for c in ws[2]] == [
            "Factura",
            "Fecha",
            "Pedido",
            "OC",
            "Sucursal",
            "Línea",
            "SKU",
            "Descripción",
            "Cant.",
            "P.U. Bruto",
            "Total Bruto",
            "Ajuste/NC",
            "Total Neto",
            "Costo Unit. Neto",
            "NC asociadas",
        ]
        assert ws.freeze_panes is None
        assert ws.auto_filter.ref is None  # la tabla filtra sola
        assert list(ws.tables) == ["FacturasDetalle"]
        assert ws.tables["FacturasDetalle"].ref == "A2:O4"
        f1 = [c.value for c in ws[3]]
        assert f1[0] == "F204-67375"
        assert isinstance(f1[1], date)
        assert f1[2] == "P1"
        assert f1[4] == "LIMA"  # sucursal congruente
        assert f1[5] == "01"  # línea canónica sin ruido
        assert f1[6] == "014850"
        assert f1[8] == 800.0 and f1[10] == 896.0
        assert f1[9] == '=IF(I3=0,"—",K3/I3)'
        # SUMIFS acotado al detalle de Ajustes (Q/C, filas 9-15).
        assert f1[11] == (
            "=SUMIFS('Ajustes_NC_NDB'!$Q$9:$Q$15,"
            "'Ajustes_NC_NDB'!$A$9:$A$15,A3,"
            "'Ajustes_NC_NDB'!$C$9:$C$15,G3)"
        )
        assert f1[12] == "=K3+L3"
        assert f1[13] == '=IF(I3=0,"—",M3/I3)'
        f2 = [c.value for c in ws[4]]
        assert f2[0] == "F204-67375" and f2[6] == "014851"
        assert f2[8] == 100.0 and f2[10] == 120.0
        total = [c.value for c in ws[5]]
        assert total[0] == "TOTAL"
        assert total[8] == "=SUBTOTAL(109,I3:I4)"
        assert total[10] == "=SUBTOTAL(109,K3:K4)"
        assert total[11] == "=SUBTOTAL(109,L3:L4)"
        assert total[12] == "=SUBTOTAL(109,M3:M4)"
        assert total[9] == "—" and total[13] == "—"
        assert ("M3:M5", "lessThan") in _cfs(ws)

    def test_nc_hipervinculos(self, tmp_db, tmp_path):
        ws = self._wb(tmp_db, tmp_path)["Facturas"]
        link = ws.cell(row=3, column=15).value
        assert link.startswith("=HYPERLINK(\"#'Ajustes_NC_NDB'!A9\"")
        assert "N204-900010" in link and "N204-900016" in link
        assert not any("#REF!" in str(ws.cell(row=r, column=15).value) for r in (6, 7))

    def test_fallback_sin_ajustes(self, tmp_db, tmp_path):
        ws = self._wb(tmp_db, tmp_path, solo_facturas=True)["Facturas"]
        assert ws.cell(row=3, column=12).value == -361.35
        assert ws.cell(row=4, column=12).value == 0.0
        txt = ws.cell(row=3, column=15).value
        assert isinstance(txt, str) and not txt.startswith("=")

    def test_doc_bruto_negativo(self, tmp_db, tmp_path):
        TestDescuentoEmbebido()._poblar(tmp_db)
        cli = VentasDbClient()
        kw = dict(fecha_desde="2026-09-01", fecha_hasta="2026-09-30")
        out = tmp_path / "FN.xlsx"
        rc._escribir_xlsx(
            out,
            "56101",
            "M",
            df_det=cli.fetch_facturas_sku_detalle_cliente("00056101", **kw),
            hojas={"facturas"},
        )
        ws = load_workbook(str(out))["Facturas"]
        assert ws.cell(row=3, column=11).value == -50.0
        assert ws.cell(row=3, column=13).value == "=K3+L3"


class TestParidad5Hojas:
    """Los valores (no fórmulas) cuadran entre hojas con tol S/ 0.01."""

    def _valores(self, ws, col, hasta_total=True):
        out = []
        # BD starts at row 2 (no banner); other sheets at row 3.
        start = 2 if ws.title == "BD_Registro" else 3
        for r in range(start, ws.max_row + 1):
            if ws.cell(row=r, column=1).value == "TOTAL":
                if hasta_total:
                    break
                continue
            v = ws.cell(row=r, column=col).value
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out.append(v)
        return out

    def test_totales_cruzados(self, tmp_db, tmp_path):
        wb = TestXlsx5Hojas()._todo(tmp_db, tmp_path)
        bd = wb["BD_Registro"]
        neto_bd = round(sum(self._valores(bd, 12)), 2)
        cant_bd = round(sum(self._valores(bd, 10)), 2)
        assert neto_bd == 696.0
        assert cant_bd == 700.0
        # Resumen mensual: columnas de valores (bruta, comps, físicas).
        res = wb["Resumen Ejecutivo"]
        bruta = sum(self._valores(res, 2))
        comps = sum(self._valores(res, 3)) + sum(self._valores(res, 4)) + sum(self._valores(res, 5))
        assert round(bruta + comps, 2) == neto_bd
        ufis = sum(self._valores(res, 7)) + sum(self._valores(res, 8))
        assert round(ufis, 2) == cant_bd
        # Consolidado A: componentes = mismos bucket del Resumen.
        con = wb["Consolidado"]
        assert round(sum(self._valores(con, 2)), 2) == round(bruta, 2)
        # Ajustes detalle: S/ NC (signo) = DEV+DESC+NDB del Resumen.
        aju = wb["Ajustes_NC_NDB"]
        assert round(sum(self._valores(aju, 17)), 2) == round(comps, 2)
        # Facturas (tabla única): Total Bruto = bruta (solo F/B del rango).
        fac = wb["Facturas"]
        assert round(sum(self._valores(fac, 11)), 2) == round(bruta, 2)


class TestSimetriaTitulos:
    """Títulos con el mismo prefijo, chip sin IGV y vocabulario único."""

    def _wb(self, tmp_db, tmp_path):
        return TestXlsx5Hojas()._todo(tmp_db, tmp_path)

    def test_todos_los_titulos_con_prefijo(self, tmp_db, tmp_path):
        wb = self._wb(tmp_db, tmp_path)
        visibles = [ws for ws in wb.worksheets if ws.sheet_state != "hidden"]
        assert visibles, "sin hojas visibles"
        operativas_planas = {"Sucursales", "Sucursal_Linea_Mes", "Sucursal_SKU_Mes"}
        for ws in visibles:
            if ws.title in operativas_planas:
                assert ws["A1"].value in {"Orden Pareto", "Mes"}, ws.title
                assert "COMPRAS NETAS" not in str(ws["A1"].value)
                continue
            if ws.title == "Resumen Ejecutivo":
                t = ws["A1"].value
                assert isinstance(t, str) and t.startswith("COMPRAS NETAS —"), (
                    f"{ws.title}: título asimétrico {t!r}"
                )
                assert "importes sin IGV" in str(ws["A2"].value), ws.title
                continue
            # Operativas limpias: título en print-header, no en A1.
            assert ws.oddHeader.left.text, f"{ws.title}: sin header"
            assert "COMPRAS NETAS" in ws.oddHeader.left.text, ws.title
            # A1 es el subtítulo o header de tabla, no el banner.
            assert "COMPRAS NETAS" not in str(ws["A1"].value), ws.title

    def test_vocabulario_sin_variantes(self, tmp_db, tmp_path):
        wb = self._wb(tmp_db, tmp_path)
        malos = {
            "P. Unit.",
            "S/ Total",
            "U.fact",
            "U.dev",
            "U.netas",
            "% dev",
            "SOLES NETOS (S/)",
            "DEV S/",
            "DESC S/",
            "DEV",
            "DESC",
            "Tabla de hechos",
            "SKU General",
            "Por Pedido",
            "Documentos",
            "Devoluciones",
            "HISTORICO",
            "COMPARATIVO",
            "Configuracion",
            "DIARIO",
        }
        for ws in wb.worksheets:
            for r in ws.iter_rows(min_row=1, max_row=12):
                for c in r:
                    if isinstance(c.value, str):
                        assert c.value not in malos, f"{ws.title}!{c.coordinate}: {c.value!r}"

    def test_encabezados_por_hoja(self, tmp_db, tmp_path):
        wb = self._wb(tmp_db, tmp_path)
        assert [c.value for c in wb["Consolidado"][2]][:2] == ["Línea", "Bruta S/"]
        assert [c.value for c in wb["Facturas"][2]][:2] == ["Factura", "Fecha"]
        assert [c.value for c in wb["Sucursales"][1]][:4] == [
            "Orden Pareto",
            "Código sucursal",
            "Sucursal",
            "Tipo sucursal",
        ]
        assert [c.value for c in wb["Sucursal_Linea_Mes"][1]][:6] == [
            "Mes",
            "Código sucursal",
            "Sucursal",
            "Tipo sucursal",
            "Código línea",
            "Línea",
        ]
        assert [c.value for c in wb["Sucursal_SKU_Mes"][1]][:8] == [
            "Mes",
            "Código sucursal",
            "Sucursal",
            "Tipo sucursal",
            "Código SKU",
            "Artículo",
            "Código línea",
            "Línea",
        ]


class TestMetricasFisicasEconomicas:
    """§22 (1-6): DEV mueve S/ y unidades; DESC/NDB solo S/.

    Incluye descuento legacy CON cantidad (+175): sin la guarda física
    inflaría las unidades netas.
    """

    def _poblar(self, tmp_db):
        from src.core.xls_processor import derivar_campos

        base = dict(
            id_articulo="014850",
            original_sku="014850",
            nom_articulo="PELOTA PVC #5",
            id_linea="0101",
            nom_linea="GASEOSAS",
            id_grupo="01",
            nom_grupo="G",
            id_tipo="01",
            nom_tipo="T",
            id_familia="01",
            nom_familia="F",
            id_cliente="00056101",
            doc_cliente="20601024714",
            nom_cliente="MULTICOPIAS MARY E.I.R.L.",
            tpo_doc="F01",
            serie_doc="204",
            nro_doc="67375",
            referencia="",
            moneda="Soles",
            cantidad=100.0,
            cantidad_fae=100.0,
            soles=1000.0,
            dolares=0.0,
            precio_unitario=10.0,
            anho=2026,
            mes=9,
            fecha_orig="2026-09-05",
            fecha_ref=None,
            fecha_venc=None,
            cod_sucursal="01",
            nom_sucursal="LIMA",
            departamento="LIMA",
            provincia="LIMA",
            distrito="SAN ISIDRO",
            id_vendedor="01188",
            nom_vendedor="VEND",
            id_pedido="P1",
            file_source="t",
            mes_ref="2026-09",
            tipo_operacion="",
            factura_ref_serie="",
            factura_ref_nro="",
            folio_unico="",
        )
        dev = dict(
            base,
            tpo_doc="NCR",
            serie_doc="N204",
            nro_doc="900010",
            referencia="F01/204-67375",
            cantidad=-10.0,
            cantidad_fae=-10.0,
            soles=-100.0,
            fecha_orig="2026-09-10",
            folio_unico="",
        )
        desc = dict(
            base,
            tpo_doc="NCR",
            serie_doc="N204",
            nro_doc="900011",
            referencia="F01/204-67375",
            cantidad=175.0,
            cantidad_fae=-5.0,
            soles=-20.0,
            fecha_orig="2026-09-12",
            folio_unico="",
        )
        ndb = dict(
            base,
            tpo_doc="NDB",
            serie_doc="N204",
            nro_doc="900012",
            referencia="F01/204-67375",
            cantidad=3.0,
            cantidad_fae=0.5,
            soles=30.0,
            fecha_orig="2026-09-15",
            folio_unico="",
        )
        rows = []
        for v in (base, dev, desc, ndb):
            d = dict(v)
            derivar_campos(d)
            rows.append(d)
        assert rows[2]["tipo_operacion"] == "ajuste_valor"  # cant>0 sigue ajuste
        conn = tmp_db.get_conn()
        ventas_db.insert_ventas(conn, rows)

    def _sums(self, tmp_db, **kw):
        self._poblar(tmp_db)
        df = VentasDbClient().fetch_compras_cliente("00056101", **kw)
        return round(float(df["SOLES"].sum()), 2), float(df["CANTIDAD"].sum())

    def test_dev_reduce_unidades_y_soles(self, tmp_db):
        soles, cant = self._sums(tmp_db, incluir_nc=True)
        assert soles == 910.0  # 1000 − 100 − 20 + 30
        assert cant == 90.0  # 100 − 10 (desc/ndb no mueven unidades)

    def test_desc_con_cantidad_no_mueve_unidades(self, tmp_db):
        soles, cant = self._sums(tmp_db, incluir_nc=True)
        assert cant == 90.0  # +175 del descuento ignorado
        assert soles == 910.0  # pero sus soles sí restan

    def test_ndb_no_mueve_unidades(self, tmp_db):
        soles, cant = self._sums(tmp_db, incluir_nc=True)
        assert cant == 90.0  # +3 del NDB ignorado
        assert soles == 910.0  # pero sus soles sí suman

    def test_bruta_sin_nc(self, tmp_db):
        soles, cant = self._sums(tmp_db, incluir_nc=False)
        assert (soles, cant) == (1000.0, 100.0)

    def test_sin_devoluciones(self, tmp_db):
        soles, cant = self._sums(tmp_db, incluir_nc=True, excluir_devoluciones=True)
        assert soles == 1010.0  # 1000 − 20 + 30
        assert cant == 100.0


# ── referencias circulares ──────────────────────────────────────────

_RE_CELDA = re.compile(
    r"(?:(?:'(?P<q>[^']+)'|(?P<u>[A-Za-z_][A-Za-z0-9_.]*))!)?"
    r"(?P<a>\$?[A-Z]{1,3}\$?[1-9][0-9]*)"
    r"(?::(?P<b>\$?[A-Z]{1,3}\$?[1-9][0-9]*))?"
)
_RE_COLUMNA = re.compile(
    r"(?:(?:'(?P<q>[^']+)'|(?P<u>[A-Za-z_][A-Za-z0-9_.]*))!)?"
    r"\$?(?P<a>[A-Z]{1,3})\$?:\$?(?P<b>[A-Z]{1,3})\$?"
)


def _refs(formula, hoja, wb):
    """Celdas (hoja, coordenada) que lee una fórmula, sin literales de texto."""
    limpio = re.sub(r'"[^"]*"', "", formula[1:])
    out = set()
    for m in _RE_CELDA.finditer(limpio):
        h = m.group("q") or m.group("u") or hoja
        a = m.group("a").replace("$", "")
        b = (m.group("b") or "").replace("$", "")
        if not b:
            out.add((h, a))
            continue
        f1, c1 = coordinate_to_tuple(a)
        f2, c2 = coordinate_to_tuple(b)
        if abs(f1 - f2) * abs(c1 - c2) > 20000:
            continue
        for rr in range(min(f1, f2), max(f1, f2) + 1):
            for cc in range(min(c1, c2), max(c1, c2) + 1):
                out.add((h, f"{get_column_letter(cc)}{rr}"))
    for m in _RE_COLUMNA.finditer(limpio):
        h = m.group("q") or m.group("u") or hoja
        if h not in wb.sheetnames:
            continue
        c1 = column_index_from_string(m.group("a"))
        c2 = column_index_from_string(m.group("b"))
        for cc in range(min(c1, c2), max(c1, c2) + 1):
            for rr in range(1, wb[h].max_row + 1):
                out.add((h, f"{get_column_letter(cc)}{rr}"))
    return out


def _ciclos(wb):
    """Ciclos del grafo de dependencias: son las referencias circulares."""
    deps = {}
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                if isinstance(c.value, str) and c.value.startswith("="):
                    deps[(ws.title, c.coordinate)] = _refs(c.value, ws.title, wb)
    estado, camino, hallados = {}, [], []

    def dfs(n):
        estado[n] = 1
        camino.append(n)
        for m in sorted(deps.get(n, ())):
            if estado.get(m) == 1:
                hallados.append(tuple(camino[camino.index(m) :] + [m]))
            elif estado.get(m) is None:
                dfs(m)
        camino.pop()
        estado[n] = 2

    for n in sorted(deps):
        if estado.get(n) is None:
            dfs(n)
    return hallados


class TestSinReferenciasCirculares:
    """Excel avisa si una fórmula (directa o indirecta) depende de sí misma."""

    def test_libro_completo_sin_ciclos(self, tmp_db, tmp_path):
        wb = TestXlsx5Hojas()._todo(tmp_db, tmp_path)
        ciclos = _ciclos(wb)
        assert not ciclos, "referencia circular: " + " | ".join(
            " → ".join(f"{h}!{c}" for h, c in ciclo) for ciclo in ciclos[:5]
        )

    def test_neto_de_fila_suma_solo_componentes(self, tmp_db, tmp_path):
        """Regresión: el Neto por línea no se incluye en su propio SUM."""
        wb = TestXlsx5Hojas()._todo(tmp_db, tmp_path)
        ws = wb["Consolidado"]
        assert ws.cell(row=2, column=6).value == "Neto S/"
        assert ws.cell(row=3, column=6).value == "=SUM(B3:E3)"

    def test_cabecera_ajustes_con_sumifs(self, tmp_db, tmp_path):
        TestNotasSku()._poblar_notas(tmp_db)
        cli = VentasDbClient()
        kw = dict(fecha_desde="2026-09-01", fecha_hasta="2026-09-30")
        out = tmp_path / "S.xlsx"
        rc._escribir_xlsx(
            out,
            "56101",
            "M",
            df_dev=cli.fetch_notas_sku_cliente("00056101", **kw),
            hojas={"ajustes"},
        )
        wb = load_workbook(str(out))
        assert not _ciclos(wb)
