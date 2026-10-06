"""Regresion de los fixes de performance sobre `ventas`.

Los tres casos verificados contra la DB real (2.8M filas) antes de tocar codigo:

  n_facturas       3.44s -> 0.62s   (hint a idx_venta_tpodoc_folio, covering)
  nc_nd_huerfanas  7.51s -> 1.41s   (hint a idx_venta_tipo_op en la pierna externa)
  cobertura        127.43s -> 30.69s (una sola pasada en vez de 52 COUNT)

Lo que fijan estos tests no es el tiempo (imposible de assertar), sino que:
  1. el resultado sea identico al de la query sin hint,
  2. la cobertura por columna sea identica a la del loop de un COUNT por columna,
  3. un hint a un indice inexistente caiga al plan original en vez de romper.
"""

from __future__ import annotations

import sqlite3

import pytest

from src.core import ventas_db


class TestHintsDeIndice:
    def test_sin_hint_quita_el_indexed_by(self):
        assert ventas_db.sin_hint("SELECT 1 FROM t INDEXED BY ix") == "SELECT 1 FROM t"
        assert (
            ventas_db.sin_hint("SELECT 1 FROM ventas n INDEXED BY idx_venta_tipo_op WHERE 1")
            == "SELECT 1 FROM ventas n WHERE 1"
        )

    def test_sin_hint_no_toca_una_query_sin_hint(self):
        q = "SELECT COUNT(*) FROM ventas WHERE tpo_doc LIKE 'F01%'"
        assert ventas_db.sin_hint(q) == q

    def test_con_indice_cae_si_el_indice_no_existe(self, tmp_db):
        # El hint hace que la query dependa del indice. Si en una DB vieja no
        # esta, no debe reventar: se reintenta sin hint.
        conn = tmp_db.get_conn()
        sql = "SELECT COUNT(*) FROM ventas INDEXED BY idx_no_existe_xyz WHERE 1=1"
        with pytest.raises(sqlite3.OperationalError):
            conn.execute(sql).fetchone()
        assert ventas_db.con_indice(conn, sql).fetchone()[0] == 0

    def test_con_indice_usa_el_query_original_si_hay_indice(self, tmp_db):
        conn = tmp_db.get_conn()
        assert (
            ventas_db.con_indice(
                conn, "SELECT COUNT(*) FROM ventas INDEXED BY idx_venta_mes"
            ).fetchone()[0]
            == 0
        )


class TestNFacturas:
    def test_hint_no_cambia_el_resultado(self, tmp_db, sample_ventas):
        conn = tmp_db.get_conn()
        tmp_db.insert_ventas(conn, sample_ventas)
        plano = conn.execute(
            "SELECT COUNT(DISTINCT folio_unico) FROM ventas WHERE tpo_doc LIKE 'F01%'"
        ).fetchone()[0]
        con_hint = ventas_db.con_indice(
            conn,
            "SELECT COUNT(DISTINCT folio_unico) FROM ventas INDEXED BY idx_venta_tpodoc_folio "
            "WHERE tpo_doc LIKE 'F01%'",
        ).fetchone()[0]
        assert plano == con_hint
        assert plano > 0

    def test_refresh_stats_cache_escribe_n_facturas(self, tmp_db, sample_ventas):
        conn = tmp_db.get_conn()
        tmp_db.insert_ventas(conn, sample_ventas)
        tmp_db.refresh_stats_cache(conn)
        row = conn.execute("SELECT value FROM stats_cache WHERE key='n_facturas'").fetchone()
        assert row is not None
        assert row[0] > 0


