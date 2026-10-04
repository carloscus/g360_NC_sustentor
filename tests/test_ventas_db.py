"""Tests de la DB local: schema, insercion, dedup, vistas y cliente de lectura."""

import sqlite3

import pytest

from src.core import ventas_db
from src.core.ventas_db_client import VentasDbClient


class TestSchema:
    def test_init_db_crea_tablas_y_vistas(self, tmp_db):
        conn = sqlite3.connect(f"file:{ventas_db.db_path()}?mode=ro", uri=True)
        try:
            tablas = {
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            vistas = {
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='view'")
            }
        finally:
            conn.close()
        assert {
            "ventas",
            "sync_log",
            "mes_checksums",
            "audit_log",
            "stats_cache",
            "nc_asociadas",
        } <= tablas
        assert {
            "vw_dim_cliente",
            "vw_dim_articulo",
            "vw_documento",
            "vw_devoluciones",
            "vw_facturas_disponibles",
        } <= vistas

    def test_wal_mode(self, tmp_db):
        conn = ventas_db.get_conn()
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"

    def test_readonly_no_crea_db(self, tmp_path, monkeypatch):
        # data dir vacio aislado: get_read_conn debe fallar (sin crear archivo)
        monkeypatch.setenv("G360_DATA_DIR", str(tmp_path / "vacio"))
        with pytest.raises(FileNotFoundError):
            ventas_db.get_read_conn()
        assert not ventas_db.db_exists()

    def test_init_db_asegura_dim(self, tmp_db):
        # Las dim_* vienen del archivo del origen; si falta alguna, init_db
        # la crea vacía en vez de romper vistas y validadores.
        conn = ventas_db.get_conn()
        conn.execute("DROP TABLE IF EXISTS dim_vendedor")
        conn.commit()
        ventas_db.init_db(conn)
        tablas = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert {
            "dim_articulo",
            "dim_cliente",
            "dim_documento",
            "dim_linea",
            "dim_ruc",
            "dim_vendedor",
        } <= tablas
        # Y no toca las que existen con datos.
        conn.execute("INSERT INTO dim_vendedor (id_vendedor, nom_vendedor) VALUES ('178','X')")
        conn.commit()
        ventas_db.init_db(conn)
        assert (
            conn.execute(
                "SELECT nom_vendedor FROM dim_vendedor WHERE id_vendedor='178'"
            ).fetchone()[0]
            == "X"
        )


class TestCompletitud:
    def _fila(self, **over):
        from src.core.xls_processor import derivar_campos

        v = dict(
            id_articulo="02211",
            original_sku="02211",
            nom_articulo="X",
            id_linea="01",
            nom_linea="G",
            id_grupo="01",
            nom_grupo="G",
            id_tipo="01",
            nom_tipo="T",
            id_familia="01",
            nom_familia="F",
            id_cliente="00068414",
            doc_cliente="20100047218",
            nom_cliente="DEMO",
            tpo_doc="F01",
            serie_doc="001",
            nro_doc="1",
            referencia="",
            moneda="Soles",
            cantidad=10.0,
            cantidad_fae=0.0,
            soles=100.0,
            dolares=0.0,
            precio_unitario=10.0,
            anho=2024,
            mes=1,
            fecha_orig="2024-01-15",
            fecha_ref=None,
            fecha_venc=None,
            cod_sucursal="01",
            nom_sucursal="L",
            departamento="LIMA",
            provincia="LIMA",
            distrito="LIMA",
            id_vendedor="178",
            nom_vendedor="MILCA",
            id_pedido="P1",
            ord_compra="",
            file_source="t",
            mes_ref="2024-01",
            tipo_operacion="venta",
            factura_ref_serie="",
            factura_ref_nro="",
            folio_unico="",
            id_ubigeo="150101",
            estado_linea="LINEA TRADICIONAL",
            canal_distribucion="MAYORISTA",
            id_guia="G1",
            nom_condicion_pago="CONTADO",
            division="CONSUMO MASIVO",
            fec_cargo="",
        )
        v.update(over)
        derivar_campos(v)
        return v

    def _insertar(self, conn, filas):
        cols = ventas_db.INSERT_COLS.split(",")
        sql = f"INSERT INTO ventas ({ventas_db.INSERT_COLS}) VALUES ({','.join('?' * len(cols))})"
        for v in filas:
            conn.execute(sql, [v.get(c) for c in cols])
        conn.commit()

    def test_reparar_nombres_vendedor(self, tmp_db):
        conn = ventas_db.get_conn()
        conn.execute("INSERT INTO dim_vendedor (id_vendedor, nom_vendedor) VALUES ('178','MILCA')")
        self._insertar(
            conn, [self._fila(nom_vendedor=""), self._fila(nro_doc="2", nom_vendedor="YA")]
        )
        assert ventas_db.reparar_nombres_vendedor(conn) == 1
        assert (
            conn.execute("SELECT nom_vendedor FROM ventas WHERE nro_doc='1'").fetchone()[0]
            == "MILCA"
        )
        assert ventas_db.reparar_nombres_vendedor(conn) == 0

    def test_reparar_no_inventa_si_maestro_vacio(self, tmp_db):
        conn = ventas_db.get_conn()
        conn.execute("INSERT INTO dim_vendedor (id_vendedor, nom_vendedor) VALUES ('M10','')")
        self._insertar(conn, [self._fila(id_vendedor="M10", nom_vendedor="")])
        assert ventas_db.reparar_nombres_vendedor(conn) == 0
        assert conn.execute("SELECT nom_vendedor FROM ventas").fetchone()[0] == ""

    def test_malformado_respeta_historico_en_maestro(self, tmp_db):
        conn = ventas_db.get_conn()
        conn.execute("INSERT INTO dim_vendedor (id_vendedor, nom_vendedor) VALUES ('54','HIST')")
        self._insertar(conn, [self._fila(id_vendedor="54", nom_vendedor="HIST")])
        r = ventas_db.auditar_completidad(conn)
        assert "vendedor_malformado" not in {h["regla"] for h in r["hallazgos"]}

    def test_limpiar_doc_cliente(self, tmp_db):
        conn = ventas_db.get_conn()
        self._insertar(
            conn,
            [
                self._fila(nro_doc="1", doc_cliente="< INGRESE DNI >"),
                self._fila(nro_doc="2", doc_cliente=".46138563"),
                self._fila(nro_doc="3", doc_cliente="NIT574619"),
                self._fila(nro_doc="4", doc_cliente="20100047218"),
            ],
        )
        r = ventas_db.limpiar_doc_cliente(conn)
        assert r["limpiados"] == 2
        vals = dict(conn.execute("SELECT nro_doc, doc_cliente FROM ventas").fetchall())
        assert vals == {"1": "", "2": "46138563", "3": "NIT574619", "4": "20100047218"}

    def test_ruc_compartidos_solo_reporte(self, tmp_db):
        conn = ventas_db.get_conn()
        self._insertar(
            conn,
            [
                self._fila(nro_doc="1", id_cliente="A", doc_cliente="X"),
                self._fila(nro_doc="2", id_cliente="B", doc_cliente="X"),
                self._fila(nro_doc="3", id_cliente="C", doc_cliente="S/N"),
            ],
        )
        rep = ventas_db.reportar_ruc_compartidos(conn)
        assert len(rep) == 1 and rep[0]["doc"] == "X"
        assert {c[0] for c in rep[0]["clientes"]} == {"A", "B"}

    def test_auditar_completidad_sana(self, tmp_db):
        conn = ventas_db.get_conn()
        conn.execute("INSERT INTO dim_vendedor (id_vendedor, nom_vendedor) VALUES ('178','MILCA')")
        self._insertar(conn, [self._fila()])
        r = ventas_db.auditar_completidad(conn)
        assert r["ok"] is True and r["hallazgos"] == [] and r["filas"] == 1

    def test_auditar_completidad_detecta(self, tmp_db):
        conn = ventas_db.get_conn()
        conn.execute("INSERT INTO dim_vendedor (id_vendedor, nom_vendedor) VALUES ('178','MILCA')")
        self._insertar(
            conn,
            [
                self._fila(nro_doc="1"),
                self._fila(nro_doc="2", precio_unitario=999.0),
                self._fila(nro_doc="3", nom_vendedor=""),
                self._fila(nro_doc="4", id_vendedor="78"),
                self._fila(nro_doc="5", doc_cliente="S/N"),
            ],
        )
        conn.execute("UPDATE ventas SET folio_unico = 'ROTO' WHERE nro_doc = '1'")
        conn.commit()
        r = ventas_db.auditar_completidad(conn)
        reglas = {h["regla"] for h in r["hallazgos"]}
        assert {
            "folio_inconsistente",
            "precio_inconsistente",
            "vendedor_sin_nombre",
            "vendedor_malformado",
            "doc_placeholder",
        } <= reglas
        assert r["ok"] is False


class TestChunksDiarios:
    """ "Actualizar hoy" re-descarga día por día y reemplaza por chunk (F6).

    La intranet devuelve documentos de días anteriores ingresados tarde, así que
    un chunk de un día trae filas con `fecha_orig` de otro. El borrado va por
    día: el día del label se reemplaza, los tardíos de otros días se agregan
    sin borrar al vecino. mes_ref siempre mensual canónico.
    """

    def _fila(self, fecha, nro, mes_ref, sku="02211"):
        from src.core.xls_processor import derivar_campos

        v = {
            "id_articulo": sku,
            "id_linea": "01",
            "id_cliente": "00004884",
            "tpo_doc": "F01",
            "serie_doc": "001",
            "nro_doc": nro,
            "fecha_orig": fecha,
            "mes_ref": mes_ref,
            "cantidad": 1.0,
            "soles": 10.0,
            "anho": 2026,
            "mes": 9,
            "id_vendedor": "178",
            "nom_vendedor": "MILCA",
            "folio_unico": "",
        }
        derivar_campos(v)
        return v

    def _dias(self, conn):
        return {
            r[0]: r[1]
            for r in conn.execute("SELECT fecha_orig, COUNT(*) FROM ventas GROUP BY 1").fetchall()
        }

    def test_chunk_diario_no_borra_el_dia_vecino(self, tmp_db):
        conn = ventas_db.get_conn()
        ventas_db.insert_ventas(
            conn,
            [self._fila("2026-09-14", str(i), "2026-09") for i in range(3)],
            label="2026-09-14",
        )
        assert self._dias(conn) == {"2026-09-14": 3}

        # El chunk del 15 trae 2 docs suyos + 1 doc del 14 ingresado tarde.
        ventas_db.insert_ventas(
            conn,
            [
                self._fila("2026-09-15", "a", "2026-09"),
                self._fila("2026-09-15", "b", "2026-09"),
                self._fila("2026-09-14", "c", "2026-09"),
            ],
            label="2026-09-15",
        )
        # El 14 conserva sus 3 y gana el tardío: 4, no 1.
        assert self._dias(conn) == {"2026-09-14": 4, "2026-09-15": 2}

    def test_repetir_chunk_reemplaza_sin_duplicar(self, tmp_db):
        conn = ventas_db.get_conn()
        lote = [self._fila("2026-09-15", str(i), "2026-09") for i in range(4)]
        ventas_db.insert_ventas(conn, lote, label="2026-09-15")
        ventas_db.insert_ventas(conn, lote, label="2026-09-15")
        assert self._dias(conn) == {"2026-09-15": 4}

    def test_chunk_diario_respeta_otro_mes(self, tmp_db):
        conn = ventas_db.get_conn()
        ventas_db.insert_ventas(
            conn, [self._fila("2026-08-31", "x", "2026-08")], label="2026-08-31"
        )
        ventas_db.insert_ventas(
            conn, [self._fila("2026-09-01", "y", "2026-09")], label="2026-09-01"
        )
        assert self._dias(conn) == {"2026-08-31": 1, "2026-09-01": 1}

    def test_tardio_duplicado_lo_limpia_dedup(self, tmp_db):
        """El doc del 14 que vuelve en el chunk del 15 no debe quedar doble."""
        conn = ventas_db.get_conn()
        ventas_db.insert_ventas(
            conn, [self._fila("2026-09-14", "c", "2026-09")], label="2026-09-14"
        )
        ventas_db.insert_ventas(
            conn, [self._fila("2026-09-14", "c", "2026-09")], label="2026-09-15"
        )
        assert (
            conn.execute("SELECT COUNT(*) FROM ventas WHERE folio_unico = 'F01/001/c'").fetchone()[
                0
            ]
            == 2
        )
        ventas_db.dedup_ventas(conn)
        assert (
            conn.execute("SELECT COUNT(*) FROM ventas WHERE folio_unico = 'F01/001/c'").fetchone()[
                0
            ]
            == 1
        )

    def test_ventas_db_audita_vendedores_despues_de_insertar(self, tmp_db):
        from src.core.xls_processor import normalize_seller_id

        filas = [self._fila("2026-09-15", str(i), "2026-09-15") for i in range(2)]
        filas[0]["id_vendedor"] = "01178"
        assert normalize_seller_id(filas[0]["id_vendedor"]) == "178"
        filas[0]["id_vendedor"] = normalize_seller_id(filas[0]["id_vendedor"])
        ventas_db.insert_ventas(conn := ventas_db.get_conn(), filas)
        r = ventas_db.auditar_vendedores(conn)
        assert r["ok"] is True
        assert r["malformados"] == []


class TestWriter:
    def test_insert_y_delete_then_insert_por_mes(self, tmp_db, sample_ventas):
        conn = ventas_db.get_conn()
        n1 = ventas_db.insert_ventas(conn, sample_ventas[:2])
        assert n1 == 2
        # Reinsertar mismo mes: reemplaza, no duplica
        n2 = ventas_db.insert_ventas(conn, sample_ventas[:2])
        assert n2 == 2
        total = conn.execute("SELECT COUNT(*) FROM ventas").fetchone()[0]
        assert total == 2

    def test_insert_diario_por_fechas(self, tmp_db, sample_ventas):
        conn = ventas_db.get_conn()
        diarias = [
            dict(v, mes_ref="2024-01-15", nro_doc=str(500000 + i), folio_unico="")
            for i, v in enumerate(sample_ventas[:2])
        ]
        from src.core.xls_processor import derivar_campos

        for v in diarias:
            derivar_campos(v)
        ventas_db.insert_ventas(conn, diarias)
        assert conn.execute("SELECT COUNT(*) FROM ventas").fetchone()[0] == 2

    def test_dedup_por_folio_sku(self, tmp_db, sample_ventas):
        conn = ventas_db.get_conn()
        from src.core.xls_processor import derivar_campos

        dup = [dict(v) for v in sample_ventas[:2]]
        for v in dup:
            derivar_campos(v)
        # Insertar duplicado en el mismo batch (mismo folio_unico + sku)
        batch = dup + [dict(dup[0])]
        ventas_db.insert_ventas(conn, batch)
        assert (
            conn.execute("SELECT COUNT(*) FROM ventas WHERE id_articulo='02211'").fetchone()[0] == 2
        )
        eliminadas = ventas_db.dedup_ventas(conn)
        assert eliminadas == 1
        total = conn.execute("SELECT COUNT(*) FROM ventas WHERE id_articulo='02211'").fetchone()[0]
        assert total == 1


class TestCliente:
    def test_test_connection_sin_db(self, tmp_db, tmp_path, monkeypatch):
        # Eliminar la db creada por la fixture
        ventas_db.db_path().unlink(missing_ok=True)
        ok, msg = VentasDbClient().test_connection()
        assert not ok
        assert "no existe" in msg.lower() or "Primera carga" in msg

    def test_test_connection_ok(self, populated_db):
        ok, msg = VentasDbClient().test_connection()
        assert ok
        assert "5 filas" in msg

    def test_fetch_historial_mapeo_derivadas(self, populated_db):
        df = VentasDbClient().fetch_historial(id_cliente="00068414")
        assert len(df) == 5
        for col in (
            "DOC_ID",
            "TIPO_CLASE",
            "FACTURA_REF",
            "NC_ASOCIADAS",
            "AFECTA_CANTIDAD",
            "AFECTA_VALOR",
        ):
            assert col in df.columns
        row_f = df[df["TIPO_CLASE"] == "factura"].iloc[0]
        assert row_f["DOC_ID"] == "F012-457996"
        assert sorted(row_f["NC_ASOCIADAS"]) == ["NN012-900001", "NN012-900002", "NN012-900003"]

    def test_fetch_historial_filtros_fecha_en_sql(self, populated_db):
        df = VentasDbClient().fetch_historial(
            id_cliente="00068414", fecha_desde="2024-02-01", fecha_hasta="2024-02-28"
        )
        assert len(df) == 3
        assert (df["FECHA"].dt.strftime("%Y-%m") == "2024-02").all()

    def test_fetch_historial_filtro_articulo(self, populated_db):
        df = VentasDbClient().fetch_historial(id_cliente="00068414", id_articulo="02211")
        assert set(df["CODIGO"]) == {"02211"}

    def test_fetch_historial_filtro_documento(self, populated_db):
        df = VentasDbClient().fetch_historial(serie_doc="N012", nro_doc="900001")
        assert len(df) == 1
        assert df.iloc[0]["DOC_ID"] == "NN012-900001"
        assert df.iloc[0]["TIPO_CLASE"] == "devolucion"
        assert df.iloc[0]["AFECTA_CANTIDAD"]

    def test_nota_debito_sin_impacto(self, populated_db):
        df = VentasDbClient().fetch_historial(serie_doc="N012", nro_doc="900003")
        assert df.iloc[0]["TIPO_CLASE"] == "cargo"
        assert not df.iloc[0]["AFECTA_CANTIDAD"]

    def test_fetch_vendedores_y_clientes(self, populated_db):
        cli = VentasDbClient()
        vends = cli.fetch_vendedores()
        # id canónico (sufijo): '01177' del fixture sale como '177'.
        assert vends == [{"id": "177", "codigo": "177", "nombre": "VENDEDOR UNO"}]
        clientes = cli.fetch_clientes()
        assert clientes[0]["id"] == "00068414"
        # Ambas formas traen la misma cartera (match por sufijo).
        por_vend = cli.fetch_clientes(vendedor_id="01177")
        assert por_vend[0]["id"] == "00068414"
        assert cli.fetch_clientes(vendedor_id="177") == por_vend

    def test_fetch_vendedor_by_id(self, populated_db):
        v = VentasDbClient().fetch_vendedor_by_id("01177")
        assert v == {"id": "177", "codigo": "177", "nombre": "VENDEDOR UNO"}
        assert VentasDbClient().fetch_vendedor_by_id("177") == v
        assert VentasDbClient().fetch_vendedor_by_id("99999") is None

    def test_fetch_rucs_partidos(self, tmp_db):
        """Mismo RUC con 2 ids (legacy) se detecta; RUCs sanos no."""
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
            id_cliente="00054954",
            doc_cliente="00141149027",
            nom_cliente="CLIENTE PARTIDO S.A.C.",
            tpo_doc="F01",
            serie_doc="204",
            nro_doc="67375",
            referencia="",
            moneda="Soles",
            cantidad=10.0,
            cantidad_fae=0.0,
            soles=100.0,
            dolares=0.0,
            precio_unitario=10.0,
            anho=2024,
            mes=2,
            fecha_orig="2024-02-01",
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
            mes_ref="2024-02",
            tipo_operacion="",
            factura_ref_serie="",
            factura_ref_nro="",
            folio_unico="",
        )
        otro_id = dict(base, id_cliente="00059242", nro_doc="67376")
        basura = dict(base, id_cliente="00000010", doc_cliente="00", nro_doc="67377")
        unico = dict(base, id_cliente="00056101", doc_cliente="20601024714", nro_doc="67378")
        rows = []
        for v in (base, otro_id, basura, unico):
            d = dict(v)
            derivar_campos(d)
            rows.append(d)
        conn = tmp_db.get_conn()
        ventas_db.insert_ventas(conn, rows)
        mapa = VentasDbClient().fetch_rucs_partidos()
        assert mapa.get("00141149027") == 2  # el RUC partido
        assert "00" not in mapa  # basura (2 chars) excluida
        assert "20601024714" not in mapa  # RUC único no se marca

    def test_badge_ruc_helper(self):
        from src.ui.widgets.cliente_picker import badge_ruc

        assert badge_ruc("00141149027", 2) == " · 00141149027 · 2 códigos"
        assert badge_ruc("20601024714", 1) == ""  # único: sin ruido
        assert badge_ruc("", 5) == ""  # sin RUC
        assert badge_ruc("00", 78) == ""  # basura < 8 chars

    def _dos_clientes(self, tmp_db):
        """A=00068414 (1 doc) y B=00016841 (3 docs): '684' es prefijo
        del id corto de A pero solo substring de B."""
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
            id_cliente="00068414",
            doc_cliente="20100047218",
            nom_cliente="CLIENTE A S.A.C.",
            tpo_doc="F01",
            serie_doc="204",
            nro_doc="67375",
            referencia="",
            moneda="Soles",
            cantidad=10.0,
            cantidad_fae=0.0,
            soles=100.0,
            dolares=0.0,
            precio_unitario=10.0,
            anho=2024,
            mes=2,
            fecha_orig="2024-02-01",
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
            mes_ref="2024-02",
            tipo_operacion="",
            factura_ref_serie="",
            factura_ref_nro="",
            folio_unico="",
        )
        filas = [base]
        for i, nro in enumerate(("67376", "67377", "67378")):
            filas.append(
                dict(
                    base,
                    id_cliente="00016841",
                    doc_cliente="20100047219",
                    nom_cliente="OTRO CLIENTE S.A.C.",
                    nro_doc=nro,
                    fecha_orig=f"2024-02-0{i + 2}",
                )
            )
        rows = []
        for v in filas:
            d = dict(v)
            derivar_campos(d)
            rows.append(d)
        conn = tmp_db.get_conn()
        ventas_db.insert_ventas(conn, rows)

    def test_busqueda_rankea_exactitud(self, tmp_db):
        self._dos_clientes(tmp_db)
        cli = VentasDbClient()
        # Prefijo del id corto gana al conteo de docs.
        r = cli.fetch_clientes(search="684")
        assert [x["id"] for x in r] == ["00068414", "00016841"]
        # Exacto en forma corta o canónica.
        assert cli.fetch_clientes(search="68414")[0]["id"] == "00068414"
        assert cli.fetch_clientes(search="00068414")[0]["id"] == "00068414"
        # RUC exacto.
        assert cli.fetch_clientes(search="20100047218")[0]["id"] == "00068414"
        # Sin búsqueda: orden anterior intacto (por docs).
        assert cli.fetch_clientes()[0]["id"] == "00016841"

    def test_busqueda_rankea_en_agg(self, tmp_db):
        self._dos_clientes(tmp_db)
        ventas_db.refresh_agg_cliente_mes()
        r = VentasDbClient().fetch_clientes(search="684")
        assert [x["id"] for x in r] == ["00068414", "00016841"]

    def test_vendedor_prefijado_se_fusiona(self, tmp_db):
        """'177' y '01177' son el mismo vendedor: un dropdown, cartera única."""
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
            id_vendedor="01177",
            nom_vendedor="VENDEDOR UNO",
            id_pedido="P1",
            file_source="t",
            mes_ref="2026-09",
            tipo_operacion="",
            factura_ref_serie="",
            factura_ref_nro="",
            folio_unico="",
        )
        pelado = dict(base, id_vendedor="177", nro_doc="67376", fecha_orig="2026-09-06")
        rows = []
        for v in (base, pelado):
            d = dict(v)
            derivar_campos(d)
            rows.append(d)
        conn = tmp_db.get_conn()
        ventas_db.insert_ventas(conn, rows)
        cli = VentasDbClient()
        vends = [x for x in cli.fetch_vendedores(solo_lineas_activas=False) if x["id"] == "177"]
        assert len(vends) == 1  # una sola entrada fusionada
        assert cli.fetch_clientes(
            vendedor_id="01177", solo_lineas_activas=False
        ) == cli.fetch_clientes(vendedor_id="177", solo_lineas_activas=False)

    def test_fetch_vendedores_solo_lineas_activas(self, populated_db):
        """Con solo_lineas_activas=True (default), solo vendedores con F*/B* de líneas activas."""
        cli = VentasDbClient()
        # Default: solo lineas activas (allowlist por defecto: 24 lineas, incluye "01")
        vends = cli.fetch_vendedores()
        assert len(vends) >= 1
        # Vendedor 01177 tiene ventas F*/B* en linea 0101 (activa) → debe aparecer
        ids = {v["id"] for v in vends}
        assert "177" in ids

    def test_fetch_vendedores_sin_lineas_activas(self, populated_db):
        """Con solo_lineas_activas=False, aparecen vendedores con cualquier tipo de doc."""
        cli = VentasDbClient()
        vends_all = cli.fetch_vendedores(solo_lineas_activas=False)
        vends_active = cli.fetch_vendedores(solo_lineas_activas=True)
        # Sin filtro de líneas, al menos tantos como con filtro (puede ser más)
        assert len(vends_all) >= len(vends_active)

    def test_fetch_vendedores_min_docs(self, populated_db):
        """min_docs filtra por cantidad mínima de documentos F*/B* activos."""
        cli = VentasDbClient()
        # Con min_docs=1, debe haber al menos el vendedor 177 (tiene F012 y F014)
        vends = cli.fetch_vendedores(min_docs=1)
        ids = {v["id"] for v in vends}
        assert "177" in ids
        # Con min_docs alto (> docs del vendedor en fixture), lista vacía
        vends_empty = cli.fetch_vendedores(min_docs=99999)
        assert vends_empty == []

    def test_fetch_client_by_id(self, populated_db):
        c = VentasDbClient().fetch_client_by_id("00068414")
        assert c is not None
        assert c["id"] == "00068414"
        assert c["nombre"].strip().upper() == "CLIENTE DEMO SAC"
        assert VentasDbClient().fetch_client_by_id("99999") is None

    def test_fetch_facturas_cliente(self, populated_db):
        fs = VentasDbClient().fetch_facturas_cliente("00068414")
        assert len(fs) == 1
        f = fs[0]
        assert f["id"] == "F012-012-457996"
        assert f["fecha"] == "2024-01-15"

    def test_fetch_facturas_disponibles(self, populated_db):
        fd = VentasDbClient().fetch_facturas_disponibles("00068414", "02211")
        assert len(fd) == 1
        row = fd.iloc[0]
        assert float(row["saldo_disponible"]) == pytest.approx(90.0)
        # NCR total (100 FAE de 100 vendidas) ajusta precio: 2.5 - (50/100) = 2.0
        assert float(row["precio_para_devolucion"]) == pytest.approx(2.0)
        assert row["estado_periodo"] == "DENTRO_PERIOD"

    def test_fetch_facturas_disponibles_fallback_sku_sin_filas(self, populated_db):
        df = VentasDbClient().fetch_facturas_disponibles("00068414", "SKU_INEXISTENTE")
        assert df.empty


