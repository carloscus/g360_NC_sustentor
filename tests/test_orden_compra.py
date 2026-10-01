"""Orden de compra: normalización, alias por cliente y backfill desde raw/."""

import sqlite3

import pytest

from src.core import ventas_db
from src.core.oc_backfill import backfill_orden_compra
from src.core.xls_processor import normalize_orden_compra

HEADER = (
    "ANHO,MES,ID_CLIENTE,DOC_CLIENTE,NOM_CLIENTE,ID_LINEA,NOM_LINEA,"
    "ID_ARTICULO,NOM_ARTICULO,ID_VENDEDOR,NOM_VENDEDOR,TPO_DOC,SERIE_DOC,"
    "NRO_DOC,FECHA_ORIG,CANTIDAD,SOLES,ORD_COMPRA"
)


def fila(nro, oc="", cliente="00002482", fecha="2026-09-20"):
    return (
        f"2026,9,{cliente},20100047218,TAI LOY,01,GASEOSAS,000123,GASEOSA,"
        f"178,MILCA,F01,001,{nro},{fecha},10,250.00,{oc}"
    )


def _db(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    conn.execute(ventas_db.CREATE_TABLE_VENTAS)
    # F4: dim_vendedor lo crea init_db con forma de contrato (no mínima).
    return conn


def _est(tmp_path):
    """Sidecar temporal aislado (oc_alias + day_state, no toca producción)."""
    conn = sqlite3.connect(str(tmp_path / "e.db"))
    conn.executescript(ventas_db.CREATE_ESTADO_TABLES)
    conn.commit()
    return conn


def _vender(conn, nro, oc="", cliente="00002482", fecha="2026-09-20"):
    # F2: la O/C vive en ord_compra (normalizada); orden_compra no existe.
    conn.execute(
        "INSERT INTO ventas (id_articulo, id_linea, id_cliente, tpo_doc, serie_doc, "
        "nro_doc, fecha_orig, mes_ref, cantidad, soles, anho, mes, id_vendedor, "
        "nom_vendedor, ord_compra) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "000123",
            "01",
            cliente,
            "F01",
            "001",
            nro,
            fecha,
            "2026-09",
            10.0,
            250.0,
            2026,
            9,
            "178",
            "MILCA",
            oc,
        ),
    )


class TestNormalizar:
    @pytest.mark.parametrize(
        "crudo,canon",
        [
            ("000000000001385", "1385"),
            ("001561", "1561"),
            ("14608982", "14608982"),
            (" 001561 ", "1561"),
            ("oc2020245992", "OC2020245992"),
            ("OC261448", "OC261448"),
            ("P01275", "P01275"),
            ("002001015PRT26", "002001015PRT26"),
            ("0", "0"),
            ("", ""),
            ("nan", ""),
            (None, ""),
        ],
    )
    def test_casos_reales(self, crudo, canon):
        assert normalize_orden_compra(crudo) == canon

    def test_idempotente(self):
        for v in ("1385", "OC2020245992", "P01275", "0"):
            assert normalize_orden_compra(normalize_orden_compra(v)) == v

    def test_quita_caracteres_de_control(self):
        # El export a veces prefija basura no imprimible (visto real en prod).
        assert normalize_orden_compra(chr(31) * 3 + "00091900") == "91900"