class TestNCHuerfanas:
    def _ncr(self, nro_doc, referencia):
        """NCR de ajuste de valor: es el tipo que si puebla factura_ref_*.

        Ojo: una devolucion (cantidad<0) queda tipo_operacion='devolucion' con
        factura_ref_serie/nro vacios, y `nc_nd_huerfanas` las excluye por
        diseno (no hay referencia contra la que buscarla). Para ejercitar la
        query hace falta un ajuste_valor (cantidad=0) o un NDB.
        """
        from src.core.xls_processor import derivar_campos

        r = dict(
            id_articulo="02211",
            original_sku="02211",
            nom_articulo="GASEOSA 3L",
            id_linea="0101",
            nom_linea="GASEOSAS",
            id_cliente="00068414",
            doc_cliente="20100047218",
            nom_cliente="CLIENTE DEMO SAC",
            tpo_doc="NCR",
            serie_doc="012",
            nro_doc=nro_doc,
            referencia=referencia,
            cantidad=0.0,
            soles=-50.0,
            anho=2024,
            mes=2,
            fecha_orig="2024-02-10",
            mes_ref="2024-02",
            cod_sucursal="01",
        )
        derivar_campos(r)
        assert r["tipo_operacion"] == "ajuste_valor"
        assert r["factura_ref_nro"]
        return r

    def _factura(self):
        return dict(
            id_articulo="02211",
            original_sku="02211",
            nom_articulo="GASEOSA 3L",
            id_linea="0101",
            nom_linea="GASEOSAS",
            id_cliente="00068414",
            doc_cliente="20100047218",
            nom_cliente="CLIENTE DEMO SAC",
            tpo_doc="F012",
            serie_doc="012",
            nro_doc="457996",
            cantidad=1.0,
            soles=10.0,
            anho=2024,
            mes=1,
            fecha_orig="2024-01-15",
            mes_ref="2024-01",
            cod_sucursal="01",
        )

    def test_sin_factura_referida_cuenta_como_huerfana(self, tmp_db):
        conn = tmp_db.get_conn()
        tmp_db.insert_ventas(conn, [self._ncr("900001", "F01/999-999999")])
        assert tmp_db.nc_nd_huerfanas(conn) == 1

    def test_con_factura_presente_no_cuenta(self, tmp_db):
        conn = tmp_db.get_conn()
        tmp_db.insert_ventas(conn, [self._factura(), self._ncr("900001", "F01/012-457996")])
        assert tmp_db.nc_nd_huerfanas(conn) == 0

    def test_hint_no_cambia_el_conteo(self, tmp_db, sample_ventas):
        # El fix mete INDEXED BY idx_venta_tipo_op en la pierna externa. Lo que
        # importa es que devuelva exactamente lo mismo que la query sin hint.
        conn = tmp_db.get_conn()
        tmp_db.insert_ventas(conn, sample_ventas)
        tmp_db.insert_ventas(conn, [self._ncr("900002", "F01/888-888888")])
        plano = conn.execute(
            "SELECT COUNT(*) FROM ventas n "
            "WHERE n.tipo_operacion IN ('devolucion','ajuste_valor','nota_debito') "
            "AND COALESCE(n.factura_ref_serie,'') != '' "
            "AND COALESCE(n.factura_ref_nro,'') != '' "
            "AND NOT EXISTS (SELECT 1 FROM ventas f WHERE f.tpo_doc LIKE 'F01%' "
            "AND f.serie_doc = n.factura_ref_serie AND f.nro_doc = n.factura_ref_nro)"
        ).fetchone()[0]
        assert tmp_db.nc_nd_huerfanas(conn) == plano == 1


class TestCoberturaUnaPasada:
    def _loop_original(self, conn, cols, total):
        """La cobertura tal como estaba antes: un COUNT por columna."""
        out = {}
        for c in cols:
            n = conn.execute(
                f'SELECT COUNT(*) FROM ventas WHERE "{c}" IS NOT NULL '
                f"AND TRIM(CAST(\"{c}\" AS TEXT)) <> ''"
            ).fetchone()[0]
            out[c] = round(100.0 * n / total, 2) if total else 100.0
        return out

    def _cols(self, conn):
        return [
            row[1] for row in conn.execute("PRAGMA table_info(ventas)").fetchall() if row[1] != "id"
        ]

    def test_una_pasada_iguala_al_loop(self, tmp_db, sample_ventas):
        # El fix es 52 COUNT -> 1 SELECT con SUM(CASE) por columna. Si el
        # predicado cambia por una coma mal puesta, los numeros divergen.
        conn = tmp_db.get_conn()
        tmp_db.insert_ventas(conn, sample_ventas)
        r = tmp_db.auditar_completidad(conn)
        total = conn.execute("SELECT COUNT(*) FROM ventas").fetchone()[0]
        assert r["cobertura"] == self._loop_original(conn, self._cols(conn), total)

    def test_una_pasada_cubre_todas_las_columnas(self, tmp_db, sample_ventas):
        conn = tmp_db.get_conn()
        tmp_db.insert_ventas(conn, sample_ventas)
        r = tmp_db.auditar_completidad(conn)
        assert set(r["cobertura"]) == set(self._cols(conn))

    def test_db_vacia_no_rompe(self, tmp_db):
        r = tmp_db.auditar_completidad(tmp_db.get_conn())
        assert r["filas"] == 0
        assert all(v == 100.0 for v in r["cobertura"].values())


