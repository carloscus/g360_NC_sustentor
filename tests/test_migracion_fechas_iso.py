"""La migración de fechas debe apuntar a la base que la app usa.

Regression: `_db()` hardcodeaba `%APPDATA%\\g360-db-ventas\\data\\historial.db`,
que es la base del Tauri. `ventas_db_config.db_path()` resuelve a
`%APPDATA%\\g360-erp-nc-sustentor\\data\\historial.db`. Con el path hardcodeado
la migración habría tocado una base que este repo nunca abre y dejado sin
migrar la que sí usa.

Cubre además el ciclo `--aplicar` -> `--revertir` por columna: `--revertir` se
anunciaba en la salida pero no existía en el argparse, así que la única vuelta
atrás que ofrecía la migración no hacía nada.
"""

from __future__ import annotations

import sqlite3

import pytest

from src.core import ventas_db_config
from src.core.migrar_fechas_iso import COLS, _conteo, _db, log_de


def test_db_por_defecto_es_la_que_usa_la_app():
    assert _db() == ventas_db_config.db_path()


def test_db_por_defecto_respeta_environ(monkeypatch, tmp_path):
    # Si la app está apuntando a otra data dir (tests, portable, copia de
    # trabajo), la migración debe seguirla en vez de ir a la ruta fija.
    monkeypatch.setenv("G360_DATA_DIR", str(tmp_path / "otro"))
    assert _db() == tmp_path / "otro" / "historial.db"


def test_no_apunta_a_la_base_del_tauri(monkeypatch, tmp_path):
    # Guarda explicita del error original.
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert "g360-db-ventas" not in _db().as_posix()


def test_log_por_columna():
    assert log_de("fec_cargo") == "_migracion_fec_cargo"
    assert log_de("fecha_ref") == "_migracion_fecha_ref"


def test_columna_incluye_las_tres_del_erp():
    # `fecha_orig` no va: siempre fue ISO.
    assert set(COLS) == {"fec_cargo", "fecha_ref", "fecha_venc"}
    assert "fecha_orig" not in COLS


class TestConteo:
    def _db_con(self, valores):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE ventas (fec_cargo TEXT)")
        conn.executemany("INSERT INTO ventas VALUES (?)", [(v,) for v in valores])
        return conn

    def test_clasifica_por_formato(self):
        conn = self._db_con(
            [
                "31/12/2015",  # dd/mm/yyyy  -> migrable
                "31-12-2015",  # dd-mm-yyyy  -> migrable
                "2015-12-31",  # ISO         -> ya ok
                "",  # vacia
                None,  # nula
                "no es fecha",  # basura: ni migrable ni ISO
            ]
        )
        c = _conteo(conn)
        assert c["dd/mm/yyyy"] == 1
        assert c["dd-mm-yyyy"] == 1
        assert c["ISO (ya ok)"] == 1
        assert c["vacias"] == 2
        assert c["total"] == 6
        conn.close()

    def test_base_vacia(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE ventas (fec_cargo TEXT)")
        assert _conteo(conn)["total"] == 0
        conn.close()


class TestIdaYVuelta:
    """`--revertir` es la red de seguridad de `--aplicar`."""

    def _db_temporal(self, tmp_path, valores):
        ruta = tmp_path / "historial.db"
        conn = sqlite3.connect(ruta)
        cols = ", ".join(f"{c} TEXT" for c in COLS)
        conn.execute(f"CREATE TABLE ventas (id INTEGER PRIMARY KEY, {cols})")
        for i, v in enumerate(valores, start=1):
            conn.execute(
                f"INSERT INTO ventas (id, {', '.join(COLS)}) VALUES ({i}, {', '.join('?' * len(COLS))})",
                v,
            )
        conn.commit()
        conn.close()
        return ruta

    def _correr(self, ruta, *flags):
        import sys

        from src.core import migrar_fechas_iso as M

        argv = sys.argv
        sys.argv = ["migrar_fechas_iso", "--db", str(ruta), *flags]
        try:
            return M.main()
        finally:
            sys.argv = argv

    def _leer(self, ruta, col):
        conn = sqlite3.connect(ruta)
        out = [r[0] for r in conn.execute(f"SELECT {col} FROM ventas ORDER BY id")]
        conn.close()
        return out

    def test_aplicar_luego_revertir_deja_la_base_identica(self, tmp_path):
        original = ["31/12/2015", "01/01/2016", "2016-02-03", "", None, "no es fecha"]
        # Una fila por valor, con el mismo valor en las tres columnas: asi cada
        # columna exercises el ciclo completo.
        ruta = self._db_temporal(tmp_path, [(v, v, v) for v in original])

        assert self._correr(ruta) == 0  # solo informa
        for col in COLS:
            assert self._leer(ruta, col) == original  # no escribe sin --aplicar

        assert self._correr(ruta, "--aplicar") == 0
        esperado = ["2015-12-31", "2016-01-01", "2016-02-03", "", None, "no es fecha"]
        for col in COLS:
            assert self._leer(ruta, col) == esperado

        assert self._correr(ruta, "--revertir") == 0
        for col in COLS:
            assert self._leer(ruta, col) == original

    def test_una_sola_columna_no_toca_las_demas(self, tmp_path):
        ruta = self._db_temporal(tmp_path, [("31/12/2015", "31/12/2015", "31/12/2015")])
        assert self._correr(ruta, "--aplicar", "--column", "fecha_ref") == 0
        assert self._leer(ruta, "fecha_ref") == ["2015-12-31"]
        assert self._leer(ruta, "fec_cargo") == ["31/12/2015"]
        assert self._leer(ruta, "fecha_venc") == ["31/12/2015"]

    def test_aplicar_es_idempotente(self, tmp_path):
        ruta = self._db_temporal(tmp_path, [("31/12/2015", "", "")])
        assert self._correr(ruta, "--aplicar") == 0
        assert self._correr(ruta, "--aplicar") == 0  # segunda vez: nada que migrar
        assert self._leer(ruta, "fec_cargo") == ["2015-12-31"]

    def test_revertir_sin_log_no_falla(self, tmp_path):
        ruta = self._db_temporal(tmp_path, [("31/12/2015", "", "")])
        assert self._correr(ruta, "--revertir") == 0
        assert self._leer(ruta, "fec_cargo") == ["31/12/2015"]

    def test_revertir_es_idempotente(self, tmp_path):
        ruta = self._db_temporal(tmp_path, [("31/12/2015", "", "")])
        self._correr(ruta, "--aplicar")
        assert self._correr(ruta, "--revertir") == 0
        assert self._correr(ruta, "--revertir") == 0  # log ya vacio
        assert self._leer(ruta, "fec_cargo") == ["31/12/2015"]

    def test_columna_desconocida_se_rechaza(self, tmp_path):
        ruta = self._db_temporal(tmp_path, [("31/12/2015", "", "")])
        assert self._correr(ruta, "--aplicar", "--column", "fecha_orig") == 2
        assert self._leer(ruta, "fec_cargo") == ["31/12/2015"]

    def test_aplicar_y_revertir_no_juntos(self, tmp_path):
        ruta = self._db_temporal(tmp_path, [("31/12/2015", "", "")])
        with pytest.raises(SystemExit):
            self._correr(ruta, "--aplicar", "--revertir")
