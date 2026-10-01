# -*- coding: utf-8 -*-
"""Backup e importación multi-PC de la DB SQLite local del sustentor.

Extraída de src/core/ventas_db.py: backup consistente (checkpoint WAL,
validación de integridad, rotación), importación de una DB origen (valida
integridad/schema, hace backup previo y reemplaza) y cálculo del primer día
del mes siguiente al último capturado (para sync incremental).

Evita importar ``ventas_db`` a nivel de módulo (sería un ciclo): accede vía
import perezoso dentro de cada función. Se re-exporta desde ventas_db.py al
final del módulo para no romper los call-sites existentes.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)


def _vdb():
    """Retorna el módulo ventas_db (cargado, por eso el import es perezoso)."""
    from src.core import ventas_db

    return ventas_db


# ── Backup / import (multi-PC) ──────────────────────────────────────────────


def backup_dir() -> Path:
    return _vdb().data_dir() / "backup"


def backup_db(max_keep: int = 8) -> Path | None:
    """Respaldo consistente del SQLite (checkpoint WAL primero).
    Semanal: salta si ya existe un backup de hoy. Rota hasta max_keep copias."""
    vdb = _vdb()
    if not vdb.db_exists():
        return None
    bdir = backup_dir()
    bdir.mkdir(parents=True, exist_ok=True)
    hoy = date.today().strftime("%Y%m%d")
    del_dia = bdir / f"historial_{hoy}.db"
    if del_dia.exists():
        return None  # ya hay backup de hoy
    # Checkpoint WAL para que la copia incluya todo
    conn = vdb.connect(readonly=False)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
    import shutil

    tmp = bdir / f".tmp_{hoy}.db"
    shutil.copy2(vdb.db_path(), tmp)
    # Validar integridad antes de promocionar
    check = sqlite3.connect(f"file:{tmp.as_posix()}?mode=ro", uri=True)
    try:
        ok = check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        check.close()
    if not ok:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("backup descartado: integrity_check fallo")
    tmp.rename(del_dia)
    # Rotacion: conservar las mas recientes
    backups = sorted(bdir.glob("historial_*.db"))
    for old in backups[:-max_keep]:
        old.unlink(missing_ok=True)
    return del_dia


def import_db(origen: Path) -> dict:
    """Reemplaza la DB local por otra (ej. copiada de otra PC de la red).
    Valida integridad y schema antes; hace backup del archivo actual.
    Requiere que no haya captura en curso."""
    vdb = _vdb()
    origen = Path(origen)
    if not origen.exists():
        raise FileNotFoundError(f"no existe: {origen}")
    # Validar integridad del origen
    check = sqlite3.connect(f"file:{origen.as_posix()}?mode=ro", uri=True)
    try:
        if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("archivo origen corrupto (integrity_check fallo)")
        tablas = {r[0] for r in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        check.close()
    if "ventas" not in tablas:
        raise ValueError("no es una DB del sustentor (falta tabla ventas)")
    # Stats de lo que se importa
    check2 = sqlite3.connect(f"file:{origen.as_posix()}?mode=ro", uri=True)
    try:
        row = check2.execute(
            "SELECT COUNT(*), MIN(fecha_orig), MAX(fecha_orig) FROM ventas"
        ).fetchone()
    finally:
        check2.close()
    stats = {"filas": row[0], "fecha_min": row[1], "fecha_max": row[2]}
    if not row[0]:
        raise ValueError("archivo origen vacio")
    # Backup de la DB actual antes de reemplazar
    if vdb.db_exists():
        vdb.backup_db()
    vdb.reset_connections()
    destino = vdb.db_path()
    destino.parent.mkdir(parents=True, exist_ok=True)
    import shutil

    if destino.exists():
        # Reemplazo en caliente (Windows-safe): copiar el contenido con la
        # backup API de SQLite dentro del archivo vivo. Borrar o reemplazar
        # el archivo (incl. -wal/-shm) falla con WinError 32 si otro hilo
        # (UI/poll/captura) mantiene la DB abierta en modo WAL, porque
        # reset_connections() solo cierra las conexiones del hilo actual.
        src = sqlite3.connect(f"file:{origen.as_posix()}?mode=ro", uri=True, timeout=60)
        try:
            dst = sqlite3.connect(str(destino), timeout=60)
            try:
                dst.execute("PRAGMA busy_timeout = 60000")
                try:
                    src.backup(dst, pages=32)
                except sqlite3.OperationalError as ex:
                    raise RuntimeError(
                        "La DB local está en uso (posible captura en curso). "
                        f"Detén la captura e reintenta: {ex}"
                    ) from ex
            finally:
                dst.close()
        finally:
            src.close()
    else:
        shutil.copy2(origen, destino)
    vdb.init_db()  # asegurar vistas nuevas (ej. vw_impacto_documento) en DBs viejas
    vdb.invalidate_lineas_cache()  # la DB local fue reemplazada por completo
    vdb.invalidate_sucursales_cache()
    vdb.refresh_lineas_async()  # re-calienta el catalogo en background
    vdb.refresh_sucursales_async()
    try:
        # F2: la copia de archivo trae ord_compra crudo; se normaliza in situ
        # para que el picker de O/C no nazca muerto tras un import.
        from src.core.oc_backfill import remapar_orden_compra_desde_origen

        stats["orden_compra_remapeadas"] = remapar_orden_compra_desde_origen()["filas_actualizadas"]
    except Exception as e:
        log.warning("remap orden_compra post-import omitido: %s", e)
        stats["orden_compra_remapeadas"] = 0
    try:
        # El archivo es nuevo: el watermark viejo mentiría (fmax se auto-cura
        # desde los datos, pero reconstruir evita re-descargas inútiles).
        from src.core.cartucho import reconstruir_day_state

        _c = vdb.get_conn()
        _e = vdb.connect_estado(readonly=False)
        try:
            vdb.init_estado(_e)
            stats["dias_reconstruidos"] = reconstruir_day_state(_c, _e)
        finally:
            _e.close()
    except Exception as e:
        log.warning("rebuild day_state post-import omitido: %s", e)
    try:
        # Checkpoint suave (no bloqueante): colapsa el WAL si no hay lectores
        # activos; si hay, SQLite lo hará solo más tarde.
        vdb.get_conn().execute("PRAGMA wal_checkpoint(PASSIVE)")
    except Exception:
        pass
    return stats


def next_month_after_last(desde: date) -> date:
    """Primer dia del mes siguiente al ultimo mes capturado (para sync incremental)."""
    vdb = _vdb()
    health = vdb.db_health()
    fmax = health.get("fecha_max") if health.get("exists") else None
    if fmax:
        try:
            y, m = int(fmax[:4]), int(fmax[5:7])
            return date(y + (m == 12), (m % 12) + 1, 1)
        except (ValueError, TypeError):
            pass
    return date(desde.year, desde.month, 1)