class TestComprasCliente:
    def test_neto_por_mes_y_linea(self, populated_db):
        df = VentasDbClient().fetch_compras_cliente(
            "00068414", fecha_desde="2024-01-01", fecha_hasta="2024-12-31"
        )
        assert list(df.columns[:6]) == [
            "MES_REF",
            "COD_LINEA",
            "LINEA",
            "SOLES",
            "CANTIDAD",
            "N_DOCS",
        ]
        assert set(df["MES_REF"]) == {"2024-01", "2024-02"}
        # COD_LINEA canónico (sufijo): '0101' del fixture sale como '01'.
        assert set(df["COD_LINEA"]) == {"01"}
        ene = df[df["MES_REF"] == "2024-01"].iloc[0]
        assert float(ene["SOLES"]) == pytest.approx(350.0)
        assert float(ene["CANTIDAD"]) == pytest.approx(150.0)
        feb = df[df["MES_REF"] == "2024-02"].iloc[0]
        # NCR devolucion (-25) + NCR ajuste (-50) + NDB (+30) = -45
        assert float(feb["SOLES"]) == pytest.approx(-45.0)
        assert float(feb["CANTIDAD"]) == pytest.approx(-10.0)
        assert float(df["SOLES"].sum()) == pytest.approx(305.0)
        assert float(df["CANTIDAD"].sum()) == pytest.approx(140.0)

    def test_solo_facturas_sin_nc(self, populated_db):
        df = VentasDbClient().fetch_compras_cliente(
            "00068414",
            fecha_desde="2024-01-01",
            fecha_hasta="2024-12-31",
            incluir_nc=False,
        )
        assert set(df["MES_REF"]) == {"2024-01"}
        assert float(df["SOLES"].sum()) == pytest.approx(350.0)

    def test_filtro_rango_excluye_mes(self, populated_db):
        df = VentasDbClient().fetch_compras_cliente(
            "00068414", fecha_desde="2024-02-01", fecha_hasta="2024-02-28"
        )
        assert set(df["MES_REF"]) == {"2024-02"}
        assert float(df["SOLES"].sum()) == pytest.approx(-45.0)

    def test_cliente_sin_compras(self, populated_db):
        df = VentasDbClient().fetch_compras_cliente("99999999")
        assert df.empty


