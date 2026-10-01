"""Backfill de orden_compra desde los exports crudos en raw/.

La red nunca trae ORD_COMPRA (la fuente no tiene la columna), así que es la
única columna que solo se llena desde intranet/disco. Recorre los archivos de
raw/, y por documento hace UPDATE de la columna (sin DELETE ni INSERT: no
puede duplicar ni tocar el resto). Solo rellena vacíos: idempotente,
re-ejecutable y respeta revisiones manuales.

Si dos formas crudas del mismo cliente colapsan al mismo canónico, no se
fusiona: se guarda la forma cruda y el par queda 'pendiente' en oc_alias
para revisión (ver ocs_pendientes / oc_resolver).
"""

from __future__ import annotations

import logging
import re
import sqlite3
from collections import Counter
from pathlib import Path

from src.core import ventas_db
from src.core.xls_processor import (
    _is_header_row,
    _map_headers,
    clean_str,
    load_report_source,
    normalize_client_id,
    normalize_orden_compra,
)

log = logging.getLogger(__name__)

_RX_ARCHIVO = re.compile(r"ventas_(\d{4}-\d{2}(?:-\d{2})?)")


def _kind_por_extension(path: Path) -> str:
    suf = path.suffix.lower()
    if suf == ".csv":
        return "csv"
    if suf in (".html", ".htm"):
        return "html"
    return "xls"


def _docs_con_oc(rows: list[list[str]]) -> dict[tuple[str, str, str], dict]:
    """Matriz -> {(tpo, serie, nro): {cliente, ocs: Counter}} con OC informada."""
    header_idx = next((i for i, r in enumerate(rows) if _is_header_row(r)), None)
    if header_idx is None:
        return {}
    colmap = _map_headers(rows[header_idx])

    def g(record: list[str], name: str) -> str:
        idx = colmap.get(name)
        return clean_str(record[idx]) if idx is not None and idx < len(record) else ""

    docs: dict[tuple[str, str, str], dict] = {}
    for record in rows[header_idx + 1 :]:
        if not any(record) or _is_header_row(record):
            continue
        tpo, serie, nro = g(record, "tpo_doc"), g(record, "serie_doc"), g(record, "nro_doc")
        if not tpo or not nro:
            continue
        oc = g(record, "ord_compra")
        if not oc:
            continue
        key = (tpo, serie, nro)
        d = docs.get(key)
        if d is None:
            d = docs[key] = {
                "cliente": normalize_client_id(g(record, "id_cliente_raw")),
                "ocs": Counter(),
            }
        d["ocs"][oc] += 1
    return docs


def _abrir_estado(conn_estado: sqlite3.Connection | None = None):
    """Sidecar real (con init) si el llamador no pasa uno propio (tests)."""
    if conn_estado is None:
        conn_estado = ventas_db.connect_estado(readonly=False)
        ventas_db.init_estado(conn_estado)
        return conn_estado, True
    return conn_estado, False


def remapar_orden_compra_desde_origen(
    conn: sqlite3.Connection | None = None,
    progress_cb=None,
    conn_estado: sqlite3.Connection | None = None,
) -> dict:
    """Normaliza ord_compra in situ (F2: ya no hay columna orden_compra).

    Recorre los (cliente, ord_compra) crudos y los lleva a su forma canónica
    pasando por normalize_orden_compra y oc_alias. Regla de colisiones: si dos
    formas crudas colapsan al mismo canónico sin revisión, NO se fusiona (queda
    'pendiente'); un 'separado' nunca se toca; un 'confirmado' siempre fusiona.
    Idempotente: lo ya normalizado se omite.
    El alias vive en el sidecar (conn_estado).
    """
    cerrar = conn is None
    if conn is None:
        conn = ventas_db.connect(readonly=False)
    conn.execute("PRAGMA busy_timeout=60000")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(ventas)").fetchall()}
    if "ord_compra" not in cols:
        return {"filas_actualizadas": 0, "colisiones": 0, "grupos": 0, "omitido": "falta columna"}
    # OJO: no resetear conn_estado a None acá: el llamador puede pasar el suyo
    # (tests). _abrir_estado solo abre el real si viene None.
    conn_estado, cerrar_estado = _abrir_estado(conn_estado)
    try:
        res = {"filas_actualizadas": 0, "colisiones": 0, "grupos": 0}
        # Índice de apoyo (los UPDATE por grupo lo necesitan; también sirve
        # al picker de O/C). Permanente como el resto de idx_venta_* del app.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ventas_cliente_oc ON ventas(id_cliente, ord_compra)"
        )
        grupos = conn.execute(
            "SELECT id_cliente, ord_compra FROM ventas "
            "WHERE TRIM(IFNULL(ord_compra, '')) <> '' "
            "GROUP BY 1, 2"
        ).fetchall()
        with ventas_db.write_txn(conn):
            for i, (cid, raw_oc) in enumerate(grupos, 1):
                res["grupos"] += 1
                if progress_cb and (i == 1 or i == len(grupos) or i % 5000 == 0):
                    progress_cb(i, len(grupos), f"remap {cid}/{raw_oc}")
                oc_norm = normalize_orden_compra(raw_oc or "")
                if not oc_norm or oc_norm == raw_oc:
                    continue
                otros = ventas_db.oc_alias_upsert(conn_estado, cid, raw_oc, oc_norm)
                est = conn_estado.execute(
                    "SELECT estado FROM oc_alias WHERE id_cliente=? AND oc_raw=?", (cid, raw_oc)
                ).fetchone()
                est = est[0] if est else "auto"
                if est == "separado":
                    continue
                if est != "confirmado" and otros:
                    res["colisiones"] += 1
                    continue
                cur = conn.execute(
                    "UPDATE ventas SET ord_compra = ? WHERE id_cliente = ? AND ord_compra = ?",
                    (oc_norm, cid, raw_oc),
                )
                res["filas_actualizadas"] += cur.rowcount
        return res
    finally:
        if cerrar_estado:
            conn_estado.close()
        if cerrar:
            conn.close()