class TestCoberturaEnParalelo:
    def test_grupos_reparten_cada_columna_una_vez(self, tmp_db):
        # `cols[i::n]` debe cubrir las 52 columnas exactas una vez: si una
        # quedara fuera, `set(crudos) == set(cols)` no cumplira y el resultado
        # vendria incompleto.
        cols = [f"c{i}" for i in range(52)]
        for n in (2, 4, 8):
            grupos = [cols[i::n] for i in range(n)]
            assert sorted(c for g in grupos for c in g) == sorted(cols)
            assert all(g for g in grupos)

    @pytest.mark.parametrize("hilos", [2, 4, 8])
    def test_mismo_resultado_cualquier_numero_de_hilos(
        self, tmp_db, sample_ventas, monkeypatch, hilos
    ):
        # El reparto es una optimizacion: el numero de grupos no puede cambiar
        # el resultado.
        conn = tmp_db.get_conn()
        tmp_db.insert_ventas(conn, sample_ventas)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(ventas)").fetchall() if r[1] != "id"]
        reference = dict(
            zip(
                cols,
                (int(n or 0) for n in conn.execute(ventas_db._sql_cobertura(cols)).fetchone()),
            )
        )
        monkeypatch.setattr(ventas_db, "COBERTURA_HILOS", hilos)
        assert ventas_db.cobertura_por_columna(conn, cols) == reference

    def test_detecta_la_ruta_del_archivo(self, tmp_db):
        # Con un archivo, la cobertura puede ir en paralelo (conexiones propias).
        assert ventas_db._ruta_de(tmp_db.get_conn()) != ""

    def test_in_memory_cae_a_una_pasada(self):
        # Una base in-memory no se puede reabrir desde otro hilo: tiene que
        # notar la falta de ruta y salir por la pasada unica, no reventar.
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE ventas (a TEXT, b TEXT, c TEXT)")
        conn.executemany("INSERT INTO ventas VALUES (?,?,?)", [("x", "", None), ("y", "z", "w")])
        assert ventas_db._ruta_de(conn) == ""
        assert ventas_db.cobertura_por_columna(conn, ["a", "b", "c"]) == {
            "a": 2,
            "b": 1,
            "c": 1,
        }
        conn.close()

    def test_sin_columnas_devuelve_vacio(self, tmp_db):
        assert ventas_db.cobertura_por_columna(tmp_db.get_conn(), []) == {}

    def test_una_sola_columna_por_el_camino_simple(self, tmp_db, sample_ventas):
        conn = tmp_db.get_conn()
        tmp_db.insert_ventas(conn, sample_ventas)
        total = conn.execute("SELECT COUNT(*) FROM ventas").fetchone()[0]
        assert ventas_db.cobertura_por_columna(conn, ["id_cliente"]) == {"id_cliente": total}

    def test_cae_a_una_pasada_si_el_paralelo_falla(self, tmp_db, sample_ventas, monkeypatch):
        # Si un hilo explota (DB bloqueada, ruta que no se puede abrir), el
        # resultado tiene que ser el de la pasada unica, no una excepcion.
        conn = tmp_db.get_conn()
        tmp_db.insert_ventas(conn, sample_ventas)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(ventas)").fetchall() if r[1] != "id"]
        reference = dict(
            zip(
                cols,
                (int(n or 0) for n in conn.execute(ventas_db._sql_cobertura(cols)).fetchone()),
            )
        )

        def _boom(ruta, cs):
            raise sqlite3.OperationalError("DB bloqueada")

        monkeypatch.setattr(ventas_db, "_cobertura_en_ruta", _boom)
        assert ventas_db.cobertura_por_columna(conn, cols) == reference

    def test_ignora_un_grupo_incompleto(self, tmp_db, sample_ventas, monkeypatch):
        # Guarda extra: si un grupo volviera con columnas de menos, no se
        # devuelve un dict incompleto en silencio.
        conn = tmp_db.get_conn()
        tmp_db.insert_ventas(conn, sample_ventas)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(ventas)").fetchall() if r[1] != "id"]
        real = ventas_db._cobertura_en_ruta
        monkeypatch.setattr(
            ventas_db,
            "_cobertura_en_ruta",
            lambda ruta, cs: {c: 0 for c in cs[:-1]} if len(cs) > 1 else real(ruta, cs),
        )
        got = ventas_db.cobertura_por_columna(conn, cols)
        assert set(got) == set(cols)
