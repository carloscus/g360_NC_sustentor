"""Discovery y sincronización de la base de datos SQLite del sustentor.

La DB "fuente" (fat, llena por g360-ventas-db) vive en una carpeta conocida
(ej. %APPDATA%/g360-db-ventas/data). Este módulo la localiza, toma un
snapshot consistente (la copia puede estar con WAL activo) y la importa a la
DB local por si solo hay que actualizar días recientes.
"""

from __future__ import annotations

import logging
import os
import socket
import sqlite3
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# ── Rutas convencionales de la DB fuente (g360-ventas-db) ──────────────────


def _default_orphign_paths() -> list[Path]:
    """Rutas candidatas donde puede estar historial.db (en orden de prioridad).

    Prevalece: 1) variable G360_DB_ORIGEN, 2) %APPDATA%\\g360-db-ventas\\data
    (la usada por el repo Tauri), 3) variantes conocidas.
    """
    candidatas: list[Path] = []
    env = os.getenv("G360_DB_ORIGEN")
    if env:
        candidatas.append(Path(env))
    appdata = os.getenv("APPDATA") or (str(Path.home() / "AppData" / "Roaming"))
    appdata = Path(appdata)
    candidatas += [
        appdata / "g360-db-ventas" / "data",
        appdata / "g360-ventas-db" / "data",
        Path.home() / "Documents" / "G360-ecosystem" / "projects" / "g360-ventas-db" / "data",
        Path("C:/g360-db-ventas/data"),
    ]
    # Origen configurado manualmente en la config local
    try:
        from src.core.ventas_db_config import load_app_config

        extra = load_app_config().get("db_origen")
        if extra:
            candidatas.insert(0, Path(extra))
    except Exception:
        pass
    return candidatas


def buscar_db_remota(rutas: Optional[list[Path]] = None) -> Optional[dict]:
    """Busca un historial.db válido en las rutas candidatas.

    Ligero: solo comprueba existencia/tamaño/fecha (sin integrity_check, que
    en una DB de ~1 GB tarda). La validación completa ocurre al sincronizar.

    Returns:
        Dict con la info o None si no hay ninguna fuente.
    """
    if rutas is None:
        rutas = _default_orphign_paths()
    visitados = set()
    for r in rutas:
        r = Path(r) if not isinstance(r, Path) else r
        try:
            r = r.resolve()
        except OSError:
            continue
        if r in visitados:
            continue
        visitados.add(r)
        candidato = r / "historial.db" if r.is_dir() else r
        if not candidato or not candidato.exists():
            continue
        try:
            st = candidato.stat()
        except OSError:
            continue
        return {
            "origen": str(r),
            "ruta": str(candidato),
            "ruta_corta": str(candidato).replace(str(Path.home()), "~", 1),
            "size_bytes": st.st_size,
            "size_mb": st.st_size / (1024 * 1024),
            "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
        }
    return None


def snapshot_consistente(origen: Path, destino: Path, progress_cb=None) -> Path:
    """Copia consistente via SQLite backup API (incluye el contenido del WAL).

    Seguro para copiar una DB que otra app tiene abierta en modo WAL: produce
    un archivo único, sin -wal/-shm, listo para import_db.
    """
    destino = Path(destino)
    if destino.exists():
        destino.unlink(missing_ok=True)
    destino.parent.mkdir(parents=True, exist_ok=True)

    src = sqlite3.connect(f"file:{_path_to_uri(origen)}?mode=ro", uri=True, timeout=30)
    try:
        dst = sqlite3.connect(str(destino))
        try:
            src.backup(dst, pages=1, progress=progress_cb)
        finally:
            dst.close()
    finally:
        src.close()
    return destino


def _path_to_uri(p: Path) -> str:
    """str() portable de una ruta para URIs de SQLite (usa / )."""
    return str(p).replace("\\", "/")


