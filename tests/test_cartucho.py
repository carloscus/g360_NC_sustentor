"""Cartucho: exportar/importar, superset vs merge, unión de sidecar."""

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from src.core import ventas_db
from src.core.cartucho import (
    CARTUCHO_VERSION,
    adoptar_lineas,
    diagnostico_cobertura,
    es_superset,
    exportar_cartucho,
    importar_cartucho,
    leer_allowlist_cartucho,
    reconstruir_day_state,
    unir_sidecar,
)


class TestAdoptarLineas:
    def test_lee_sin_extraer_y_adopta(self, tmp_db, tmp_path):
        conn = ventas_db.get_conn()
        _vender(conn, "2026-09-28", "1")
        r = exportar_cartucho(tmp_path / "cartucho-test")
        # leer sin extraer los 3 GB
        leidas = leer_allowlist_cartucho(r["zip"])
        assert "01" in leidas and len(leidas) == 24
        # adoptar otras líneas de un cartucho ajeno
        antes = list(ventas_db.allowed_lines())
        res = adoptar_lineas(["ZZ", "01"])
        assert res["antes"] == antes
        assert ventas_db.allowed_lines() == ["01", "ZZ"]
        # y volver atrás no rompe nada
        adoptar_lineas(antes)
        assert ventas_db.allowed_lines() == sorted(antes)


class TestSemillaSuelta:
    def test_db_suelto_se_importa_como_semilla(self, tmp_db, tmp_path):
        import sqlite3

        # .db suelto con forma de contrato (imita la carpeta de ventas-db)
        suelto = tmp_path / "suelto.db"
        c = sqlite3.connect(str(suelto))
        c.execute(ventas_db.CREATE_TABLE_VENTAS)
        c.executescript(ventas_db.CREATE_AUDIT_TABLES)
        c.executescript(ventas_db.CREATE_DIM_TABLES)
        c.executescript(ventas_db.CREATE_FACT_TABLES)
        c.execute("PRAGMA user_version = 3")
        c.execute(
            "INSERT INTO ventas (id_articulo, id_linea, id_cliente, tpo_doc, serie_doc, "
            "nro_doc, fecha_orig, mes_ref, cantidad, soles, anho, mes) VALUES "
            "('A1','01','C1','F01','001','1','2026-09-28','2026-09',1.0,10.0,2026,9)"
        )
        c.commit()
        c.close()
        # renombrar a historial.db para el bootstrap por archivo
        seed = tmp_path / "historial.db"
        suelto.rename(seed)
        r = importar_cartucho(seed)
        assert r["modo"] == "reemplazar"
        assert r["manifiesto"]["tipo"] == "semilla"
        conn = ventas_db.get_conn()
        assert conn.execute("SELECT COUNT(*) FROM ventas").fetchone()[0] == 1

    def test_carpeta_con_db_suelta(self, tmp_db, tmp_path):
        import sqlite3

        d = tmp_path / "data-ajena"
        d.mkdir()
        c = sqlite3.connect(str(d / "historial.db"))
        c.execute(ventas_db.CREATE_TABLE_VENTAS)
        c.executescript(ventas_db.CREATE_AUDIT_TABLES)
        c.executescript(ventas_db.CREATE_DIM_TABLES)
        c.executescript(ventas_db.CREATE_FACT_TABLES)
        c.execute("PRAGMA user_version = 3")
        c.execute(
            "INSERT INTO ventas (id_articulo, id_linea, id_cliente, tpo_doc, serie_doc, "
            "nro_doc, fecha_orig, mes_ref, cantidad, soles, anho, mes) VALUES "
            "('A1','01','C1','F01','001','1','2026-09-28','2026-09',1.0,10.0,2026,9)"
        )
        c.commit()
        c.close()
        r = importar_cartucho(d)
        assert r["manifiesto"]["tipo"] == "semilla"
        conn = ventas_db.get_conn()
        assert conn.execute("SELECT COUNT(*) FROM ventas").fetchone()[0] == 1


class TestReconstruirDayState:
    def test_deriva_cerrados_del_archivo(self, tmp_db):
        conn = ventas_db.get_conn()
        _vender(conn, "2026-09-27", "1", soles=10.0)
        _vender(conn, "2026-09-28", "2", soles=20.0)
        _vender(conn, "2026-09-28", "3", soles=30.0)
        est = ventas_db.connect_estado()
        n = reconstruir_day_state(conn, est)
        assert n == 2
        rows = dict(est.execute("SELECT dia, estado FROM day_state").fetchall())
        assert rows == {"2026-09-27": "cerrado", "2026-09-28": "cerrado"}
        tot = est.execute("SELECT filas, soles FROM day_state WHERE dia='2026-09-28'").fetchone()
        assert tuple(tot) == (2, 50.0)
        est.close()