def backfill_orden_compra(
    conn=None,
    raw_dir=None,
    desde: str = "2016-01",
    progress_cb=None,
    conn_estado: sqlite3.Connection | None = None,
) -> dict:
    """Rellena ord_compra vacía desde los exports de raw/ >= desde (F2).

    Returns: dict con archivos, docs_vistos, filas_actualizadas, sin_match,
    multi_oc, colisiones y pendientes (lista de ocs_pendientes al final).
    El alias vive en el sidecar (conn_estado). Lo que trae se normaliza
    con la misma regla de colisiones del remap.
    """
    cerrar = conn is None
    if raw_dir is None:
        raw_dir = ventas_db.raw_dir()
    raw_dir = Path(raw_dir)
    if conn is None:
        conn = ventas_db.connect(readonly=False)
    conn_estado, cerrar_estado = _abrir_estado(conn_estado)
    conn.execute("PRAGMA busy_timeout=60000")
    ventas_db.init_db(conn)

    archivos = sorted(
        p
        for p in raw_dir.iterdir()
        if p.is_file()
        and p.suffix.lower() in (".xls", ".csv", ".html", ".htm")
        and (m := _RX_ARCHIVO.match(p.name))
        and m.group(1) >= desde
    )
    res = {
        "archivos": 0,
        "docs_vistos": 0,
        "filas_actualizadas": 0,
        "sin_match": 0,
        "multi_oc": 0,
        "colisiones": 0,
        "omitidos": 0,
        "omitidos_ya_procesados": 0,
    }
    try:
        for i, path in enumerate(archivos, 1):
            if progress_cb:
                progress_cb(i, len(archivos), f"OC {path.name}")
            ya = conn.execute(
                "SELECT 1 FROM oc_archivos WHERE archivo = ?", (path.name,)
            ).fetchone()
            if ya:
                res["omitidos_ya_procesados"] += 1
                continue
            try:
                rows = load_report_source(path.read_bytes(), _kind_por_extension(path))
            except Exception as e:  # noqa: BLE001 - un archivo corrupto no aborta 600
                log.warning("OC %s ilegible: %s", path.name, e)
                res["omitidos"] += 1
                continue
            docs = _docs_con_oc(rows)
            if not docs:
                continue
            res["archivos"] += 1
            filas_archivo = 0
            with ventas_db.write_txn(conn):
                for (tpo, serie, nro), d in docs.items():
                    res["docs_vistos"] += 1
                    if len(d["ocs"]) > 1:
                        res["multi_oc"] += 1
                    oc_raw = d["ocs"].most_common(1)[0][0]
                    oc_norm = normalize_orden_compra(oc_raw)
                    if not oc_norm:
                        continue
                    otros = ventas_db.oc_alias_upsert(conn_estado, d["cliente"], oc_raw, oc_norm)
                    # Un confirmado/separado nunca se degrada: manda la revisión.
                    est = conn_estado.execute(
                        "SELECT estado FROM oc_alias WHERE id_cliente=? AND oc_raw=?",
                        (d["cliente"], oc_raw),
                    ).fetchone()
                    est = est[0] if est else "auto"
                    if est == "confirmado":
                        valor = oc_norm
                    elif est == "separado":
                        valor = oc_raw
                    else:
                        valor = oc_raw if otros else oc_norm
                        if otros:
                            res["colisiones"] += 1
                    # Match exacto (usa idx_venta_doc); el formato del ERP es
                    # estable y ya se verificó idéntico en ambas puntas.
                    # F2: se rellena ord_compra (vacío), ya normalizado.
                    cur = conn.execute(
                        "UPDATE ventas SET ord_compra = ? WHERE tpo_doc = ? "
                        "AND serie_doc = ? AND nro_doc = ? "
                        "AND (ord_compra IS NULL OR TRIM(ord_compra) = '')",
                        (valor, tpo, serie, nro),
                    )
                    if cur.rowcount:
                        res["filas_actualizadas"] += cur.rowcount
                        filas_archivo += cur.rowcount
                    else:
                        res["sin_match"] += 1
                conn.execute(
                    "INSERT INTO oc_archivos (archivo, docs, filas) VALUES (?,?,?) "
                    "ON CONFLICT(archivo) DO UPDATE SET docs = excluded.docs, "
                    "filas = excluded.filas, procesado_en = datetime('now')",
                    (path.name, len(docs), filas_archivo),
                )
            conn.commit()
        res["pendientes"] = ventas_db.ocs_pendientes(conn, conn_estado)
        return res
    finally:
        if cerrar_estado:
            conn_estado.close()
        if cerrar:
            conn.close()