def sincronizar_desde_remota(
    rutas: Optional[list[Path]] = None,
    progress_cb=None,
) -> dict:
    """Busca la DB fuente, toma snapshot consistente e importa a la local.

    La importación valida integridad/schema, hace backup del archivo local
    actual y reemplaza. Requiere que no haya una captura en curso.

    Returns:
        Dict con stats: filas, fecha_min, fecha_max, origen, size_mb, mtime.
    """
    from src.core import ventas_db

    info = buscar_db_remota(rutas)
    if not info:
        raise RuntimeError(
            "No se encontró la DB fuente (g360-db-ventas). Revisa la ruta o "
            "defínela con la variable G360_DB_ORIGEN o config db_origen."
        )
    origen = Path(info["ruta"])
    if not origen.exists():
        raise FileNotFoundError(f"La DB fuente ya no existe: {origen}")

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        staging = Path(tmp.name)
    try:
        if progress_cb:
            progress_cb(0, 1, "Snapshot consistente...")
        snapshot_consistente(origen, staging, progress_cb=progress_cb)
        if progress_cb:
            progress_cb(0, 1, "Validando e importando local...")
        stats = ventas_db.import_db(staging)
        stats.update({"origen": info["ruta"], "size_mb": info["size_mb"], "mtime": info["mtime"]})
        return stats
    finally:
        staging.unlink(missing_ok=True)


# ── Import parcial (últimos N años) desde la DB fuente ──────────────────────

# Columnas destino del app (schema de ventas_db.CREATE_TABLE_VENTAS,
# sin `id`/`capturado_en` que tienen DEFAULT). F2: `ord_compra` viaja
# directo (el valor ya viene normalizado); `mes_ref` se deriva de fecha_orig.
_VENTAS_TARGET_COLS = (
    "id_articulo",
    "original_sku",
    "nom_articulo",
    "id_linea",
    "nom_linea",
    "id_grupo",
    "nom_grupo",
    "id_tipo",
    "nom_tipo",
    "id_familia",
    "nom_familia",
    "id_cliente",
    "doc_cliente",
    "nom_cliente",
    "tpo_doc",
    "serie_doc",
    "nro_doc",
    "referencia",
    "moneda",
    "cantidad",
    "soles",
    "dolares",
    "precio_unitario",
    "cantidad_fae",
    "anho",
    "mes",
    "fecha_orig",
    "fecha_ref",
    "fecha_venc",
    "cod_sucursal",
    "nom_sucursal",
    "departamento",
    "provincia",
    "distrito",
    "id_vendedor",
    "nom_vendedor",
    "id_pedido",
    "ord_compra",
    "file_source",
    "tipo_operacion",
    "factura_ref_serie",
    "factura_ref_nro",
    "folio_unico",
    # Extras de la fat DB que se conservan (si la fuente las tiene)
    "canal_distribucion",
    "division",
    "estado_linea",
    "fec_cargo",
    "id_guia",
    "nom_condicion_pago",
)


def _fecha_corte_anyos(fecha_max: str, anyos: int) -> Optional[str]:
    """fecha_max menos `anyos` años, en formato YYYY-MM-DD. None si es inválida."""
    try:
        y, m, d = (int(p) for p in fecha_max.split("-")[:3])
        return f"{y - anyos:04d}-{m:02d}-{d:02d}"
    except Exception:
        return None


