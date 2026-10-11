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
from datetime import date, datetime
from pathlib import Path

log = logging.getLogger(__name__)


def _vdb():
    """Retorna el módulo ventas_db (cargado, por eso el import es perezoso)."""
    from src.core import ventas_db

    return ventas_db


# ── Backup / import (multi-PC) ──────────────────────────────────────────────


def backup_dir() -> Path:
    return _vdb().data_dir() / "backup"


def backup_db(max_keep: int = 2, force: bool = False, motivo: str = "") -> Path | None:
    """Respaldo consistente del SQLite (checkpoint WAL primero).

    `max_keep=2`: la DB es de varios GB y cada copia es completa. Con 8 se
    acumularon ~30 GB de copias casi identicas; 2 (estado actual + el previo)
    alcanzan para volver atras y ocupan la mitad.

    `force=True` lo usan las operaciones destructivas (reemplazar la DB). Sin
    eso el daily se los saltaba en silencio: `backup_db()` devolvia None
    porque ya habia backup del dia, la UI anunciaba "backup automatico" y en
    realidad no habia copia del estado que se iba a pisar. Devuelve el Path
    del backup hecho (o None si no se hizo nada) para que el llamador decida.

    La rotacion ordena por FECHA DE MODIFICACION, no por nombre: los backups
    forzados llevan prefijo ("historial_pre-import_...") y por nombre
    "pre-" ordena despues de cualquier "2026...", asi que un forzado del dia
    10 se tomaria por mas nuevo que el diario del 11 y la rotacion expulsaria
    al equivocado.
    """
    vdb = _vdb()
    if not vdb.db_exists():
        return None
    bdir = backup_dir()
    bdir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if force:
        etiqueta = f"pre-{motivo}" if motivo else "pre"
        base = f"historial_{etiqueta}_{stamp}"
    else:
        base = f"historial_{date.today().strftime('%Y%m%d')}"
        if (bdir / f"{base}.db").exists():
            return None  # ya hay backup del dia

    # Dos forzados dentro del mismo segundo comparten `stamp`: el segundo
    # copy2 sobreescribiria el primero y la rotacion creeria que hay una sola
    # copia. Sufijo incremental ante colision.
    destino = bdir / f"{base}.db"
    intento = 1
    while destino.exists():
        destino = bdir / f"{base}-{intento}.db"
        intento += 1

    # Checkpoint WAL para que la copia incluya todo
    conn = vdb.connect(readonly=False)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
    import shutil

    tmp = bdir / f".tmp_{destino.stem}.db"
    shutil.copy2(vdb.db_path(), tmp)
    # quick_check, NO integrity_check. Es el mismo criterio que usa el import
    # del origen y por el mismo motivo (el comentario de `import_db` lo
    # explica): en una DB de 3.7 GB el chequeo completo tarda 12-25 min, y eso
    # se pagaba en CADA backup, incluido el diario de la captura. quick_check
    # verifica la estructura de paginas, que es lo que importa para saber que
    # la copia sirve como origen de una restauracion.
    check = sqlite3.connect(f"file:{tmp.as_posix()}?mode=ro", uri=True)
    try:
        ok = check.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    finally:
        check.close()
    if not ok:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("backup descartado: quick_check fallo")
    tmp.rename(destino)
    # Rotacion: conservar las mas recientes por fecha de modificacion.
    backups = sorted(bdir.glob("historial_*.db"), key=lambda p: (p.stat().st_mtime, p.name))
    for old in backups[:-max_keep]:
        old.unlink(missing_ok=True)
    return destino


def _gb(n: float) -> str:
    return f"{n / 1024**3:.1f} GB"


