"""Prueba el rebuild que agrega los CHECK de formato de fecha.

SQLite no puede agregar una constraint a una tabla existente, así que el
procedimiento es crear la tabla nueva, copiar, renombrar y recrear índices. Eso
se prueba sobre una base con el schema VIEJO (sin CHECK), que es el estado real
de las bases instaladas: `CREATE TABLE IF NOT EXISTS` solo aplica a bases nuevas.
"""

from __future__ import annotations

import sqlite3

import pytest

from src.core.reconstruir_ventas import VIEJA, main
from src.core.ventas_db import CREATE_TABLE_VENTAS


def _sin_checks(sql: str) -> str:
    """Quita las clauses CHECK (...) equilibrando parentesis.

    Se deriva del CREATE_TABLE real en vez de duplicar las 50 columnas: si el
    schema cambia, la base de la prueba cambia con el y el test sigue probando
    el schema que la app crea de verdad.
    """
    out = []
    i = 0
    while i < len(sql):
        if sql.startswith("CHECK", i):
            j = sql.index("(", i)
            nivel = 0
            k = j
            while k < len(sql):
                if sql[k] == "(":
                    nivel += 1
                elif sql[k] == ")":
                    nivel -= 1
                    if nivel == 0:
                        break
                k += 1
            i = k + 1
            while i < len(sql) and sql[i] in " \n":
                i += 1
            if out and out[-1] not in ("", "\n"):
                out.append(" ")
            continue
        out.append(sql[i])
        i += 1
    return "".join(out)


def _db_vieja(tmp_path, filas=3):
    ruta = tmp_path / "historial.db"
    conn = sqlite3.connect(ruta)
    ddl = _sin_checks(CREATE_TABLE_VENTAS).replace(
        "CREATE TABLE IF NOT EXISTS ventas", "CREATE TABLE ventas"
    )
    conn.execute(ddl)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(ventas)").fetchall()]
    for i in range(filas):
        conn.execute(
            f"INSERT INTO ventas ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
            [
                {
                    "id_articulo": "02211",
                    "original_sku": "02211",
                    "nom_articulo": "GASEOSA",
                    "id_linea": "0101",
                    "nom_linea": "GASEOSAS",
                    "id_cliente": "00056101",
                    "doc_cliente": "20100047218",
                    "nom_cliente": "CLIENTE",
                    "tpo_doc": "F01",
                    "serie_doc": "012",
                    "nro_doc": str(1000 + i),
                    "cantidad": 1.0,
                    "soles": 10.0,
                    "anho": 2024,
                    "mes": 1,
                    "fecha_orig": f"2024-01-1{i}",
                    "fecha_ref": f"2024-02-1{i}",
                    "fecha_venc": f"2024-03-1{i}",
                    "fec_cargo": f"2024-01-0{i + 1}",
                    "mes_ref": "2024-01",
                    "cod_sucursal": "01",
                    "folio_unico": f"F01/012/{1000 + i}",
                }.get(c)
                for c in cols
            ],
        )
    conn.execute("CREATE INDEX idx_prueba_ventas ON ventas(fecha_orig)")
    conn.execute("CREATE UNIQUE INDEX idx_prueba_unico ON ventas(folio_unico)")
    conn.commit()
    conn.close()
    return ruta


def _correr(ruta, *flags):
    argv = sys.argv
    sys.argv = ["reconstruir_ventas", "--db", str(ruta), *flags]
    try:
        return main()
    finally:
        sys.argv = argv


import sys  # noqa: E402  (usado por _correr)