def sincronizar_desde_remota_parcial(
    anyos: Optional[int] = 10,
    rutas: Optional[list[Path]] = None,
    progress_cb=None,
) -> dict:
    """Copia solo los últimos `anyos` años de la DB fuente a la local.

    A diferencia de ``sincronizar_desde_remota`` (que reemplaza el archivo con
    la copia completa de la fat DB), reconstruye la DB local con el schema del
    app: copia ``ord_compra`` directo (F2, ya normalizado), conserva
    las columnas extra de g360-ventas-db presentes en ventas (canal_distribucion,
    division, estado_linea, fec_cargo, id_guia, nom_condicion_pago) y recalcula
    los agregados (agg_cliente_mes, nc_asociadas, stats_cache, mes_checksums)
    sobre las filas copiadas. Solo trae la tabla ``ventas`` (descarta las tablas
    internas de la fat DB: dim_*, fact_venta_mes, su sync_log/stats_cache, etc.).

    Args:
        anyos: cuántos años atrás se corta, contando desde la fecha máxima
            de la fuente. None → copia completa (todos los años).

    Returns:
        Dict con stats: filas, fecha_min, fecha_max, origen, size_mb, mtime,
        filas_fuente y fecha_corte (None = todos los años).
    """
    from src.core import ventas_db

    info = buscar_db_remota(rutas)
    if not info:
        raise RuntimeError(
            "No se encontró la DB fuente (g360-db-ventas). Revisa la ruta o "
            "defínela con la variable G360_DB_ORIGEN o config db_origen."
        )
    origen = Path(info["ruta"])
    if not origen.exists():
        raise FileNotFoundError(f"La DB fuente ya no existe: {origen}")

    if progress_cb:
        progress_cb(0, 1, "Leyendo la DB fuente...")
    # La propia consulta INSERT...SELECT es una transacción de lectura sobre la
    # fuente (snapshot consistente): no hace falta snapshot_consistente.
    src = sqlite3.connect(f"file:{_path_to_uri(origen)}?mode=ro", uri=True, timeout=60)
    try:
        cols_src = {r[1] for r in src.execute("PRAGMA table_info(ventas)")}
        if "ventas" not in {
            r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }:
            raise ValueError("no es una DB de ventas (falta tabla ventas)")
        fmax = src.execute("SELECT MAX(fecha_orig) FROM ventas").fetchone()[0]
        filas_fuente = src.execute("SELECT COUNT(*) FROM ventas").fetchone()[0]
        if not filas_fuente:
            raise ValueError("la DB fuente está vacía (sin filas en ventas)")
    except sqlite3.DatabaseError:
        raise ValueError("archivo origen no es una DB SQLite válida")

    try:
        if progress_cb:
            progress_cb(0, 1, f"Fuente: {filas_fuente:,} filas · fecha máx. {fmax or '?'}")
        corte: Optional[str] = None
        if anyos and anyos > 0:
            corte = _fecha_corte_anyos(fmax, anyos) if fmax else None

        # Mapear cada columna destino a un SELECT sobre la fuente.
        # F2: ord_compra viaja directo (identidad); la normalización vive
        # en el dato, no en una columna aparte.
        col_exprs: list[str] = []
        for c in _VENTAS_TARGET_COLS:
            if c in cols_src:
                col_exprs.append(f"CAST({c} AS INTEGER)" if c in ("anho", "mes") else c)
            else:
                col_exprs.append("''")
        target_cols = ", ".join(_VENTAS_TARGET_COLS) + ", mes_ref"
        select_exprs = ", ".join(col_exprs) + ", COALESCE(substr(fecha_orig, 1, 7), '') AS mes_ref"
        sql = f"INSERT INTO dst.ventas ({target_cols}) SELECT {select_exprs} FROM ventas"
        if corte:
            sql += " WHERE fecha_orig >= ?"
    finally:
        src.close()

    # Staging: schema del app pero con solo el subconjunto copiado
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        staging = Path(tmp.name)
    try:
        conn = sqlite3.connect(str(staging), timeout=60)
        try:
            conn.execute(ventas_db.CREATE_TABLE_VENTAS)
        finally:
            conn.close()

        src = sqlite3.connect(f"file:{_path_to_uri(origen)}?mode=ro", uri=True, timeout=60)
        try:
            src.execute("ATTACH DATABASE ? AS dst", (str(staging),))
            try:
                if progress_cb:
                    progress_cb(
                        0,
                        1,
                        f"Copiando {'todos los años' if corte is None else f'desde {corte} (últimos {anyos} años)'}...",
                    )
                params = (corte,) if corte else None
                cur = src.execute(sql, params) if params else src.execute(sql)
                filas = int(cur.rowcount)
                src.commit()  # cerrar la transacción de escritura antes de DETACH
            finally:
                src.execute("DETACH DATABASE dst")
        finally:
            src.close()

        if progress_cb:
            progress_cb(0, 1, f"Copiadas {filas:,} filas — reconstruyendo índices y agregados...")
        conn = sqlite3.connect(str(staging), timeout=60)
        try:
            ventas_db.init_db(conn)  # índices, vistas, agg_cliente_mes (se autopopula)
            ventas_db.populate_nc_asociadas(conn)
            ventas_db.refresh_stats_cache(conn)
            for (mes,) in conn.execute("SELECT DISTINCT mes_ref FROM ventas"):
                ventas_db.record_month_checksum(conn, str(mes))
            conn.commit()
        finally:
            conn.close()

        if progress_cb:
            progress_cb(0, 1, "Validando e importando a la DB local...")
        # La copia de red trae id_vendedor crudo de la fuente: se audita el
        # staging antes de importar para no colgar un vendedor truncado o con
        # el prefijo '01' sin que nada lo delate.
        conn = sqlite3.connect(str(staging), timeout=60)
        try:
            auditoria = ventas_db.auditar_vendedores(conn)
        finally:
            conn.close()
        stats = ventas_db.import_db(staging)
        stats.update(
            {
                "origen": info["ruta"],
                "size_mb": info["size_mb"],
                "mtime": info["mtime"],
                "filas_fuente": filas_fuente,
                "fecha_corte": corte,
                "vendedores_malformados": auditoria["n_malformados"],
                "filas_vendedor_malformado": auditoria["filas_malformadas"],
            }
        )
        return stats
    finally:
        staging.unlink(missing_ok=True)


