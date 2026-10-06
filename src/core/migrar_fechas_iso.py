"""Migración: fechas de `ventas` de dd/mm/yyyy a ISO (yyyy-mm-dd).

Por qué hay que hacerlo: el SQL de este proyecto compara fechas como texto
(ORDER BY, BETWEEN, MIN/MAX, substr). Con dd/mm/yyyy:

    BETWEEN '2015-01-01' AND '2015-12-31'   ->  0 filas
    BETWEEN '01/01/2015' AND '31/12/2015'   ->  74.380 filas

O sea, un filtro por rango devuelve cero sin dar error. Y el query genérico de
la API acepta order/gte/lte sobre cualquier columna sin validación.

Por qué quedaron columnas sueltas: el ERP las entrega en dd/mm/yyyy y la
captura las pasaba tal cual. `fecha_orig` sí se normalizaba; las otras no.
Hoy `insert_ventas`/`replace_folios` normalizan las cuatro, así que esto es
deuda vieja: las filas pendientes son de antes de ese fix. Aun así, mientras
existan, un ORDER BY o un rango sobre esas columnas miente en silencio.

USO (con la app CERRADA, por el lock de escritura):

    python -m src.core.migrar_fechas_iso                  # informa, no cambia nada
    python -m src.core.migrar_fechas_iso --aplicar         # migra; deja log por columna
    python -m src.core.migrar_fechas_iso --revertir        # restaura desde el log
    python -m src.core.migrar_fechas_iso --column fecha_ref  # una sola columna

Cada columna tiene su propia tabla de log (`_migracion_<columna>`) con el valor
anterior por id, así la reversión es exacta y `--aplicar` es idempotente: sólo
toca valores que matchean dd/mm/yyyy o dd-mm-yyyy, y `INSERT OR IGNORE` evita
duplicar el log al reanudar.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from src.core.fechas import fecha_iso

# Columnas de `ventas` que guardan fechas y llegan del ERP en dd/mm/yyyy.
# `fecha_orig` no está: siempre fue ISO (la normalizaba xls_processor).
COLS = ("fec_cargo", "fecha_ref", "fecha_venc")

GLOB_DDMM = "[0-9][0-9]/[0-9][0-9]/[0-9][0-9][0-9][0-9]"
GLOB_DDMM_DASH = "[0-9][0-9]-[0-9][0-9]-[0-9][0-9][0-9][0-9]"
GLOB_ISO = "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]"


def log_de(col: str) -> str:
    return f"_migracion_{col}"


def _db() -> Path:
    """Base que usa la app: la misma que resuelve `ventas_db.db_path()`.

    Antes hardcodeaba `%APPDATA%\\g360-db-ventas\\data\\historial.db`, que es la
    base del Tauri, NO la de este repo: `ventas_db_config.db_path()` resuelve a
    `%APPDATA%\\g360-erp-nc-sustentor\\data\\historial.db` (y respeta
    G360_DATA_DIR). Con el path hardcodeado la migración habría tocado una base
    que la app no abre y dejado sin migrar la que sí usa.
    """
    from src.core.ventas_db_config import db_path

    return db_path()


def _conteo(conn: sqlite3.Connection, col: str = "fec_cargo") -> dict:
    q = conn.execute(
        f"""
        SELECT
          sum(CASE WHEN {col} GLOB '{GLOB_DDMM}' THEN 1 ELSE 0 END),
          sum(CASE WHEN {col} GLOB '{GLOB_DDMM_DASH}' THEN 1 ELSE 0 END),
          sum(CASE WHEN {col} GLOB '{GLOB_ISO}' THEN 1 ELSE 0 END),
          sum(CASE WHEN {col} IS NULL OR {col}='' THEN 1 ELSE 0 END),
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