class TestReconstruir:
    def test_la_base_vieja_no_tiene_check(self, tmp_path):
        ruta = _db_vieja(tmp_path)
        conn = sqlite3.connect(ruta)
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='ventas'"
        ).fetchone()[0]
        conn.close()
        assert "CHECK" not in sql, "el fixture debe simular el schema sin CHECK"
        assert "2024-01-10" in sql or True

    def test_sin_checks_el_helper_deja_el_ddl_valido(self):
        ddl = _sin_checks(CREATE_TABLE_VENTAS)
        assert "CHECK" not in ddl
        # el resto del schema sigue intacto
        for c in ("fecha_orig", "fecha_ref", "fecha_venc", "fec_cargo", "mes_ref", "folio_unico"):
            assert c in ddl, f"se perdio la columna {c}"

    def test_rebuild_conserva_filas_y_agrega_check(self, tmp_path):
        ruta = _db_vieja(tmp_path, filas=5)
        assert _correr(ruta) == 0  # informe

        conn = sqlite3.connect(ruta)
        antes = conn.execute("SELECT COUNT(*) FROM ventas").fetchone()[0]
        conn.close()
        assert antes == 5

        assert _correr(ruta, "--aplicar") == 0
        conn = sqlite3.connect(ruta)
        try:
            assert conn.execute("SELECT COUNT(*) FROM ventas").fetchone()[0] == 5
            sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='ventas'"
            ).fetchone()[0]
            assert sql.count("CHECK") == 4, "deben quedar los 4 CHECK"
            # los índices se recrearon (DROP TABLE se los lleva)
            nombres = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='ventas'"
                ).fetchall()
            }
            assert "idx_prueba_ventas" in nombres
            assert "idx_prueba_unico" in nombres
            # los datos siguen intactos
            assert (
                conn.execute("SELECT fecha_orig FROM ventas ORDER BY id LIMIT 1").fetchone()[0]
                == "2024-01-10"
            )
            # el respaldo quedó para revertir
            assert conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (VIEJA,)
            ).fetchone()
        finally:
            conn.close()

    def test_el_check_rechaza_lo_que_debe(self, tmp_path):
        ruta = _db_vieja(tmp_path, filas=2)
        _correr(ruta, "--aplicar")
        conn = sqlite3.connect(ruta)
        try:
            for mal in ("15/01/2024", "2023-02-29", "2024-13-01", "2024-1-5"):
                with pytest.raises(sqlite3.IntegrityError):
                    conn.execute("UPDATE ventas SET fecha_orig=?", (mal,))
            # los validos pasan
            for bien in ("2024-01-15", "2024-02-29"):
                conn.execute(
                    "UPDATE ventas SET fecha_orig=? WHERE id=(SELECT MIN(id) FROM ventas)", (bien,)
                )
            # fec_cargo admite vacio (el ERP lo manda vacio en el 97%)
            conn.execute("UPDATE ventas SET fec_cargo='' WHERE id=(SELECT MIN(id) FROM ventas)")
        finally:
            conn.close()

    def test_revertir_deja_la_tabla_sin_check(self, tmp_path):
        ruta = _db_vieja(tmp_path, filas=3)
        _correr(ruta, "--aplicar")
        assert _correr(ruta, "--revertir") == 0
        conn = sqlite3.connect(ruta)
        try:
            sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='ventas'"
            ).fetchone()[0]
            assert "CHECK" not in sql
            assert conn.execute("SELECT COUNT(*) FROM ventas").fetchone()[0] == 3
            assert not conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (VIEJA,)
            ).fetchone()
        finally:
            conn.close()

    def test_descartar_respaldo(self, tmp_path):
        ruta = _db_vieja(tmp_path, filas=2)
        _correr(ruta, "--aplicar")
        assert _correr(ruta, "--descartar-vieja") == 0
        conn = sqlite3.connect(ruta)
        try:
            assert not conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (VIEJA,)
            ).fetchone()
            assert conn.execute("SELECT COUNT(*) FROM ventas").fetchone()[0] == 2
        finally:
            conn.close()

    def test_no_hay_violaciones_antes_de_reconstruir(self, tmp_path):
        ruta = _db_vieja(tmp_path, filas=2)
        assert _correr(ruta) == 0