def _vender(conn, fecha, nro, oc="", cliente="C1", soles=10.0):
    conn.execute(
        "INSERT INTO ventas (id_articulo, id_linea, id_cliente, tpo_doc, serie_doc, "
        "nro_doc, fecha_orig, mes_ref, cantidad, soles, anho, mes, ord_compra) VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "A1",
            "01",
            cliente,
            "F01",
            "001",
            nro,
            fecha,
            fecha[:7],
            1.0,
            soles,
            int(fecha[:4]),
            int(fecha[5:7]),
            oc,
        ),
    )
    conn.commit()


def _cartucho_minimo(dest: Path, ventas_filas, alias_filas=()):
    """Arma una carpeta cartucho válida a mano (incoming controlado)."""
    dest.mkdir(parents=True, exist_ok=True)
    pdb = dest / "historial.db"
    c = sqlite3.connect(str(pdb))
    c.execute(ventas_db.CREATE_TABLE_VENTAS)
    c.executescript(ventas_db.CREATE_AUDIT_TABLES)
    c.executescript(ventas_db.CREATE_DIM_TABLES)
    c.executescript(ventas_db.CREATE_FACT_TABLES)
    c.execute("PRAGMA user_version = 3")
    for f in ventas_filas:
        c.execute(
            "INSERT INTO ventas (id_articulo, id_linea, id_cliente, tpo_doc, serie_doc, "
            "nro_doc, fecha_orig, mes_ref, cantidad, soles, anho, mes, ord_compra) VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            f,
        )
    # day_checksums para el chequeo rápido
    c.execute(
        "INSERT INTO day_checksums (dia, total_filas, total_soles, checksum) "
        "SELECT substr(fecha_orig,1,10), COUNT(*), ROUND(SUM(soles),2), '' "
        "FROM ventas GROUP BY 1"
    )
    c.commit()
    n = c.execute("SELECT COUNT(*) FROM ventas").fetchone()[0]
    fmax = c.execute("SELECT MAX(fecha_orig) FROM ventas").fetchone()[0]
    fmin = c.execute("SELECT MIN(fecha_orig) FROM ventas").fetchone()[0]
    c.close()
    se = sqlite3.connect(str(dest / "estado_sustentor.db"))
    se.executescript(ventas_db.CREATE_ESTADO_TABLES)
    for a in alias_filas:
        se.execute("INSERT INTO oc_alias (id_cliente, oc_raw, oc_norm, estado) VALUES (?,?,?,?)", a)
    se.commit()
    se.close()

    def _sha(p):
        h = hashlib.sha256()
        h.update(Path(p).read_bytes())
        return h.hexdigest()

    dias = {}
    cc = sqlite3.connect(str(pdb))
    for d, nn, ss in cc.execute("SELECT dia, total_filas, total_soles FROM day_checksums"):
        dias[d] = [nn, ss]
    cc.close()
    man = {
        "cartucho_version": CARTUCHO_VERSION,
        "generado_en": "2026-09-29T00:00:00",
        "generado_por": "TEST",
        "contrato_version": 3,
        "db": {
            "archivo": "historial.db",
            "sha256": _sha(pdb),
            "bytes": pdb.stat().st_size,
            "user_version": 3,
            "ventas_filas": n,
            "soles_total": 0,
            "fecha_min": fmin,
            "fecha_max": fmax,
            "tablas": {"ventas": n},
        },
        "sidecar": {
            "archivo": "estado_sustentor.db",
            "sha256": _sha(dest / "estado_sustentor.db"),
            "oc_alias": {"total": len(alias_filas)},
            "day_state": {},
        },
        "dias": dias,
        "config": {"allowlist": ["01"]},
    }
    (dest / "CARTUCHO.json").write_text(json.dumps(man), encoding="utf-8")
    (dest / "config_sanitizado.json").write_text("{}", encoding="utf-8")
    return dest


