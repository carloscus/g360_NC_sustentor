"""Tests de discovery y sincronización con la DB fuente (g360-db-ventas)."""

import pytest


def _copiar_db_local_a(origen_dir, db_file):
    """Copia la DB local actual a un directorio 'fuente' como historial.db.

    Hace checkpoint del WAL para que los datos queden en el archivo principal
    (igual que la fuente real de g360-db-ventas, que además se lee con
    snapshot consistente que incluye el WAL)."""
    from src.core import ventas_db
    import shutil
    from pathlib import Path

    conn = ventas_db.get_conn()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    origen_dir = Path(origen_dir)
    origen_dir.mkdir(parents=True, exist_ok=True)
    destino = origen_dir / "historial.db"
    shutil.copy2(ventas_db.db_path(), destino)
    return destino


class TestBuscarDbRemota:
    def test_no_encuentra_fuente(self, populated_db, tmp_path):
        from src.core.db_network import buscar_db_remota

        assert buscar_db_remota([tmp_path / "no_existe"]) is None
        assert buscar_db_remota([]) is None

    def test_encuentra_historial(self, populated_db, tmp_path):
        from src.core.db_network import buscar_db_remota

        _copiar_db_local_a(tmp_path / "fuente", populated_db.db_path())
        info = buscar_db_remota([tmp_path / "fuente"])
        assert info is not None
        assert info["ruta"].endswith("historial.db")
        assert info["size_bytes"] > 0
        assert info["size_mb"] > 0
        assert info["mtime"]

    def test_acepta_archivo_directo(self, populated_db, tmp_path):
        from src.core.db_network import buscar_db_remota

        db = _copiar_db_local_a(tmp_path / "vv", populated_db.db_path())
        info = buscar_db_remota([db])
        assert info is not None
        assert info["ruta"] == str(db)


class TestSincronizarDesdeRemota:
    def test_reemplaza_local_con_la_fuente(self, populated_db, tmp_path):
        from src.core import ventas_db
        from src.core.db_network import sincronizar_desde_remota

        fuente = _copiar_db_local_a(tmp_path / "fuente", populated_db.db_path())
        filas_fuente = ventas_db.get_conn().execute("SELECT COUNT(*) FROM ventas").fetchone()[0]

        # Alterar la local (simula datos locales desactualizados)
        extra = dict(
            id_articulo="09999",
            nom_articulo="EXTRA",
            id_linea="01AD",
            nom_linea="LINEA",
            id_cliente="0001",
            doc_cliente="20100000001",
            nom_cliente="CLIENTE X",
            tpo_doc="F012",
            serie_doc="012",
            nro_doc="999999",
            moneda="Soles",
            cantidad=1.0,
            soles=1.0,
            precio_unitario=1.0,
            anho=2024,
            mes=12,
            fecha_orig="2024-12-01",
            cod_sucursal="01",
            nom_sucursal="LIMA",
            departamento="LIMA",
            provincia="LIMA",
            distrito="MIRAFLORES",
            id_vendedor="01177",
            nom_vendedor="V",
            file_source="test",
            mes_ref="2024-12",
            tipo_operacion="",
            factura_ref_serie="",
            factura_ref_nro="",
            folio_unico="",
        )
        from src.core.xls_processor import derivar_campos

        derivar_campos(extra)
        ventas_db.insert_ventas(ventas_db.get_conn(), [extra])
        antes = ventas_db.get_conn().execute("SELECT COUNT(*) FROM ventas").fetchone()[0]
        assert antes > filas_fuente

        # Después de sincronizar, la local debe volver a ser la fuente
        progresos = []

        def cb(*args):
            progresos.append(args)

        stats = sincronizar_desde_remota([tmp_path / "fuente"], progress_cb=cb)
        assert stats["filas"] == filas_fuente
        assert stats["origen"] == str(fuente)
        despues = ventas_db.get_conn().execute("SELECT COUNT(*) FROM ventas").fetchone()[0]
        assert despues == filas_fuente

    def test_falla_sin_fuente(self, populated_db, tmp_path):
        from src.core.db_network import sincronizar_desde_remota

        with pytest.raises(RuntimeError):
            sincronizar_desde_remota([tmp_path / "sin_historial"])

    def test_rechaza_archivo_invalido(self, populated_db, tmp_path):
        import sqlite3

        from src.core.db_network import sincronizar_desde_remota

        malo = tmp_path / "malo"
        malo.mkdir()
        (malo / "historial.db").write_bytes(b"basura")
        with pytest.raises((sqlite3.DatabaseError, ValueError)):
            sincronizar_desde_remota([malo])


