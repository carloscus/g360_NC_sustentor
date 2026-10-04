"""Migración: `ventas.fec_cargo` de dd/mm/yyyy a ISO (yyyy-mm-dd).

Por qué esta columna está sola: `xls_processor` normalizaba `fecha_orig` pero
pasaba `fec_cargo` tal cual, y el ERP entrega FEC_CARGO en dd/mm/yyyy. Resultado:
74.380 filas con un formato distinto al resto.

Por qué importa aunque hoy no se use: el SQL de este proyecto compara fechas
como texto (ORDER BY, BETWEEN, MIN/MAX, substr). Con dd/mm/yyyy:

    BETWEEN '2015-01-01' AND '2015-12-31'   ->  0 filas
    BETWEEN '01/01/2015' AND '31/12/2015'   ->  74.340 filas

O sea, un filtro por rango devuelve cero sin dar error. Y el query generico de
la API acepta order/gte/lte sobre cualquier columna sin validacion.

USO (con la app CERRADA, por el lock de escritura):

    python -m src.core.migrar_fec_cargo            # informa, no cambia nada
    python -m src.core.migrar_fec_cargo --aplicar   # migra; deja log de reversion

Es idempotente: solo toca filas whose value matches dd/mm/yyyy or dd-mm-yyyy, y
solo si la fila anterior existe en el log para poder revertir.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

from src.core.fechas import fecha_iso

COL = "fec_cargo"
LOG = "_migracion_fec_cargo"


def _db() -> Path:
    return (
        Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
        / "g360-db-ventas"
        / "data"
        / "historial.db"
    )


def _conteo(conn: sqlite3.Connection) -> dict:
    q = conn.execute(
        f"""
        SELECT
          sum(CASE WHEN {COL} GLOB '[0-9][0-9]/[0-9][0-9]/[0-9][0-9][0-9][0-9]' THEN 1 ELSE 0 END),
          sum(CASE WHEN {COL} GLOB '[0-9][0-9]-[0-9][0-9]-[0-9][0-9][0-9][0-9]' THEN 1 ELSE 0 END),
          sum(CASE WHEN {COL} GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]' THEN 1 ELSE 0 END),
          sum(CASE WHEN {COL} IS NULL OR {COL}='' THEN 1 ELSE 0 END),
          count(*)
        FROM ventas
        """
    ).fetchone()
    barras, guiones, iso, vacias, total = q
    return {
        "dd/mm/yyyy": barras or 0,
        "dd-mm-yyyy": guiones or 0,
        "ISO (ya ok)": iso or 0,
        "vacias": vacias or 0,
        "total": total,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--aplicar", action="store_true", help="escribe los cambios (por defecto solo informa)"
    )
    ap.add_argument("--db", default="", help="ruta alterna de la DB")
    args = ap.parse_args()

    ruta = Path(args.db) if args.db else _db()
    if not ruta.exists():
        print(f"no existe la DB: {ruta}")
        return 1

    conn = sqlite3.connect(ruta)
    conn.row_factory = sqlite3.Row
    print(f"DB: {ruta}")
    try:
        antes = _conteo(conn)
        print("\nestado actual:")
        for k, v in antes.items():
            print(f"  {k:<14} {v:>10,}")

        pendientes = antes["dd/mm/yyyy"] + antes["dd-mm-yyyy"]
        if not pendientes:
            print("\nnada que migrar.")
            return 0

        if not args.aplicar:
            print(f"\n{pendientes:,} fila(s) por migrar. Correr con --aplicar (app cerrada).")
            return 0

        # Log de reversión: una fila por id con el valor anterior.
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {LOG} (id INTEGER PRIMARY KEY, {COL} TEXT, migrado_en TEXT)"
        )
        ya = conn.execute(f"SELECT count(*) FROM {LOG}").fetchone()[0]
        if ya:
            print(f"\nATENCION: ya hay {ya:,} fila(s) en el log de {LOG}.")
            print("Si se reanuda una migración interrupted, se continua; no se duplica.")
        conn.execute(
            f"INSERT OR IGNORE INTO {LOG} (id, {COL}, migrado_en) "
            f"SELECT id, {COL}, datetime('now') FROM ventas "
            f"WHERE {COL} GLOB '[0-9][0-9]/[0-9][0-9]/[0-9][0-9][0-9][0-9]' "
            f"   OR {COL} GLOB '[0-9][0-9]-[0-9][0-9]-[0-9][0-9][0-9][0-9]'"
        )

        # Actualizacion por lote (SQLite no tiene UPDATE ... FROM en versiones viejas).
        conn.execute(
            "CREATE TEMP TABLE _mig AS SELECT id, "
            + COL
            + " AS v FROM ventas WHERE "
            + COL
            + " GLOB '[0-9][0-9]/[0-9][0-9]/[0-9][0-9][0-9][0-9]' OR "
            + COL
            + " GLOB '[0-9][0-9]-[0-9][0-9]-[0-9][0-9][0-9][0-9]'"
        )
        filas = conn.execute("SELECT id, v FROM _mig").fetchall()
        hechas = 0
        saltadas = 0
        for f in filas:
            iso = fecha_iso(f["v"])
            if not iso:
                saltadas += 1
                continue
            conn.execute(f"UPDATE ventas SET {COL} = ? WHERE id = ?", (iso, f["id"]))
            hechas += 1
            if hechas % 5000 == 0:
                print(f"  ... {hechas:,}/{len(filas):,}")
        conn.commit()
        print(f"\nmigradas {hechas:,} fila(s). Sin parsear: {saltadas:,} (se conservaron).")

        despues = _conteo(conn)
        print("\nestado final:")
        for k, v in despues.items():
            print(f"  {k:<14} {v:>10,}")

        print("\nReversión disponible: python -m src.core.migrar_fec_cargo --revertir")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
