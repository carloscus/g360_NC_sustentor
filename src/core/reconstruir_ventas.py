"""Reconstruye `ventas` para agregar los CHECK de formato de fecha.

Por qué una reconstrucción y no un ALTER: SQLite no puede agregar una
constraint a una tabla existente. El procedimiento es crear la tabla nueva con
la constraint, copiar, cambiar el nombre y recrear los índices.

Reversión sin costo: la tabla vieja se conserva como `ventas_pre_chk` en vez de
borrarse. Si algo quedo mal, `ALTER TABLE ventas_pre_chk RENAME TO ventas` y
listo. Se borra con `--descartar-vieja` cuando ya se verificó.

Uso (con las apps cerradas, por el lock de escritura):

    python -m src.core.reconstruir_ventas              # informa
    python -m src.core.reconstruir_ventas --aplicar    # reconstruye
    python -m src.core.reconstruir_ventas --revertir   # vuelve atrás
    python -m src.core.reconstruir_ventas --descartar-vieja   # borra el respaldo

El CHECK es el MISMO que define `ventas_db.CREATE_TABLE_VENTAS`; se importa de
ahí en vez de reescribirlo, para que schema y migración no puedan divergir.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

from src.core.ventas_db import CREATE_TABLE_VENTAS, db_path

VIEJA = "ventas_pre_chk"


def _log(msg: str) -> None:
    print(msg, flush=True)


def _indice(conn: sqlite3.Connection, col: str) -> int:
    return len(
        conn.execute(
            f"SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='{col}'"
        ).fetchall()
    )


def _checks(conn: sqlite3.Connection) -> int:
    fila = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='ventas'"
    ).fetchone()
    if not fila or not fila[0]:
        return 0
    return fila[0].count("CHECK")


def _violadores(conn: sqlite3.Connection) -> dict:
    """Cuantas filas impedirían la copia (debería ser 0)."""
    from src.core.ventas_db import GLOB_ISO

    out = {}
    for col, admite in (
        ("fecha_orig", False),
        ("fecha_ref", True),
        ("fecha_venc", True),
        ("fec_cargo", True),
    ):
        where = f"{col} IS NOT NULL AND {col} <> ''" if admite else col
        out[col] = conn.execute(
            f"SELECT COUNT(*) FROM ventas WHERE {where} AND NOT ("
            f"{col} GLOB '{GLOB_ISO}' AND COALESCE(date({col}), '') = {col})"
        ).fetchone()[0]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Reconstruye `ventas` con CHECK de fechas.")
    grupo = ap.add_mutually_exclusive_group()
    grupo.add_argument("--aplicar", action="store_true")
    grupo.add_argument("--revertir", action="store_true")
    grupo.add_argument("--descartar-vieja", action="store_true")
    ap.add_argument("--db", default="", help="ruta alterna de la DB (por defecto la de la app)")
    args = ap.parse_args()

    ruta = Path(args.db) if args.db else db_path()
    if args.db:
        _log(f"AVISO: usando la base indicada a mano, no la de la app: {ruta}")
    if not ruta.exists():
        _log(f"no existe la DB: {ruta}")
        return 1

    conn = sqlite3.connect(ruta)
    conn.row_factory = sqlite3.Row
    try:
        tiene_vieja = bool(
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (VIEJA,)
            ).fetchone()
        )
        if args.descartar_vieja:
            if not tiene_vieja:
                _log(f"no hay {VIEJA}: nada que descartar.")
                return 0
            conn.execute(f"DROP TABLE {VIEJA}")
            conn.commit()
            _log(f"respaldo {VIEJA} descartado.")
            return 0
        if args.revertir:
            if not tiene_vieja:
                _log(f"no hay {VIEJA}: nada que revertir.")
                return 0
            # Los índices de la tabla actual se van con el DROP; los del
            # respaldo siguen puestos sobre ventas_pre_chk y hay que soltarlos
            # para que el rename no los deje apuntando a una tabla que ya no
            # existe con ese nombre.
            nombres = [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?",
                    (VIEJA,),
                ).fetchall()
            ]
            for n in nombres:
                conn.execute(f"DROP INDEX IF EXISTS {n}")
            conn.execute("DROP TABLE ventas")
            conn.execute(f"ALTER TABLE {VIEJA} RENAME TO ventas")
            conn.commit()
            _log(f"revertido: {VIEJA} volvio a ser `ventas`.")
            _log(f"  indices dropping antes del rename: {len(nombres)}")
            return 0

        total = conn.execute("SELECT COUNT(*) FROM ventas").fetchone()[0]
        checks = _checks(conn)
        _log(f"DB: {ruta}")
        _log(
            f"  filas: {total:,}   indices en ventas: {_indice(conn, 'ventas')}   CHECKs: {checks}"
        )
        if tiene_vieja:
            _log(
                f"  hay un {VIEJA} de una corrida previa: se usa --descartar-vieja antes de reintentar"
            )
            return 1
        if checks > 0:
            _log("  la tabla ya tiene CHECK: nada que hacer.")
            return 0

        viol = _violadores(conn)
        _log("  filas que impedirian la copia:")
        for c, n in viol.items():
            _log(f"    {c:<11} {n:>8,}")
        if any(viol.values()):
            _log("  HAY VIOLACIONES: normaliza primero con migrar_fechas_iso.")
            return 1
        _log("  0 violaciones: se puede reconstruir.")
        if not args.aplicar:
            _log("\nsolo informe. Para escribir: --aplicar")
            return 0

        # Los índices se capturan ANTES: el rename se los lleva.
        indices = [
            r[0]
            for r in conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='ventas' "
                "AND sql IS NOT NULL"
            ).fetchall()
        ]
        _log(f"\nindices a recrear: {len(indices)}")
        t0 = time.perf_counter()
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("BEGIN")
        # La tabla ORIGINAL se renombra, no se borra: ese es el respaldo que
        # permite revertir sin volver a copiar 2.8M de filas. Usar el mismo
        # nombre para la tabla nueva y para el respaldo deja el respaldo
        # inexistente al final, porque el RENAME final se lo lleva.
        conn.execute(f"ALTER TABLE ventas RENAME TO {VIEJA}")
        # Los índices ACOMPAÑAN a la tabla renombrada (SQLite reescribe su SQL
        # para apuntar a ventas_pre_chk) y conservan el nombre. Si no se sueltan
        # primero, recrearlos falla con "index already exists".
        for sql in indices:
            nombre = sql.split("INDEX")[-1].split("ON")[0].strip().replace('"', "").strip()
            conn.execute(f"DROP INDEX IF EXISTS {nombre}")
        conn.execute(CREATE_TABLE_VENTAS)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(ventas)").fetchall()]
        _log("  copiando filas...")
        conn.execute(
            f"INSERT INTO ventas ({', '.join(cols)}) SELECT {', '.join(cols)} FROM {VIEJA}"
        )
        copiadas = conn.execute("SELECT COUNT(*) FROM ventas").fetchone()[0]
        _log(f"  copiadas: {copiadas:,} de {total:,}")
        for sql in indices:
            conn.execute(sql)
        _log(f"  indices recreados: {len(indices)}")
        conn.commit()
        _log(f"  CHECKs ahora: {_checks(conn)}   indice: {time.perf_counter() - t0:.1f}s")
        _log(f"\nrespaldo conservado como {VIEJA}. Verificá y después:")
        _log("  python -m src.core.reconstruir_ventas --descartar-vieja")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