class TestSnapshotConsistente:
    def test_produce_db_valida(self, populated_db, tmp_path):
        from src.core.db_network import snapshot_consistente

        fuente = _copiar_db_local_a(tmp_path / "snap", populated_db.db_path())
        destino = tmp_path / "snap_copy.db"
        snapshot_consistente(fuente, destino)
        import sqlite3

        conn = sqlite3.connect(destino)
        try:
            ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
            tablas = {
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        finally:
            conn.close()
        assert ok == "ok"
        assert "ventas" in tablas


class TestSincronizarParcial:
    """Import parcial (últimos N años) con schema de la fat DB de g360-ventas-db."""

    @staticmethod
    def _fat_db(populated_db, tmp_path):
        """Fuente que imita a la fat DB: con ord_compra (F2, igual que local)
        y filas de varios años (con columnas extra informadas)."""
        import sqlite3

        _copiar_db_local_a(tmp_path / "fat", populated_db.db_path())
        fuente = tmp_path / "fat" / "historial.db"
        conn = sqlite3.connect(fuente, timeout=30)
        conn.commit()

        def _fila(fecha, nro, oc, canal=""):
            base = dict(
                id_articulo="02211",
                original_sku="02211",
                nom_articulo="A",
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
                nom_cliente="C",
                tpo_doc="F012",
                serie_doc="012",
                nro_doc=nro,
                moneda="Soles",
                cantidad=1.0,
                soles=10.0,
                precio_unitario=10.0,
                anho=int(fecha[:4]),
                mes=int(fecha[5:7]),
                fecha_orig=fecha,
                cod_sucursal="01",
                nom_sucursal="LIMA",
                departamento="LIMA",
                provincia="LIMA",
                distrito="SAN ISIDRO",
                id_vendedor="01177",
                nom_vendedor="V",
                id_pedido="P1",
                file_source="tarui",
                mes_ref=fecha[:7],
                tipo_operacion="",
                factura_ref_serie="",
                factura_ref_nro="",
                folio_unico="",
                ord_compra=oc,
                canal_distribucion=canal,
            )
            cols = ",".join(f'"{k}"' for k in base)
            ph = ",".join("?" for _ in base)
            conn.execute(
                f'INSERT INTO "main"."ventas" ({cols}) VALUES ({ph})',
                [base[k] for k in base],
            )

        _fila("2010-05-10", "A001", "OC-2010", canal="CANAL-OLD")
        _fila("2013-05-10", "A002", "OC-2013")
        _fila("2020-05-10", "A003", "OC-2020", canal="CANAL-A")
        _fila("2024-11-20", "A004", "OC-2024")
        conn.commit()
        conn.close()
        return fuente

    def test_solo_ultimos_anyos_con_mapeo(self, populated_db, tmp_path):
        """Copía 10 años (desde fecha_max): descarta los viejos, copia
        ord_compra directo (F2), conserva las columnas extra y reconstruye
        los agregados."""
        from src.core import ventas_db
        from src.core.db_network import sincronizar_desde_remota_parcial

        fuente = self._fat_db(populated_db, tmp_path)
        stats = sincronizar_desde_remota_parcial(anyos=10, rutas=[fuente.parent])

        # fecha_max de la fuente = 2024-11-20 → corte = 2014-11-20
        assert stats["fecha_corte"] == "2014-11-20"
        # Quedan: 5 filas del fixture (2024) + 2020 + 2024 = 7; se van 2010 y 2013
        assert stats["filas"] == 7

        conn = ventas_db.get_conn()
        try:
            n = conn.execute("SELECT COUNT(*) FROM ventas").fetchone()[0]
            assert n == 7
            fmin, fmax = conn.execute(
                "SELECT MIN(fecha_orig), MAX(fecha_orig) FROM ventas"
            ).fetchone()
            assert fmin == "2020-05-10"
            assert fmax == "2024-11-20"
            # ord_compra copiado directo (F2, identidad)
            assert (
                conn.execute("SELECT COUNT(*) FROM ventas WHERE ord_compra = 'OC-2024'").fetchone()[
                    0
                ]
                == 1
            )
            assert (
                conn.execute("SELECT COUNT(*) FROM ventas WHERE ord_compra = 'OC-2010'").fetchone()[
                    0
                ]
                == 0
            )
            # Columnas extra de la fat DB conservadas en el schema local
            cols = {r[1] for r in conn.execute("PRAGMA table_info(ventas)")}
            assert "ord_compra" in cols
            assert "orden_compra" not in cols
            assert "canal_distribucion" in cols
            assert (
                conn.execute(
                    "SELECT canal_distribucion FROM ventas WHERE fecha_orig='2020-05-10'"
                ).fetchone()[0]
                == "CANAL-A"
            )
            assert (
                conn.execute(
                    "SELECT COUNT(*) FROM ventas WHERE canal_distribucion='CANAL-OLD'"
                ).fetchone()[0]
                == 0
            )
            # Agregados reconstruidos
            assert conn.execute("SELECT COUNT(*) FROM agg_cliente_mes").fetchone()[0] > 0
            assert (
                conn.execute("SELECT COUNT(*) FROM stats_cache").fetchone()[0] >= 6
            )  # refresh_stats_cache repobló los KPI
        finally:
            conn.close()

    def test_sin_ventana_copia_todo(self, populated_db, tmp_path):
        """anyos=None → replica la fuente completa (mapeo y schema igual)."""
        from src.core import ventas_db
        from src.core.db_network import sincronizar_desde_remota_parcial

        fuente = self._fat_db(populated_db, tmp_path)
        stats = sincronizar_desde_remota_parcial(anyos=None, rutas=[fuente.parent])

        assert stats["fecha_corte"] is None
        assert stats["filas"] == 9  # 5 fixture + 4 extras

        conn = ventas_db.get_conn()
        try:
            assert conn.execute("SELECT COUNT(*) FROM ventas").fetchone()[0] == 9
            assert conn.execute("SELECT MIN(fecha_orig) FROM ventas").fetchone()[0] == "2010-05-10"
            assert (
                conn.execute("SELECT COUNT(*) FROM ventas WHERE ord_compra = 'OC-2010'").fetchone()[
                    0
                ]
                == 1
            )
            assert (
                conn.execute(
                    "SELECT canal_distribucion FROM ventas WHERE fecha_orig='2010-05-10'"
                ).fetchone()[0]
                == "CANAL-OLD"
            )
        finally:
            conn.close()

    def test_falla_sin_fuente(self, populated_db, tmp_path):
        from src.core.db_network import sincronizar_desde_remota_parcial

        with pytest.raises(RuntimeError):
            sincronizar_desde_remota_parcial(anyos=10, rutas=[tmp_path / "nada"])


class TestGetDbInfoQuick:
    """get_db_info_quick: validacion rapida sin integrity_check (lento en GB)."""

    def test_db_valida(self, populated_db, tmp_path):
        from src.core import ventas_db
        from src.core.db_network import get_db_info_quick

        db = _copiar_db_local_a(tmp_path / "fuente", populated_db.db_path())
        info = get_db_info_quick(db)
        assert info["exists"] is True
        assert info["error"] is None
        assert info["integrity"] == "ok"
        filas = ventas_db.get_conn().execute("SELECT COUNT(*) FROM ventas").fetchone()[0]
        assert info["rows"] == filas
        assert info["size_mb"] > 0
        assert info["fecha_max"] is not None

    def test_no_existe(self, tmp_path):
        from src.core.db_network import get_db_info_quick

        info = get_db_info_quick(tmp_path / "no_existe.db")
        assert info["exists"] is False
        assert info["error"] is not None

    def test_sin_tabla_ventas(self, tmp_path):
        import sqlite3

        from src.core.db_network import get_db_info_quick

        db = tmp_path / "otra.db"
        conn = sqlite3.connect(str(db))
        try:
            conn.execute("CREATE TABLE otra (id INTEGER)")
            conn.commit()
        finally:
            conn.close()
        info = get_db_info_quick(db)
        assert info["error"] is not None
        assert "ventas" in info["error"]

    def test_timeout_red_lenta_retorna_parcial(self, populated_db, tmp_path):
        """timeout_s=0 fuerza el aborto: schema OK pero conteo omitido."""
        from src.core.db_network import get_db_info_quick

        db = _copiar_db_local_a(tmp_path / "fuente", populated_db.db_path())
        info = get_db_info_quick(db, timeout_s=0)
        assert info["error"] is None
        assert info["exists"] is True
        assert info["partial"] is True
        assert info["size_mb"] > 0


class TestContrastar:
    def _origen(self, tmp_path, filas):
        import sqlite3

        d = tmp_path / "fuente"
        d.mkdir(parents=True, exist_ok=True)
        p = d / "historial.db"
        c = sqlite3.connect(str(p))
        c.execute(
            "CREATE TABLE ventas (tpo_doc TEXT, serie_doc TEXT, nro_doc TEXT, "
            "fecha_orig TEXT, soles REAL, estado_linea TEXT, "
            "orden_compra TEXT, ord_compra TEXT)"
        )
        c.executemany("INSERT INTO ventas VALUES (?,?,?,?,?,?,?,?)", filas)
        c.commit()
        c.close()
        return d

    def _local(self, conn, filas):
        for f in filas:
            conn.execute(
                "INSERT INTO ventas (tpo_doc, serie_doc, nro_doc, fecha_orig, soles, "
                "estado_linea, ord_compra, id_cliente, id_articulo, id_linea, mes_ref, "
                "cantidad, anho, mes) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (*f, 1.0, 2026, 9),
            )
        conn.commit()

    def test_sin_fuente(self, tmp_db, tmp_path):
        from src.core import ventas_db
        from src.core.db_network import contrastar_con_origen

        r = contrastar_con_origen(
            "2026-09-01", "2026-09-30", rutas=[tmp_path / "vacio"], conn=ventas_db.get_conn()
        )
        assert r["origen"] is None

    def test_detecta_columna_perdida(self, tmp_db, tmp_path):
        from src.core import ventas_db
        from src.core.db_network import contrastar_con_origen

        src = self._origen(
            tmp_path,
            [
                ("F01", "001", "1", "2026-09-20", 100.0, "LINEA TRADICIONAL", "", ""),
                ("F01", "001", "2", "2026-09-20", 200.0, "LINEA NUEVA", "", ""),
            ],
        )
        conn = ventas_db.get_conn()
        self._local(
            conn,
            [
                ("F01", "001", "1", "2026-09-20", 100.0, "", "", "C1", "A1", "01", "2026-09"),
                ("F01", "001", "2", "2026-09-20", 200.0, "", "", "C1", "A1", "01", "2026-09"),
            ],
        )
        r = contrastar_con_origen("2026-09-01", "2026-09-30", rutas=[src], conn=conn)
        assert r["ok"] is False
        assert "estado_linea" in r["columnas_perdidas"]
        assert r["columnas"]["estado_linea"] == {"local": 0, "origen": 2, "pierde": True}
        assert r["filas"] == {"local": 2, "origen": 2}
        assert r["folios_solo_local"] == 0 and r["folios_solo_origen"] == 0

    def test_ok_cuando_cubre_y_detecta_folios(self, tmp_db, tmp_path):
        from src.core import ventas_db
        from src.core.db_network import contrastar_con_origen

        src = self._origen(
            tmp_path,
            [
                ("F01", "001", "1", "2026-09-20", 100.0, "LINEA TRADICIONAL", "", ""),
            ],
        )
        conn = ventas_db.get_conn()
        self._local(
            conn,
            [
                (
                    "F01",
                    "001",
                    "1",
                    "2026-09-20",
                    100.0,
                    "LINEA TRADICIONAL",
                    "",
                    "C1",
                    "A1",
                    "01",
                    "2026-09",
                ),
                (
                    "F01",
                    "001",
                    "9",
                    "2026-09-20",
                    50.0,
                    "LINEA TRADICIONAL",
                    "",
                    "C1",
                    "A1",
                    "01",
                    "2026-09",
                ),
            ],
        )
        r = contrastar_con_origen("2026-09-01", "2026-09-30", rutas=[src], conn=conn)
        assert r["ok"] is True
        assert r["folios_solo_local"] == 1 and r["folios_solo_origen"] == 0


class TestContratoPostImport:
    def test_import_db_aplica_remap_oc(self, tmp_db, tmp_path):
        import sqlite3

        from src.core import ventas_db
        from src.core.ventas_db_backup import import_db

        fx = tmp_path / "fx" / "historial.db"
        fx.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(str(fx))
        c.execute(ventas_db.CREATE_TABLE_VENTAS)
        # F2: ord_compra ya viene en CREATE_TABLE_VENTAS (no hace falta ALTER).
        c.execute(
            "INSERT INTO ventas (id_articulo, id_linea, id_cliente, tpo_doc, serie_doc, nro_doc, "
            "fecha_orig, soles, cantidad, anho, mes, mes_ref, ord_compra) VALUES "
            "('A1','01','C1','F01','001','1','2026-09-20',100.0,10.0,2026,9,'2026-09','001561'),"
            "('A1','01','C1','F01','001','2','2026-09-20',200.0,20.0,2026,9,'2026-09','')"
        )
        c.commit()
        c.close()
        stats = import_db(fx)
        assert stats["filas"] == 2
        conn = ventas_db.get_conn()
        assert (
            conn.execute("SELECT ord_compra FROM ventas WHERE nro_doc = '1'").fetchone()[0]
            == "1561"
        )
        assert stats.get("orden_compra_remapeadas") == 1
        # Y el auditor ya no reporta O/C sin normalizar.
        assert not [
            h
            for h in ventas_db.auditar_completidad(conn)["hallazgos"]
            if h["regla"] == "oc_sin_normalizar"
        ]


class TestTraerFoliosFaltantes:
    def _origen(self, tmp_path, filas):
        import sqlite3

        d = tmp_path / "fuente"
        d.mkdir(parents=True, exist_ok=True)
        p = d / "historial.db"
        c = sqlite3.connect(str(p))
        c.execute(
            "CREATE TABLE ventas (tpo_doc TEXT, serie_doc TEXT, nro_doc TEXT, "
            "fecha_orig TEXT, soles REAL, id_cliente TEXT, id_articulo TEXT, "
            "id_linea TEXT, mes_ref TEXT, cantidad REAL, anho INTEGER, mes INTEGER, "
            "ord_compra TEXT)"
        )
        c.executemany("INSERT INTO ventas VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", filas)
        c.commit()
        c.close()
        return d

    def test_sin_fuente(self, tmp_db, tmp_path):
        from src.core.db_network import traer_folios_faltantes

        r = traer_folios_faltantes("2026-09-01", "2026-09-30", rutas=[tmp_path / "vacio"])
        assert r["origen"] is None and r["folios"] == 0

    def test_copia_solo_faltantes_y_remapea(self, tmp_db, tmp_path):
        from src.core import ventas_db
        from src.core.db_network import traer_folios_faltantes

        src = self._origen(
            tmp_path,
            [
                (
                    "F01",
                    "001",
                    "1",
                    "2026-09-20",
                    100.0,
                    "C1",
                    "A1",
                    "01",
                    "2026-09",
                    1.0,
                    2026,
                    9,
                    "001561",
                ),
                (
                    "F01",
                    "001",
                    "2",
                    "2026-09-20",
                    200.0,
                    "C1",
                    "A1",
                    "01",
                    "2026-09",
                    1.0,
                    2026,
                    9,
                    "0042",
                ),
            ],
        )
        conn = ventas_db.get_conn()
        # F2: ord_compra ya viene en CREATE_TABLE_VENTAS (no hace falta ALTER).
        conn.execute(
            "INSERT INTO ventas (tpo_doc, serie_doc, nro_doc, fecha_orig, soles, "
            "id_cliente, id_articulo, id_linea, mes_ref, cantidad, anho, mes) VALUES "
            "('F01','001','1','2026-09-20',100.0,'C1','A1','01','2026-09',1.0,2026,9)"
        )
        conn.commit()
        r = traer_folios_faltantes("2026-09-01", "2026-09-30", rutas=[src], conn=conn)
        assert r["folios"] == 1 and r["filas"] == 1
        # El existente no se duplicó ni se tocó.
        assert conn.execute("SELECT COUNT(*) FROM ventas WHERE nro_doc = '1'").fetchone()[0] == 1
        # El nuevo vino con todo y su O/C quedó normalizada in situ por el remap (F2).
        assert (
            conn.execute("SELECT ord_compra FROM ventas WHERE nro_doc = '2'").fetchone()[0] == "42"
        )
        assert r["oc_remapeadas"] == 1
        # Idempotente: segunda pasada no trae nada.
        r2 = traer_folios_faltantes("2026-09-01", "2026-09-30", rutas=[src], conn=conn)
        assert (r2["folios"], r2["filas"]) == (0, 0)