class TestAlias:
    def test_sin_colision_guarda_norm(self, tmp_path):
        conn = _db(tmp_path)
        est = _est(tmp_path)
        ventas_db.init_db(conn)
        assert ventas_db.oc_alias_upsert(est, "C1", "001561", "1561") == []
        _vender(conn, "1", oc="")
        conn.commit()
        conn.close()
        est.close()

    def test_colision_no_fusiona(self, tmp_path):
        conn = _db(tmp_path)
        est = _est(tmp_path)
        ventas_db.init_db(conn)
        assert ventas_db.oc_alias_upsert(est, "C1", "001561", "1561") == []
        otros = ventas_db.oc_alias_upsert(est, "C1", "1561", "1561")
        assert otros == ["001561"]
        pend = ventas_db.ocs_pendientes(conn, est)
        assert len(pend) == 1 and pend[0]["norm"] == "1561"
        assert sorted(pend[0]["raws"]) == ["001561", "1561"]
        conn.close()
        est.close()

    def test_colision_es_por_cliente(self, tmp_path):
        conn = _db(tmp_path)
        est = _est(tmp_path)
        ventas_db.init_db(conn)
        ventas_db.oc_alias_upsert(est, "C1", "001561", "1561")
        assert ventas_db.oc_alias_upsert(est, "C2", "1561", "1561") == []
        assert ventas_db.ocs_pendientes(conn, est) == []
        conn.close()
        est.close()

    def test_resolver_confirmado_fusiona(self, tmp_path):
        conn = _db(tmp_path)
        est = _est(tmp_path)
        ventas_db.init_db(conn)
        ventas_db.oc_alias_upsert(est, "C1", "001561", "1561")
        ventas_db.oc_alias_upsert(est, "C1", "1561", "1561")
        _vender(conn, "1", oc="001561", cliente="C1")
        _vender(conn, "2", oc="1561", cliente="C1")
        conn.commit()
        assert ventas_db.oc_resolver(conn, "C1", "1561", "confirmado", est) == 2
        assert {r[0] for r in conn.execute("SELECT DISTINCT ord_compra FROM ventas")} == {"1561"}
        assert ventas_db.ocs_pendientes(conn, est) == []
        conn.close()
        est.close()

    def test_resolver_separado_no_toca(self, tmp_path):
        conn = _db(tmp_path)
        est = _est(tmp_path)
        ventas_db.init_db(conn)
        ventas_db.oc_alias_upsert(est, "C1", "001561", "1561")
        ventas_db.oc_alias_upsert(est, "C1", "1561", "1561")
        _vender(conn, "1", oc="001561", cliente="C1")
        conn.commit()
        assert ventas_db.oc_resolver(conn, "C1", "1561", "separado", est) == 0
        assert conn.execute("SELECT ord_compra FROM ventas").fetchone()[0] == "001561"
        conn.close()
        est.close()

    def test_resolver_invalido(self, tmp_path):
        conn = _db(tmp_path)
        est = _est(tmp_path)
        ventas_db.init_db(conn)
        with pytest.raises(ValueError):
            ventas_db.oc_resolver(conn, "C1", "1561", "fusionar", est)
        conn.close()
        est.close()


def _raw(tmp_path, nombre, filas):
    d = tmp_path / "raw"
    d.mkdir(exist_ok=True)
    (d / nombre).write_text(HEADER + "\n" + "\n".join(filas) + "\n", encoding="utf-8")
    return d


class TestBackfill:
    def test_rellena_vacios_y_respeta_llenos(self, tmp_path):
        conn = _db(tmp_path)
        est = _est(tmp_path)
        ventas_db.init_db(conn)
        _vender(conn, "100", oc="")
        _vender(conn, "101", oc="MANUAL")
        conn.commit()
        raw = _raw(
            tmp_path,
            "ventas_2026-09-20.csv",
            [fila("100", "001561"), fila("101", "009999"), fila("999", "007777")],
        )
        r = backfill_orden_compra(conn, raw, desde="2026-01", conn_estado=est)
        assert r["filas_actualizadas"] == 1
        # sin_match = docs sin filas actualizadas: el 999 (no existe) y el 101 (ya lleno).
        assert r["sin_match"] == 2
        assert (
            conn.execute("SELECT ord_compra FROM ventas WHERE nro_doc='100'").fetchone()[0]
            == "1561"
        )
        assert (
            conn.execute("SELECT ord_compra FROM ventas WHERE nro_doc='101'").fetchone()[0]
            == "MANUAL"
        )
        conn.close()
        est.close()

    def test_colision_guarda_crudo_y_reporta(self, tmp_path):
        conn = _db(tmp_path)
        est = _est(tmp_path)
        ventas_db.init_db(conn)
        _vender(conn, "100", oc="")
        _vender(conn, "101", oc="")
        conn.commit()
        raw = _raw(tmp_path, "ventas_2026-09-20.csv", [fila("100", "001561"), fila("101", "01561")])
        r = backfill_orden_compra(conn, raw, desde="2026-01", conn_estado=est)
        assert r["colisiones"] == 1
        vals = dict(conn.execute("SELECT nro_doc, ord_compra FROM ventas").fetchall())
        # El primero fusiona; el segundo colisiona y queda en crudo sin fusionar.
        assert vals == {"100": "1561", "101": "01561"}
        assert len(r["pendientes"]) == 1
        assert r["pendientes"][0]["norm"] == "1561"
        assert sorted(r["pendientes"][0]["raws"]) == ["001561", "01561"]
        conn.close()
        est.close()

    def test_idempotente(self, tmp_path):
        conn = _db(tmp_path)
        est = _est(tmp_path)
        ventas_db.init_db(conn)
        _vender(conn, "100", oc="")
        conn.commit()
        raw = _raw(tmp_path, "ventas_2026-09-20.csv", [fila("100", "001561")])
        r1 = backfill_orden_compra(conn, raw, desde="2026-01", conn_estado=est)
        r2 = backfill_orden_compra(conn, raw, desde="2026-01", conn_estado=est)
        assert (r1["filas_actualizadas"], r2["filas_actualizadas"]) == (1, 0)
        assert r2["omitidos_ya_procesados"] == 1
        assert conn.execute("SELECT ord_compra FROM ventas").fetchone()[0] == "1561"
        conn.close()
        est.close()

    def test_filtra_por_desde_e_ignora_ilegibles(self, tmp_path):
        conn = _db(tmp_path)
        est = _est(tmp_path)
        ventas_db.init_db(conn)
        _vender(conn, "100", oc="")
        conn.commit()
        raw = _raw(tmp_path, "ventas_2026-09-20.csv", [fila("100", "001561")])
        (raw / "ventas_2020-01.csv").write_text("basura sin header\nxxx\n", encoding="utf-8")
        (raw / "notas.txt").write_text("no es un export\n", encoding="utf-8")
        r = backfill_orden_compra(conn, raw, desde="2026-01", conn_estado=est)
        assert r["archivos"] == 1 and r["filas_actualizadas"] == 1
        conn.close()
        est.close()


