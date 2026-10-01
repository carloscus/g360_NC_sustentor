"""Tienda real = (cliente, sucursal): catálogo, filtros y % por tienda.

ACUMULADO sin código es 'principal' si su cliente tiene otras sucursales
(es su matriz) o 'unica' si no tiene ninguna: nunca se descarta.
"""

import pytest

from src.core import ventas_db
from src.core.ventas_db_client import VentasDbClient

CLI_A = "00006121"
CLI_B = "00009999"


def _fila(
    nro,
    cliente=CLI_A,
    cod="01",
    nom="SANTA ROSA",
    ubigeo="150103",
    distrito="ATE",
    soles=100.0,
    fecha="2026-09-10",
):
    return dict(
        id_articulo="000123",
        original_sku="000123",
        nom_articulo="GASEOSA",
        id_linea="01",
        nom_linea="GASEOSAS",
        id_grupo="01",
        nom_grupo="G",
        id_tipo="01",
        nom_tipo="T",
        id_familia="01",
        nom_familia="F",
        id_cliente=cliente,
        doc_cliente="20100047218",
        nom_cliente="CLIENTE " + cliente,
        tpo_doc="F01",
        serie_doc="001",
        nro_doc=nro,
        referencia="",
        moneda="Soles",
        cantidad=10.0,
        cantidad_fae=0.0,
        soles=soles,
        dolares=0.0,
        precio_unitario=soles / 10.0,
        anho=2026,
        mes=9,
        fecha_orig=fecha,
        fecha_ref=None,
        fecha_venc="2026-10-20",
        cod_sucursal=cod,
        nom_sucursal=nom,
        departamento="LIMA",
        provincia="LIMA",
        distrito=distrito,
        id_vendedor="178",
        nom_vendedor="MILCA",
        id_pedido="P1",
        ord_compra="",
        file_source="test",
        mes_ref="2026-09",
        tipo_operacion="",
        factura_ref_serie="",
        factura_ref_nro="",
        folio_unico=f"F01/001-{nro}",
        id_ubigeo=ubigeo,
        estado_linea="",
        canal_distribucion="",
        id_guia="",
        nom_condicion_pago="CONTADO",
        division="CIPTECH",
        fec_cargo="",
    )


def _poblar(tmp_db):
    conn = tmp_db.get_conn()
    ventas_db.insert_ventas(
        conn,
        [
            _fila("1", cod="01", nom="SANTA ROSA", soles=300.0, fecha="2026-09-10"),
            _fila(
                "2",
                cod="02",
                nom="ANTONIO LORENA",
                ubigeo="080106",
                distrito="SANTIAGO",
                soles=100.0,
                fecha="2026-09-11",
            ),
            _fila(
                "3",
                cod="",
                nom="ACUMULADO",
                ubigeo="",
                distrito="",
                soles=600.0,
                fecha="2026-09-12",
            ),
            _fila(
                "4",
                cliente=CLI_B,
                cod="",
                nom="ACUMULADO",
                ubigeo="",
                distrito="",
                soles=500.0,
                fecha="2026-09-13",
            ),
        ],
    )
    return conn


class TestIndiceCompuesto:
    def test_init_db_lo_crea(self, tmp_db):
        conn = tmp_db.get_conn()
        idx = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'ventas'"
            ).fetchall()
        }
        assert "idx_venta_cliente_sucursal" in idx

    def test_explain_busca_por_par(self, tmp_db):
        conn = tmp_db.get_conn()
        plan = " | ".join(
            r[3]
            for r in conn.execute(
                "EXPLAIN QUERY PLAN SELECT COUNT(*) FROM ventas "
                "WHERE id_cliente = ? AND cod_sucursal = ?",
                (CLI_A, "01"),
            ).fetchall()
        )
        assert "idx_venta_cliente_sucursal" in plan, plan
        assert "SCAN" not in plan, plan


class TestCategoria:
    @pytest.mark.parametrize(
        "nom,cod,otras,esperada",
        [
            ("SANTA ROSA", "01", False, "sucursal"),
            ("ACUMULADO", "", True, "principal"),
            ("ACUMULADO", "", False, "unica"),
            ("acumulado", "", True, "principal"),
            ("OTRO", "", False, "sin_dato"),
            ("", "", False, "sin_dato"),
            (None, None, False, "sin_dato"),
        ],
    )
    def test_casos(self, nom, cod, otras, esperada):
        assert ventas_db.categoria_sucursal(nom, cod, otras) == esperada


class TestCatalogo:
    def test_pares_y_moda(self, tmp_db):
        _poblar(tmp_db)
        cat = {
            (r["id_cliente"], r["cod_sucursal"]): r
            for r in ventas_db.distinct_sucursales(force=True)
        }
        assert (CLI_A, "01") in cat and (CLI_A, "02") in cat
        assert (CLI_A, "") in cat  # ACUMULADO también cataloga
        assert (CLI_B, "") in cat
        assert cat[(CLI_A, "01")]["nom_sucursal"] == "SANTA ROSA"
        assert cat[(CLI_A, "01")]["filas"] == 1
        assert cat[(CLI_A, "01")]["soles"] == 300.0
        assert cat[(CLI_A, "01")]["id_ubigeo"] == "150103"

    def test_nombre_por_moda(self, tmp_db):
        conn = tmp_db.get_conn()
        ventas_db.insert_ventas(
            conn,
            [
                _fila("10", cod="05", nom="TIENDA X", fecha="2026-09-14"),
                _fila("11", cod="05", nom="TIENDA X", fecha="2026-09-15"),
                _fila("12", cod="05", nom="TIENDA Y", fecha="2026-09-16"),
            ],
        )
        cat = {
            (r["id_cliente"], r["cod_sucursal"]): r
            for r in ventas_db.distinct_sucursales(force=True)
        }
        assert cat[(CLI_A, "05")]["nom_sucursal"] == "TIENDA X"
        assert cat[(CLI_A, "05")]["filas"] == 3

    def test_insercion_invalida_cache(self, tmp_db):
        assert (CLI_A, "09") not in {
            (r["id_cliente"], r["cod_sucursal"]) for r in ventas_db.distinct_sucursales(force=True)
        }
        conn = tmp_db.get_conn()
        ventas_db.insert_ventas(conn, [_fila("20", cod="09", nom="NUEVA", fecha="2026-09-17")])
        assert (CLI_A, "09") in {
            (r["id_cliente"], r["cod_sucursal"]) for r in ventas_db.distinct_sucursales()
        }