# Columnas a contrastar entre local y fuente: las que una captura puede
# perder (las 7 que el parse ignoraba) más O/C y los totales del rango.
# F2: ord_compra compara COBERTURA (filas informadas). El local lleva el valor
# plegado y el origen el crudo, pero la cobertura es idéntica (F2a no cambió
# qué filas tienen valor). Que el valor esté normalizado lo verifica el
# auditor oc_sin_normalizar, no este conteo.
COLUMNAS_CONTRASTE = (
    "id_ubigeo",
    "estado_linea",
    "canal_distribucion",
    "id_guia",
    "nom_condicion_pago",
    "division",
    "fec_cargo",
    "ord_compra",
    "nom_vendedor",
)


def contrastar_con_origen(desde: str, hasta: str, rutas=None, conn=None) -> dict:
    """Compara el rango [desde, hasta] entre la DB local y la fuente.

    Detecta columnas que una captura perdió: por columna, filas informadas
    local vs origen, más conteo de folios exclusivos de cada lado. Solo
    lectura en ambas. Si no hay fuente, devuelve {"origen": None}.
    """
    from src.core import ventas_db

    info = buscar_db_remota(rutas)
    if not info:
        return {"origen": None, "desde": desde, "hasta": hasta}
    cerrar = conn is None
    if conn is None:
        conn = ventas_db.connect(readonly=True)
    try:
        src = sqlite3.connect(
            f"file:{_path_to_uri(Path(info['ruta']))}?mode=ro", uri=True, timeout=60
        )
        try:
            cols_src = {r[1] for r in src.execute("PRAGMA table_info(ventas)").fetchall()}
            cols_loc = {r[1] for r in conn.execute("PRAGMA table_info(ventas)").fetchall()}
            rango = "fecha_orig >= ? AND fecha_orig <= ?"
            n_loc, s_loc = conn.execute(
                f"SELECT COUNT(*), ROUND(COALESCE(SUM(soles),0),2) FROM ventas WHERE {rango}",
                (desde, hasta),
            ).fetchone()
            n_src, s_src = src.execute(
                f"SELECT COUNT(*), ROUND(COALESCE(SUM(soles),0),2) FROM ventas WHERE {rango}",
                (desde, hasta),
            ).fetchone()
            columnas: dict[str, dict] = {}
            for c in COLUMNAS_CONTRASTE:
                nl = ns = None
                if c in cols_loc:
                    nl = conn.execute(
                        f"SELECT COUNT(*) FROM ventas WHERE {rango} AND TRIM(IFNULL({c},'')) <> ''",
                        (desde, hasta),
                    ).fetchone()[0]
                if c in cols_src:
                    ns = src.execute(
                        f"SELECT COUNT(*) FROM ventas WHERE {rango} AND TRIM(IFNULL({c},'')) <> ''",
                        (desde, hasta),
                    ).fetchone()[0]
                columnas[c] = {
                    "local": nl,
                    "origen": ns,
                    "pierde": (nl is not None and ns is not None and nl < ns),
                }
            fol_loc = {
                r[0]
                for r in conn.execute(
                    f"SELECT DISTINCT tpo_doc || serie_doc || nro_doc FROM ventas WHERE {rango}",
                    (desde, hasta),
                ).fetchall()
            }
            fol_src = {
                r[0]
                for r in src.execute(
                    f"SELECT DISTINCT tpo_doc || serie_doc || nro_doc FROM ventas WHERE {rango}",
                    (desde, hasta),
                ).fetchall()
            }
            perdidas = sorted(c for c, v in columnas.items() if v["pierde"])
            return {
                "origen": info.get("ruta_corta", info["ruta"]),
                "desde": desde,
                "hasta": hasta,
                "filas": {"local": int(n_loc), "origen": int(n_src)},
                "soles": {"local": float(s_loc or 0), "origen": float(s_src or 0)},
                "columnas": columnas,
                "columnas_perdidas": perdidas,
                "folios_solo_local": len(fol_loc - fol_src),
                "folios_solo_origen": len(fol_src - fol_loc),
                "ok": not perdidas,
            }
        finally:
            src.close()
    finally:
        if cerrar:
            conn.close()