class TestEstado:
    def test_db_health(self, populated_db):
        h = ventas_db.db_health()
        assert h["exists"]
        assert h["rows"] == 5
        assert h["fecha_min"] == "2024-01-15"
        assert h["fecha_max"] == "2024-02-20"
        assert h["size_mb"] >= 0

    def test_missing_months(self, populated_db):
        from datetime import date

        # Nuevo contrato: missing_months = meses NO COMPLETOS (ausentes o truncos).
        # 2024-01 y 2024-02 estan truncos (al dia 15 y 20) -> faltan.
        faltantes = ventas_db.missing_months(date(2024, 1, 1), date(2024, 3, 31))
        assert faltantes == ["2024-01", "2024-02", "2024-03"]

    def test_next_month_after_last(self, populated_db):
        from datetime import date

        assert ventas_db.next_month_after_last(date(2024, 1, 1)) == date(2024, 3, 1)

    def test_stats_cache(self, populated_db):
        conn = ventas_db.get_conn()
        ventas_db.refresh_stats_cache(conn)
        val = conn.execute("SELECT value FROM stats_cache WHERE key='total_rows'").fetchone()[0]
        assert val == 5.0

    def test_sync_log(self, populated_db):
        conn = ventas_db.get_conn()
        ventas_db.register_sync(conn, "capture", "ok", 5, 12.3)
        row = conn.execute(
            "SELECT tipo, estado, filas_subidas FROM sync_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert tuple(row) == ("capture", "ok", 5)

    def test_checksum_agrega_dias_y_feriados(self, populated_db):
        """Mes capturado por dias (fallback): checksum agrega todas las filas
        del mes (label diario). Dias feriados sin movimientos no afectan."""
        from src.core.xls_processor import derivar_campos

        conn = ventas_db.get_conn()
        base = dict(
            id_articulo="02211",
            original_sku="02211",
            nom_articulo="X",
            id_linea="0101",
            nom_linea="GASEOSAS",
            id_grupo="01",
            nom_grupo="G",
            id_tipo="01",
            nom_tipo="T",
            id_familia="01",
            nom_familia="F",
            id_cliente="00068414",
            doc_cliente="20100047218",
            nom_cliente="CLIENTE DEMO SAC",
            tpo_doc="F01",
            serie_doc="012",
            nro_doc="555001",
            referencia="",
            moneda="Soles",
            cantidad=10.0,
            cantidad_fae=0.0,
            soles=25.0,
            dolares=0.0,
            precio_unitario=2.5,
            anho=2024,
            mes=1,
            fecha_orig="2024-01-16",
            fecha_ref=None,
            fecha_venc=None,
            cod_sucursal="01",
            nom_sucursal="LIMA",
            departamento="LIMA",
            provincia="LIMA",
            distrito="SAN ISIDRO",
            id_vendedor="01177",
            nom_vendedor="VEND",
            id_pedido="P1",
            file_source="t",
            mes_ref="2024-01-16",
            tipo_operacion="",
            factura_ref_serie="",
            factura_ref_nro="",
            folio_unico="",
        )
        d17 = dict(base, nro_doc="555002", fecha_orig="2024-01-17", mes_ref="2024-01-17")
        rows = [base, d17]
        for v in rows:
            derivar_campos(v)
        ventas_db.insert_ventas(conn, rows)
        n, tot = ventas_db.record_month_checksum(conn, "2024-01")
        # 2 mensuales existentes (250+100) + 2 diarias nuevas (25+25)
        assert n == 4
        assert tot == pytest.approx(400.0)
        # integridad: agregar dias al mismo mes no produce drift
        ok, drifts, sin = ventas_db.verify_integrity(conn)
        assert ok, drifts
        # dia feriado: insertar dia sin filas no cambia el checksum
        n2, tot2 = ventas_db.record_month_checksum(conn, "2024-01")
        assert n2 == n

    def _row(self, mes_ref="2024-01", folio="F1", soles=100.0):
        from src.core.xls_processor import derivar_campos

        v = dict(
            id_articulo="02211",
            original_sku="02211",
            nom_articulo="X",
            id_linea="0101",
            nom_linea="GASEOSAS",
            id_grupo="01",
            nom_grupo="G",
            id_tipo="01",
            nom_tipo="T",
            id_familia="01",
            nom_familia="F",
            id_cliente="00068414",
            doc_cliente="20100047218",
            nom_cliente="CLIENTE DEMO SAC",
            tpo_doc="F01",
            serie_doc="012",
            nro_doc=folio,
            referencia="",
            moneda="Soles",
            cantidad=1.0,
            cantidad_fae=0.0,
            soles=soles,
            dolares=0.0,
            precio_unitario=soles,
            anho=int(mes_ref[:4]),
            mes=int(mes_ref[5:7]),
            fecha_orig=mes_ref,
            fecha_ref=None,
            fecha_venc=None,
            cod_sucursal="01",
            nom_sucursal="LIMA",
            departamento="LIMA",
            provincia="LIMA",
            distrito="SAN ISIDRO",
            id_vendedor="01177",
            nom_vendedor="VEND",
            id_pedido="P1",
            file_source="t",
            mes_ref=mes_ref,
            tipo_operacion="",
            factura_ref_serie="",
            factura_ref_nro="",
            folio_unico=folio,
        )
        derivar_campos(v)
        return v

    def test_verify_integrity_acepta_checksum_del_productor(self, tmp_db):
        """Los meses que bajan del productor traen su propio formato de checksum.

        `verify_integrity` debe validar las cifras (total_filas/total_soles), no
        la cadena: comparar el string marca drift un mes que esta integro.
        """
        conn = tmp_db.get_conn()
        tmp_db.insert_ventas(conn, [self._row(soles=100.0)])
        tmp_db.record_month_checksum(conn, "2024-01")
        ok, drifts, sin = ventas_db.verify_integrity(conn)
        assert ok and drifts == [] and sin == []

        conn.execute(
            "UPDATE mes_checksums SET checksum = '0000002c-03e8d4c8-00000000-00000000' "
            "WHERE mes_ref = '2024-01'"
        )
        ok, drifts, sin = ventas_db.verify_integrity(conn)
        assert ok, drifts

    def test_verify_integrity_marca_drift_con_cifras_distintas(self, tmp_db):
        conn = tmp_db.get_conn()
        tmp_db.insert_ventas(conn, [self._row(folio="F1", soles=100.0)])
        tmp_db.record_month_checksum(conn, "2024-01")
        tmp_db.insert_ventas(conn, [self._row(folio="F2", soles=50.0)])
        ok, drifts, sin = ventas_db.verify_integrity(conn)
        assert not ok
        assert [d[0] for d in drifts] == ["2024-01"]
        assert drifts[0][1:] == (1, 2, 150.0)


class TestCobertura:
    def test_months_coverage(self, populated_db):
        cov = ventas_db.months_coverage()
        assert [c["mes_ref"] for c in cov] == ["2024-01", "2024-02"]
        assert cov[0]["filas"] == 2
        assert cov[1]["filas"] == 3
        assert cov[0]["fecha_min"] == "2024-01-15"

    def test_has_month(self, populated_db):
        assert ventas_db.has_month("2024-01")
        assert not ventas_db.has_month("2024-03")

    def test_month_end(self):
        assert ventas_db.month_end("2024-01") == "2024-01-31"
        assert ventas_db.month_end("2024-02") == "2024-02-29"
        assert ventas_db.month_end("2024-12") == "2024-12-31"

    def test_incomplete_months(self, populated_db):
        # Ambos meses del fixture son incompletos por diseno:
        # 2024-01 llega solo al dia 15 y 2024-02 al dia 20 (< fin de mes).
        # El mes en curso se excluye (lo cubre la ventana diaria).
        inc = ventas_db.incomplete_months()
        from datetime import date

        cur = f"{date.today().year}-{date.today().month:02d}"
        esperado = ["2024-01", "2024-02"]
        if cur in esperado:
            esperado.remove(cur)
        assert inc == esperado

    def test_coverage_summary(self, populated_db):
        s = ventas_db.coverage_summary()
        assert "2 meses (2024-01 a 2024-02)" in s
        assert "5 filas" in s

    def test_allowlist_usuario_24_codigos(self):
        usuario = {
            "01",
            "02",
            "09",
            "11",
            "14",
            "72",
            "73",
            "75",
            "76",
            "77",
            "78",
            "79",
            "81",
            "85",
            "99",
            "AD",
            "CA",
            "CB",
            "CC",
            "CD",
            "CE",
            "CF",
            "CG",
            "MA",
        }
        assert set(ventas_db.DEFAULT_ALLOWED_LINES) == usuario
        # normalizacion: el canonico es pelado (igual que la entrada)
        from src.core.xls_processor import normalize_line_id

        for code in usuario:
            canon = normalize_line_id(code)
            assert canon == code
            assert ventas_db.is_allowed_line(canon), code


class TestLineas:
    """distinct_lineas(): catálogo de líneas presentes en la DB (pestaña Líneas)."""

    def test_sin_db_retorna_vacio(self, tmp_db, tmp_path, monkeypatch):

        monkeypatch.setenv("G360_DATA_DIR", str(tmp_path / "vacia"))
        assert ventas_db.distinct_lineas() == []

    def test_agrupa_por_codigo_con_nombre(self, populated_db):
        conn = populated_db.get_conn()
        extra = dict(
            id_articulo="09999",
            original_sku="09999",
            nom_articulo="EXTRA",
            id_linea="01AD",
            nom_linea="ALIMENTOS",
            id_grupo="01",
            nom_grupo="G",
            id_tipo="01",
            nom_tipo="T",
            id_familia="01",
            nom_familia="F",
            id_cliente="00068414",
            doc_cliente="20100047218",
            nom_cliente="X",
            tpo_doc="F012",
            serie_doc="012",
            nro_doc="1",
            referencia="",
            moneda="Soles",
            cantidad=1.0,
            cantidad_fae=0.0,
            soles=1.0,
            dolares=0.0,
            precio_unitario=1.0,
            anho=2024,
            mes=1,
            fecha_orig="2024-01-16",
            fecha_ref=None,
            fecha_venc=None,
            cod_sucursal="01",
            nom_sucursal="LIMA",
            departamento="LIMA",
            mes_ref="2024-01",
        )
        populated_db.insert_ventas(conn, [extra])
        lineas = {l["codigo"]: l["nombre"] for l in populated_db.distinct_lineas()}
        assert lineas["0101"] == "GASEOSAS"
        assert lineas["01AD"] == "ALIMENTOS"
        cods = [l["codigo"] for l in populated_db.distinct_lineas()]
        assert cods == sorted(cods)

    def test_muestra_linea_no_aprobada(self, populated_db):
        """El catalogo refleja la DB aunque la linea no este en el allowlist."""
        conn = populated_db.get_conn()
        extra = dict(
            id_articulo="07777",
            original_sku="07777",
            nom_articulo="RARO",
            id_linea="01ZZ",
            nom_linea="NO APROBADA",
            id_grupo="01",
            nom_grupo="G",
            id_tipo="01",
            nom_tipo="T",
            id_familia="01",
            nom_familia="F",
            id_cliente="00068414",
            doc_cliente="20100047218",
            nom_cliente="X",
            tpo_doc="F012",
            serie_doc="012",
            nro_doc="2",
            referencia="",
            moneda="Soles",
            cantidad=1.0,
            cantidad_fae=0.0,
            soles=1.0,
            dolares=0.0,
            precio_unitario=1.0,
            anho=2024,
            mes=1,
            fecha_orig="2024-01-17",
            fecha_ref=None,
            fecha_venc=None,
            cod_sucursal="01",
            nom_sucursal="LIMA",
            departamento="LIMA",
            mes_ref="2024-01",
        )
        cods = [l["codigo"] for l in populated_db.distinct_lineas()]
        assert "01ZZ" not in cods  # 1a llamada: puebla el cache sin 01ZZ
        populated_db.insert_ventas(conn, [extra])
        cods2 = [l["codigo"] for l in populated_db.distinct_lineas()]
        assert "01ZZ" in cods2  # insert invalida el cache => refresco inmediato

    def test_serve_stale_no_bloquea_y_refresca_en_fondo(self, populated_db, monkeypatch):
        """Cache vencido: se devuelve la copia al instante y un escaneo en
        background la actualiza (sin bloquear 28s el modal)."""
        import time

        d1 = populated_db.distinct_lineas()
        assert d1
        monkeypatch.setattr(ventas_db, "_LINEAS_LIST_TTL", 0.0)  # fuerza el vencimiento
        t0 = time.time()
        d2 = populated_db.distinct_lineas()
        assert time.time() - t0 < 1.0, "serve-stale devolvio bloqueando"
        assert d2 == d1  # misma copia (sin reescaneo en el hilo de llamada)
        # El refresh en background re-cientra el cache en pocos milisegundos
        for _ in range(30):
            time.sleep(0.1)
            if ventas_db._LINEAS_LIST_CACHE["ts"] > t0:
                break
        assert ventas_db._LINEAS_LIST_CACHE["ts"] > t0


class TestDocumentoDetallado:
    """fetch_documento + vw_impacto_documento: base para calcular futuras NC."""

    @pytest.fixture
    def doc_completo(self, tmp_db):
        """Factura 2 SKUs + devolucion + ajuste valor + NDB cross-referenciados."""
        from src.core.xls_processor import derivar_campos

        conn = ventas_db.get_conn()
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
            dolares=265.95,
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
        sku2 = dict(
            base,
            id_articulo="014851",
            nom_articulo="PELOTA PVC BOY",
            cantidad=100.0,
            soles=120.0,
            dolares=35.0,
            precio_unitario=1.2,
        )
        dev = dict(
            base,
            tpo_doc="NCR",
            serie_doc="N204",
            nro_doc="900010",
            referencia="F01/204-67375",
            cantidad=-200.0,
            soles=-224.0,
            precio_unitario=1.12,
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
            precio_unitario=-0.12,
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
            cantidad_fae=0.0,
            soles=50.0,
            precio_unitario=0.0,
            fecha_orig="2026-09-15",
            folio_unico="",
        )
        rows = [base, sku2, dev, aju, ndb]
        for v in rows:
            derivar_campos(v)
        ventas_db.insert_ventas(conn, rows)
        return {"base": base, "sku2": sku2, "dev": dev, "aju": aju, "ndb": ndb}

    def test_cabecera(self, doc_completo):
        d = VentasDbClient().fetch_documento("204", "67375")
        assert d is not None
        cab = d["cabecera"]
        assert cab["tpo_doc"] == "F01"
        assert cab["nom_cliente"] == "MULTICOPIAS MARY E.I.R.L."
        assert cab["n_lineas"] == 2
        assert abs(cab["total_soles"] - 1016.0) < 0.01  # 896 + 120

    def test_lineas_por_sku(self, doc_completo):
        d = VentasDbClient().fetch_documento("204", "67375")
        assert len(d["lineas"]) == 2
        skus = {r["id_articulo"] for r in d["lineas"]}
        assert skus == {"014850", "014851"}

    def test_asociados_detallados(self, doc_completo):
        d = VentasDbClient().fetch_documento("204", "67375")
        asoc = d["asociados"]
        assert len(asoc) == 3
        tipos = {r["tipo_operacion"] for r in asoc}
        assert tipos == {"devolucion", "ajuste_valor", "nota_debito"}
        # todos referencian la factura
        assert all(r["doc_id"].startswith(("N", "D")) for r in asoc)
        dev = next(r for r in asoc if r["tipo_operacion"] == "devolucion")
        assert dev["cantidad"] == -200.0
        assert dev["folio_unico"] == "NCR/N204/900010"

    def test_impacto_por_sku(self, doc_completo):
        d = VentasDbClient().fetch_documento("204", "67375")
        imp = {r["id_articulo"]: r for r in d["impacto"]}
        # SKU con devolucion + descuento + NDB
        r = imp["014850"]
        assert r["cant_vendida"] == 800.0
        assert r["cant_devuelta"] == 200.0
        assert r["soles_descuento"] == 96.0
        assert r["fae_descuento"] == 800.0
        assert r["soles_nota_debito"] == 50.0
        assert r["n_devoluciones"] == 1 and r["n_ajustes"] == 1 and r["n_notas_debito"] == 1
        assert r["saldo_disponible"] == 600.0
        # NC total (FAE 800 == 800) -> precio = 1.12 - 96/800 + NDB 50/800 = 1.0625
        assert abs(r["precio_neto"] - 1.0625) < 0.001
        # SKU sin movimientos: saldo = vendido, precio neto = original
        r2 = imp["014851"]
        assert r2["saldo_disponible"] == 100.0
        assert r2["precio_neto"] == 1.2
        assert r2["n_devoluciones"] == 0

    def test_documento_inexistente(self, doc_completo):
        assert VentasDbClient().fetch_documento("204", "000000") is None

    def test_vw_impacto_solo_facturas(self, doc_completo):
        conn = ventas_db.get_conn()
        docs = {r[0] for r in conn.execute("SELECT DISTINCT tpo_doc FROM vw_impacto_documento")}
        assert all(t.startswith("F01") for t in docs)


class TestBackupEImport:
    def test_huerfanas(self, populated_db):
        """NC cuyo factura_ref no existe en DB -> huerfana."""
        conn = ventas_db.get_conn()
        base_h = 0
        # populated_db: las 3 NC referencian F012-012-457996 que SI existe -> 0 huerfanas
        assert ventas_db.nc_nd_huerfanas(conn) == base_h
        # Insertar NC que referencia factura inexistente
        from src.core.xls_processor import derivar_campos

        huerfana = dict(
            id_articulo="02211",
            original_sku="02211",
            nom_articulo="X",
            id_linea="0101",
            nom_linea="GASEOSAS",
            id_grupo="01",
            nom_grupo="G",
            id_tipo="01",
            nom_tipo="T",
            id_familia="01",
            nom_familia="F",
            id_cliente="00068414",
            doc_cliente="20100047218",
            nom_cliente="CLIENTE DEMO SAC",
            tpo_doc="NCR",
            serie_doc="N012",
            nro_doc="999888",
            referencia="F01/999-111111",
            moneda="Soles",
            cantidad=-5.0,
            cantidad_fae=0.0,
            soles=-10.0,
            dolares=0.0,
            precio_unitario=2.0,
            anho=2024,
            mes=3,
            fecha_orig="2024-03-01",
            fecha_ref=None,
            fecha_venc=None,
            cod_sucursal="01",
            nom_sucursal="LIMA",
            departamento="LIMA",
            provincia="LIMA",
            distrito="SAN ISIDRO",
            id_vendedor="01177",
            nom_vendedor="VEND",
            id_pedido="P1",
            file_source="t",
            mes_ref="2024-03",
            tipo_operacion="",
            factura_ref_serie="",
            factura_ref_nro="",
            folio_unico="",
        )
        derivar_campos(huerfana)
        ventas_db.insert_ventas(conn, [huerfana])
        assert ventas_db.nc_nd_huerfanas(conn) == 1

    def test_backup_db(self, populated_db):
        bpath = ventas_db.backup_db()
        assert bpath is not None and bpath.exists()
        # Segunda llamada el mismo dia: salta
        assert ventas_db.backup_db() is None
        # Rotacion: max_keep
        bdir = ventas_db.backup_dir()
        assert len(list(bdir.glob("historial_*.db"))) >= 1
        # El backup es legible y tiene datos
        import sqlite3

        c = sqlite3.connect(f"file:{bpath.as_posix()}?mode=ro", uri=True)
        try:
            assert c.execute("SELECT COUNT(*) FROM ventas").fetchone()[0] == 5
        finally:
            c.close()

    def test_import_db_rechaza_archivo_invalido(self, populated_db, tmp_path):
        basura = tmp_path / "no_db.db"
        basura.write_bytes(b"esto no es sqlite" * 100)
        with pytest.raises(Exception):
            ventas_db.import_db(basura)

    def test_import_db_reemplaza_local(self, populated_db, tmp_db, tmp_path):
        # DB origen: nueva, con otra fila
        import sqlite3

        origen = tmp_path / "otra_pc.db"
        c = sqlite3.connect(origen)
        try:
            # Copiar la estructura+datos desde la db de la fixture
            src_db = ventas_db.db_path()
            c2 = sqlite3.connect(src_db)
            try:
                c.executescript("".join(c2.iterdump()))
            finally:
                c2.close()
            c.execute("UPDATE ventas SET nom_cliente='CLIENTE IMPORTADO'")
            c.commit()
        finally:
            c.close()
        stats = ventas_db.import_db(origen)
        assert stats["filas"] == 5
        conn = ventas_db.get_conn()
        nom = conn.execute("SELECT DISTINCT nom_cliente FROM ventas LIMIT 1").fetchone()[0]
        assert nom == "CLIENTE IMPORTADO"

    def test_reset_connections(self, populated_db):
        conn1 = ventas_db.get_conn()
        ventas_db.reset_connections()
        conn2 = ventas_db.get_conn()
        assert conn2 is not conn1


class TestImpactoExacto:
    """Regla exact-match: NCR solo afecta precio si SUM(fae por folio) ==
    cantidad facturada (eps); NDB directa aumenta; lo demas informativo."""

    @pytest.fixture
    def caso_exact(self, tmp_db, sample_ventas):
        from src.core.xls_processor import derivar_campos

        base = dict(sample_ventas[0])
        base.update(
            tpo_doc="F01",
            serie_doc="900",
            nro_doc="1",
            id_articulo="SK1",
            nom_articulo="SKU UNO",
            cantidad=100.0,
            soles=1000.0,
            precio_unitario=10.0,
        )
        # NCR exacta: fae 100 == 100 -> DIRECTA (-0.50/u)
        na = dict(
            base,
            tpo_doc="NCR",
            serie_doc="N900",
            nro_doc="A1",
            referencia="F01/900-1",
            cantidad=0.0,
            cantidad_fae=100.0,
            soles=-50.0,
            fecha_orig="2024-02-01",
            mes_ref="2024-02",
        )
        # NCR parcial 50/100 -> informativa
        nb = dict(
            base,
            tpo_doc="NCR",
            serie_doc="N900",
            nro_doc="B1",
            referencia="F01/900-1",
            cantidad=0.0,
            cantidad_fae=50.0,
            soles=-30.0,
            fecha_orig="2024-02-02",
            mes_ref="2024-02",
        )
        # NCR exceso 150/100 -> informativa
        nc = dict(
            base,
            tpo_doc="NCR",
            serie_doc="N900",
            nro_doc="C1",
            referencia="F01/900-1",
            cantidad=0.0,
            cantidad_fae=150.0,
            soles=-60.0,
            fecha_orig="2024-02-03",
            mes_ref="2024-02",
        )
        # NDB con SKU -> aumenta (+0.20/u)
        nd = dict(
            base,
            tpo_doc="NDB",
            serie_doc="N900",
            nro_doc="D1",
            referencia="F01/900-1",
            cantidad=0.0,
            cantidad_fae=1.0,
            soles=20.0,
            fecha_orig="2024-02-04",
            mes_ref="2024-02",
        )
        # NDB sin SKU atribuible -> informativa (no joinea)
        ne = dict(
            base,
            tpo_doc="NDB",
            serie_doc="N900",
            nro_doc="E1",
            id_articulo="99004",
            nom_articulo="MORA",
            referencia="F01/900-1",
            cantidad=0.0,
            cantidad_fae=1.0,
            soles=5.0,
            fecha_orig="2024-02-05",
            mes_ref="2024-02",
        )
        rows = [base, na, nb, nc, nd, ne]
        for v in rows:
            derivar_campos(v)
        ventas_db.insert_ventas(ventas_db.get_conn(), rows)
        return rows

    def test_precio_neto_solo_directa_mas_ndb(self, caso_exact):
        from src.core.ventas_db_client import VentasDbClient

        d = VentasDbClient().fetch_documento("900", "1")
        imp = d["impacto"][0]
        # 10.00 - 50/100 + 20/100 = 9.70 (parcial/exceso/mora no tocan precio)
        assert abs(imp["precio_neto"] - 9.70) < 0.001
        assert abs(imp["soles_descuento"] - 50.0) < 0.01
        assert abs(imp["fae_descuento"] - 100.0) < 0.01
        assert imp["n_ajustes"] == 1
        assert abs(imp["soles_nota_debito"] - 20.0) < 0.01
        assert imp["n_notas_debito"] == 1

    def test_bucket_informativo(self, caso_exact):
        conn = ventas_db.get_conn()
        row = conn.execute(
            "SELECT soles_descuento_inf, n_ajustes_inf FROM vw_impacto_documento "
            "WHERE serie_doc='900' AND nro_doc='1'"
        ).fetchone()
        assert abs(row[0] - 90.0) < 0.01  # 30 + 60
        assert row[1] == 2

    def test_totales_vs_parciales(self, caso_exact):
        conn = ventas_db.get_conn()
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM vw_nc_totales WHERE serie_doc='900' AND nro_doc='1'"
            ).fetchone()[0]
            == 1
        )
        # La factura con folio directo no aparece en parciales
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM vw_nc_parciales "
                "WHERE factura_ref_serie='900' AND factura_ref_nro='1'"
            ).fetchone()[0]
            == 0
        )


