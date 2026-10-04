"""Cartucho: unidad de transporte de la DB entre PCs.

Un cartucho es UN ARCHIVO .zip con adentro:
  historial.db            DB con forma canónica (checkpointeada, sin -wal)
  estado_sustentor.db     sidecar (oc_alias + day_state, lo no re-derivable)
  config_sanitizado.json  allowlist + retención (NUNCA llaves ni credenciales)
  CARTUCHO.json           manifiesto legible por máquina y humano

Un solo archivo = nada se olvida en el USB (el sidecar viaja sí o sí),
pesa menos (DEFLATE) y el ZIP trae CRC por archivo más nuestro sha256.

exportar_cartucho() genera data/export/cartucho-<ts>.zip. importar_cartucho()
acepta la carpeta o el .zip: valida (manifiesto, sha256, contrato), decide
reemplazo vs merge por folio (superset por día) y nunca destruye trabajo
local sin avisar: el sidecar se une (no se pisa) y los conflictos de O/C
se loguean.

La config de líneas NO viaja como orden: cada PC guarda la suya. El cartucho
lleva la DB completa; las líneas son filtro de pantalla (F6).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from src.core import ventas_db

log = logging.getLogger(__name__)

CARTUCHO_VERSION = 1
MANIFIESTO = "CARTUCHO.json"
MIEMBROS = ("historial.db", "estado_sustentor.db", "config_sanitizado.json", MANIFIESTO)

# Claves de config que SÍ pueden viajar (todo lo demás se descarta,
# en particular credenciales, tokens y llaves).
_CONFIG_SEGURAS = frozenset(
    {
        "allowed_lines",
        "app_retention_years",
        "auto_sync",
        "auto_daily_capture",
        "capture_times",
    }
)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for bloque in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(bloque)
    return h.hexdigest()


def _ahora_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def exportar_cartucho(
    destino: Path | str | None = None, comprimir: bool = True, conservar_carpeta: bool = False
) -> dict:
    """Genera data/export/cartucho-<ts>.zip con DB + sidecar + config + manifiesto.

    Hace checkpoint (colapsa el -wal), verifica el contrato de forma y el
    integrity_check antes de copiar. Si algo no cumple, no exporta.
    Con comprimir=True (default) empaqueta los 4 archivos en un .zip (DEFLATE)
    y borra la carpeta (el zip ES el cartucho); con conservar_carpeta=True
    la deja además para inspección.
    Returns {zip, carpeta, archivos, filas, ...}.
    """
    from src.core.ventas_db_config import data_dir

    base = data_dir() / "export"
    base.mkdir(parents=True, exist_ok=True)
    carpeta = Path(destino) if destino else base / f"cartucho-{datetime.now():%Y%m%d-%H%M%S}"
    if carpeta.exists():
        raise FileExistsError(f"ya existe: {carpeta}")
    carpeta.mkdir(parents=True)

    db_src = ventas_db.db_path()
    est_src = ventas_db.estado_path()
    if not db_src.exists():
        raise FileNotFoundError(f"no existe DB local: {db_src}")

    # 1. Contrato + integridad ANTES de copiar (no exportar basura).
    rep = ventas_db.verificar_contrato()
    if not rep["ok"]:
        raise ventas_db.ContratoDBError(f"no se exporta: la DB local no cumple ({rep['detalle']})")
    chk = sqlite3.connect(f"file:{db_src.as_posix()}?mode=ro", uri=True)
    try:
        if chk.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("integrity_check != ok, no se exporta")
        ventas_filas = chk.execute("SELECT COUNT(*) FROM ventas").fetchone()[0]
        fmin, fmax = chk.execute("SELECT MIN(fecha_orig), MAX(fecha_orig) FROM ventas").fetchone()
        soles = chk.execute("SELECT ROUND(COALESCE(SUM(soles),0),2) FROM ventas").fetchone()[0]
        tablas = {
            r[0]: None
            for r in chk.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
        for t in tablas:
            try:
                tablas[t] = chk.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except Exception:
                tablas[t] = -1
        dias = {
            d: [int(n), float(s)]
            for d, n, s in chk.execute(
                "SELECT dia, total_filas, total_soles FROM day_checksums"
            ).fetchall()
        }
    finally:
        chk.close()

    # 2. Checkpoint: el cartucho no lleva -wal (archivo autocontenido).
    w = ventas_db.connect(readonly=False)
    try:
        w.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        w.commit()
    finally:
        w.close()

    # 3. Copiar DB + sidecar.
    db_dst = carpeta / "historial.db"
    shutil.copy2(db_src, db_dst)
    sidecar = None
    if est_src.exists():
        sidecar = carpeta / "estado_sustentor.db"
        shutil.copy2(est_src, sidecar)

    # 4. Config sanitizada (allowlist como documentación, sin llaves).
    cfg_segura = _config_sanitizada()
    (carpeta / "config_sanitizado.json").write_text(
        json.dumps(cfg_segura, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # 5. Manifiesto.
    alias_resumen: dict = {}
    day_resumen: dict = {}
    if sidecar:
        se = sqlite3.connect(f"file:{sidecar.as_posix()}?mode=ro", uri=True)
        try:
            alias_resumen = {"total": se.execute("SELECT COUNT(*) FROM oc_alias").fetchone()[0]}
            for est_, n in se.execute(
                "SELECT estado, COUNT(*) FROM oc_alias GROUP BY estado"
            ).fetchall():
                alias_resumen[est_] = n
            dr = se.execute("SELECT COUNT(*), MIN(dia), MAX(dia) FROM day_state").fetchone()
            day_resumen = {"dias": dr[0], "desde": dr[1], "hasta": dr[2]}
        finally:
            se.close()
    manifiesto = {
        "cartucho_version": CARTUCHO_VERSION,
        # tipo: "trabajo" (con sidecar y decisiones O/C) o "semilla" (DB
        # recién salida de g360-ventas-db, sin sidecar). El importador acepta
        # ambos; la semilla no trae alias ni watermark.
        "tipo": "trabajo" if sidecar else "semilla",
        "generado_en": _ahora_iso(),
        "generado_por": _origen_pc(),
        "contrato_version": ventas_db.CONTRACTO_VERSION,
        "db": {
            "archivo": "historial.db",
            "sha256": _sha256(db_dst),
            "bytes": db_dst.stat().st_size,
            "user_version": ventas_db.CONTRACTO_VERSION,
            "ventas_filas": ventas_filas,
            "soles_total": soles,
            "fecha_min": fmin,
            "fecha_max": fmax,
            "tablas": tablas,
        },
        "sidecar": {
            "archivo": "estado_sustentor.db" if sidecar else None,
            "sha256": _sha256(sidecar) if sidecar else None,
            "oc_alias": alias_resumen,
            "day_state": day_resumen,
        },
        "dias": dias,
        "config": cfg_segura,
    }
    (carpeta / MANIFIESTO).write_text(json.dumps(manifiesto, ensure_ascii=False), encoding="utf-8")
    out: dict = {
        "carpeta": str(carpeta),
        "ventas_filas": ventas_filas,
        "fecha_max": fmax,
        "archivos": 4 if sidecar else 3,
    }
    if comprimir:
        zpath = carpeta.with_suffix(".zip")
        if zpath.exists():
            zpath.unlink()
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for nombre in MIEMBROS:
                p = carpeta / nombre
                if p.exists():
                    zf.write(p, arcname=nombre)
        out["zip"] = str(zpath)
        out["zip_mb"] = round(zpath.stat().st_size / (1024 * 1024))
        if not conservar_carpeta:
            shutil.rmtree(carpeta, ignore_errors=True)
            out["carpeta"] = None
        log.info(
            "cartucho exportado: %s (%s filas, %s MB)", zpath, f"{ventas_filas:,}", out["zip_mb"]
        )
    else:
        log.info("cartucho exportado: %s (%s filas)", carpeta, f"{ventas_filas:,}")
    return out


def _origen_pc() -> str:
    import os
    import socket

    try:
        return f"{socket.gethostname()}-{os.getenv('USERNAME', '?')}"
    except Exception:
        return "?"


def _config_sanitizada() -> dict:
    """Allowlist + retención sin llaves. Documentación, no se aplica al importar."""
    from src.core.ventas_db_config import allowed_lines, config_file_path

    out: dict = {"allowlist": list(allowed_lines()), "nota": "documentación, no se aplica"}
    try:
        raw = json.loads(config_file_path().read_text(encoding="utf-8"))
        for k, v in raw.items():
            if k in _CONFIG_SEGURAS and not isinstance(v, dict):
                out[k] = v
    except Exception:
        pass
    return out


def _envolver_semilla(db_suelto: Path) -> Path:
    """Envuelve un historial.db suelto como cartucho semilla al vuelo.

    Crea una carpeta temp con hardlink (instantáneo, sin duplicar 3 GB;
    copia si el volumen no lo permite) + manifiesto sintetizado desde el
    propio archivo (contrato, rango, días, sha). Es el bootstrap: permite
    sembrar desde la carpeta de g360-ventas-db sin pasos previos.
    El llamador limpia la carpeta (ver finally de importar_cartucho).
    """
    tmp = Path(tempfile.mkdtemp(prefix="semilla-"))
    destino = tmp / "historial.db"
    try:
        os.link(db_suelto, destino)
    except Exception:
        shutil.copy2(db_suelto, destino)
    src = sqlite3.connect(f"file:{destino.as_posix()}?mode=ro", uri=True, timeout=60)
    try:
        rep = ventas_db.verificar_contrato(src)
        if rep["tablas_faltantes"] or rep["columnas_difieren"]:
            raise ventas_db.ContratoDBError(f"el archivo no cumple: {rep['detalle']}")
        n = src.execute("SELECT COUNT(*) FROM ventas").fetchone()[0]
        fmin, fmax = src.execute("SELECT MIN(fecha_orig), MAX(fecha_orig) FROM ventas").fetchone()
        soles = src.execute("SELECT ROUND(COALESCE(SUM(soles),0),2) FROM ventas").fetchone()[0]
        tablas = {}
        for t in ventas_db.CONTRACTO_TABLAS:
            try:
                tablas[t] = src.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except Exception:
                tablas[t] = -1
        dias = {
            d: [int(nn), float(ss)]
            for d, nn, ss in src.execute(
                "SELECT dia, total_filas, total_soles FROM day_checksums"
            ).fetchall()
        }
        if not dias:
            dias = {
                d: [int(nn), float(ss)]
                for d, nn, ss in src.execute(
                    "SELECT substr(fecha_orig,1,10), COUNT(*), "
                    "ROUND(COALESCE(SUM(soles),0),2) FROM ventas "
                    "WHERE length(fecha_orig)>=10 GROUP BY 1"
                ).fetchall()
            }
    finally:
        src.close()
    man = {
        "cartucho_version": CARTUCHO_VERSION,
        "tipo": "semilla",
        "generado_en": _ahora_iso(),
        "generado_por": f"semilla-de:{db_suelto.parent}",
        "contrato_version": ventas_db.CONTRACTO_VERSION,
        "db": {
            "archivo": "historial.db",
            "sha256": _sha256(destino),
            "bytes": destino.stat().st_size,
            "user_version": ventas_db.CONTRACTO_VERSION,
            "ventas_filas": n,
            "soles_total": soles,
            "fecha_min": fmin,
            "fecha_max": fmax,
            "tablas": tablas,
        },
        "sidecar": {"archivo": None, "sha256": None, "oc_alias": {}, "day_state": {}},
        "dias": dias,
        "config": {"allowlist": [], "nota": "semilla: sin config"},
    }
    (tmp / MANIFIESTO).write_text(json.dumps(man, ensure_ascii=False), encoding="utf-8")
    (tmp / "config_sanitizado.json").write_text("{}", encoding="utf-8")
    return tmp


def _leer_manifiesto(carpeta: Path) -> dict:
    mf = carpeta / MANIFIESTO
    if not mf.exists():
        raise FileNotFoundError(f"no es un cartucho (falta {MANIFIESTO}): {carpeta}")
    man = json.loads(mf.read_text(encoding="utf-8"))
    if man.get("cartucho_version") != CARTUCHO_VERSION:
        raise ValueError(
            f"cartucho_version={man.get('cartucho_version')} (soporto {CARTUCHO_VERSION})"
        )
    return man


def es_superset(
    conn_in: sqlite3.Connection, conn_loc: sqlite3.Connection, tolerancia_soles: float = 0.01
) -> dict:
    """¿El entrante contiene todo lo local, por día? (filas y soles).

    Usa day_checksums si ambas las tienen, si no GROUP BY directo.
    Returns {superset, dias_revisados, dias_faltantes:[...]}.
    Un día local sin filas en el entrante, o con menos filas, rompe el superset.
    Soles con tolerancia (coma flotante), solo como señal secundaria.
    """

    def _dias(conn):
        try:
            rows = conn.execute(
                "SELECT dia, total_filas, total_soles FROM day_checksums"
            ).fetchall()
            if rows:
                return {d: (int(n), float(s)) for d, n, s in rows}
        except Exception:
            pass
        return {
            d: (int(n), float(s))
            for d, n, s in conn.execute(
                "SELECT substr(fecha_orig,1,10), COUNT(*), "
                "ROUND(COALESCE(SUM(soles),0),2) FROM ventas "
                "WHERE length(fecha_orig)>=10 GROUP BY 1"
            ).fetchall()
        }

    loc = _dias(conn_loc)
    inn = _dias(conn_in)
    faltan = [d for d, (n, _s) in loc.items() if inn.get(d, (0, 0.0))[0] < n]
    return {"superset": not faltan, "dias_revisados": len(loc), "dias_faltantes": sorted(faltan)}


def reconstruir_day_state(conn: sqlite3.Connection, conn_estado: sqlite3.Connection) -> int:
    """Deriva day_state del archivo: cada día presente queda 'cerrado'.

    Se usa tras un REEMPLAZO (el archivo manda). No importa watermark viejo:
    si el día está en el archivo con sus filas, está cerrado.
    """
    dias = conn.execute(
        "SELECT substr(fecha_orig,1,10), COUNT(*), "
        "ROUND(COALESCE(SUM(soles),0),2) FROM ventas "
        "WHERE length(fecha_orig)>=10 GROUP BY 1"
    ).fetchall()
    with ventas_db.write_txn(conn_estado):
        conn_estado.execute("DELETE FROM day_state")
        conn_estado.executemany(
            "INSERT INTO day_state (dia, ultima_captura, filas, soles, estado, "
            "cerrado_en) VALUES (?, datetime('now'), ?, ?, 'cerrado', "
            "datetime('now'))",
            [(d, int(n), float(s)) for d, n, s in dias],
        )
    return len(dias)


def unir_sidecar(conn_estado_loc: sqlite3.Connection, sidecar_in: Path) -> dict:
    """Une el sidecar entrante al local (nunca pisa a ciegas).

    oc_alias por PK (id_cliente, oc_raw): si la clave no existe se inserta;
    si existe con DISTINTO oc_norm hay conflicto -> gana el entrante (decisión
    del usuario), el descartado queda en el log y se cuenta. day_state NO se
    toca acá (lo decide reemplazar vs mergear).
    Returns {alias_nuevos, alias_conflictos: [{cliente, raw, local, entrante}]}.
    """
    src = sqlite3.connect(f"file:{sidecar_in.as_posix()}?mode=ro", uri=True)
    try:
        nuevas = 0
        conflictos: list[dict] = []
        with ventas_db.write_txn(conn_estado_loc):
            for cid, raw, norm, est_ in src.execute(
                "SELECT id_cliente, oc_raw, oc_norm, estado FROM oc_alias"
            ).fetchall():
                cur = conn_estado_loc.execute(
                    "SELECT oc_norm, estado FROM oc_alias WHERE id_cliente=? AND oc_raw=?",
                    (cid, raw),
                ).fetchone()
                if cur is None:
                    conn_estado_loc.execute(
                        "INSERT INTO oc_alias (id_cliente, oc_raw, oc_norm, estado) "
                        "VALUES (?,?,?,?)",
                        (cid, raw, norm, est_),
                    )
                    nuevas += 1
                elif cur[0] != norm:
                    conflictos.append(
                        {
                            "cliente": cid,
                            "raw": raw,
                            "local": cur[0],
                            "entrante": norm,
                            "estado_local": cur[1],
                            "estado_entrante": est_,
                        }
                    )
                    conn_estado_loc.execute(
                        "UPDATE oc_alias SET oc_norm=?, estado=?, "
                        "revisado_en=datetime('now') "
                        "WHERE id_cliente=? AND oc_raw=?",
                        (norm, est_, cid, raw),
                    )
                    log.warning(
                        "conflicto O/C %s/%s: local=%r entrante=%r (gana entrante)",
                        cid,
                        raw,
                        cur[0],
                        norm,
                    )
        return {"alias_nuevos": nuevas, "alias_conflictos": conflictos}
    finally:
        src.close()


def leer_allowlist_cartucho(origen: Path | str) -> list[str]:
    """Lee el allowlist del manifiesto sin extraer la DB (solo CARTUCHO.json).

    Sirve para "adoptar líneas" sin importar nada.
    """
    origen = Path(origen)
    if origen.is_file() and origen.suffix.lower() == ".zip":
        with zipfile.ZipFile(origen) as zf:
            man = json.loads(zf.read(MANIFIESTO).decode("utf-8"))
    else:
        carp = origen if origen.is_dir() else origen.parent
        man = json.loads((carp / MANIFIESTO).read_text(encoding="utf-8"))
    return [str(x).upper() for x in (man.get("config", {}).get("allowlist") or [])]


def adoptar_lineas(allowlist: list[str]) -> dict:
    """Guarda el allowlist como configuración local (previa confirmación en UI).

    Returns {antes, despues}. No toca la DB, solo config.json + cache.
    """
    from src.core.ventas_db_config import (
        allowed_lines,
        load_app_config,
        reset_allowed_lines_cache,
        save_app_config,
    )

    antes = list(allowed_lines())
    nuevas = sorted({str(x).upper() for x in allowlist if str(x).strip()})
    if not nuevas:
        raise ValueError("el cartucho no trae allowlist")
    cfg = load_app_config()
    cfg["allowed_lines"] = nuevas
    save_app_config(cfg)
    reset_allowed_lines_cache()
    return {"antes": antes, "despues": list(allowed_lines())}


def diagnostico_cobertura(conn: sqlite3.Connection) -> list[dict]:
    """Por día: set de líneas presentes vs allowlist local.

    Si un día trae menos líneas distintas de las esperadas, se reporta (el
    superset no detecta config divergente: día con 0 filas locales pasa).
    Red de seguridad post-importación.
    """
    from src.core.ventas_db_config import allowed_lines

    esperadas = set(allowed_lines())
    out = []
    for dia, lineas in conn.execute(
        "SELECT substr(fecha_orig,1,10), "
        "GROUP_CONCAT(DISTINCT id_linea) FROM ventas "
        "WHERE length(fecha_orig)>=10 GROUP BY 1 ORDER BY 1"
    ).fetchall():
        presentes = set((lineas or "").split(",")) - {""}
        faltan = sorted(esperadas - presentes)
        if faltan:
            out.append(
                {"dia": dia, "lineas_presentes": sorted(presentes), "lineas_faltantes": faltan}
            )
    return out


def importar_cartucho(origen: Path | str, forzar: str = "") -> dict:
    """Valida un cartucho y lo aplica: reemplazo si es superset, merge si no.

    origen: carpeta del cartucho o .zip (se extrae a temp y se limpia solo).
    forzar: "" (decide solo), "reemplazar" o "mergear" (el usuario elige).
    Nunca destruye sin backup previo. Returns el reporte de lo hecho.
    """
    from src.core.ventas_db_backup import backup_db

    origen = Path(origen)
    tmpdir = None
    if origen.is_file() and origen.suffix.lower() == ".zip":
        tmpdir = Path(tempfile.mkdtemp(prefix="cartucho-"))
        with zipfile.ZipFile(origen) as zf:
            for m in zf.namelist():
                # Seguridad: nada de paths absolutos ni .. (zip malicioso).
                if Path(m).is_absolute() or ".." in Path(m).parts:
                    raise ValueError(f"miembro inseguro en el zip: {m}")
            zf.extractall(tmpdir)
        carpeta = tmpdir
    elif origen.is_file() and origen.name.lower() == "historial.db":
        # .db suelto (p.ej. carpeta de g360-ventas-db): se envuelve como
        # semilla al vuelo con manifiesto sintetizado. Es el bootstrap.
        carpeta = _envolver_semilla(origen)
        tmpdir = carpeta
    elif origen.is_dir() and not (origen / MANIFIESTO).exists():
        suelto = origen / "historial.db"
        if suelto.exists():
            carpeta = _envolver_semilla(suelto)
            tmpdir = carpeta
        else:
            carpeta = origen
    else:
        carpeta = origen
    try:
        man = _leer_manifiesto(carpeta)
        db_in = carpeta / man["db"]["archivo"]
        if not db_in.exists():
            raise FileNotFoundError(f"falta {db_in}")

        # 1. Integridad de transporte.
        if _sha256(db_in) != man["db"]["sha256"]:
            raise ValueError("sha256 de historial.db no coincide: archivo corrupto o ajeno")
        side_in = carpeta / (man["sidecar"]["archivo"] or "estado_sustentor.db")
        tiene_sidecar = bool(man["sidecar"]["archivo"]) and side_in.exists()
        if tiene_sidecar and _sha256(side_in) != man["sidecar"]["sha256"]:
            raise ValueError("sha256 del sidecar no coincide")

        # 2. Contrato de forma del entrante (sin abrir el local todavía).
        src = sqlite3.connect(f"file:{db_in.as_posix()}?mode=ro", uri=True, timeout=60)
        try:
            rep = ventas_db.verificar_contrato(src)
            if rep["tablas_faltantes"] or rep["columnas_difieren"]:
                raise ventas_db.ContratoDBError(f"el cartucho no cumple: {rep['detalle']}")
        finally:
            src.close()

        # 3. Decisión superset vs merge (barata: manifiesto + day_checksums).
        conn_loc = ventas_db.connect(readonly=True)
        try:
            loc_dias = ventas_db.day_checksums_rango(conn_loc, "0000-01-01", "9999-12-31")
        finally:
            conn_loc.close()
        man_dias = man.get("dias", {})
        if forzar == "reemplazar":
            modo = "reemplazar"
        elif forzar == "mergear":
            modo = "mergear"
        else:
            faltan = [d for d, v in loc_dias.items() if man_dias.get(d, [0, 0])[0] < v["filas"]]
            modo = "reemplazar" if not faltan else "mergear"

        # 4. Backup SIEMPRE antes de tocar.
        bkp = backup_db()
        conn = ventas_db.connect(readonly=False)
        conn.execute("PRAGMA busy_timeout=120000")
        reporte: dict = {
            "modo": modo,
            "backup": str(bkp) if bkp else None,
            "manifiesto": {
                "por": man.get("generado_por"),
                "en": man.get("generado_en"),
                "tipo": man.get("tipo", "trabajo"),
            },
        }
        try:
            if modo == "reemplazar":
                # Copia vía API de backup (seguro con la DB abierta en Windows).
                src2 = sqlite3.connect(f"file:{db_in.as_posix()}?mode=ro", uri=True, timeout=60)
                try:
                    src2.backup(conn, pages=500)
                finally:
                    src2.close()
                conn.commit()
                ventas_db.init_db(conn)
                est = ventas_db.connect_estado(readonly=False)
                try:
                    ventas_db.init_estado(est)
                    n_dias = reconstruir_day_state(conn, est)
                    union = (
                        unir_sidecar(est, side_in)
                        if tiene_sidecar
                        else {"alias_nuevos": 0, "alias_conflictos": []}
                    )
                finally:
                    est.close()
                reporte.update({"dias_reconstruidos": n_dias, "sidecar": union})
            else:
                # Merge por folio: trae lo que falta sin pisar nada existente.
                from src.core.db_network import traer_folios_faltantes

                fmin = man["db"].get("fecha_min") or "2010-01-01"
                fmax = man["db"].get("fecha_max") or "2026-09-28"
                m = traer_folios_faltantes(fmin, fmax, rutas=[carpeta], conn=conn)
                est = ventas_db.connect_estado(readonly=False)
                try:
                    ventas_db.init_estado(est)
                    union = (
                        unir_sidecar(est, side_in)
                        if tiene_sidecar
                        else {"alias_nuevos": 0, "alias_conflictos": []}
                    )
                    # Watermark de los días traídos (conserva cerrados previos).
                    for _d, _v in sorted(man_dias.items()):
                        if _v[0] > 0:
                            ventas_db.record_day_capture(conn, _d, est)
                    # day_checksums al día (si no, el próximo superset compara viejo).
                    conn.execute(
                        "INSERT INTO day_checksums (dia, total_filas, total_soles, "
                        "checksum, calculado_en) "
                        "SELECT substr(fecha_orig,1,10), COUNT(*), "
                        "ROUND(COALESCE(SUM(soles),0),2), "
                        "COUNT(*) || ':' || ROUND(COALESCE(SUM(soles),0),2), "
                        "datetime('now') FROM ventas "
                        "WHERE fecha_orig >= ? AND fecha_orig <= ? "
                        "GROUP BY 1 "
                        "ON CONFLICT(dia) DO UPDATE SET total_filas=excluded.total_filas, "
                        "total_soles=excluded.total_soles, checksum=excluded.checksum, "
                        "calculado_en=excluded.calculado_en",
                        (fmin, fmax),
                    )
                finally:
                    est.close()
                reporte.update({"merge": m, "sidecar": union})
            conn.commit()
            reporte["cobertura"] = diagnostico_cobertura(conn)
            return reporte
        finally:
            conn.close()
    finally:
        if tmpdir is not None:
            shutil.rmtree(tmpdir, ignore_errors=True)