def traer_folios_faltantes(
    desde: str, hasta: str, rutas=None, conn=None, progress_cb=None, conn_estado=None
) -> dict:
    """Copia desde la fuente solo los folios del rango que faltan en local.

    INSERT puro por folio (no existen acá): no puede duplicar ni borrar nada
    existente. Después corre el remap de O/C (normaliza in situ, F2).
    Es la forma segura de "ponerse al día" contra la canónica sin un
    reemplazo completo (que tiraría el trabajo local: alias, day_state).
    """
    from src.core import ventas_db

    info = buscar_db_remota(rutas)
    if not info:
        return {"origen": None, "folios": 0, "filas": 0}
    cerrar = conn is None
    if conn is None:
        conn = ventas_db.connect(readonly=False)
    conn.execute("PRAGMA busy_timeout=60000")
    try:
        src = sqlite3.connect(
            f"file:{_path_to_uri(Path(info['ruta']))}?mode=ro", uri=True, timeout=60
        )
        try:
            cols_src = [r[1] for r in src.execute("PRAGMA table_info(ventas)").fetchall()]
            cols_loc = [r[1] for r in conn.execute("PRAGMA table_info(ventas)").fetchall()]
            cols = [c for c in cols_loc if c in cols_src and c != "id"]
            rango = "fecha_orig >= ? AND fecha_orig <= ?"
            fol_src = {
                r[0]
                for r in src.execute(
                    f"SELECT DISTINCT tpo_doc || serie_doc || nro_doc FROM ventas WHERE {rango}",
                    (desde, hasta),
                ).fetchall()
            }
            fol_loc = {
                r[0]
                for r in conn.execute(
                    f"SELECT DISTINCT tpo_doc || serie_doc || nro_doc FROM ventas WHERE {rango}",
                    (desde, hasta),
                ).fetchall()
            }
            faltan = sorted(fol_src - fol_loc)
            filas = 0
            with ventas_db.write_txn(conn):
                for i, fol in enumerate(faltan, 1):
                    if progress_cb and (i == 1 or i == len(faltan) or i % 200 == 0):
                        progress_cb(i, len(faltan), f"folio {fol}")
                    rows = src.execute(
                        f"SELECT {', '.join(cols)} FROM ventas "
                        f"WHERE tpo_doc || serie_doc || nro_doc = ?",
                        (fol,),
                    ).fetchall()
                    if rows:
                        conn.executemany(
                            f"INSERT INTO ventas ({', '.join(cols)}) "
                            f"VALUES ({', '.join('?' * len(cols))})",
                            rows,
                        )
                        filas += len(rows)
            remap = {"filas_actualizadas": 0}
            if filas:
                from src.core.oc_backfill import remapar_orden_compra_desde_origen

                remap = remapar_orden_compra_desde_origen(conn, conn_estado=conn_estado)
            return {
                "origen": info.get("ruta_corta", info["ruta"]),
                "desde": desde,
                "hasta": hasta,
                "folios": len(faltan),
                "filas": int(filas),
                "oc_remapeadas": int(remap.get("filas_actualizadas", 0)),
            }
        finally:
            src.close()
    finally:
        if cerrar:
            conn.close()