class TestAggClienteMes:
    """Resumen cliente x mes usado por el fast-path de fetch_clientes."""

    def test_refresh_puebla_tabla(self, populated_db):
        n = ventas_db.refresh_agg_cliente_mes()
        assert n > 0
        conn = ventas_db.get_conn()
        # La factura de muestra es 2024-01; la NCR (2024-02) no cuenta como venta.
        meses = {r[0] for r in conn.execute("SELECT DISTINCT mes FROM agg_cliente_mes")}
        assert meses == {"2024-01"}

    def test_refresh_incluye_vendedor(self, populated_db):
        conn = ventas_db.get_conn()
        conn.execute(
            "INSERT INTO ventas (id_articulo,id_linea,id_cliente,nom_cliente,tpo_doc,"
            "serie_doc,nro_doc,cantidad,soles,fecha_orig,anho,mes,mes_ref,id_vendedor) "
            "VALUES ('02211','0101','00099999','OTRO','F012','012','500',10,25,'2024-01-20',2024,1,'2024-01','01200')"
        )
        conn.commit()
        ventas_db.refresh_agg_cliente_mes()
        pares = {
            tuple(r)
            for r in conn.execute("SELECT id_cliente, id_vendedor FROM agg_cliente_mes").fetchall()
        }
        assert ("00099999", "01200") in pares, pares
        assert ("00068414", "01177") in pares, pares

    def test_fetch_clientes_usa_agg_y_coincide(self, populated_db):
        # Detalle (sin agg)
        cli = VentasDbClient()
        det = cli.fetch_clientes(fecha_desde="2024-01-01", fecha_hasta="2024-01-31")
        ids_det = {c["id"] for c in det}
        # Con agg poblado
        ventas_db.refresh_agg_cliente_mes()
        agg = cli.fetch_clientes(fecha_desde="2024-01-01", fecha_hasta="2024-01-31")
        assert {c["id"] for c in agg} == ids_det
        assert "00068414" in {c["id"] for c in agg}