def _fila(fecha, nro, oc="", cliente="C1", soles=10.0):
    return (
        "A1",
        "01",
        cliente,
        "F01",
        "001",
        nro,
        fecha,
        fecha[:7],
        1.0,
        soles,
        int(fecha[:4]),
        int(fecha[5:7]),
        oc,
    )


class TestExportar:
    def test_genera_carpeta_y_manifiesto(self, tmp_db, tmp_path):
        conn = ventas_db.get_conn()
        _vender(conn, "2026-09-28", "1", oc="1561")
        r = exportar_cartucho(tmp_path / "cartucho-test", comprimir=False)
        carp = Path(r["carpeta"])
        assert (carp / "historial.db").exists()
        assert (carp / "estado_sustentor.db").exists()
        assert (carp / "config_sanitizado.json").exists()
        man = json.loads((carp / "CARTUCHO.json").read_text(encoding="utf-8"))
        assert man["cartucho_version"] == CARTUCHO_VERSION
        assert man["tipo"] == "trabajo"
        assert man["db"]["ventas_filas"] == 1
        assert man["db"]["fecha_max"] == "2026-09-28"
        # sha declarado coincide con el archivo
        h = hashlib.sha256()
        h.update((carp / "historial.db").read_bytes())
        assert h.hexdigest() == man["db"]["sha256"]
        # config sin secretos aunque el config real los tenga
        cfg = json.loads((carp / "config_sanitizado.json").read_text(encoding="utf-8"))
        assert not any(k in cfg for k in ("intranet", "supabase"))

    def test_comprime_a_zip_un_solo_archivo(self, tmp_db, tmp_path):
        import zipfile

        conn = ventas_db.get_conn()
        _vender(conn, "2026-09-28", "1", oc="1561")
        r = exportar_cartucho(tmp_path / "cartucho-test")
        assert r["zip"].endswith(".zip")
        assert Path(r["zip"]).exists()
        assert r["carpeta"] is None  # carpeta borrada, el zip ES el cartucho
        with zipfile.ZipFile(r["zip"]) as zf:
            assert set(zf.namelist()) == {
                "historial.db",
                "estado_sustentor.db",
                "config_sanitizado.json",
                "CARTUCHO.json",
            }

    def test_import_desde_zip(self, tmp_db, tmp_path):
        conn = ventas_db.get_conn()
        _vender(conn, "2026-09-28", "1", soles=10.0)
        ventas_db.record_day_checksum(conn, "2026-09-28")
        conn.commit()
        r = exportar_cartucho(tmp_path / "cartucho-test")
        # Vaciar local y reimportar desde el zip: roundtrip completo.
        conn.execute("DELETE FROM ventas")
        conn.commit()
        r2 = importar_cartucho(r["zip"])
        assert r2["modo"] == "reemplazar"
        assert conn.execute("SELECT COUNT(*) FROM ventas").fetchone()[0] == 1


class TestImportar:
    def test_rechaza_sin_manifiesto(self, tmp_db, tmp_path):
        d = tmp_path / "vacio"
        d.mkdir()
        with pytest.raises(FileNotFoundError):
            importar_cartucho(d)

    def test_rechaza_sha_roto(self, tmp_db, tmp_path):
        conn = ventas_db.get_conn()
        _vender(conn, "2026-09-28", "1")
        cart = _cartucho_minimo(tmp_path / "cart", [_fila("2026-09-28", "9")])
        # corromper el archivo entrante
        with open(cart / "historial.db", "ab") as f:
            f.write(b"X")
        with pytest.raises(ValueError, match="sha256"):
            importar_cartucho(cart)

    def test_superset_reemplaza(self, tmp_db, tmp_path):
        conn = ventas_db.get_conn()
        _vender(conn, "2026-09-28", "1", soles=10.0)
        ventas_db.record_day_checksum(conn, "2026-09-28")
        conn.commit()
        # entrante con EL MISMO día pero más filas -> superset
        cart = _cartucho_minimo(
            tmp_path / "cart",
            [
                _fila("2026-09-28", "1", soles=10.0),
                _fila("2026-09-28", "2", soles=20.0),
            ],
        )
        r = importar_cartucho(cart)
        assert r["modo"] == "reemplazar"
        n = conn.execute("SELECT COUNT(*) FROM ventas").fetchone()[0]
        assert n == 2
        # day_state reconstruido del archivo: cerrado con conteos reales
        est = ventas_db.connect_estado()
        row = est.execute("SELECT estado, filas FROM day_state WHERE dia='2026-09-28'").fetchone()
        assert tuple(row) == ("cerrado", 2)
        est.close()

    def test_no_superset_mergea(self, tmp_db, tmp_path):
        conn = ventas_db.get_conn()
        _vender(conn, "2026-09-28", "1", soles=10.0)
        ventas_db.record_day_checksum(conn, "2026-09-28")
        conn.commit()
        # entrante con OTRO folio del mismo día (ni más ni menos filas):
        # no es superset en cobertura de folios -> merge
        cart = _cartucho_minimo(
            tmp_path / "cart",
            [
                _fila("2026-09-27", "9", soles=50.0),
            ],
        )
        r = importar_cartucho(cart)
        assert r["modo"] == "mergear"
        n = conn.execute("SELECT COUNT(*) FROM ventas").fetchone()[0]
        assert n == 2  # 1 local + 1 traído, nada pisado