@dataclass
class DbNetworkEntry:
    """Entry encontrado en la red."""

    ip: str
    path: str
    size_bytes: int
    last_write: str


def get_local_subnet() -> str:
    """Determina el segmento de red local (ej: '172.16.30')."""
    try:
        hostname = socket.gethostname()
        ip = socket.gethostbyname(hostname)
        # Extraer primeros 3 octetos
        parts = ip.split(".")
        if len(parts) == 4:
            return f"{parts[0]}.{parts[1]}.{parts[2]}"
    except Exception:
        pass
    # Fallback: intentar desde ipconfig
    try:
        result = subprocess.run(["ipconfig", "/all"], capture_output=True, text=True, timeout=5)
        for line in result.stdout.splitlines():
            if "IPv4" in line or "Address" in line:
                parts = line.strip().split()
                for p in parts:
                    if p.count(".") == 3 and p.replace(".", "").isdigit():
                        octets = p.split(".")
                        return f"{octets[0]}.{octets[1]}.{octets[2]}"
    except Exception:
        pass
    return "192.168.1"  # default


def _check_share(ip: str, share_name: str = "g360-erp-nc-sustentor") -> bool:
    """Verifica si un share SMB es accesible."""
    try:
        path = f"\\\\{ip}\\{share_name}"
        result = subprocess.run(["net", "use", path], capture_output=True, timeout=3)
        return result.returncode == 0
    except Exception:
        return False


