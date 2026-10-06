"""Aplica idx_venta_capturado_en a una DB de ventas existente.

No reconstruye la tabla: solo agrega el indice faltante. Es idempotente.

    python -m src.core.agregar_indice_capturado_en              # informa
    python -m src.core.agregar_indice_capturado_en --aplicar    # crea el indice
    python -m src.core.agregar_indice_capturado_en --db RUTA    # otra base

Uso con las apps cerradas: crear un indice escribe sobre los 2.8M de filas.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

from src.core.ventas_db import db_path

INDICE = "idx_venta_capturado_en"
COLUMNA = "capturado_en"


def _log(msg: str) -> None:
    print(msg, flush=True)


def _tiene_indice(conn: sqlite3.Connection) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (INDICE,)
        ).fetchone()
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=f"Crea {INDICE} si falta.")
    ap.add_argument("--aplicar", action="store_true", help="escribe (sin esto solo informa)")
    ap.add_argument("--db", default="", help="ruta alterna de la DB")
    args = ap.parse_args()

    ruta = Path(args.db) if args.db else db_path()
    if args.db:
        _log(f"AVISO: usando la base indicada a mano, no la de la app: {ruta}")
    if not ruta.exists():
        _log(f"no existe la DB: {ruta}")
        return 1

    conn = sqlite3.connect(ruta)
    try:
        if _tiene_indice(conn):
            _log(f"{INDICE} ya existe: nada que hacer.")
            return 0

        cols = [r[1] for r in conn.execute("PRAGMA table_info(ventas)")]
        if COLUMNA not in cols:
            _log(f"la tabla `ventas` no tiene la columna {COLUMNA}: nada que hacer.")
            return 1

        t = time.perf_counter()
        antes = conn.execute("SELECT MAX(capturado_en) FROM ventas").fetchone()[0]
        _log(f"MAX({COLUMNA}) antes: {antes!r}   ({time.perf_counter() - t:.1f}s)")

        if not args.aplicar:
            _log("\nsolo informe. Para escribir: --aplicar")
            return 0

        _log(f"creando {INDICE} sobre ~2.8M de filas...")
        t = time.perf_counter()
        conn.execute(f"CREATE INDEX IF NOT EXISTS {INDICE} ON ventas({COLUMNA})")
        conn.commit()
        _log(f"  indice creado en {time.perf_counter() - t:.1f}s")

        t = time.perf_counter()
        despues = conn.execute(f"SELECT MAX({COLUMNA}) FROM ventas").fetchone()[0]
        _log(f"MAX({COLUMNA}) despues: {despues!r}   ({time.perf_counter() - t:.3f}s)")

        if despues != antes:
            _log("ERROR: el valor cambio. No deberia haber pasado.")
            return 1

        plan = [
            r[-1] for r in conn.execute(f"EXPLAIN QUERY PLAN SELECT MAX({COLUMNA}) FROM ventas")
        ]
        _log(f"  plan: {plan}")
        n = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='index'"
            " AND tbl_name='ventas' AND sql IS NOT NULL"
        ).fetchone()[0]
        _log(f"  indices en ventas ahora: {n}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