class TestSidecar:
    def test_union_sin_conflicto(self, tmp_path):
        est = sqlite3.connect(str(tmp_path / "loc.db"))
        est.executescript(ventas_db.CREATE_ESTADO_TABLES)
        est.execute("INSERT INTO oc_alias VALUES ('C','A','N','auto',NULL)")
        est.commit()
        inc = tmp_path / "inc.db"
        e2 = sqlite3.connect(str(inc))
        e2.executescript(ventas_db.CREATE_ESTADO_TABLES)
        e2.execute("INSERT INTO oc_alias VALUES ('C','B','N','auto',NULL)")
        e2.commit()
        e2.close()
        r = unir_sidecar(est, inc)
        assert r == {"alias_nuevos": 1, "alias_conflictos": []}
        assert est.execute("SELECT COUNT(*) FROM oc_alias").fetchone()[0] == 2
        est.close()

    def test_conflicto_gana_entrante_y_loguea(self, tmp_path):
        est = sqlite3.connect(str(tmp_path / "loc.db"))
        est.executescript(ventas_db.CREATE_ESTADO_TABLES)
        est.execute("INSERT INTO oc_alias VALUES ('C','R','N1','confirmado',NULL)")
        est.commit()
        inc = tmp_path / "inc.db"
        e2 = sqlite3.connect(str(inc))
        e2.executescript(ventas_db.CREATE_ESTADO_TABLES)
        e2.execute("INSERT INTO oc_alias VALUES ('C','R','N2','auto',NULL)")
        e2.commit()
        e2.close()
        r = unir_sidecar(est, inc)
        assert r["alias_nuevos"] == 0 and len(r["alias_conflictos"]) == 1
        c = r["alias_conflictos"][0]
        assert (c["local"], c["entrante"]) == ("N1", "N2")
        assert est.execute("SELECT oc_norm FROM oc_alias WHERE oc_raw='R'").fetchone()[0] == "N2"
        est.close()