class TestAnclados:
    """Anclados (favoritos) por usuario en config."""

    def test_save_load(self, tmp_db):
        ventas_db.save_pinned(["C1", "C2"], ["V1"], user="U1")
        p = ventas_db.load_pinned(user="U1")
        assert p == {"clientes": ["C1", "C2"], "vendedores": ["V1"]}

    def test_dedup_preserva_orden(self, tmp_db):
        ventas_db.save_pinned(["A", "A", "B"], [], user="U1")
        assert ventas_db.load_pinned(user="U1")["clientes"] == ["A", "B"]

    def test_isolamiento_por_usuario(self, tmp_db):
        ventas_db.save_pinned(["C1"], ["V1"], user="U1")
        assert ventas_db.load_pinned(user="U2") == {"clientes": [], "vendedores": []}

    def test_toggle_alterna(self, tmp_db):
        ventas_db.save_pinned(["C1"], [], user="U1")
        assert ventas_db.toggle_pinned("clientes", "C2", user="U1") is True
        assert ventas_db.load_pinned(user="U1")["clientes"] == ["C1", "C2"]
        assert ventas_db.toggle_pinned("clientes", "C1", user="U1") is False
        assert ventas_db.load_pinned(user="U1")["clientes"] == ["C2"]

    def test_toggle_kind_invalido(self, tmp_db):
        with pytest.raises(ValueError):
            ventas_db.toggle_pinned("facturas", "X", user="U1")
