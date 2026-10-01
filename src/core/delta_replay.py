"""Kit de replay: exporta e importa el sidecar de estado.

Si la DB se reemplaza (sync por red, import manual, cartucho), el archivo
nuevo trae todo lo de la fuente pero pierde lo que solo existe acá: las
revisiones de colisiones de O/C (oc_alias) y los watermarks (day_state).
Este módulo los vuelca a un archivo chico y los restaura después, para que
un reemplazo + replay deje todo idéntico.

oc_archivos NO viaja: marca archivos raw de ESTA pc (los raw no viajan).
day_checksums tampoco: es metadata del archivo, se reconstruye de ventas.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from src.core import ventas_db

TABLAS_DELTA = ("oc_alias", "day_state")


def exportar_delta(path: str | Path, conn_estado: sqlite3.Connection | None = None) -> dict:
    """Vuelca el sidecar a un SQLite nuevo. Returns {tabla: filas}."""
    cerrar = conn_estado is None
    if conn_estado is None:
        conn_estado = ventas_db.connect_estado(readonly=True)
    try:
        path = Path(path)
        if path.exists():
            path.unlink()
        dst = sqlite3.connect(str(path))
        try:
            out: dict[str, int] = {}
            for t in TABLAS_DELTA:
                ddl = conn_estado.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (t,)
                ).fetchone()
                if not ddl:
                    continue
                dst.execute(ddl[0])
                filas = conn_estado.execute(f"SELECT * FROM {t}").fetchall()
                if filas:
                    cols = [r[1] for r in conn_estado.execute(f"PRAGMA table_info({t})").fetchall()]
                    dst.executemany(
                        f"INSERT INTO {t} ({', '.join(cols)}) "
                        f"VALUES ({', '.join('?' * len(cols))})",
                        filas,
                    )
                out[t] = len(filas)
            dst.commit()
            return out
        finally:
            dst.close()
    finally:
        if cerrar:
            conn_estado.close()


def importar_delta(path: str | Path, conn_estado: sqlite3.Connection | None = None) -> dict:
    """Restaura el delta en el sidecar (merge por PK: el delta manda).

    Las filas locales que no están en el delta sobreviven. Requiere que las
    tablas existan (init_estado las crea). La unión con detección de
    conflictos entre sidecars (F5) extiende esta función, no la reemplaza.
    """
    cerrar = conn_estado is None
    if conn_estado is None:
        conn_estado = ventas_db.connect_estado(readonly=False)
        ventas_db.init_estado(conn_estado)
    try:
        if not Path(path).exists():
            raise FileNotFoundError(f"no existe delta: {path}")
        src = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
        try:
            tablas = {
                r[0]
                for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
            out: dict[str, int] = {}
            with ventas_db.write_txn(conn_estado):
                for t in TABLAS_DELTA:
                    if t not in tablas:
                        continue
                    cols = [r[1] for r in src.execute(f"PRAGMA table_info({t})").fetchall()]
                    filas = src.execute(f"SELECT * FROM {t}").fetchall()
                    if filas:
                        conn_estado.executemany(
                            f"INSERT OR REPLACE INTO {t} ({', '.join(cols)}) "
                            f"VALUES ({', '.join('?' * len(cols))})",
                            filas,
                        )
                    out[t] = len(filas)
            return out
        finally:
            src.close()
    finally:
        if cerrar:
            conn_estado.close()