def _estructura_ok(db: Path) -> tuple[bool, str]:
    """¿El archivo abre y tiene lo mínimo para ser la DB del sustentor?

    Deliberadamente NO es `quick_check`: eso son ~56 s sobre 3.7 GB y ya se
    corrio sobre el origen, de donde salio cada pagina copiada. Con el destino
    alcanza con verificar que se puede abrir, que la tabla `ventas` existe y
    que tiene el numero de filas esperado: si la copia se cortó a mitad, el
    esquema o el conteo no cierran.
    """
    try:
        c = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=30)
    except sqlite3.Error as ex:
        return False, f"no abre: {ex}"
    try:
        tablas = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "ventas" not in tablas:
            return False, "falta la tabla ventas"
        # COUNT(*) y MIN/MAX salen por indice: segundos, no minutos.
        n, fmin, fmax = c.execute(
            "SELECT COUNT(*), MIN(fecha_orig), MAX(fecha_orig) FROM ventas"
        ).fetchone()
    except sqlite3.Error as ex:
        return False, f"no se pudo leer: {ex}"
    finally:
        c.close()
    if not n:
        return False, "la tabla ventas quedo vacia"
    return True, f"{n:,} filas ({fmin} -> {fmax})"


def _chequear_espacio(origen: Path, destino: Path) -> None:
    """Aborta antes de copiar si el disco no puede absorber la operacion.

    Reemplazar una DB de varios GB hace CRECER el archivo destino hasta el
    tamano del origen (la backup API no lo trunca). Si el disco se llena a
    mitad de camino, el archivo vivo queda con paginas viejas mezcladas con
    nuevas: corrupto y sin forma de recalcular. Es el fallo mas probable y el
    mas caro, y se detecta con una llamada antes de tocar nada.
    """
    import shutil as _sh

    try:
        libre = _sh.disk_usage(destino.parent).free
    except OSError:
        return  # sin medicion no se bloquea: el backup previo ya es la red

    necesario = max(0, origen.stat().st_size - destino.stat().st_size)
    # Margen: el WAL de destino y los .db-shm/-wal se reescriben durante la
    # operacion. Sin holgura, un disco "justo" falla igual.
    margen = 512 * 1024**2
    if libre < necesario + margen:
        raise RuntimeError(
            f"Espacio insuficiente para reemplazar la DB: se necesitan "
            f"{_gb(necesario + margen)} (crecimiento {_gb(necesario)} + margen "
            f"512 MB) y hay {_gb(libre)} libres en {destino.parent}. "
            "Liberá espacio y reintentá: no se tocó la DB actual."
        )
    log.info("import_db: espacio ok (libre %s, necesario %s)", _gb(libre), _gb(necesario + margen))


def _restaura_desde_backup(bkp: Path, destino: Path) -> bool:
    """Vuelve a copiar el backup sobre el archivo vivo. True si se restauró.

    Primero in-place, que es lo unico que funciona si otro hilo tiene la DB
    abierta en WAL (Windows no deja ni borrar ni renombrar). Pero si el archivo
    quedo ilegible (copia a medias que rompio el encabezado), SQLite no lo
    puede abrir y el in-place no tiene a quien escribir: ahi se cae al
    reemplazo de archivo completo, que es correcto siempre que nadie mas lo
    tenga tomado.
    """
    import shutil as _sh

    def _valido() -> bool:
        chk = sqlite3.connect(f"file:{destino.as_posix()}?mode=ro", uri=True)
        try:
            return chk.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        finally:
            chk.close()

    try:
        src = sqlite3.connect(f"file:{bkp.as_posix()}?mode=ro", uri=True, timeout=60)
        try:
            dst = sqlite3.connect(str(destino), timeout=60)
            try:
                dst.execute("PRAGMA busy_timeout = 60000")
                src.backup(dst, pages=4096, sleep=0.01)
            finally:
                dst.close()
        finally:
            src.close()
        ok = _valido()
        via = "in-place"
    except Exception:
        log.warning(
            "import_db: restore in-place imposible (archivo ilegible), "
            "probando reemplazo de archivo"
        )
        try:
            for sidecar in (
                destino.with_name(destino.name + "-wal"),
                destino.with_name(destino.name + "-shm"),
            ):
                sidecar.unlink(missing_ok=True)
            destino.unlink(missing_ok=True)
            _sh.copy2(bkp, destino)
            ok = _valido()
            via = "reemplazo de archivo"
        except Exception:
            log.exception("import_db: fallo la restauracion desde %s", bkp.name)
            return False

    if ok:
        # El backup YA esta en disco y sirve tal cual: no se copia otra vez.
        # Con 3.7 GB un copy2 de mas son minutos y otro tanto de disco, y la
        # copia original no se va a tocar: la rotacion la borra cuando toque.
        log.warning("import_db: DB restaurada desde %s (via %s)", bkp.name, via)
    return ok