def _list_db_on_share(ip: str, share_name: str = "g360-erp-nc-sustentor") -> list[DbNetworkEntry]:
    """Busca historial.db en un share SMB."""
    entries = []
    try:
        data_dir = f"\\\\{ip}\\{share_name}\\data"
        result = subprocess.run(["dir", "/a", data_dir], capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            return entries
        # Parsear salida de dir
        lines = result.stdout.splitlines()
        for line in lines:
            if "historial.db" in line.lower() and ".db" in line:
                parts = line.split()
                if len(parts) >= 3:
                    try:
                        size = int(parts[-2]) if parts[-2].isdigit() else 0
                        date_str = parts[0] + " " + parts[1] if len(parts) > 1 else ""
                        entries.append(
                            DbNetworkEntry(
                                ip=ip,
                                path=f"{data_dir}\\historial.db",
                                size_bytes=size,
                                last_write=date_str,
                            )
                        )
                    except (ValueError, IndexError):
                        pass
    except Exception as e:
        log.debug(f"Error listing share {ip}: {e}")
    return entries


def scan_network_for_db(
    subnet: Optional[str] = None, timeout_per_ip: float = 1.0
) -> list[DbNetworkEntry]:
    """Escanea el segmento de red buscando historial.db.

    Args:
        subnet: Segmento red (ej '172.16.30'). Si None, usa el auto-detectado.
        timeout_per_ip: Tiempo maximo por IP.

    Returns:
        Lista de entradas encontradas.
    """
    if subnet is None:
        subnet = get_local_subnet()

    results = []
    total = time.time()
    log.info(f"Escaneando red {subnet}.x ...")

    for i in range(1, 255):
        ip = f"{subnet}.{i}"
        # Check SMB accessibility
        if _check_share(ip):
            entries = _list_db_on_share(ip)
            results.extend(entries)
            log.info(f"  Found DB at {ip}: {[e.path for e in entries]}")

        # Progress every 20 IPs
        if i % 20 == 0:
            elapsed = time.time() - total
            log.info(f"  Scanned {i}/254 IPs ({elapsed:.1f}s)...")

    return results


def get_network_db_info(db_path: Path) -> dict:
    """Extrae info de integridad de una DB (local o red)."""
    info = {
        "path": str(db_path),
        "exists": db_path.exists(),
        "size_mb": db_path.stat().st_size / (1024 * 1024) if db_path.exists() else 0,
        "checksum": None,
        "rows": 0,
        "fecha_max": None,
        "error": None,
    }
    if not db_path.exists():
        return info
    try:
        import sqlite3

        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        try:
            info["rows"] = conn.execute("SELECT COUNT(*) FROM ventas").fetchone()[0]
            info["fecha_max"] = conn.execute("SELECT MAX(fecha_orig) FROM ventas").fetchone()[0]
            cs = conn.execute("SELECT ROUND(SUM(soles), 2) FROM ventas").fetchone()[0]
            info["checksum"] = cs
            # Integrity check rapido
            integ = conn.execute("PRAGMA integrity_check").fetchone()[0]
            info["integrity"] = integ
        finally:
            conn.close()
    except Exception as e:
        info["error"] = str(e)
        log.warning(f"Error reading DB {db_path}: {e}")
    return info


def get_db_info_quick(db_path: Path, timeout_s: float = 20.0) -> dict:
    """Validacion rapida de una DB: schema, filas, fecha_max (<1s local).

    Evita a proposito PRAGMA quick_check/integrity_check: en DBs de
    2-3 GB tardan 2+ min (medido: quick_check=116s). La verificacion
    profunda ocurre dentro de ``import_db`` durante el reemplazo,
    donde ya hay barra de progreso visible.

    Args:
        timeout_s: si COUNT/MAX exceden este tiempo (red lenta), se
            abortan y se retorna modo ``partial=True`` con lo ya
            verificado (existe, tamano, schema). El reemplazo sigue
            permitido: import_db es la compuerta real de integridad.
    """
    info = {
        "path": str(db_path),
        "exists": db_path.exists(),
        "size_mb": db_path.stat().st_size / (1024 * 1024) if db_path.exists() else 0,
        "rows": 0,
        "fecha_max": None,
        "integrity": None,
        "partial": False,
        "error": None,
    }
    if not db_path.exists():
        info["error"] = "archivo no existe"
        return info
    try:
        import sqlite3

        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        try:
            # Verificar schema (1 pagina: rapido aun en red)
            tablas = {
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if "ventas" not in tablas:
                info["error"] = "no es DB del sustentor (falta tabla ventas)"
                return info
            # Abortar COUNT/MAX si la red es lenta
            t_start = time.monotonic()

            def _abort():
                return 1 if (time.monotonic() - t_start) >= timeout_s else 0

            conn.set_progress_handler(_abort, 1 if timeout_s <= 0 else 1000)
            try:
                # Filas y fecha max (usan indice: ~0.1s en 2.8M filas local)
                info["rows"] = conn.execute("SELECT COUNT(*) FROM ventas").fetchone()[0]
                info["fecha_max"] = conn.execute("SELECT MAX(fecha_orig) FROM ventas").fetchone()[0]
                info["integrity"] = "ok"
            except sqlite3.OperationalError as ex:
                if "interrupted" in str(ex).lower():
                    info["partial"] = True
                    info["integrity"] = "pendiente"
                    log.info(
                        "get_db_info_quick timeout (%.0fs) en %s: conteo omitido",
                        timeout_s,
                        db_path,
                    )
                else:
                    raise
            finally:
                conn.set_progress_handler(None, 0)
        finally:
            conn.close()
    except Exception as e:
        info["error"] = str(e)
        log.warning("get_db_info_quick error %s: %s", db_path, e)
    return info


def copy_db_network(source_path: str, dest_path: Path, progress_cb=None) -> dict:
    """Copia DB desde red local a destino.

    Returns:
        Dict con resultado de la copia.
    """
    result = {
        "success": False,
        "bytes_copied": 0,
        "error": None,
    }
    try:
        src = Path(source_path)
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        # Copiar usando shutil para mejor manejo de errores
        total_size = src.stat().st_size
        copied = 0

        with open(src, "rb") as fsrc, open(dest_path, "wb") as fdst:
            while True:
                chunk = fsrc.read(8192)
                if not chunk:
                    break
                fdst.write(chunk)
                copied += len(chunk)
                if progress_cb and total_size > 0:
                    progress_cb(copied, total_size)

        result["bytes_copied"] = copied
        result["success"] = True

        # Validar integridad post-copia
        if dest_path.exists():
            info = get_network_db_info(dest_path)
            result["validated"] = info.get("integrity") == "ok"
            result["rows"] = info.get("rows", 0)
            result["checksum"] = info.get("checksum")

    except Exception as e:
        result["error"] = str(e)
        log.error(f"Error copying DB from {source_path}: {e}")

    return result