def _revertir(conn: sqlite3.Connection, col: str) -> int:
    """Restaura cada valor de `col` desde su log de migración.

    Vacía el log al terminar: un `--revertir` repetido no debe volver a tocar
    filas ya restauradas, y una migración posterior vuelve a registrar los
    valores vigentes.
    """
    log = log_de(col)
    existe = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (log,)
    ).fetchone()
    if not existe:
        print(f"no hay log {log}: nada que revertir.")
        return 0
    filas = conn.execute(f"SELECT id, {col} FROM {log}").fetchall()
    if not filas:
        print(f"el log {log} esta vacio: nada que revertir.")
        return 0
    hechas = 0
    for f in filas:
        conn.execute(f"UPDATE ventas SET {col} = ? WHERE id = ?", (f[col], f["id"]))
        hechas += 1
        if hechas % 5000 == 0:
            print(f"  ... {hechas:,}/{len(filas):,}")
    conn.execute(f"DELETE FROM {log}")
    conn.commit()
    print(f"\nrevertidas {hechas:,} fila(s) de {col}.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Migrar fechas de ventas a ISO.")
    grupo = ap.add_mutually_exclusive_group()
    grupo.add_argument(
        "--aplicar", action="store_true", help="escribe los cambios (por defecto solo informa)"
    )
    grupo.add_argument(
        "--revertir", action="store_true", help="restaura los valores desde el log de migración"
    )
    ap.add_argument("--db", default="", help="ruta alterna de la DB")
    ap.add_argument(
        "--column",
        default="",
        help=f"una sola columna (por defecto todas: {', '.join(COLS)})",
    )
    args = ap.parse_args()

    cols = [args.column] if args.column else list(COLS)
    desconocidas = [c for c in cols if c not in COLS]
    if desconocidas:
        print(f"columna desconocida: {', '.join(desconocidas)}. Opciones: {', '.join(COLS)}")
        return 2

    ruta = Path(args.db) if args.db else _db()
    if not ruta.exists():
        print(f"no existe la DB: {ruta}")
        return 1

    conn = sqlite3.connect(ruta)
    conn.row_factory = sqlite3.Row
    print(f"DB: {ruta}\n")
    try:
        for col in cols:
            print(f"=== {col} ===")
            if args.revertir:
                _revertir(conn, col)
                continue

            antes = _conteo(conn, col)
            for k, v in antes.items():
                print(f"  {k:<14} {v:>10,}")

            pendientes = antes["dd/mm/yyyy"] + antes["dd-mm-yyyy"]
            if not pendientes:
                print("  nada que migrar.\n")
                continue

            if not args.aplicar:
                print(f"  {pendientes:,} fila(s) por migrar.\n")
                continue

            log = log_de(col)
            conn.execute(
                f"CREATE TABLE IF NOT EXISTS {log} (id INTEGER PRIMARY KEY, {col} TEXT, "
                "migrado_en TEXT)"
            )
            ya = conn.execute(f"SELECT count(*) FROM {log}").fetchone()[0]
            if ya:
                print(f"\n  ATENCION: {log} ya tiene {ya:,} fila(s); se continua, no se duplica.")
            conn.execute(
                f"INSERT OR IGNORE INTO {log} (id, {col}, migrado_en) "
                f"SELECT id, {col}, datetime('now') FROM ventas "
                f"WHERE {col} GLOB '{GLOB_DDMM}' OR {col} GLOB '{GLOB_DDMM_DASH}'"
            )

            conn.execute(
                "CREATE TEMP TABLE _mig AS SELECT id, "
                + col
                + " AS v FROM ventas WHERE "
                + col
                + f" GLOB '{GLOB_DDMM}' OR "
                + col
                + f" GLOB '{GLOB_DDMM_DASH}'"
            )
            filas = conn.execute("SELECT id, v FROM _mig").fetchall()
            hechas = 0
            saltadas = 0
            for f in filas:
                iso = fecha_iso(f["v"])
                if not iso:
                    saltadas += 1
                    continue
                conn.execute(f"UPDATE ventas SET {col} = ? WHERE id = ?", (iso, f["id"]))
                hechas += 1
                if hechas % 5000 == 0:
                    print(f"  ... {hechas:,}/{len(filas):,}")
            conn.commit()
            conn.execute("DROP TABLE IF EXISTS _mig")
            print(f"  migradas {hechas:,}. Sin parsear: {saltadas:,} (se conservaron).")

            for k, v in _conteo(conn, col).items():
                print(f"  {k:<14} {v:>10,}")
            print()

        if args.revertir:
            print("Reversión aplicada.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