class TestDelta:
    def test_export_import_roundtrip(self, tmp_path):
        from src.core.delta_replay import exportar_delta, importar_delta

        conn = _db(tmp_path)
        est = _est(tmp_path)
        ventas_db.init_db(conn)
        ventas_db.oc_alias_upsert(est, "C1", "001561", "1561")
        ventas_db.oc_alias_upsert(est, "C1", "01561", "1561")
        ventas_db.oc_resolver(conn, "C1", "1561", "confirmado", est)
        conn.commit()
        ventas_db.record_day_capture(conn, "2026-09-28", est)
        dest = tmp_path / "delta.db"
        out = exportar_delta(dest, est)
        assert out == {"oc_alias": 2, "day_state": 1}
        # Wipe simulado: tablas vacías de nuevo.
        est.execute("DELETE FROM oc_alias")
        est.execute("DELETE FROM day_state")
        est.commit()
        assert ventas_db.ocs_pendientes(conn, est) == []
        back = importar_delta(dest, est)
        assert back == {"oc_alias": 2, "day_state": 1}
        assert (
            est.execute("SELECT estado FROM oc_alias WHERE oc_raw='001561'").fetchone()[0]
            == "confirmado"
        )
        assert est.execute("SELECT estado FROM day_state").fetchone()[0] == "provisional"
        conn.close()
        est.close()

    def test_backfill_respeta_confirmado_y_separado(self, tmp_path):
        conn = _db(tmp_path)
        est = _est(tmp_path)
        ventas_db.init_db(conn)
        ventas_db.oc_alias_upsert(est, "00002482", "001561", "1561")
        ventas_db.oc_alias_upsert(est, "00002482", "01561", "1561")
        ventas_db.oc_resolver(conn, "00002482", "1561", "confirmado", est)
        _vender(conn, "100", oc="", cliente="00002482")
        _vender(conn, "101", oc="", cliente="00002482")
        conn.commit()
        raw = _raw(
            tmp_path,
            "ventas_2026-09-20.csv",
            [fila("100", "001561", cliente="00002482"), fila("101", "01561", cliente="00002482")],
        )
        r = backfill_orden_compra(conn, raw, desde="2026-01", conn_estado=est)
        # El confirmado fusiona aunque el crudo difiera; no genera pendiente nuevo.
        assert r["colisiones"] == 0
        assert dict(conn.execute("SELECT nro_doc, ord_compra FROM ventas").fetchall()) == {
            "100": "1561",
            "101": "1561",
        }
        assert ventas_db.ocs_pendientes(conn, est) == []
        conn.close()
        est.close()

    def test_no_degrada_revision(self, tmp_path):
        from src.core.delta_replay import exportar_delta

        conn = _db(tmp_path)
        est = _est(tmp_path)
        ventas_db.init_db(conn)
        ventas_db.oc_alias_upsert(est, "00002482", "001561", "1561")
        ventas_db.oc_alias_upsert(est, "00002482", "01561", "1561")
        ventas_db.oc_resolver(conn, "00002482", "1561", "separado", est)
        conn.commit()
        dest = tmp_path / "delta.db"
        exportar_delta(dest, est)
        # Re-correr el backfill no debe reabrir la revisión.
        raw = _raw(tmp_path, "ventas_2026-09-20.csv", [fila("100", "001561", cliente="00002482")])
        _vender(conn, "100", oc="", cliente="00002482")
        conn.commit()
        backfill_orden_compra(conn, raw, desde="2026-01", conn_estado=est)
        assert (
            est.execute("SELECT estado FROM oc_alias WHERE oc_raw='001561'").fetchone()[0]
            == "separado"
        )
        assert ventas_db.ocs_pendientes(conn, est) == []
        conn.close()