class TestFiltrosTienda:
    def test_par_cliente_sucursal(self, tmp_db):
        _poblar(tmp_db)
        df = VentasDbClient().fetch_historial(sucursal_cliente=[(CLI_A, "02")])
        assert len(df) == 1
        assert df.iloc[0]["DOC_ID"] == "F001-2"

    def test_por_nombre_normalizado(self, tmp_db):
        _poblar(tmp_db)
        df = VentasDbClient().fetch_historial(nombres_sucursal=["  santa rosa "])
        assert len(df) == 1
        assert df.iloc[0]["DOC_ID"] == "F001-1"

    def test_por_ubigeo_y_distrito(self, tmp_db):
        _poblar(tmp_db)
        df = VentasDbClient().fetch_historial(id_ubigeos=["080106"])
        assert len(df) == 1
        assert df.iloc[0]["DOC_ID"] == "F001-2"
        df = VentasDbClient().fetch_historial(distritos=["ATE"])
        assert len(df) == 1
        assert df.iloc[0]["DOC_ID"] == "F001-1"


class TestDistribucion:
    def test_porcentajes_y_categorias(self, tmp_db):
        _poblar(tmp_db)
        df = VentasDbClient().distribucion_por_tienda(
            id_cliente=CLI_A, fecha_desde="2026-09-01", fecha_hasta="2026-09-30"
        )
        assert len(df) == 3
        por_cod = {r["COD_SUCURSAL"]: r for _, r in df.iterrows()}
        assert por_cod["01"]["PCT_CLIENTE"] == pytest.approx(30.0)
        assert por_cod["02"]["PCT_CLIENTE"] == pytest.approx(10.0)
        assert por_cod[""]["PCT_CLIENTE"] == pytest.approx(60.0)
        assert por_cod["01"]["CATEGORIA"] == "sucursal"
        assert por_cod[""]["CATEGORIA"] == "principal"
        assert por_cod[""]["NOM_SUCURSAL"] == "ACUMULADO"
        assert df["PCT_CLIENTE"].sum() == pytest.approx(100.0)

    def test_acumulado_unico(self, tmp_db):
        _poblar(tmp_db)
        df = VentasDbClient().distribucion_por_tienda(
            id_cliente=CLI_B, fecha_desde="2026-09-01", fecha_hasta="2026-09-30"
        )
        assert len(df) == 1
        assert df.iloc[0]["CATEGORIA"] == "unica"
        assert df.iloc[0]["PCT_CLIENTE"] == pytest.approx(100.0)

    def test_rango_acota(self, tmp_db):
        _poblar(tmp_db)
        df = VentasDbClient().distribucion_por_tienda(
            id_cliente=CLI_A, fecha_desde="2026-09-10", fecha_hasta="2026-09-10"
        )
        assert len(df) == 1
        assert df.iloc[0]["COD_SUCURSAL"] == "01"


class TestAnalisisMensual:
    def test_pareto_detalles_y_acumulado_principal(self, tmp_db):
        _poblar(tmp_db)
        out = VentasDbClient().fetch_analisis_sucursales_cliente(
            CLI_A, fecha_desde="2026-09-01", fecha_hasta="2026-09-30", solo_lineas_activas=False
        )
        pareto = out["pareto"]
        por_cod = {r["COD_SUCURSAL"]: r for _, r in pareto.iterrows()}
        assert len(pareto) == 3
        assert por_cod["01"]["PCT_BRUTA"] == pytest.approx(0.30)
        assert por_cod["02"]["PCT_BRUTA"] == pytest.approx(0.10)
        assert por_cod[""]["PCT_BRUTA"] == pytest.approx(0.60)
        assert por_cod[""]["TIPO_SUCURSAL"] == "principal"
        assert out["linea_mes"]["SOLES"].sum() == pytest.approx(1000.0)
        assert out["sku_mes"]["SOLES"].sum() == pytest.approx(1000.0)
        assert set(out["sku_mes"]["MES_REF"]) == {"2026-09"}

    def test_acumulado_unico_y_rango_mes(self, tmp_db):
        _poblar(tmp_db)
        out = VentasDbClient().fetch_analisis_sucursales_cliente(
            CLI_B, fecha_desde="2026-09-01", fecha_hasta="2026-09-30", solo_lineas_activas=False
        )
        assert len(out["pareto"]) == 1
        assert out["pareto"].iloc[0]["TIPO_SUCURSAL"] == "unica"
        assert out["pareto"].iloc[0]["PCT_ACUMULADO"] == pytest.approx(1.0)
        assert set(out["linea_mes"]["MES_REF"]) == {"2026-09"}