def _recuperar(bkp, destino: Path, causa: Exception, contexto: str):
    """Deja la DB como estaba y falla. Siempre levanta.

    Se llama cuando la copia in-place fallo: el archivo esta en estado
    desconocido y el backup previo es lo unico que puede devolverlo a un
    estado usable. Si no hay backup, el mensaje lo dice: la recuperacion
    manual es la unica via.
    """
    if bkp is None:
        raise RuntimeError(
            f"{contexto}: {causa}\n\nLa DB local puede quedar incompleta y no "
            "habia backup previo para restaurar. Revisa el historial antes de "
            "volver a capturar."
        ) from causa

    if _restaura_desde_backup(bkp, destino):
        raise RuntimeError(
            f"{contexto}: {causa}\n\nSe restauro el historial anterior desde "
            f"{bkp.name}. La DB local quedo como estaba antes del intento; "
            "podes reintentar."
        ) from causa

    raise RuntimeError(
        f"{contexto}: {causa}\n\nNo se pudo restaurar automaticamente desde "
        f"{bkp}. Recuperala a mano de {bkp} antes de seguir usando la app."
    ) from causa


def import_db(origen: Path, progress_cb=None) -> dict:
    """Reemplaza la DB local por otra (ej. copiada de otra PC de la red).
    Valida integridad y schema antes; hace backup del archivo actual.
    Requiere que no haya captura en curso.

    progress_cb(pct, msg=None): recibe 0..1 con el progreso y el mensaje del
    paso actual, para que el dialogo muestre EN QUE paso está y no parezca
    congelado (en una DB de 3.7 GB hay 7 pasos pesados, algunos en minutos).
    """
    import time

    vdb = _vdb()
    origen = Path(origen)
    if not origen.exists():
        raise FileNotFoundError(f"no existe: {origen}")
    _t_import = time.perf_counter()

    def _paso(msg, pct=None):
        """Reporta el paso (log + callback con elapsed del import completo)."""
        log.info("import_db [%4.0fs]: %s", time.perf_counter() - _t_import, msg)
        if progress_cb and pct is not None:
            try:
                progress_cb(pct, msg)
            except Exception:
                pass

    # Validar integridad del origen. quick_check (estructura de paginas) en
    # vez de integrity_check: en 3.7 GB el completo tarda 12-25 min (el
    # ">10 min sin terminar" del reemplazo), el rapero ~2.5 min. La backup
    # API ademas falla ante paginas corruptas y hay backup de la DB actual.
    _paso("Validando integridad del origen (puede tardar)...", 0.02)
    t0 = time.perf_counter()
    check = sqlite3.connect(f"file:{origen.as_posix()}?mode=ro", uri=True)
    try:
        if check.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("archivo origen corrupto (quick_check fallo)")
        tablas = {r[0] for r in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        check.close()
    _paso(f"Integridad OK ({time.perf_counter() - t0:.0f}s)", 0.05)
    if "ventas" not in tablas:
        raise ValueError("no es una DB del sustentor (falta tabla ventas)")
    # Stats de lo que se importa (otro scan completo)
    _paso("Contando filas del origen...", 0.08)
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
        _paso("Backup de la DB actual...", 0.10)
        t0 = time.perf_counter()
        # force=True: el daily puede ya existir (la captura lo hace al iniciar)
        # y entonces la copia del estado ACTUAL se saltaba en silencio. Sin
        # ese respaldo, un reemplazo a medias deja la DB irrecuperable.
        bkp = vdb.backup_db(force=True, motivo="import")
        if bkp is None:
            raise RuntimeError(
                "No se pudo respaldar la DB actual: se aborta el reemplazo "
                "para no perder el historial vigente."
            )
        log.info("import_db: backup previo en %s", bkp.name)
        _paso(f"Backup listo ({time.perf_counter() - t0:.0f}s)", 0.15)
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
        _chequear_espacio(origen, destino)
        _paso("Copiando origen -> local...", 0.15)
        t0 = time.perf_counter()
        src = sqlite3.connect(f"file:{origen.as_posix()}?mode=ro", uri=True, timeout=60)
        try:
            dst = sqlite3.connect(str(destino), timeout=60)
            try:
                dst.execute("PRAGMA busy_timeout = 60000")

                def _progress(_status, remaining, total):
                    # Progreso real de la backup API mapeado a 0.15..0.80.
                    # Con 16 MB por paso son ~230 callbacks/3.7 GB.
                    if progress_cb and total:
                        try:
                            frac = (total - remaining) / total
                            progress_cb(0.15 + frac * 0.65, "Copiando origen -> local...")
                        except Exception:
                            pass

                try:
                    # pages=32 seria ~29k iteraciones para 3.7 GB; 16 MB por
                    # paso copia en <1 min local y da progreso incremental.
                    src.backup(dst, pages=4096, sleep=0.01, progress=_progress)
                except Exception as ex:
                    # La copia es IN-PLACE sobre el archivo vivo: cualquier
                    # fallo (disco lleno, DB en uso, E/S) la deja con paginas
                    # viejas mezcladas con nuevas. No se distingue cual de
                    # ellos toco el archivo a medias: TODOS pasan por el mismo
                    # camino, que es intentar dejar la DB como estaba.
                    _recuperar(bkp, destino, ex, "La copia del historial fallo")
            finally:
                dst.close()
        finally:
            src.close()

        # Verificar ANTES de seguir reconstruyendo: una copia "terminada" que
        # dejo el archivo truncado o corrupto no sirve de nada. El chequeo es
        # ESTRUCTURAL y barato (abrir + leer sqlite_master + contar filas por
        # indice): un quick_check completo son ~56 s en 3.7 GB y ya se hizo
        # sobre el ORIGEN al principio, que es de donde salio cada pagina.
        integro, detalle = _estructura_ok(destino)
        if not integro:
            # "Termino sin excepcion" no es "termino bien": la copia pudo
            # truncar o dejar paginas a medias y seguir igual.
            _recuperar(
                bkp,
                destino,
                RuntimeError(f"la copia dejo la DB incompleta ({detalle})"),
                "La copia del historial dejo la DB incompleta",
            )
        _paso(f"Copiado en {time.perf_counter() - t0:.0f}s — {detalle}", 0.80)
    else:
        shutil.copy2(origen, destino)
        if progress_cb:
            try:
                progress_cb(0.80, "Copiando origen -> local...")
            except Exception:
                pass
    _paso("Reconstruyendo índices, vistas y agregados...", 0.82)
    t0 = time.perf_counter()
    vdb.init_db()  # asegurar vistas nuevas (ej. vw_impacto_documento) en DBs viejas
    _paso(f"Estructura lista ({time.perf_counter() - t0:.0f}s)", 0.87)
    vdb.invalidate_lineas_cache()  # la DB local fue reemplazada por completo
    vdb.invalidate_sucursales_cache()
    vdb.refresh_lineas_async()  # re-calienta el catalogo en background
    vdb.refresh_sucursales_async()
    try:
        # F2: la copia de archivo trae ord_compra crudo; se normaliza in situ
        # para que el picker de O/C no nazca muerto tras un import.
        from src.core.oc_backfill import remapar_orden_compra_desde_origen

        _paso("Normalizando orden_compra...", 0.90)
        t0 = time.perf_counter()
        stats["orden_compra_remapeadas"] = remapar_orden_compra_desde_origen()["filas_actualizadas"]
        _paso(f"Remap listo ({time.perf_counter() - t0:.0f}s)", 0.93)
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
            _paso("Reconstruyendo day_state...", 0.94)
            t0 = time.perf_counter()
            vdb.init_estado(_e)
            stats["dias_reconstruidos"] = reconstruir_day_state(_c, _e)
            _paso(f"day_state listo ({time.perf_counter() - t0:.0f}s)", 0.97)
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
    _paso(
        f"Import completo en {time.perf_counter() - _t_import:.0f}s ({stats['filas']:,} filas)",
        0.99,
    )
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