class TestCompartirRoundTrip:
    """Simula el flujo completo de compartir: DB cargada -> zip -> otra PC.

    Es el camino que recorren dos maquinas distintas (exportar en una, importar
    en la otra). data_dir() lee G360_DATA_DIR en cada llamada, asi que cambiar
    el env simula literalmente "la otra PC" sin tocar la DB real.
    """

    def test_zip_generado_y_aceptado_por_otra_pc(self, tmp_db, tmp_path, monkeypatch):
        from datetime import datetime

        # ── PC de origen: DB con datos + O/C normalizado ──────────────
        conn = ventas_db.get_conn()
        _vender(conn, "2026-09-28", "1", oc="DP-2026-0001")
        _vender(conn, "2026-09-28", "2", oc="DP-2026-0001")
        _vender(conn, "2026-09-29", "3", oc="DP-2026-0002", soles=25.0)
        conn.close()

        # El sidecar viaja con el cartucho: el alias queda registrado aca.
        e = ventas_db.connect_estado(readonly=False)
        ventas_db.oc_alias_upsert(e, "C1", "DP-2026-0001", "DP-2026-0001")
        e.commit()
        e.close()

        # ── Exportar: el .zip es lo que se comparte ──────────────────
        # destino= es la carpeta de salida; la DB sigue en el data_dir del
        # fixture (no hace falta moverlo: exportar solo lee de ahi).
        export_dir = tmp_path / "export"
        export_dir.mkdir()
        r = exportar_cartucho(export_dir / f"cartucho-{datetime.now():%Y%m%d-%H%M%S}")
        zip_path = Path(r["zip"])

        assert zip_path.exists(), "no se genero el .zip"
        assert zip_path.suffix == ".zip"
        assert r["archivos"] == 4
        assert r["ventas_filas"] == 3
        # La carpeta intermedia se borra: el zip ES el cartucho.
        assert r["carpeta"] is None or not Path(r["carpeta"]).exists()

        # El zip es autocontenido: trae DB + sidecar + config + manifiesto.
        import zipfile

        with zipfile.ZipFile(zip_path) as zf:
            assert set(zf.namelist()) == {
                "historial.db",
                "estado_sustentor.db",
                "config_sanitizado.json",
                "CARTUCHO.json",
            }
            man = json.loads(zf.read("CARTUCHO.json").decode("utf-8"))
        assert man["db"]["ventas_filas"] == 3
        assert man["cartucho_version"] == CARTUCHO_VERSION
        # Se lee la allowlist sin extraer los MB del zip.
        assert "01" in leer_allowlist_cartucho(zip_path)

        # ── PC destino: data dir NUEVA ──────────────────────────────
        # Una PC nueva nace con la DB creada por el arranque de la app
        # (init_db); importar_cartucho espera que el archivo local exista.
        monkeypatch.setenv("G360_DATA_DIR", str(tmp_path / "data_destino"))
        ventas_db.reset_allowed_lines_cache()
        ventas_db.invalidate_lineas_cache()
        ventas_db.invalidate_sucursales_cache()
        assert not ventas_db.db_path().exists(), "la PC destino deberia nacer vacia"
        ventas_db.init_db()  # bootstrap del arranque
        ventas_db.init_estado()
        assert ventas_db.db_path().exists()

        res = importar_cartucho(zip_path)

        assert res["modo"] == "reemplazar", res
        # Los datos llegaron completos.
        conn2 = ventas_db.get_conn()
        n = conn2.execute("SELECT COUNT(*) FROM ventas").fetchone()[0]
        assert n == 3
        ocs = {
            r[0]
            for r in conn2.execute("SELECT DISTINCT ord_compra FROM ventas WHERE ord_compra<>''")
        }
        assert ocs == {"DP-2026-0001", "DP-2026-0002"}
        conn2.close()
        # El sidecar tambien: las revisiones de O/C no se pierden al reemplazar.
        e2 = ventas_db.connect_estado(readonly=True)
        assert e2.execute("SELECT COUNT(*) FROM oc_alias WHERE id_cliente='C1'").fetchone()[0] >= 1
        e2.close()
        # Y la DB importada sigue cumpliendo el contrato (es lo que valida
        # el proximo export en la PC destino).
        assert ventas_db.verificar_contrato()["ok"]

    def test_db_vacia_en_destino_no_rompe(self, tmp_db, tmp_path, monkeypatch):
        """Exportar sin datos debe fallar limpio, no dejar un cartucho roto."""
        export_dir = tmp_path / "export"
        export_dir.mkdir()
        monkeypatch.setenv("G360_DATA_DIR", str(tmp_path / "data_vacia"))
        ventas_db.reset_allowed_lines_cache()
        with pytest.raises(Exception):
            exportar_cartucho(export_dir / "cartucho-vacio")
        # No queda ningun zip huerfano.
        assert list(export_dir.glob("*.zip")) == []


class TestSuperset:
    def test_por_dia(self, tmp_path):
        a = sqlite3.connect(":memory:")
        b = sqlite3.connect(":memory:")
        for c in (a, b):
            c.execute(
                "CREATE TABLE day_checksums (dia TEXT PRIMARY KEY, "
                "total_filas INT, total_soles REAL, checksum TEXT)"
            )
        a.execute("INSERT INTO day_checksums VALUES ('2026-09-28', 2, 30.0, 'x')")
        b.execute("INSERT INTO day_checksums VALUES ('2026-09-28', 3, 40.0, 'y')")
        assert es_superset(b, a)["superset"] is True
        assert es_superset(a, b)["superset"] is False
        a.close()
        b.close()


class TestCobertura:
    def test_dia_sin_lineas_esperadas(self, tmp_db):
        conn = ventas_db.get_conn()
        _vender(conn, "2026-09-28", "1")
        # id_linea=A1 no está en la allowlist default -> se reporta
        rep = diagnostico_cobertura(conn)
        assert rep and rep[0]["dia"] == "2026-09-28"