class TestRemapOrigen:
    # F2: una sola columna (ord_compra). _vender escribe el crudo directo;
    # el remap normaliza in situ. No hay "columna par" que respetar.
    def test_normaliza_crudo(self, tmp_path):
        from src.core.oc_backfill import remapar_orden_compra_desde_origen

        conn = _db(tmp_path)
        est = _est(tmp_path)
        ventas_db.init_db(conn)
        _vender(conn, "100", oc="001561")
        _vender(conn, "101", oc="")
        conn.commit()
        r = remapar_orden_compra_desde_origen(conn, conn_estado=est)
        assert r["filas_actualizadas"] == 1
        assert (
            conn.execute("SELECT ord_compra FROM ventas WHERE nro_doc='100'").fetchone()[0]
            == "1561"
        )
        conn.close()
        est.close()

    def test_respeta_normalizado_y_es_idempotente(self, tmp_path):
        from src.core.oc_backfill import remapar_orden_compra_desde_origen

        conn = _db(tmp_path)
        est = _est(tmp_path)
        ventas_db.init_db(conn)
        _vender(conn, "100", oc="MANUAL")
        conn.commit()
        r1 = remapar_orden_compra_desde_origen(conn, conn_estado=est)
        r2 = remapar_orden_compra_desde_origen(conn, conn_estado=est)
        assert (r1["filas_actualizadas"], r2["filas_actualizadas"]) == (0, 0)
        assert (
            conn.execute("SELECT ord_compra FROM ventas WHERE nro_doc='100'").fetchone()[0]
            == "MANUAL"
        )
        conn.close()
        est.close()

    def test_colision_a_pendiente(self, tmp_path):
        from src.core.oc_backfill import remapar_orden_compra_desde_origen

        conn = _db(tmp_path)
        est = _est(tmp_path)
        ventas_db.init_db(conn)
        _vender(conn, "100", oc="001561")
        _vender(conn, "101", oc="01561")
        conn.commit()
        r = remapar_orden_compra_desde_origen(conn, conn_estado=est)
        assert r["colisiones"] == 1
        # El primero fusiona; el segundo colisiona y queda en crudo (pendiente).
        # (Comportamiento preservado de F1: order-dependent, no se fusiona sin revisión.)
        assert (
            conn.execute("SELECT ord_compra FROM ventas WHERE nro_doc='100'").fetchone()[0]
            == "1561"
        )
        assert (
            conn.execute("SELECT ord_compra FROM ventas WHERE nro_doc='101'").fetchone()[0]
            == "01561"
        )
        assert len(ventas_db.ocs_pendientes(conn, est)) == 1
        conn.close()
        est.close()

    def test_sin_columna_omite(self, tmp_path):
        from src.core.oc_backfill import remapar_orden_compra_desde_origen

        conn = sqlite3.connect(str(tmp_path / "min.db"))
        conn.execute("CREATE TABLE ventas (id INTEGER PRIMARY KEY, orden_compra TEXT)")
        conn.commit()
        assert remapar_orden_compra_desde_origen(conn)["omitido"] == "falta columna"
        conn.close()
