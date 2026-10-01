"""Campos críticos (O/C, sucursal, división, vencimiento, condición de pago).

Red de seguridad anti-O/C: cobertura + índice por campo (auditor),
filtros habilitados en fetch_historial (kwargs opcionales, sin UI) y
EXPLAIN que usa índice en vez de escaneo completo.
"""

import sqlite3

from src.core import ventas_db
from src.core.ventas_db_client import VentasDbClient

INDICES_ESPERADOS = {
    "division": "idx_venta_division",
    "nom_condicion_pago": "idx_venta_condicion",
    "cod_sucursal": "idx_venta_sucursal",
    "fecha_venc": "idx_venta_venc",
    "ord_compra": "idx_venta_oc",
}


def _fila(**kw):
    base = dict(
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
        id_cliente="00002482",
        doc_cliente="20100047218",
        nom_cliente="TAI LOY",
        tpo_doc="F01",
        serie_doc="001",
        nro_doc="1",
        referencia="",
        moneda="Soles",
        cantidad=10.0,
        cantidad_fae=0.0,
        soles=250.0,
        dolares=0.0,
        precio_unitario=25.0,
        anho=2026,
        mes=9,
        fecha_orig="2026-09-20",
        fecha_ref=None,
        fecha_venc="2026-10-20",
        cod_sucursal="01",
        nom_sucursal="LIMA",
        departamento="LIMA",
        provincia="LIMA",
        distrito="MIRAFLORES",
        id_vendedor="178",
        nom_vendedor="MILCA",
        id_pedido="P1",
        ord_compra="11457",
        file_source="test",
        mes_ref="2026-09",
        tipo_operacion="",
        factura_ref_serie="",
        factura_ref_nro="",
        folio_unico="F01/001-1",
        id_ubigeo="",
        estado_linea="",
        canal_distribucion="",
        id_guia="",
        nom_condicion_pago="FACTURA 30 DIAS",
        division="CIPTECH",
        fec_cargo="",
    )
    base.update(kw)
    return base


def _insertar(tmp_db, filas):
    conn = tmp_db.get_conn()
    ventas_db.insert_ventas(conn, filas)
    return conn


class TestIndices:
    def test_init_db_crea_indices_campos(self, tmp_db):
        conn = tmp_db.get_conn()
        idx = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'ventas'"
            ).fetchall()
        }
        for campo, nombre in INDICES_ESPERADOS.items():
            assert nombre in idx, f"falta {nombre} para {campo}"

    def test_explain_usa_indice_no_scan(self, tmp_db):
        conn = tmp_db.get_conn()
        casos = [
            ("division", "CIPTECH"),
            ("nom_condicion_pago", "X"),
            ("cod_sucursal", "01"),
            ("fecha_venc", "2026-10-20"),
            ("ord_compra", "11457"),
        ]
        for campo, val in casos:
            plan = " | ".join(
                r[3]
                for r in conn.execute(
                    f"EXPLAIN QUERY PLAN SELECT COUNT(*) FROM ventas WHERE {campo} = ?", (val,)
                ).fetchall()
            )
            assert INDICES_ESPERADOS[campo] in plan, plan
            assert "SCAN" not in plan, plan


class TestAuditor:
    def test_todo_ok_menos_nom_sucursal(self, tmp_db):
        _insertar(tmp_db, [_fila(nro_doc="1"), _fila(nro_doc="2")])
        conn = tmp_db.get_conn()
        rep = {r["campo"]: r for r in ventas_db.auditar_campos_criticos(conn)}
        assert {r["campo"] for r in rep.values()} == {
            "ord_compra",
            "cod_sucursal",
            "nom_sucursal",
            "division",
            "fecha_venc",
            "nom_condicion_pago",
        }
        for campo in ("ord_compra", "cod_sucursal", "division", "fecha_venc", "nom_condicion_pago"):
            assert rep[campo]["veredicto"] == "ok", (campo, rep[campo])
            assert rep[campo]["tiene_indice"] is True
            assert rep[campo]["cobertura"] == 1.0
        # nom_sucursal: 100% cobertura pero sin índice todavía (a demanda).
        assert rep["nom_sucursal"]["veredicto"] == "sin_indice"
        assert rep["nom_sucursal"]["tiene_indice"] is False

    def test_detecta_campo_degradado(self, tmp_db):
        _insertar(tmp_db, [_fila(nro_doc="1", division=""), _fila(nro_doc="2", division="")])
        conn = tmp_db.get_conn()
        rep = {r["campo"]: r for r in ventas_db.auditar_campos_criticos(conn)}
        assert rep["division"]["veredicto"] == "degradado"
        assert rep["division"]["cobertura"] == 0.0

    def test_detecta_columna_perdida(self, tmp_path):
        # Una captura que ni trae la columna: veredicto 'perdido'.
        conn = sqlite3.connect(str(tmp_path / "mini.db"))
        conn.execute("CREATE TABLE ventas (id INTEGER PRIMARY KEY, ord_compra TEXT)")
        rep = {r["campo"]: r for r in ventas_db.auditar_campos_criticos(conn)}
        assert rep["division"]["veredicto"] == "perdido"
        assert rep["division"]["tiene_indice"] is False
        assert rep["ord_compra"]["veredicto"] == "degradado"
        conn.close()


class TestFiltrosHabilitados:
    def _poblar(self, tmp_db):
        _insertar(
            tmp_db,
            [
                _fila(
                    nro_doc="1",
                    folio_unico="F01/001-1",
                    division="CIPTECH",
                    nom_condicion_pago="FACTURA 30 DIAS",
                    cod_sucursal="01",
                    fecha_venc="2026-10-20",
                    ord_compra="11457",
                ),
                _fila(
                    nro_doc="2",
                    folio_unico="F01/001-2",
                    division="CONSUMO MASIVO",
                    nom_condicion_pago="CONTADO",
                    cod_sucursal="02",
                    fecha_venc="2026-11-05",
                    ord_compra="999",
                ),
            ],
        )

    def test_defaults_sin_filtrar(self, tmp_db):
        self._poblar(tmp_db)
        df = VentasDbClient().fetch_historial(id_cliente="00002482")
        assert len(df) == 2

    def test_division(self, tmp_db):
        self._poblar(tmp_db)
        df = VentasDbClient().fetch_historial(id_cliente="00002482", divisiones=["CIPTECH"])
        assert len(df) == 1
        assert df.iloc[0]["DOC_ID"] == "F001-1"

    def test_condicion_y_sucursal(self, tmp_db):
        self._poblar(tmp_db)
        df = VentasDbClient().fetch_historial(
            id_cliente="00002482", condiciones_pago=["CONTADO"], sucursales=["02"]
        )
        assert len(df) == 1
        assert df.iloc[0]["DOC_ID"] == "F001-2"

    def test_rango_vencimiento(self, tmp_db):
        self._poblar(tmp_db)
        df = VentasDbClient().fetch_historial(
            id_cliente="00002482", fecha_venc_desde="2026-10-01", fecha_venc_hasta="2026-10-31"
        )
        assert len(df) == 1
        assert df.iloc[0]["DOC_ID"] == "F001-1"

    def test_lista_vacia_es_sin_filtro(self, tmp_db):
        # Misma convención que id_pedidos/ordenes: [] = no filtrar.
        self._poblar(tmp_db)
        df = VentasDbClient().fetch_historial(id_cliente="00002482", divisiones=[])
        assert len(df) == 2
