"""SQLite local del sustentor — port de src/db/{schema,writer}.rs de g360-ventas-db.

Crea y mantiene %APPDATA%/g360-erp-nc-sustentor/data/historial.db con el mismo
schema que el repo Tauri: tabla ventas, indices, vistas y tablas de auditoria.
Modo WAL + busy_timeout para convivir con lectores concurrentes.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path

log = logging.getLogger(__name__)


# ── Config / paths / estado de usuario (extraido a ventas_db_config.py) ─
from src.core.ventas_db_config import (  # noqa: F401  (re-exportado p/ compatibilidad)
    APP_DIR_NAME,
    data_dir,
    db_path,
    estado_path,
    raw_dir,
    config_file_path,
    DEFAULT_ALLOWED_LINES,
    allowed_lines,
    is_allowed_line,
    active_line_sql,
    reset_allowed_lines_cache,
    load_app_config,
    save_app_config,
    load_pinned,
    save_pinned,
    toggle_pinned,
    load_recent,
    push_recent,
)


# ── Schema (port fiel de schema.rs) ─────────────────────────────────────────

CREATE_TABLE_VENTAS = """
CREATE TABLE IF NOT EXISTS ventas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    id_articulo TEXT NOT NULL, original_sku TEXT, nom_articulo TEXT,
    id_linea TEXT NOT NULL, nom_linea TEXT,
    id_grupo TEXT, nom_grupo TEXT,
    id_tipo TEXT, nom_tipo TEXT,
    id_familia TEXT, nom_familia TEXT,
    id_cliente TEXT NOT NULL, doc_cliente TEXT, nom_cliente TEXT,
    tpo_doc TEXT NOT NULL, serie_doc TEXT, nro_doc TEXT,
    referencia TEXT, moneda TEXT DEFAULT 'Soles',
    cantidad REAL NOT NULL, soles REAL NOT NULL, dolares REAL, precio_unitario REAL,
    cantidad_fae REAL,
    anho INTEGER NOT NULL, mes INTEGER NOT NULL,
    fecha_orig TEXT NOT NULL,
    fecha_ref TEXT, fecha_venc TEXT,
    cod_sucursal TEXT, nom_sucursal TEXT,
    departamento TEXT, provincia TEXT, distrito TEXT,
    id_vendedor TEXT, nom_vendedor TEXT,
    id_pedido TEXT,
    ord_compra TEXT,
    file_source TEXT, mes_ref TEXT NOT NULL,
    capturado_en TEXT DEFAULT (datetime('now')),
    tipo_operacion TEXT DEFAULT 'venta',
    factura_ref_serie TEXT,
    factura_ref_nro TEXT,
    folio_unico TEXT,
    -- Columnas extra de la fat DB (g360-ventas-db) conservadas al copiar la
    -- fuente: desde fase 2 también las llena la captura intranet.
    -- Orden EXACTO del canónico (F4): el DDL define el contrato de forma.
    id_ubigeo TEXT,
    canal_distribucion TEXT,
    nom_condicion_pago TEXT,
    estado_linea TEXT,
    division TEXT,
    fec_cargo TEXT,
    id_guia TEXT
)
"""

CREATE_AUDIT_TABLES = """
CREATE TABLE IF NOT EXISTS sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo TEXT NOT NULL DEFAULT 'capture',
    estado TEXT NOT NULL DEFAULT 'pending',
    filas_solicitadas INTEGER DEFAULT 0,
    filas_subidas INTEGER DEFAULT 0,
    filas_limpiadas INTEGER DEFAULT 0,
    duracion_segundos REAL,
    error_message TEXT,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS mes_checksums (
    mes_ref TEXT NOT NULL,
    checksum TEXT NOT NULL,
    total_filas INTEGER NOT NULL,
    total_soles REAL NOT NULL,
    total_cantidad REAL,
    calculado_en TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (mes_ref)
);
CREATE TABLE IF NOT EXISTS day_checksums (
    dia TEXT NOT NULL,
    total_filas INTEGER NOT NULL,
    total_soles REAL NOT NULL,
    checksum TEXT NOT NULL,
    calculado_en TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (dia)
);
CREATE TABLE IF NOT EXISTS oc_archivos (
    archivo TEXT NOT NULL PRIMARY KEY,
    docs INTEGER NOT NULL DEFAULT 0,
    filas INTEGER NOT NULL DEFAULT 0,
    procesado_en TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tabla TEXT NOT NULL,
    operacion TEXT NOT NULL,
    folio_unico TEXT,
    id_articulo TEXT,
    filas_afectadas INTEGER DEFAULT 1,
    detalle TEXT,
    creado_en TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS stats_cache (
    key TEXT PRIMARY KEY,
    value REAL NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_stats_key ON stats_cache(key);

-- Indices
CREATE INDEX IF NOT EXISTS idx_sync_log_fecha ON sync_log(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_folio ON audit_log(folio_unico);
CREATE INDEX IF NOT EXISTS idx_audit_log_fecha ON audit_log(creado_en DESC);
"""

# ── Sidecar de estado (estado_sustentor.db) ────────────────────────────────
# oc_alias + day_state viven FUERA de historial.db: son estado local que viaja
# en el cartucho, no parte del contrato de forma. DDL idéntico al que tenían
# en CREATE_AUDIT_TABLES (no cambia ni una coma: la migración es copia 1:1).
CREATE_ESTADO_TABLES = """
CREATE TABLE IF NOT EXISTS day_state (
    dia TEXT NOT NULL PRIMARY KEY,
    ultima_captura TEXT NOT NULL DEFAULT (datetime('now')),
    filas INTEGER NOT NULL DEFAULT 0,
    soles REAL NOT NULL DEFAULT 0,
    estado TEXT NOT NULL DEFAULT 'provisional',
    cerrado_en TEXT
);
CREATE INDEX IF NOT EXISTS idx_day_state_estado ON day_state(estado);
CREATE TABLE IF NOT EXISTS oc_alias (
    id_cliente TEXT NOT NULL,
    oc_raw TEXT NOT NULL,
    oc_norm TEXT NOT NULL,
    estado TEXT NOT NULL DEFAULT 'auto',
    revisado_en TEXT,
    PRIMARY KEY (id_cliente, oc_raw)
);
CREATE INDEX IF NOT EXISTS idx_oc_alias_norm ON oc_alias(id_cliente, oc_norm);
"""

# ── Contrato de forma del cartucho (F4) ──────────────────────────────────
# Tablas + columnas + orden EXACTOS de la canónica (g360-db-ventas).
# Generado desde su sqlite_master; no se edita a mano (verificar_contrato
# detecta cualquier desvío). La forma la define la canónica: este proyecto
# se adapta y nunca la muta.
CONTRACTO_VERSION = 3
CONTRACTO_TABLAS = {
    "ventas": [
        "id",
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
        "mes_ref",
        "capturado_en",
        "tipo_operacion",
        "factura_ref_serie",
        "factura_ref_nro",
        "folio_unico",
        "id_ubigeo",
        "canal_distribucion",
        "nom_condicion_pago",
        "estado_linea",
        "division",
        "fec_cargo",
        "id_guia",
    ],
    "stats_cache": ["key", "value", "updated_at"],
    "stats_por_mes": ["mes_ref", "filas", "ventas_soles", "clientes", "actualizado_en", "cantidad"],
    "sync_log": [
        "id",
        "tipo",
        "estado",
        "filas_solicitadas",
        "filas_subidas",
        "filas_limpiadas",
        "duracion_segundos",
        "error_message",
        "started_at",
        "finished_at",
    ],
    "mes_checksums": [
        "mes_ref",
        "checksum",
        "total_filas",
        "total_soles",
        "total_cantidad",
        "calculado_en",
    ],
    "sync_months": [
        "mes_ref",
        "local_checksum",
        "remote_checksum",
        "estado",
        "filas",
        "intentos",
        "ultimo_error",
        "actualizado_en",
    ],
    "audit_log": [
        "id",
        "tabla",
        "operacion",
        "folio_unico",
        "id_articulo",
        "filas_afectadas",
        "detalle",
        "creado_en",
    ],
    "dim_ruc": [
        "ruc",
        "nom_cliente",
        "departamento",
        "provincia",
        "distrito",
        "n_clientes_unicos",
        "actualizado_en",
    ],
    "dim_cliente": [
        "id_cliente",
        "nom_cliente",
        "doc_cliente",
        "departamento",
        "provincia",
        "distrito",
        "n_ventas",
        "total_soles",
        "total_dolares",
        "ultima_compra",
        "actualizado_en",
    ],
    "dim_vendedor": [
        "id_vendedor",
        "nom_vendedor",
        "n_ventas",
        "total_soles",
        "n_clientes",
        "ultima_venta",
        "actualizado_en",
    ],
    "dim_articulo": [
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
        "n_ventas",
        "total_cantidad",
        "total_soles",
        "precio_promedio",
        "ultima_venta",
        "actualizado_en",
    ],
    "dim_documento": [
        "folio_unico",
        "tpo_doc",
        "serie_doc",
        "nro_doc",
        "fecha_orig",
        "id_cliente",
        "id_vendedor",
        "n_lineas",
        "total_cantidad",
        "total_soles",
        "total_dolares",
        "moneda",
        "actualizado_en",
    ],
    "dim_linea": [
        "id_linea",
        "nom_linea",
        "id_grupo",
        "nom_grupo",
        "id_familia",
        "nom_familia",
        "n_ventas",
        "total_soles",
        "n_clientes",
        "actualizado_en",
    ],
    "fact_venta_mes": [
        "id_linea",
        "id_articulo",
        "mes_ref",
        "cantidad",
        "soles",
        "devuelto",
        "soles_netos",
    ],
}
# Tablas locales toleradas (no son parte del contrato, pero se permiten):
# cachés derivables (agg_cliente_mes, nc_asociadas), metadata del archivo
# (day_checksums) y progreso local (oc_archivos). Cualquier OTRA tabla extra
# se reporta como hallazgo.
TABLAS_LOCALES_TOLERADAS = frozenset(
    {"agg_cliente_mes", "nc_asociadas", "oc_archivos", "day_checksums"}
)


class ContratoDBError(Exception):
    """El archivo no cumple el contrato de forma del cartucho."""


def verificar_contrato(conn: sqlite3.Connection | None = None) -> dict:
    """Compara el archivo contra CONTRACTO_TABLAS + CONTRACTO_VERSION.

    Returns {ok, version_ok, version, tablas_faltantes, columnas_difieren,
    tablas_extra_no_toleradas, tablas_locales}. Solo lectura. Las tablas
    locales toleradas se reportan pero no rompen el ok.
    """
    cerrar = conn is None
    if conn is None:
        if not db_exists():
            return {
                "ok": False,
                "version_ok": False,
                "version": None,
                "tablas_faltantes": sorted(CONTRACTO_TABLAS),
                "columnas_difieren": {},
                "tablas_extra_no_toleradas": [],
                "tablas_locales": [],
                "detalle": "no existe historial.db",
            }
        conn = connect(readonly=True)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        tablas = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
        faltantes = sorted(t for t in CONTRACTO_TABLAS if t not in tablas)
        difieren: dict[str, dict] = {}
        for t in sorted(set(CONTRACTO_TABLAS) & tablas):
            actuales = [r[1] for r in conn.execute(f"PRAGMA table_info({t})")]
            esperadas = CONTRACTO_TABLAS[t]
            if actuales != esperadas:
                difieren[t] = {"esperadas": len(esperadas), "actuales": len(actuales)}
        extras = sorted(
            t for t in tablas if t not in CONTRACTO_TABLAS and t not in TABLAS_LOCALES_TOLERADAS
        )
        locales = sorted(t for t in tablas if t in TABLAS_LOCALES_TOLERADAS)
        version_ok = version == CONTRACTO_VERSION
        ok = version_ok and not faltantes and not difieren and not extras
        detalle = []
        if not version_ok:
            detalle.append(f"user_version={version} (contrato {CONTRACTO_VERSION})")
        detalle += [f"falta tabla {t}" for t in faltantes]
        detalle += [f"columnas difieren en {t}" for t in difieren]
        detalle += [f"tabla extra no tolerada: {t}" for t in extras]
        return {
            "ok": ok,
            "version_ok": version_ok,
            "version": version,
            "tablas_faltantes": faltantes,
            "columnas_difieren": difieren,
            "tablas_extra_no_toleradas": extras,
            "tablas_locales": locales,
            "detalle": "; ".join(detalle),
        }
    finally:
        if cerrar:
            conn.close()


# Tablas de hechos/agregados de la canónica (DDL verbatim). Un archivo fresco
# las crea vacías para nacer cumplido; el contenido viaja por copia (F5).
CREATE_FACT_TABLES = """
CREATE TABLE IF NOT EXISTS fact_venta_mes (
    id_linea TEXT NOT NULL,
    id_articulo TEXT NOT NULL,
    mes_ref TEXT NOT NULL,
    cantidad REAL DEFAULT 0,
    soles REAL DEFAULT 0,
    devuelto REAL DEFAULT 0,
    soles_netos REAL DEFAULT 0,
    PRIMARY KEY (id_linea, id_articulo, mes_ref)
);
CREATE TABLE IF NOT EXISTS stats_por_mes (
    mes_ref TEXT PRIMARY KEY,
    filas INTEGER NOT NULL DEFAULT 0,
    ventas_soles REAL NOT NULL DEFAULT 0,
    clientes INTEGER NOT NULL DEFAULT 0,
    actualizado_en TEXT NOT NULL DEFAULT (datetime('now'))
, cantidad REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS sync_months (
    mes_ref TEXT PRIMARY KEY,
    local_checksum TEXT NOT NULL,
    remote_checksum TEXT,
    estado TEXT NOT NULL DEFAULT 'pending',
    filas INTEGER DEFAULT 0,
    intentos INTEGER DEFAULT 0,
    ultimo_error TEXT,
    actualizado_en TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# Tablas dimensionales del ecosistema (las crea y mantiene la DB fuente).
# Seguro de compatibilidad: si un archivo llega sin ellas, el app las crea
# vacías en vez de romper vistas y validadores. DDL espejo de g360-db-ventas.
CREATE_DIM_TABLES = """
CREATE TABLE IF NOT EXISTS dim_articulo (
    id_articulo TEXT PRIMARY KEY,
    original_sku TEXT,
    nom_articulo TEXT NOT NULL,
    id_linea TEXT,
    nom_linea TEXT,
    id_grupo TEXT,
    nom_grupo TEXT,
    id_tipo TEXT,
    nom_tipo TEXT,
    id_familia TEXT,
    nom_familia TEXT,
    n_ventas INTEGER DEFAULT 0,
    total_cantidad REAL DEFAULT 0,
    total_soles REAL DEFAULT 0,
    precio_promedio REAL DEFAULT 0,
    ultima_venta TEXT,
    actualizado_en TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS dim_cliente (
    id_cliente TEXT PRIMARY KEY,
    nom_cliente TEXT NOT NULL,
    doc_cliente TEXT,
    departamento TEXT,
    provincia TEXT,
    distrito TEXT,
    n_ventas INTEGER DEFAULT 0,
    total_soles REAL DEFAULT 0,
    total_dolares REAL DEFAULT 0,
    ultima_compra TEXT,
    actualizado_en TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS dim_documento (
    folio_unico TEXT PRIMARY KEY,
    tpo_doc TEXT NOT NULL,
    serie_doc TEXT NOT NULL,
    nro_doc TEXT NOT NULL,
    fecha_orig TEXT NOT NULL,
    id_cliente TEXT NOT NULL,
    id_vendedor TEXT,
    n_lineas INTEGER DEFAULT 0,
    total_cantidad REAL DEFAULT 0,
    total_soles REAL DEFAULT 0,
    total_dolares REAL DEFAULT 0,
    moneda TEXT DEFAULT 'Soles',
    actualizado_en TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS dim_linea (
    id_linea TEXT PRIMARY KEY,
    nom_linea TEXT,
    id_grupo TEXT,
    nom_grupo TEXT,
    id_familia TEXT,
    nom_familia TEXT,
    n_ventas INTEGER DEFAULT 0,
    total_soles REAL DEFAULT 0,
    n_clientes INTEGER DEFAULT 0,
    actualizado_en TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS dim_ruc (
    ruc TEXT PRIMARY KEY,
    nom_cliente TEXT NOT NULL,
    departamento TEXT,
    provincia TEXT,
    distrito TEXT,
    n_clientes_unicos INTEGER DEFAULT 0,
    actualizado_en TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS dim_vendedor (
    id_vendedor TEXT PRIMARY KEY,
    nom_vendedor TEXT NOT NULL,
    n_ventas INTEGER DEFAULT 0,
    total_soles REAL DEFAULT 0,
    n_clientes INTEGER DEFAULT 0,
    ultima_venta TEXT,
    actualizado_en TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_venta_mes ON ventas(mes_ref);",
    "CREATE INDEX IF NOT EXISTS idx_venta_cliente ON ventas(id_cliente);",
    "CREATE INDEX IF NOT EXISTS idx_venta_doc_cliente ON ventas(doc_cliente);",
    "CREATE INDEX IF NOT EXISTS idx_venta_sku ON ventas(id_articulo);",
    "CREATE INDEX IF NOT EXISTS idx_venta_orig_sku ON ventas(original_sku);",
    "CREATE INDEX IF NOT EXISTS idx_venta_linea ON ventas(id_linea);",
    "CREATE INDEX IF NOT EXISTS idx_venta_doc ON ventas(tpo_doc, serie_doc, nro_doc);",
    "CREATE INDEX IF NOT EXISTS idx_venta_doc_linea ON ventas(serie_doc, nro_doc, id_linea);",
    "CREATE INDEX IF NOT EXISTS idx_venta_ref ON ventas(referencia);",
    "CREATE INDEX IF NOT EXISTS idx_venta_tipo_op ON ventas(tipo_operacion);",
    "CREATE INDEX IF NOT EXISTS idx_venta_fact_ref ON ventas(factura_ref_serie, factura_ref_nro);",
    "CREATE INDEX IF NOT EXISTS idx_venta_fecha ON ventas(fecha_orig);",
    "CREATE INDEX IF NOT EXISTS idx_venta_soles ON ventas(soles);",
    "CREATE INDEX IF NOT EXISTS idx_venta_fecha_soles ON ventas(fecha_orig, soles);",
    "CREATE INDEX IF NOT EXISTS idx_retorno_cliente_sku ON ventas(id_cliente, id_articulo, fecha_orig);",
    "CREATE INDEX IF NOT EXISTS idx_retorno_folio ON ventas(folio_unico);",
    "CREATE INDEX IF NOT EXISTS idx_ventas_folio_articulo ON ventas(folio_unico, id_articulo);",
    "CREATE INDEX IF NOT EXISTS idx_venta_vendedor ON ventas(id_vendedor);",
    "CREATE INDEX IF NOT EXISTS idx_venta_tpodoc_folio ON ventas(tpo_doc, folio_unico);",
    "CREATE INDEX IF NOT EXISTS idx_venta_mes_fecha ON ventas(mes_ref, fecha_orig);",
    # Campos críticos del sustentor (O/C, sucursal, división, vencimiento,
    # condición de pago): sin índice, filtrar por ellos es un escaneo
    # completo de ~2.8M filas (division = 26s). Fuera del contrato de forma.
    "CREATE INDEX IF NOT EXISTS idx_venta_division ON ventas(division);",
    "CREATE INDEX IF NOT EXISTS idx_venta_condicion ON ventas(nom_condicion_pago);",
    "CREATE INDEX IF NOT EXISTS idx_venta_sucursal ON ventas(cod_sucursal);",
    "CREATE INDEX IF NOT EXISTS idx_venta_venc ON ventas(fecha_venc);",
    # O/C: el compuesto (id_cliente, ord_compra) no sirve para filtrar por
    # OC sola (columna izquierda ausente = escaneo). Índice propio.
    "CREATE INDEX IF NOT EXISTS idx_venta_oc ON ventas(ord_compra);",
    # Tienda real = (cliente, sucursal): el filtro y el % por tienda van
    # siempre por el par (el código solo se repite entre clientes).
    "CREATE INDEX IF NOT EXISTS idx_venta_cliente_sucursal ON ventas(id_cliente, cod_sucursal);",
]

CREATE_VIEWS = [
    # vw_dim_cliente
    """
    CREATE VIEW IF NOT EXISTS vw_dim_cliente AS
    SELECT id_cliente, doc_cliente, nom_cliente, departamento, provincia, distrito
    FROM (
      SELECT id_cliente, doc_cliente, nom_cliente, departamento, provincia, distrito,
             ROW_NUMBER() OVER (PARTITION BY id_cliente ORDER BY fecha_orig DESC, id DESC) rn
      FROM ventas WHERE id_cliente != ''
    ) WHERE rn = 1
    """,
    # vw_dim_articulo
    """
    CREATE VIEW IF NOT EXISTS vw_dim_articulo AS
    SELECT id_articulo, original_sku, nom_articulo, id_linea, nom_linea,
           id_grupo, nom_grupo, id_tipo, nom_tipo, id_familia, nom_familia
    FROM (
      SELECT id_articulo, original_sku, nom_articulo, id_linea, nom_linea,
             id_grupo, nom_grupo, id_tipo, nom_tipo, id_familia, nom_familia,
             ROW_NUMBER() OVER (PARTITION BY id_articulo ORDER BY fecha_orig DESC, id DESC) rn
      FROM ventas WHERE id_articulo != ''
    ) WHERE rn = 1
    """,
    # vw_dim_linea
    """
    CREATE VIEW IF NOT EXISTS vw_dim_linea AS
    SELECT id_linea, MAX(nom_linea) AS nom_linea,
           MAX(id_grupo) AS id_grupo, MAX(nom_grupo) AS nom_grupo,
           MAX(id_familia) AS id_familia, MAX(nom_familia) AS nom_familia
    FROM ventas WHERE id_linea != '' GROUP BY id_linea
    """,
    # vw_documento
    """
    CREATE VIEW IF NOT EXISTS vw_documento AS
    SELECT
        tpo_doc, serie_doc, nro_doc, mes_ref, fecha_orig,
        id_cliente, doc_cliente, nom_cliente,
        COUNT(*) AS n_lineas,
        SUM(cantidad) AS cantidad,
        ROUND(SUM(soles), 2) AS total_soles,
        ROUND(SUM(dolares), 2) AS total_dolares,
        COALESCE(factura_ref_serie,'') || '/' || COALESCE(factura_ref_nro,'') AS referencia_factura
    FROM ventas
    GROUP BY tpo_doc, serie_doc, nro_doc
    """,
    # vw_devoluciones
    """
    CREATE VIEW IF NOT EXISTS vw_devoluciones AS
    SELECT
        n.mes_ref,
        n.tpo_doc AS nc_tpo, n.serie_doc AS nc_serie, n.nro_doc AS nc_nro, n.fecha_orig AS nc_fecha,
        n.id_articulo, n.nom_articulo, n.id_linea, n.nom_linea,
        n.cantidad AS cant_devuelta, n.cantidad_fae AS fae_base, n.soles AS soles_devueltos,
        n.tipo_operacion,
        f.tpo_doc AS fac_tpo, f.serie_doc AS fac_serie, f.nro_doc AS fac_nro,
        f.fecha_orig AS fac_fecha, f.precio_unitario AS precio_original,
        ROUND(n.soles / NULLIF(n.cantidad_fae, 0), 4) AS descuento_unit,
        f.mes_ref AS fac_mes
    FROM ventas n
    LEFT JOIN ventas f
        ON f.serie_doc = n.factura_ref_serie AND f.nro_doc = n.factura_ref_nro
        AND (f.tpo_doc LIKE 'F01%' OR f.tpo_doc = 'F01')
    WHERE (n.tpo_doc LIKE '%NCR%' OR n.tpo_doc LIKE '%NDB%')
    """,
    # vw_venta_neta_producto
    """
    CREATE VIEW IF NOT EXISTS vw_venta_neta_producto AS
    SELECT
        v.id_articulo, v.nom_articulo, v.id_linea, v.nom_linea, v.mes_ref,
        SUM(CASE WHEN v.tipo_operacion = 'venta' THEN v.cantidad ELSE 0 END) AS vendido,
        SUM(CASE WHEN v.tipo_operacion = 'devolucion' THEN v.cantidad ELSE 0 END) AS devuelto,
        SUM(v.cantidad) AS cantidad_neta,
        ROUND(SUM(CASE WHEN v.tipo_operacion = 'venta' THEN v.soles ELSE 0 END), 2) AS soles_vendidos,
        ROUND(SUM(v.soles), 2) AS soles_netos,
        ROUND(SUM(v.soles) / NULLIF(SUM(v.cantidad), 0), 4) AS p_u_neto
    FROM ventas v
    GROUP BY v.id_articulo, v.mes_ref
    """,
    # vw_nc_totales
    # Facturas cuya NC atiende EXACTAMENTE la cantidad vendida (regla exact-match
    # por folio+factura+SKU, eps 1e-6). Solo folios directos; parciales, excesos
    # y consolidados van a vw_nc_parciales (informativos).
    """
    CREATE VIEW IF NOT EXISTS vw_nc_totales AS
    WITH fac AS (
      SELECT serie_doc, nro_doc, id_articulo, SUM(cantidad) AS cant
      FROM ventas WHERE tpo_doc LIKE 'F01%' GROUP BY 1, 2, 3
    ),
    fol AS (
      SELECT n.factura_ref_serie AS serie_doc, n.factura_ref_nro AS nro_doc,
             n.id_articulo AS id_articulo,
             SUM(ABS(n.soles)) AS soles_folio,
             SUM(ABS(n.cantidad_fae)) AS fae_folio
      FROM ventas n
      WHERE n.tipo_operacion = 'ajuste_valor'
      GROUP BY 1, 2, 3, n.folio_unico
    )
    SELECT fol.serie_doc, fol.nro_doc,
           SUM(fol.fae_folio) as total_fae,
           SUM(fol.soles_folio) as total_monto,
           COUNT(*) as nc_count
    FROM fol JOIN fac USING (serie_doc, nro_doc, id_articulo)
    WHERE fol.fae_folio > 0 AND ABS(fol.fae_folio - fac.cant) < 0.000001
    GROUP BY fol.serie_doc, fol.nro_doc
    """,
    # vw_nc_parciales
    # Folios de ajuste con al menos una linea NO directa (parcial, exceso,
    # consolidado de feria, referencia sin factura): informativos, no afectan
    # precio_neto.
    """
    CREATE VIEW IF NOT EXISTS vw_nc_parciales AS
    WITH fac AS (
      SELECT serie_doc, nro_doc, id_articulo, SUM(cantidad) AS cant
      FROM ventas WHERE tpo_doc LIKE 'F01%' GROUP BY 1, 2, 3
    ),
    fol AS (
      SELECT n.folio_unico AS nc_folio,
             n.factura_ref_serie AS serie_doc, n.factura_ref_nro AS nro_doc,
             n.id_articulo AS id_articulo,
             SUM(ABS(n.soles)) AS soles_folio,
             SUM(ABS(n.cantidad_fae)) AS fae_folio
      FROM ventas n
      WHERE n.tipo_operacion = 'ajuste_valor'
      GROUP BY 1, 2, 3, 4
    )
    SELECT fol.serie_doc AS factura_ref_serie, fol.nro_doc AS factura_ref_nro,
           fol.nc_folio as nc_folio,
           fol.fae_folio as cantidad_fae, fol.soles_folio as monto
    FROM fol LEFT JOIN fac USING (serie_doc, nro_doc, id_articulo)
    WHERE NOT (fac.cant IS NOT NULL AND fol.fae_folio > 0
               AND ABS(fol.fae_folio - fac.cant) < 0.000001)
      AND NOT EXISTS (
        SELECT 1 FROM vw_nc_totales t
        WHERE t.serie_doc = fol.serie_doc AND t.nro_doc = fol.nro_doc
      )
    """,
    # vw_historial_venta_cliente
    """
    CREATE VIEW IF NOT EXISTS vw_historial_venta_cliente AS
    WITH cadena AS (
      SELECT id_cliente, nom_cliente, id_vendedor, nom_vendedor,
             id_articulo, nom_articulo, id_linea, nom_linea,
             folio_unico, tpo_doc, serie_doc, nro_doc,
             fecha_orig, mes_ref, tipo_operacion, cantidad, soles, precio_unitario,
             LAG(precio_unitario)    OVER w AS precio_anterior,
             LAG(fecha_orig)         OVER w AS fecha_anterior,
             LAG(precio_unitario, 2) OVER w AS precio_anterior2
      FROM ventas
      WHERE tipo_operacion = 'venta'
      WINDOW w AS (PARTITION BY id_cliente, id_articulo ORDER BY fecha_orig, id)
    )
    SELECT * FROM cadena
    UNION ALL
    SELECT id_cliente, nom_cliente, id_vendedor, nom_vendedor,
           id_articulo, nom_articulo, id_linea, nom_linea,
           folio_unico, tpo_doc, serie_doc, nro_doc,
           fecha_orig, mes_ref, tipo_operacion, cantidad, soles,
           NULL, NULL, NULL, NULL
    FROM ventas
    WHERE tipo_operacion IN ('ajuste_valor', 'devolucion')
    """,
    # vw_radar_recompra
    """
    CREATE VIEW IF NOT EXISTS vw_radar_recompra AS
    WITH compras AS (
      SELECT id_cliente, nom_cliente, id_articulo, nom_articulo,
             id_linea, nom_linea, fecha_orig, cantidad, precio_unitario,
             LAG(fecha_orig) OVER (PARTITION BY id_cliente, id_articulo
                                   ORDER BY fecha_orig) AS fecha_previa
      FROM ventas
      WHERE tipo_operacion = 'venta' AND cantidad > 0
    ),
    gaps AS (
      SELECT id_cliente, nom_cliente, id_articulo, nom_articulo,
             id_linea, nom_linea, fecha_orig, cantidad, precio_unitario,
             CAST(julianday(fecha_orig) - julianday(fecha_previa) AS INTEGER) AS dias_gap
      FROM compras
      WHERE fecha_previa IS NOT NULL
    ),
    cadencia_sku AS (
      SELECT id_cliente, nom_cliente, id_articulo, nom_articulo, id_linea, nom_linea,
             COUNT(*)                                AS n_compras,
             MAX(fecha_orig)                         AS ultima_compra,
             CAST(ROUND(AVG(dias_gap)) AS INTEGER)   AS dias_cadencia,
             CAST(ROUND(AVG(precio_unitario), 4) AS REAL) AS precio_promedio,
             CAST(SUM(cantidad) AS REAL) / MAX(CAST(julianday(MAX(fecha_orig)) - julianday(MIN(fecha_orig)) AS INTEGER), 1)
                                                      AS und_por_dia
      FROM gaps
      GROUP BY 1,2,3,4,5,6
    ),
    cadencia_linea AS (
      SELECT id_cliente, id_linea, CAST(ROUND(AVG(dias_gap)) AS INTEGER) AS cadencia_linea
      FROM gaps
      GROUP BY 1,2
    )
    SELECT cs.*,
           CAST(julianday('now') - julianday(cs.ultima_compra) AS INTEGER) AS dias_silencio,
           COALESCE(cs.dias_cadencia, cl.cadencia_linea)                   AS cadencia_efectiva,
           CASE
             WHEN CAST(julianday('now') - julianday(cs.ultima_compra) AS INTEGER)
                  > COALESCE(cs.dias_cadencia, cl.cadencia_linea) * 1.5
             THEN 'VENCIDO'
             ELSE 'OK'
           END AS estado_oportunidad
    FROM cadencia_sku cs
    LEFT JOIN cadencia_linea cl
      ON cl.id_cliente = cs.id_cliente AND cl.id_linea = cs.id_linea
     AND cs.n_compras < 3
    """,
    # vw_facturas_disponibles
    """
    CREATE VIEW IF NOT EXISTS vw_facturas_disponibles AS
    WITH ventas_agg AS (
      SELECT
        v.id, v.folio_unico, v.serie_doc, v.nro_doc,
        v.id_cliente, v.id_articulo, v.nom_articulo,
        v.fecha_orig, v.cantidad as cantidad_vendida,
        v.precio_unitario, v.moneda, v.mes_ref,
        COALESCE(SUM(CASE WHEN d.tipo_operacion='devolucion' THEN abs(d.cantidad) ELSE 0 END), 0) as devuelto
      FROM ventas v
      LEFT JOIN ventas d ON d.factura_ref_serie = v.serie_doc AND d.factura_ref_nro = v.nro_doc
        AND d.tipo_operacion = 'devolucion'
      WHERE v.tpo_doc LIKE 'F01%'
      GROUP BY v.id, v.folio_unico, v.serie_doc, v.nro_doc, v.id_cliente, v.id_articulo,
               v.nom_articulo, v.fecha_orig, v.cantidad, v.precio_unitario, v.moneda, v.mes_ref
    )
    SELECT va.*,
      va.cantidad_vendida - va.devuelto as saldo_disponible,
      CASE
        WHEN nt.total_fae IS NOT NULL THEN
          ROUND(va.precio_unitario - (nt.total_monto / nt.total_fae), 4)
        ELSE va.precio_unitario
      END as precio_para_devolucion,
      CASE
        WHEN date(va.fecha_orig) < date('now', '-3 years') THEN 'FUERA_PERIOD'
        ELSE 'DENTRO_PERIOD'
      END as estado_periodo
    FROM ventas_agg va
    LEFT JOIN vw_nc_totales nt ON nt.serie_doc = va.serie_doc AND nt.nro_doc = va.nro_doc
    ORDER BY va.fecha_orig DESC
    """,
    # vw_nc_asociadas reemplazada por tabla materializada nc_asociadas (mas rapida).
    # Ver populate_nc_asociadas() para regenerar despues de cada captura.
    """
    CREATE TABLE IF NOT EXISTS nc_asociadas (
      factura_doc_id TEXT NOT NULL,
      nc_doc_id TEXT NOT NULL,
      nc_tpo TEXT,
      nc_serie TEXT,
      nc_nro TEXT,
      fecha_orig TEXT,
      cantidad REAL,
      soles REAL,
      PRIMARY KEY (factura_doc_id, nc_doc_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_nc_assoc_fact ON nc_asociadas(factura_doc_id);",
    # agg_cliente_mes: resumen (cliente x vendedor x mes) de facturas/boletas
    # activas. Reduce el dropdown de clientes de escanear 1.5M filas a ~30K
    # -> rango largo cae de 3.3s a <20ms. Regenerar con
    # refresh_agg_cliente_mes() tras cada captura. Granularidad mensual.
    """
    CREATE TABLE IF NOT EXISTS agg_cliente_mes (
      id_cliente TEXT NOT NULL,
      id_vendedor TEXT,
      nom_cliente TEXT,
      doc_cliente TEXT,
      mes TEXT NOT NULL,
      n_docs INTEGER NOT NULL,
      PRIMARY KEY (id_cliente, id_vendedor, mes)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_agg_mes ON agg_cliente_mes(mes, n_docs);",
    "CREATE INDEX IF NOT EXISTS idx_agg_vend ON agg_cliente_mes(id_vendedor, mes);",
    # vw_impacto_documento — por cada linea (SKU) de factura F01: cuanto devolvio,
    # cuanto ajusto (descuento), cuanto cargo en valor (NDB), saldo y precio neto.
    # Fuente base para calcular FUTURAS notas de credito (sustentor).
    #
    # Regla de negocio (exact-match, sin tolerancia 99%):
    #   NCR solo AFECTA PRECIO si SUM(cantidad_NC por folio+factura+SKU) == cantidad
    #   facturada (eps 1e-6). Lo demas (parciales, excesos, consolidados de feria,
    #   referencias erroneas) va al bucket informativo y NO toca precio_neto.
    #   NDB con documento+SKU directo AUMENTA el precio (su cantidad fisica es 0);
    #   sin SKU atribuible queda solo informativa (no joinea).
    """
    CREATE VIEW IF NOT EXISTS vw_impacto_documento AS
    SELECT
      v.id, v.folio_unico, v.tpo_doc, v.serie_doc, v.nro_doc,
      printf('%s%s-%s', substr(v.tpo_doc,1,1), v.serie_doc, v.nro_doc) AS doc_id,
      v.fecha_orig, v.mes_ref,
      v.id_cliente, v.nom_cliente, v.doc_cliente,
      v.id_articulo, v.nom_articulo, v.id_linea, v.nom_linea,
      v.moneda, v.cantidad AS cant_vendida,
      v.soles AS soles_vendidos, v.dolares AS dolares_vendidos,
      v.precio_unitario AS precio_original,
      COALESCE(d.cant_devuelta, 0)  AS cant_devuelta,
      COALESCE(d.soles_devueltos, 0) AS soles_devueltos,
      COALESCE(d.n_devoluciones, 0)  AS n_devoluciones,
      COALESCE(a.soles_descuento, 0) AS soles_descuento,
      COALESCE(a.fae_base, 0)        AS fae_descuento,
      COALESCE(a.n_ajustes, 0)       AS n_ajustes,
      COALESCE(a.soles_descuento_inf, 0) AS soles_descuento_inf,
      COALESCE(a.n_ajustes_inf, 0)   AS n_ajustes_inf,
      COALESCE(nd.soles_nd, 0)       AS soles_nota_debito,
      COALESCE(nd.n_nd, 0)           AS n_notas_debito,
      v.cantidad - COALESCE(d.cant_devuelta, 0) AS saldo_disponible,
      COALESCE(ROUND(v.precio_unitario
        - CASE WHEN COALESCE(a.cant_fac, 0) > 0
               THEN COALESCE(a.soles_descuento, 0) / a.cant_fac ELSE 0 END
        + CASE WHEN v.cantidad <> 0
               THEN COALESCE(nd.soles_nd, 0) / v.cantidad ELSE 0 END,
      4), v.precio_unitario) AS precio_neto
    FROM ventas v
    LEFT JOIN (
      SELECT factura_ref_serie, factura_ref_nro, id_articulo,
             SUM(ABS(cantidad)) AS cant_devuelta,
             SUM(ABS(soles))    AS soles_devueltos,
             COUNT(*)           AS n_devoluciones
      FROM ventas WHERE tipo_operacion = 'devolucion'
      GROUP BY 1, 2, 3
    ) d ON d.factura_ref_serie = v.serie_doc AND d.factura_ref_nro = v.nro_doc
       AND d.id_articulo = v.id_articulo
    LEFT JOIN (
      WITH fac AS (
        SELECT serie_doc, nro_doc, id_articulo, SUM(cantidad) AS cant
        FROM ventas WHERE tpo_doc LIKE 'F01%' GROUP BY 1, 2, 3
      ),
      fol AS (
        SELECT n.factura_ref_serie AS serie_doc, n.factura_ref_nro AS nro_doc,
               n.id_articulo AS id_articulo,
               SUM(ABS(n.soles)) AS soles_folio,
               SUM(ABS(n.cantidad_fae)) AS fae_folio
        FROM ventas n
        WHERE n.tipo_operacion = 'ajuste_valor'
        GROUP BY 1, 2, 3, n.folio_unico
      )
      SELECT fol.serie_doc, fol.nro_doc, fol.id_articulo,
        MAX(fac.cant) AS cant_fac,
        SUM(CASE WHEN fac.cant IS NOT NULL AND fol.fae_folio > 0
                  AND ABS(fol.fae_folio - fac.cant) < 0.000001
                 THEN fol.soles_folio ELSE 0 END) AS soles_descuento,
        SUM(CASE WHEN fac.cant IS NOT NULL AND fol.fae_folio > 0
                  AND ABS(fol.fae_folio - fac.cant) < 0.000001
                 THEN fol.fae_folio ELSE 0 END) AS fae_base,
        SUM(CASE WHEN fac.cant IS NOT NULL AND fol.fae_folio > 0
                  AND ABS(fol.fae_folio - fac.cant) < 0.000001
                 THEN 1 ELSE 0 END) AS n_ajustes,
        SUM(CASE WHEN fac.cant IS NULL OR fol.fae_folio <= 0
                  OR ABS(fol.fae_folio - fac.cant) >= 0.000001
                 THEN fol.soles_folio ELSE 0 END) AS soles_descuento_inf,
        SUM(CASE WHEN fac.cant IS NULL OR fol.fae_folio <= 0
                  OR ABS(fol.fae_folio - fac.cant) >= 0.000001
                 THEN 1 ELSE 0 END) AS n_ajustes_inf
      FROM fol LEFT JOIN fac USING (serie_doc, nro_doc, id_articulo)
      GROUP BY 1, 2, 3
    ) a ON a.serie_doc = v.serie_doc AND a.nro_doc = v.nro_doc
       AND a.id_articulo = v.id_articulo
    LEFT JOIN (
      SELECT factura_ref_serie, factura_ref_nro, id_articulo,
             SUM(ABS(soles)) AS soles_nd,
             COUNT(*)        AS n_nd
      FROM ventas WHERE tipo_operacion = 'nota_debito'
      GROUP BY 1, 2, 3
    ) nd ON nd.factura_ref_serie = v.serie_doc AND nd.factura_ref_nro = v.nro_doc
       AND nd.id_articulo = v.id_articulo
    WHERE v.tpo_doc LIKE 'F01%'
    """,
]


# ── Conexion / init ─────────────────────────────────────────────────────────

_local = threading.local()


def connect(readonly: bool = False) -> sqlite3.Connection:
    """Conexion SQLite por-hilo. readonly usa mode=ro (nunca bloquea el writer)."""
    p = db_path()
    if readonly:
        if not p.exists():
            raise FileNotFoundError(f"DB no existe: {p}")
        conn = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=30)
    else:
        p.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(p), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    if not readonly:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def _viva(conn: sqlite3.Connection | None) -> bool:
    """True si la conexión cacheada sigue usable (no la cerraron por fuera)."""
    if conn is None:
        return False
    try:
        conn.execute("SELECT 1").fetchone()
        return True
    except Exception:
        return False


def get_conn() -> sqlite3.Connection:
    """Conexion read-write reutilizable por hilo (para escritura).

    Si la cacheada murió (alguien la cerró sin expulsarla), se reabre.
    Ver bug "Cannot operate on a closed database" en capture_service.
    """
    entry = getattr(_local, "conn_rw", None)
    conn, cached_path = entry if entry else (None, None)
    current = str(db_path())
    if conn is None or cached_path != current or not _viva(conn):
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        conn = connect(readonly=False)
        _local.conn_rw = (conn, current)
    return conn


def get_read_conn() -> sqlite3.Connection:
    """Conexion read-only reutilizable por hilo (para consultas)."""
    entry = getattr(_local, "conn_ro", None)
    conn, cached_path = entry if entry else (None, None)
    current = str(db_path())
    if conn is None or cached_path != current or not _viva(conn):
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        conn = connect(readonly=True)
        _local.conn_ro = (conn, current)
    return conn


def connect_estado(readonly: bool = False) -> sqlite3.Connection:
    """Conexión al sidecar de estado (mismos pragmas que la DB principal)."""
    p = estado_path()
    if readonly:
        if not p.exists():
            raise FileNotFoundError(f"sidecar no existe: {p}")
        conn = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=30)
    else:
        p.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(p), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    if not readonly:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def init_estado(conn: sqlite3.Connection | None = None) -> Path:
    """Crea las tablas del sidecar (idempotente). No toca historial.db."""
    cerrar = conn is None
    if conn is None:
        conn = connect_estado(readonly=False)
    try:
        conn.executescript(CREATE_ESTADO_TABLES)
        conn.commit()
        return estado_path()
    finally:
        if cerrar:
            conn.close()


def estado_exists() -> bool:
    """True si el sidecar existe (un fresh install no lo tiene hasta capturar)."""
    return estado_path().exists()


@contextmanager
def write_txn(conn: sqlite3.Connection):
    """Transaccion de escritura atomica."""
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def init_db(conn: sqlite3.Connection | None = None) -> Path:
    """Crea lo faltante, verifica la forma, fija la versión. Idempotente.

    F4: construye tablas ausentes (CREATE IF NOT EXISTS, seguro), pero NUNCA
    altera una tabla existente: si alguna tabla del contrato tiene columnas
    en otro orden o sobran/faltan columnas, levanta ContratoDBError en vez
    de mutarla en silencio. La forma la define la canónica; acá no se parchea.
    Al final fija user_version al del contrato.
    """
    own = conn is None
    conn = conn or connect(readonly=False)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute(CREATE_TABLE_VENTAS)
        conn.executescript(CREATE_AUDIT_TABLES)
        conn.executescript(CREATE_DIM_TABLES)
        conn.executescript(CREATE_FACT_TABLES)
        # Gate de forma: lo construido debe cumplir; lo preexistente con
        # forma distinta bloquea (no se ALTERa).
        rep = verificar_contrato(conn)
        _mal = (
            rep["tablas_faltantes"]
            or list(rep["columnas_difieren"])
            or rep["tablas_extra_no_toleradas"]
        )
        if _mal:
            raise ContratoDBError(
                "historial.db no cumple el contrato "
                f"(v{CONTRACTO_VERSION}): {rep['detalle']}. "
                "Re-sembrar desde la canónica en vez de parchear."
            )
        conn.execute(f"PRAGMA user_version = {CONTRACTO_VERSION}")
        for stmt in CREATE_INDEXES:
            conn.execute(stmt)
        # Las vistas se recrean siempre (DROP + CREATE): CREATE VIEW IF NOT
        # EXISTS dejaria definiciones viejas en DBs existentes y los cambios
        # de regla (ej. exact-match) nunca se aplicarian.
        for v in (
            "vw_dim_cliente",
            "vw_dim_articulo",
            "vw_dim_linea",
            "vw_documento",
            "vw_devoluciones",
            "vw_venta_neta_producto",
            "vw_nc_totales",
            "vw_nc_parciales",
            "vw_historial_venta_cliente",
            "vw_radar_recompra",
            "vw_facturas_disponibles",
            "vw_impacto_documento",
        ):
            conn.execute(f"DROP VIEW IF EXISTS {v}")
        for stmt in CREATE_VIEWS:
            conn.execute(stmt)
        # Normalizacion id_pedido: espacios -> guion bajo ("SADIE 25" -> "SADIE_25").
        # Idempotente; alinea DBs viejas con la normalizacion de xls_processor.
        conn.execute(
            "UPDATE ventas SET id_pedido = REPLACE(id_pedido, ' ', '_') WHERE id_pedido LIKE '% %'"
        )
        # F4: SIN migraciones silenciosas en tablas del contrato. Si falta una
        # columna, el archivo no cumple -> verificar_contrato() lo detecta y se
        # bloquea en vez de mutar. La forma la define la canónica.
        # F4: SIN ALTERs en ventas. Si falta una columna del contrato, el
        # archivo no cumple -> verificar_contrato() (arriba) ya bloqueó.
        # La forma la define la canónica; acá no se parchea.
        # agg_cliente_mes: poblar solo si esta vacia y hay datos (evita el
        # rebuild de ~5s en cada arranque; las capturas lo refrescan luego).
        try:
            n_ventas = conn.execute("SELECT COUNT(*) FROM ventas").fetchone()[0]
            n_agg = conn.execute("SELECT COUNT(*) FROM agg_cliente_mes").fetchone()[0]
        except Exception:
            n_ventas = n_agg = 0
        conn.commit()
        if n_ventas and not n_agg:
            try:
                refresh_agg_cliente_mes(conn)
            except Exception:
                conn.rollback()
    finally:
        if own:
            conn.close()
    invalidate_card_info_cache()
    return db_path()


def db_exists() -> bool:
    return db_path().exists()


def populate_nc_asociadas(conn: sqlite3.Connection | None = None) -> int:
    """Reconstruye la tabla materializada nc_asociadas desde ventas.

    Reemplaza la vista vw_nc_asociadas (auto-join lento en 1.5M filas).
    Usa factura_ref_serie/nro directamente sin join.
    Retorna el numero de filas insertadas.
    """
    own = conn is None
    conn = conn or connect(readonly=False)
    try:
        conn.execute("DELETE FROM nc_asociadas")
        conn.execute("""
            INSERT OR IGNORE INTO nc_asociadas
            (factura_doc_id, nc_doc_id, nc_tpo, nc_serie, nc_nro, fecha_orig, cantidad, soles)
            SELECT
              'F' || factura_ref_serie || '-' || factura_ref_nro AS factura_doc_id,
              substr(tpo_doc, 1, 1) || serie_doc || '-' || nro_doc AS nc_doc_id,
              tpo_doc, serie_doc, nro_doc,
              fecha_orig, cantidad, soles
            FROM ventas
            WHERE tipo_operacion IN ('devolucion', 'ajuste_valor', 'nota_debito')
              AND factura_ref_serie IS NOT NULL AND factura_ref_serie != ''
              AND factura_ref_nro IS NOT NULL AND factura_ref_nro != ''
        """)
        n = conn.execute("SELECT COUNT(*) FROM nc_asociadas").fetchone()[0]
        conn.commit()
        invalidate_card_info_cache()
        refresh_stats_cache_async()
        return n
    except Exception:
        if not own:
            conn.rollback()
        raise
    finally:
        if own:
            conn.close()


def refresh_agg_cliente_mes(conn: sqlite3.Connection | None = None) -> int:
    """Reconstruye agg_cliente_mes (cliente x mes, facturas/boletas activas).

    Alimenta el dropdown de clientes: n_docs = COUNT(DISTINCT doc) por cliente
    y mes, con el mismo allowlist de lineas de fetch_historial. Reduce el
    escaneo de 1.5M filas a ~27K -> rango largo de 3.3s a <20ms. Retorna filas.
    """
    own = conn is None
    conn = conn or connect(readonly=False)
    lineas_sql = active_line_sql("id_linea")
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS agg_cliente_mes ("
            "id_cliente TEXT NOT NULL, id_vendedor TEXT, nom_cliente TEXT, "
            "doc_cliente TEXT, mes TEXT NOT NULL, n_docs INTEGER NOT NULL, "
            "PRIMARY KEY (id_cliente, id_vendedor, mes))"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_agg_mes ON agg_cliente_mes(mes, n_docs)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_agg_vend ON agg_cliente_mes(id_vendedor, mes)")
        conn.execute("DELETE FROM agg_cliente_mes")
        # GROUP BY con la EXPRESION substr(...) explicita, no el alias 'mes':
        # SQLite agrupa distinto por alias de funcion y pierde grupos.
        conn.execute(f"""
            INSERT OR REPLACE INTO agg_cliente_mes
                (id_cliente, id_vendedor, nom_cliente, doc_cliente, mes, n_docs)
            SELECT id_cliente, id_vendedor, MAX(nom_cliente), MAX(doc_cliente),
                   substr(fecha_orig, 1, 7) AS mes,
                   COUNT(DISTINCT serie_doc || '-' || nro_doc) AS n_docs
            FROM ventas
            WHERE id_cliente != ''
              AND (tpo_doc LIKE 'F%' OR tpo_doc LIKE 'B%')
              AND {lineas_sql}
            GROUP BY id_cliente, id_vendedor, substr(fecha_orig, 1, 7)
        """)
        n = conn.execute("SELECT COUNT(*) FROM agg_cliente_mes").fetchone()[0]
        conn.commit()
        return n
    except Exception:
        if not own:
            conn.rollback()
        raise
    finally:
        if own:
            conn.close()


def db_is_populated() -> bool:
    if not db_exists():
        return False
    try:
        conn = connect(readonly=True)
        try:
            n = conn.execute("SELECT COUNT(*) FROM ventas").fetchone()[0]
        finally:
            conn.close()
        return n > 0
    except Exception:
        return False


# ── Escritura (port de writer.rs) ───────────────────────────────────────────

# Columnas de fecha de `ventas`. Todas se guardan en ISO (yyyy-mm-dd): el SQL
# las compara como texto (ORDER BY, BETWEEN, MIN/MAX, substr), y eso solo da
# orden cronologico con ISO.
COLS_FECHA = ("fecha_orig", "fecha_ref", "fecha_venc", "fec_cargo")


def _normaliza_fechas_venta(v: dict) -> dict:
    """Devuelve el dict con las columnas de fecha en ISO.

    Idempotente y no destructivo: una fecha que no se puede interpretar se
    conserva tal cual, porque es preferible un dato raro a perder la fila.
    """
    from src.core.fechas import fecha_iso

    salida = None
    for col in COLS_FECHA:
        valor = v.get(col)
        if valor is None or valor == "":
            continue
        iso = fecha_iso(valor)
        if iso and iso != valor:
            if salida is None:
                salida = dict(v)
            salida[col] = iso
    return salida if salida is not None else v


INSERT_COLS = (
    "id_articulo,original_sku,nom_articulo,id_linea,nom_linea,id_grupo,nom_grupo,"
    "id_tipo,nom_tipo,id_familia,nom_familia,id_cliente,doc_cliente,nom_cliente,"
    "tpo_doc,serie_doc,nro_doc,referencia,moneda,cantidad,cantidad_fae,soles,dolares,"
    "precio_unitario,anho,mes,fecha_orig,fecha_ref,fecha_venc,cod_sucursal,nom_sucursal,"
    "departamento,provincia,distrito,id_vendedor,nom_vendedor,id_pedido,ord_compra,file_source,"
    "mes_ref,tipo_operacion,factura_ref_serie,factura_ref_nro,folio_unico,"
    "id_ubigeo,estado_linea,canal_distribucion,id_guia,nom_condicion_pago,division,fec_cargo"
)


def insert_ventas(conn: sqlite3.Connection, ventas: list[dict], label: str | None = None) -> int:
    """Delete-then-insert + insert mensual (F6/C1-C2).

    mes_ref siempre YYYY-MM canónico (se fuerza acá). El borrado acota por
    fecha_orig: si label es un día (chunk diario), se reemplaza SOLO ese día
    y las filas tardías de otros días se AGREGAN sin borrar al vecino; si
    label es mensual o None, se reemplazan los días presentes (un export
    mensual completo cubre su mes; uno parcial no se come días ausentes).
    Inserta en una sola transaccion. Devuelve n insertadas."""
    if not ventas:
        return 0
    invalidate_lineas_cache()
    invalidate_sucursales_cache()
    invalidate_card_info_cache()
    stats_cache_clear()
    es_diario = bool(label) and len(label) == 10 and label.count("-") == 2
    with write_txn(conn):
        if es_diario:
            conn.execute("DELETE FROM ventas WHERE substr(fecha_orig, 1, 10) = ?", (label,))
        else:
            dias = sorted(
                {
                    str(v.get("fecha_orig", ""))[:10]
                    for v in ventas
                    if len(str(v.get("fecha_orig", ""))) >= 10
                }
                - {""}
            )
            for _d in dias:
                conn.execute("DELETE FROM ventas WHERE substr(fecha_orig, 1, 10) = ?", (_d,))
        placeholders = ",".join("?" * len(INSERT_COLS.split(",")))
        sql = f"INSERT INTO ventas ({INSERT_COLS}) VALUES ({placeholders})"
        cols = INSERT_COLS.split(",")
        for v in ventas:
            v = _normaliza_fechas_venta(v)
            mr = str(v.get("mes_ref", ""))
            if len(mr) > 7:
                v = dict(v, mes_ref=mr[:7])
            conn.execute(sql, [v.get(c) for c in cols])
    return len(ventas)


def replace_folios(conn: sqlite3.Connection, ventas: list[dict]) -> int:
    """Reemplaza SOLO los folios presentes en el lote (idempotente).

    A diferencia de ``insert_ventas`` (que borra el día o el mes completo),
    este borra por ``folio_unico``: permite aplicar un diff incremental donde
    solo se re-fetchan los folios faltantes o recapturados, sin tocar las
    líneas de los folios vecinos que no vinieron en la respuesta. Reejecutarlo
    no duplica ni pierde filas. Devuelve n insertadas.
    """
    if not ventas:
        return 0
    folios = sorted({str(v.get("folio_unico") or "").strip() for v in ventas} - {""})
    if not folios:
        raise ValueError("replace_folios: el lote no trae folio_unico")
    invalidate_lineas_cache()
    invalidate_sucursales_cache()
    invalidate_card_info_cache()
    stats_cache_clear()
    cols = INSERT_COLS.split(",")
    sql = f"INSERT INTO ventas ({INSERT_COLS}) VALUES ({','.join('?' * len(cols))})"
    with write_txn(conn):
        for i in range(0, len(folios), 400):
            lote = folios[i : i + 400]
            conn.execute(
                f"DELETE FROM ventas WHERE folio_unico IN ({','.join('?' * len(lote))})",
                lote,
            )
        for v in ventas:
            v = _normaliza_fechas_venta(v)
            mr = str(v.get("mes_ref", ""))
            if len(mr) > 7:
                v = dict(v, mes_ref=mr[:7])
            conn.execute(sql, [v.get(c) for c in cols])
    return len(ventas)


def dedup_ventas(conn: sqlite3.Connection) -> int:
    """Dedup por linea (folio_unico, id_articulo): conserva MAX(id)."""
    invalidate_lineas_cache()
    invalidate_sucursales_cache()
    invalidate_card_info_cache()
    stats_cache_clear()
    with write_txn(conn):
        cur = conn.execute(
            "DELETE FROM ventas WHERE id NOT IN ("
            "  SELECT MAX(id) FROM ventas GROUP BY folio_unico, id_articulo)"
        )
        n = cur.rowcount
    return n


def refresh_stats_cache(conn: sqlite3.Connection) -> None:
    """Cache KV para KPIs sin full-scan (incluye las métricas caras:
    vendedores, facturas y líneas, que la card de salud necesita).

    Los SELECT (los caros) corren FUERA de la transacción de escritura: el
    lock se toma solo para guardar los 7 valores, no durante ~40s de scan.
    """
    stats = {
        "total_rows": "SELECT COUNT(*) FROM ventas",
        "total_soles": "SELECT ROUND(COALESCE(SUM(soles),0),2) FROM ventas",
        "n_clientes": "SELECT COUNT(DISTINCT id_cliente) FROM ventas",
        "n_articulos": "SELECT COUNT(DISTINCT id_articulo) FROM ventas",
        "n_vendedores": "SELECT COUNT(DISTINCT id_vendedor) FROM ventas",
        "n_facturas": ("SELECT COUNT(DISTINCT folio_unico) FROM ventas WHERE tpo_doc LIKE 'F01%'"),
        "n_lineas": (
            "SELECT COUNT(DISTINCT id_linea) FROM ventas "
            "WHERE id_linea IS NOT NULL AND LENGTH(TRIM(id_linea)) > 0"
        ),
    }
    valores: dict[str, float] = {}
    for key, sql in stats.items():
        val = conn.execute(sql).fetchone()[0]
        try:
            valores[key] = float(val) if val is not None else 0.0
        except (TypeError, ValueError):
            valores[key] = 0.0
    with write_txn(conn):
        # Un archivo venido de la canónica trae sus propias claves
        # (total_records, total_sales, ...): se purgan para no duplicar
        # métricas con distinta escala en el display.
        conn.execute(
            "DELETE FROM stats_cache WHERE key NOT IN (?,?,?,?,?,?,?)", tuple(stats.keys())
        )
        for key, val_f in valores.items():
            conn.execute(
                "INSERT INTO stats_cache (key, value, updated_at) VALUES (?,?,datetime('now')) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, val_f),
            )


def _sugerir_vendedor(codigo: str, maestro: dict[str, str]) -> str | None:
    """Código canónico probable de un id malformado, o None si no se puede inferir.

    El canónico son 3 caracteres (sufijo pelado del ERP: '178', 'M17', '052').
    Cubre los dos casos que se han dado: prefijo de empresa sin quitar
    ('01178') y recorte de un carácter de más ('178' -> '78'). El maestro
    (dim_vendedor) decide cuál es el código válido.
    """
    if len(codigo) == 5 and codigo.startswith("01"):
        return codigo[2:]
    n = len(codigo)
    cands = [c for c in maestro if c != codigo and len(c) > n and c.endswith(codigo)]
    if len(cands) == 1:
        return cands[0]
    return None


def reparar_nombres_vendedor(conn: sqlite3.Connection) -> int:
    """Rellena nom_vendedor vacío desde dim_vendedor. Solo toca vacíos y
    solo cuando el maestro trae nombre (si el maestro tampoco lo tiene,
    no hay nada que copiar: esas filas son legado pre-2016 sin fuente)."""
    with write_txn(conn):
        cur = conn.execute(
            "UPDATE ventas SET nom_vendedor = "
            "(SELECT d.nom_vendedor FROM dim_vendedor d "
            "WHERE d.id_vendedor = ventas.id_vendedor) "
            "WHERE TRIM(IFNULL(nom_vendedor, '')) = '' "
            "AND EXISTS (SELECT 1 FROM dim_vendedor d "
            "WHERE d.id_vendedor = ventas.id_vendedor "
            "AND TRIM(IFNULL(d.nom_vendedor, '')) <> '')"
        )
        return cur.rowcount


# Placeholders del ERP que llegaron a doc_cliente y no identifican a nadie.
_DOC_PLACEHOLDERS = frozenset({"< INGRESE DNI >", "S/N", "VVV", "00", "0", "-"})


def limpiar_doc_cliente(conn: sqlite3.Connection) -> dict:
    """Limpia doc_cliente sin tocar identificadores reales.

    - Placeholders del formulario ('< INGRESE DNI >', 'S/N', 'VVV', '00')
      y puntos sueltos ('.46138563', '74966851.') se normalizan o vacían.
    - Los IDs fiscales extranjeros (NIT, RIF, J-, B-, GB, DE, ES, IT, NL…)
      se conservan tal cual: identifican al cliente.
    Returns: {limpiados, por_regla}.
    """
    res = {"limpiados": 0, "por_regla": {}}
    with write_txn(conn):
        for ph in sorted(_DOC_PLACEHOLDERS):
            cur = conn.execute(
                "UPDATE ventas SET doc_cliente = '' WHERE TRIM(doc_cliente) = ?", (ph,)
            )
            if cur.rowcount:
                res["por_regla"][f"placeholder:{ph}"] = cur.rowcount
                res["limpiados"] += cur.rowcount
        # Punto suelto al inicio o al final ('.46138563', '74966851.').
        rows = conn.execute(
            "SELECT DISTINCT doc_cliente FROM ventas "
            "WHERE doc_cliente LIKE '.%' OR doc_cliente LIKE '%.'"
        ).fetchall()
        for (doc,) in rows:
            limpio = doc.strip().strip(".").strip()
            if limpio and limpio != doc:
                cur = conn.execute(
                    "UPDATE ventas SET doc_cliente = ? WHERE doc_cliente = ?", (limpio, doc)
                )
                res["por_regla"].setdefault("punto_suelto", 0)
                res["por_regla"]["punto_suelto"] += cur.rowcount
                res["limpiados"] += cur.rowcount
    return res


def reportar_ruc_compartidos(conn: sqlite3.Connection) -> list[dict]:
    """RUC/DNI en más de un cliente (posible duplicado de maestro).

    Solo lectura y solo reporte: fusionar clientes requiere decisión humana.
    Excluye placeholders ya limpiables.
    """
    out = []
    ph = ",".join("?" * len(_DOC_PLACEHOLDERS))
    for doc, ncli, nfil in conn.execute(
        "SELECT doc_cliente, COUNT(DISTINCT id_cliente), COUNT(*) FROM ventas "
        f"WHERE TRIM(IFNULL(doc_cliente,'')) <> '' AND doc_cliente NOT IN ({ph}) "
        "GROUP BY doc_cliente HAVING COUNT(DISTINCT id_cliente) > 1 "
        "ORDER BY 3 DESC",
        (*sorted(_DOC_PLACEHOLDERS),),
    ).fetchall():
        clientes = [
            tuple(r)
            for r in conn.execute(
                "SELECT DISTINCT id_cliente, nom_cliente FROM ventas WHERE doc_cliente = ? "
                "ORDER BY id_cliente",
                (doc,),
            ).fetchall()
        ]
        out.append({"doc": doc, "clientes": clientes, "filas": int(nfil)})
    return out


def auditar_completidad(conn: sqlite3.Connection | None = None) -> dict:
    """Census por columna + invariantes de integridad (solo lectura).

    Es el guard permanente de lo que esta auditoría encontró a mano: cada
    columna crítica informa su cobertura, y cada regla de negocio (folio,
    precio, vendedor, línea, O/C, 7 campos) se verifica explícitamente.
    Lo que acá salga mal es un dato que una ingesta perdió o nunca trajo.
    """
    cerrar = conn is None
    if conn is None:
        if not db_exists():
            return {"ok": True, "filas": 0, "cobertura": {}, "hallazgos": []}
        conn = connect(readonly=True)
    try:
        total = conn.execute("SELECT COUNT(*) FROM ventas").fetchone()[0]
        cols = [
            r[1]
            for r in conn.execute("PRAGMA table_info(ventas)").fetchall()
            if r[1] not in ("id",)
        ]
        cobertura: dict[str, float] = {}
        for c in cols:
            n = conn.execute(
                f'SELECT COUNT(*) FROM ventas WHERE "{c}" IS NOT NULL '
                f"AND TRIM(CAST(\"{c}\" AS TEXT)) <> ''"
            ).fetchone()[0]
            cobertura[c] = round(100.0 * n / total, 2) if total else 100.0
        hallazgos: list[dict] = []

        def _chequeo(nombre: str, sql: str, detalle: str = "") -> None:
            n = conn.execute(sql).fetchone()[0]
            if n:
                hallazgos.append({"regla": nombre, "filas": int(n), "detalle": detalle})

        _chequeo(
            "folio_inconsistente",
            "SELECT COUNT(*) FROM ventas "
            "WHERE folio_unico <> tpo_doc || '/' || serie_doc || '/' || nro_doc",
            "folio_unico debe ser tpo/serie/nro",
        )
        _chequeo(
            "precio_inconsistente",
            "SELECT COUNT(*) FROM ventas WHERE cantidad <> 0 "
            "AND ABS(precio_unitario - ROUND(soles / cantidad, 5)) > 0.0001",
            "precio_unitario debe ser ROUND(soles/cantidad, 5); "
            "la tolerancia excluye polvo de coma flotante",
        )
        _chequeo(
            "vendedor_sin_nombre",
            "SELECT COUNT(*) FROM ventas v "
            "WHERE TRIM(IFNULL(v.nom_vendedor,'')) = '' "
            "AND EXISTS (SELECT 1 FROM dim_vendedor d "
            "WHERE d.id_vendedor = v.id_vendedor)",
            "con fila en dim_vendedor pero sin nombre en ningún lado "
            "(legado pre-2016: solo lo trae una recaptura)",
        )
        _chequeo(
            "vendedor_malformado",
            "SELECT COUNT(*) FROM ventas v "
            "WHERE TRIM(IFNULL(v.id_vendedor,'')) <> '' "
            "AND LENGTH(TRIM(v.id_vendedor)) <> 3 "
            "AND NOT EXISTS (SELECT 1 FROM dim_vendedor d "
            "WHERE d.id_vendedor = TRIM(v.id_vendedor))",
            "fuera del canónico de 3 y sin entrada en el maestro "
            "(un histórico como '54' no se reporta)",
        )
        cols_set = set(cols)
        if "ord_compra" in cols_set:
            # F2: no hay columna par; se verifica que ord_compra YA esté
            # normalizado (punto fijo de normalize_orden_compra).
            from src.core.xls_processor import normalize_orden_compra

            _vals = [
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT ord_compra FROM ventas WHERE TRIM(IFNULL(ord_compra,'')) <> ''"
                ).fetchall()
            ]
            _n_sin = sum(1 for _v in _vals if normalize_orden_compra(_v or "") != (_v or ""))
            if _n_sin:
                hallazgos.append(
                    {
                        "regla": "oc_sin_normalizar",
                        "filas": int(_n_sin),
                        "detalle": "ord_compra no normalizado (distintos a punto fijo)",
                    }
                )
        _chequeo(
            "doc_placeholder",
            "SELECT COUNT(*) FROM ventas WHERE TRIM(doc_cliente) IN "
            "('< INGRESE DNI >', 'S/N', 'VVV', '00', '0', '-')",
            "placeholders del formulario en doc_cliente",
        )
        # Solo las columnas que por contrato van ~100% pobladas. Las
        # naturalmente dispersas (id_guia, division, fec_cargo) salen igual
        # en el dict de cobertura para revisión humana, sin hallazgo.
        for c in ("id_ubigeo", "estado_linea", "canal_distribucion", "nom_condicion_pago"):
            try:
                n = conn.execute(
                    f"SELECT COUNT(*) FROM ventas WHERE TRIM(IFNULL({c},'')) = ''"
                ).fetchone()[0]
            except sqlite3.Error:
                n = 0
            if n and n != total:
                hallazgos.append(
                    {
                        "regla": f"{c}_incompleto",
                        "filas": int(n),
                        "detalle": "columna que la captura intranet debe llenar",
                    }
                )
        return {
            "ok": not hallazgos,
            "filas": int(total),
            "cobertura": cobertura,
            "hallazgos": hallazgos,
        }
    finally:
        if cerrar:
            conn.close()


def auditar_vendedores(conn: sqlite3.Connection | None = None) -> dict:
    """Detecta ``id_vendedor`` malformados en ``ventas`` (solo lectura).

    Una ingesta puede dejar el prefijo de empresa sin quitar ('01178') o
    recortar un carácter de más ('178' -> '78'). En ambos casos el vendedor
    cuelga del reporte sin que nada lo delate: los KPIs no cuadran con la
    realidad y el cliente desaparece del selector. Conviene correr esto tras
    cada sincronización, sobre todo la de red, que copia el valor crudo de la
    DB fuente sin normalizarlo.

    Trabaja sobre el conjunto de ids distintos (cientos), no sobre las 2.8M
    filas, así que es barato: usa el índice de vendedor.

    Returns:
        dict con ``ok``, ``malformados`` (id, n, desde, hasta, sugerencia),
        ``sin_maestro`` (id, nombre, n) y los totales de cada grupo. Los
        ``sin_maestro`` no son error: un vendedor nuevo aún no cargado en el
        maestro es normal y se reporta aparte para no mezclarlo con corrupción.
    """
    cerrar = conn is None
    if conn is None:
        if not db_exists():
            return {
                "ok": True,
                "malformados": [],
                "sin_maestro": [],
                "n_malformados": 0,
                "filas_malformadas": 0,
                "n_sin_maestro": 0,
                "filas_sin_maestro": 0,
            }
        conn = connect(readonly=True)
    try:
        try:
            maestro = {
                r[0]: (r[1] or "")
                for r in conn.execute("SELECT id_vendedor, nom_vendedor FROM dim_vendedor")
            }
        except sqlite3.Error:
            maestro = {}

        usos = conn.execute(
            "SELECT id_vendedor, COUNT(*), MIN(anho || '-' || printf('%02d', mes)), "
            "MAX(anho || '-' || printf('%02d', mes)) "
            "FROM ventas WHERE id_vendedor IS NOT NULL AND TRIM(id_vendedor) <> '' "
            "GROUP BY id_vendedor"
        ).fetchall()

        malformados, sin_maestro = [], []
        for vid, n, desde, hasta in usos:
            cod = vid.strip()
            if maestro and cod in maestro:
                continue
            if len(cod) != 3:
                malformados.append(
                    {
                        "id": cod,
                        "n": int(n),
                        "desde": desde,
                        "hasta": hasta,
                        "sugerencia": _sugerir_vendedor(cod, maestro),
                    }
                )
            else:
                sin_maestro.append(
                    {
                        "id": cod,
                        "nombre": conn.execute(
                            "SELECT MAX(nom_vendedor) FROM ventas WHERE id_vendedor = ?", (vid,)
                        ).fetchone()[0]
                        or "",
                        "n": int(n),
                    }
                )
        malformados.sort(key=lambda r: -r["n"])
        sin_maestro.sort(key=lambda r: -r["n"])
        return {
            "ok": not malformados,
            "malformados": malformados,
            "sin_maestro": sin_maestro,
            "n_malformados": len(malformados),
            "filas_malformadas": sum(r["n"] for r in malformados),
            "n_sin_maestro": len(sin_maestro),
            "filas_sin_maestro": sum(r["n"] for r in sin_maestro),
        }
    finally:
        if cerrar:
            conn.close()


def stats_cache_read() -> dict:
    """KPIs precomputados {key: value}. {} si no hay o la DB no existe."""
    if not db_exists():
        return {}
    try:
        conn = connect(readonly=True)
        try:
            return {
                r[0]: r[1] for r in conn.execute("SELECT key, value FROM stats_cache").fetchall()
            }
        finally:
            conn.close()
    except Exception:
        return {}


def stats_cache_clear() -> None:
    """Invalida el cache KV (se llama en cada escritura de ventas)."""
    global _STATS_REFRESH_BUSY
    _STATS_REFRESH_BUSY = False
    if not db_exists():
        return
    try:
        conn = connect(readonly=False)
        try:
            with write_txn(conn):
                conn.execute("DELETE FROM stats_cache")
        finally:
            conn.close()
    except Exception:
        pass


_STATS_REFRESH_BUSY = False


def refresh_stats_cache_async() -> None:
    """Recalcula stats_cache en background (arranque / tras capturas)."""
    global _STATS_REFRESH_BUSY
    if _STATS_REFRESH_BUSY:
        return
    _STATS_REFRESH_BUSY = True

    def worker():
        global _STATS_REFRESH_BUSY
        try:
            conn = connect(readonly=False)
            try:
                refresh_stats_cache(conn)
            finally:
                conn.close()
        except Exception:
            pass
        finally:
            _STATS_REFRESH_BUSY = False

    threading.Thread(target=worker, daemon=True).start()


def register_sync(
    conn: sqlite3.Connection, tipo: str, estado: str, filas: int, duracion: float, error: str = ""
) -> None:
    # F4: columna canónica filas_subidas (filas traídas). Sin lector en src/;
    # solo bookkeeping append-only.
    with write_txn(conn):
        conn.execute(
            "INSERT INTO sync_log (tipo, estado, filas_subidas, duracion_segundos, error_message, started_at, finished_at) "
            "VALUES (?,?,?,?,?, datetime('now'), datetime('now'))",
            (tipo, estado, filas, duracion, error),
        )


# ── Lecturas de estado (para UI) ────────────────────────────────────────────


def db_health() -> dict:
    """Resumen de estado de la DB para mostrar en UI."""
    if not db_exists():
        return {"exists": False}
    try:
        conn = connect(readonly=True)
        try:
            row = conn.execute(
                "SELECT COUNT(*), MIN(fecha_orig), MAX(fecha_orig), MAX(capturado_en) FROM ventas"
            ).fetchone()
            n, fmin, fmax, cap = row
            size_mb = db_path().stat().st_size / (1024 * 1024)
            return {
                "exists": True,
                "rows": n,
                "fecha_min": fmin,
                "fecha_max": fmax,
                "capturado_en": cap,
                "size_mb": round(size_mb, 1),
            }
        finally:
            conn.close()
    except Exception as e:
        return {"exists": True, "error": str(e)}


def month_is_complete(label: str) -> bool:
    """True si el mes esta CUBIERTO y completo:
    - presente como chunk mensual (mes_ref=label) Y llega al fin de mes, o
    - presente como dias (mes_ref=label-*) Y llega al fin de mes.
    El mes en curso nunca se considera completo (lo cubre la ventana diaria).
    Esto evita re-intentar mensualmente meses que ya se capturaron dia a dia
    (los pesados, ej. 2024-01 que excede el timeout del server)."""
    from datetime import date as _date

    hoy = _date.today()
    cur = f"{hoy.year}-{hoy.month:02d}"
    if label == cur:
        return False
    if not db_exists():
        return False
    try:
        conn = connect(readonly=True)
        try:
            row = conn.execute(
                "SELECT MAX(fecha_orig) FROM ventas WHERE mes_ref = ?", (label,)
            ).fetchone()
            fmax = row[0] if row else None
            if not fmax:
                row2 = conn.execute(
                    "SELECT MAX(fecha_orig) FROM ventas WHERE mes_ref LIKE ?", (label + "-%",)
                ).fetchone()
                fmax = row2[0] if row2 else None
        finally:
            conn.close()
    except Exception:
        return False
    return bool(fmax) and str(fmax) >= month_end(label)


def missing_months(desde: date, hasta: date) -> list[str]:
    """Meses del rango que NO estan completos (ausentes o truncados).

    Optimizado: una sola query para todos los meses en vez de N queries individuales.
    """
    if not db_exists():
        return [f"{cur.year}-{cur.month:02d}" for cur in _iter_months(desde, hasta)]
    try:
        conn = connect(readonly=True)
        try:
            # Una sola query: max fecha por mes (tanto mensual como diario)
            rows = conn.execute("""
                SELECT substr(fecha_orig, 1, 7) AS mes, MAX(fecha_orig) AS fmax
                FROM ventas
                WHERE fecha_orig IS NOT NULL AND fecha_orig != ''
                GROUP BY substr(fecha_orig, 1, 7)
            """).fetchall()
            max_by_month = {r[0]: r[1] for r in rows}
        finally:
            conn.close()
    except Exception:
        return []

    hoy = date.today()
    cur = desde
    missing = []
    while cur <= hasta:
        label = f"{cur.year}-{cur.month:02d}"
        # Meses anteriores a FIRST_LOAD_FROM son patrimonio migrado que el
        # sistema nunca completa (origen truncado); no se listan ni se reintentan.
        if label < FIRST_LOAD_FROM.strftime("%Y-%m"):
            y, m = cur.year, cur.month
            cur = date(y + (m == 12), (m % 12) + 1, 1)
            continue
        # Mes actual nunca es completo
        if label == f"{hoy.year}-{hoy.month:02d}":
            missing.append(label)
        elif label not in max_by_month:
            missing.append(label)
        else:
            fmax = max_by_month[label]
            if str(fmax) < month_end(label):
                missing.append(label)
        y, m = cur.year, cur.month
        cur = date(y + (m == 12), (m % 12) + 1, 1)
    return missing


def _iter_months(desde: date, hasta: date):
    """Genera fechas inicio de mes entre desde y hasta."""
    cur = desde
    while cur <= hasta:
        yield cur
        y, m = cur.year, cur.month
        cur = date(y + (m == 12), (m % 12) + 1, 1)


def month_end(label: str) -> str:
    """Ultimo dia del mes 'YYYY-MM' como 'YYYY-MM-DD'."""
    y, m = int(label[:4]), int(label[5:7])
    ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
    return (date(ny, nm, 1) - timedelta(days=1)).strftime("%Y-%m-%d")


def _resumen_meses_por_rango() -> dict[str, dict]:
    """{mes YYYY-MM: {filas, fmin, fmax}} sin GROUP BY sobre expresiones.

    El GROUP BY sobre substr(fecha_orig,1,7) escanea 2.8M filas (~7s entre
    months_coverage e incomplete_months). En su lugar: DISTINCT de mes_ref
    por índice + una query de rango por mes (cada una en ms vía
    idx_venta_mes). Las etiquetas diarias ("2024-02-15") caen en su mes.
    """
    if not db_exists():
        return {}
    try:
        conn = connect(readonly=True)
        try:
            prefs = sorted(
                {
                    str(r[0])[:7]
                    for r in conn.execute(
                        "SELECT DISTINCT mes_ref FROM ventas "
                        "WHERE mes_ref IS NOT NULL AND mes_ref != ''"
                    ).fetchall()
                    if str(r[0]).strip()
                }
            )
            out: dict[str, dict] = {}
            for mes in prefs:
                try:
                    if len(mes) != 7:
                        continue
                    y, m = int(mes[:4]), int(mes[5:7])
                    if not 1 <= m <= 12:
                        continue
                    nxt = f"{y + (m == 12)}-{m % 12 + 1:02d}"
                except (TypeError, ValueError):
                    continue
                r = conn.execute(
                    "SELECT COUNT(*), MIN(fecha_orig), MAX(fecha_orig) FROM ventas "
                    "WHERE mes_ref >= ? AND mes_ref < ?",
                    (mes, nxt),
                ).fetchone()
                if r[0]:
                    out[mes] = {"filas": r[0], "fmin": r[1], "fmax": r[2]}
            return out
        finally:
            conn.close()
    except Exception:
        return {}


def months_coverage() -> list[dict]:
    """Cobertura por mes para display de UI: mes, filas, rango de fechas.

    Optimizado: DISTINCT por índice + rangos por mes en vez de
    GROUP BY sobre substr(fecha_orig).
    """
    if not db_exists():
        return []
    try:
        resumen = _resumen_meses_por_rango()
    except Exception:
        return []
    return [
        {"mes_ref": m, "filas": v["filas"], "fecha_min": v["fmin"], "fecha_max": v["fmax"]}
        for m, v in sorted(resumen.items())
    ]


def has_month(mes_ref: str) -> bool:
    """True si el mes ya tiene alguna fila descargada."""
    if not db_exists():
        return False
    try:
        conn = connect(readonly=True)
        try:
            return (
                conn.execute(
                    "SELECT 1 FROM ventas WHERE mes_ref = ? LIMIT 1", (mes_ref,)
                ).fetchone()
                is not None
            )
        finally:
            conn.close()
    except Exception:
        return False


def incomplete_months(desde: date | None = None) -> list[str]:
    """Meses PASADOS con filas pero que no cubren hasta fin de mes
    (capturas interrumpidas). El mes en curso se excluye: lo cubre la ventana diaria.

    Meses anteriores a FIRST_LOAD_FROM son patrimonio migrado que el sistema
    nunca completa (origen truncado) -> no se listan como incompletos."""
    hoy = date.today()
    cur_label = f"{hoy.year}-{hoy.month:02d}"
    since_label = desde.strftime("%Y-%m") if desde else None
    # Piso: nunca listar meses previos a FIRST_LOAD_FROM (patrimonio migrado)
    piso_label = FIRST_LOAD_FROM.strftime("%Y-%m")
    out: list[str] = []
    if not db_exists():
        return out
    try:
        resumen = _resumen_meses_por_rango()
    except Exception:
        return out
    for label in sorted(resumen):
        if label == cur_label:
            continue
        if label < piso_label:
            continue
        fmax = resumen[label]["fmax"] or ""
        if fmax < month_end(label) and (since_label is None or label >= since_label):
            out.append(label)
    return sorted(out)


def coverage_summary() -> str:
    """Resumen de cobertura para mostrar en la UI (1-2 lineas)."""
    return _card_info()["resumen"]


# Cache de distinct_lineas(): el escaneo sobre 1.5M filas (1.28 GB) tarda
# ~28s y la UI lo pide al abrir el modal de config. Estrategia multi-capa:
#   1. invalidacion explicita en cada escritura (insert/dedup/import) -> la
#      siguiente lectura es fresca (contrato con los tests).
#   2. TTL corto pero con "serve-stale": si la copia tiene mas de TTL, se
#      devuelve al instante y un escaneo en background la refresca (evita
#      bloquear 28s el modal).
#   3. Single-flight: las llamadas concurrentes comparten UN solo escaneo.
#   4. refresh_lineas_async() al arrancar {main.py} pre-carga la cache.
_LINEAS_LIST_CACHE: dict = {"ts": 0.0, "data": None}
_LINEAS_LIST_TTL = 30.0
_LINEAS_EPOCH: dict = {"v": 0}  # se incrementa en cada invalidacion
_LINEAS_REFRESH_BUSY = False
_LINEAS_LOAD_LOCK = threading.Lock()


def invalidate_lineas_cache() -> None:
    _LINEAS_LIST_CACHE["ts"] = 0.0
    _LINEAS_LIST_CACHE["data"] = None
    _LINEAS_EPOCH["v"] += 1


def _consultar_lineas() -> list[dict] | None:
    """Consulta la DB (sin cache). None == error transitorio (no commitear)."""
    try:
        conn = connect(readonly=True)
        try:
            rows = conn.execute(
                "SELECT id_linea, MAX(nom_linea) FROM ventas "
                "WHERE id_linea IS NOT NULL AND TRIM(id_linea) <> '' "
                "GROUP BY id_linea ORDER BY id_linea"
            ).fetchall()
        finally:
            conn.close()
    except Exception:
        return None
    return [{"codigo": str(r[0]), "nombre": str(r[1] or "")} for r in rows]


def _cargar_lineas_singleflight() -> list[dict]:
    """Carga de la DB en single-flight (evita 2 escaneos de 28s en paralelo).

    Si la DB se invalida DURANTE el escaneo (escritura concurrente), el
    resultado es stale: reintenta hasta 3 veces antes de rendirse.
    """
    import time as _t

    with _LINEAS_LOAD_LOCK:
        cache = _LINEAS_LIST_CACHE
        if cache["data"] is not None and (_t.time() - cache["ts"]) < _LINEAS_LIST_TTL:
            return cache["data"]
        for _ in range(3):
            epoch = _LINEAS_EPOCH["v"]
            out = _consultar_lineas()
            if out is None:
                # Error transitorio: no marcar como fresco, devolver lo previo
                return cache["data"] or []
            if _LINEAS_EPOCH["v"] == epoch:
                cache["ts"] = _t.time()
                cache["data"] = out
                return out
        return cache["data"] or out or []


def refresh_lineas_async() -> None:
    """Dispara un escaneo en background si no hay uno en curso."""
    global _LINEAS_REFRESH_BUSY
    if _LINEAS_REFRESH_BUSY:
        return
    _LINEAS_REFRESH_BUSY = True

    def worker():
        global _LINEAS_REFRESH_BUSY
        try:
            _cargar_lineas_singleflight()
        finally:
            _LINEAS_REFRESH_BUSY = False

    threading.Thread(target=worker, daemon=True).start()


def distinct_lineas(force: bool = False) -> list[dict]:
    """Todas las lineas de producto presentes en la DB local.

    Returns:
        Lista de {'codigo': id_linea, 'nombre': nom_linea} ordenada por
        codigo. Vacia si no hay DB. La columna id_linea lleva prefijo de
        sucursal ('01AD' = linea 'AD'); la allowlist compara el sufijo de
        2 caracteres (ver is_allowed_line).
    """
    import time as _t

    if not db_exists():
        invalidate_lineas_cache()
        return []
    cache = _LINEAS_LIST_CACHE
    data = cache["data"]
    if data is None or force:
        return _cargar_lineas_singleflight()
    if (_t.time() - cache["ts"]) < _LINEAS_LIST_TTL:
        return data
    # Copia vencida pero valida: servirla al instante y refrescar en background
    refresh_lineas_async()
    return data


# ── Catálogo de tiendas reales (cliente, sucursal) ─────────────────────────
# La tienda real es el PAR: el código solo se repite entre clientes ('01' =
# 3268 clientes). El nombre se toma por moda del par (7.530 pares, solo 1
# con >1 nombre). ACUMULADO sin código es la principal de su cliente cuando
# éste tiene otras sucursales, o la única cuando no tiene ninguna.
# Misma estrategia de caché que líneas: TTL + invalidación en escrituras.

_SUC_LIST_CACHE: dict = {"ts": 0.0, "data": None}
_SUC_LIST_TTL = 300.0
_SUC_EPOCH: dict = {"v": 0}
_SUC_REFRESH_BUSY = False
_SUC_LOAD_LOCK = threading.Lock()


def invalidate_sucursales_cache() -> None:
    _SUC_LIST_CACHE["ts"] = 0.0
    _SUC_LIST_CACHE["data"] = None
    _SUC_EPOCH["v"] += 1


def categoria_sucursal(
    nom_sucursal: str | None, cod_sucursal: str | None, cliente_tiene_otras: bool = False
) -> str:
    """Categoría de una fila de tienda: 'sucursal' | 'principal' | 'unica' | 'sin_dato'.

    - Con código: 'sucursal' (punto de entrega real del cliente).
    - 'ACUMULADO' sin código: 'principal' si el cliente tiene otras
      sucursales (es su matriz: LINDA 28.8%, CONTINENTAL 23.6%), 'unica'
      si no tiene ninguna (22.199 clientes solo tienen esto).
    - Otro nombre sin código o vacío: 'sin_dato'.
    Función pura (sin DB): cliente_tiene_otras lo resuelve quien consulta.
    """
    cod = (cod_sucursal or "").strip()
    if cod:
        return "sucursal"
    nom = (nom_sucursal or "").strip().upper()
    if nom == "ACUMULADO":
        return "principal" if cliente_tiene_otras else "unica"
    return "sin_dato"


def _consultar_sucursales() -> list[dict] | None:
    """Una fila por (id_cliente, cod_sucursal): nombre por moda del par."""
    try:
        conn = connect(readonly=True)
        try:
            rows = conn.execute(
                "SELECT id_cliente, cod_sucursal, nom_sucursal, nom_cliente,"
                " id_ubigeo, distrito, COUNT(*), ROUND(COALESCE(SUM(soles),0),2),"
                " MIN(substr(fecha_orig,1,7)), MAX(substr(fecha_orig,1,7))"
                " FROM (SELECT id_cliente, UPPER(TRIM(IFNULL(cod_sucursal,'')))"
                " AS cod_sucursal, UPPER(TRIM(IFNULL(nom_sucursal,'')))"
                " AS nom_sucursal, nom_cliente, id_ubigeo, distrito,"
                " fecha_orig, soles FROM ventas)"
                " GROUP BY id_cliente, cod_sucursal, nom_sucursal"
            ).fetchall()
        finally:
            conn.close()
    except Exception:
        return None
    # Moda del nombre por par (solo 1 par real tiene >1 nombre).
    mejor: dict[tuple, dict] = {}
    for cid, cod, nom, clinom, ubi, dist, n, soles, fmin, fmax in rows:
        key = (str(cid or ""), str(cod or ""))
        cur = mejor.get(key)
        if cur is None or n > cur["_n"]:
            mejor[key] = {
                "id_cliente": str(cid or ""),
                "cod_sucursal": str(cod or ""),
                "nom_sucursal": str(nom or ""),
                "nom_cliente": str(clinom or ""),
                "id_ubigeo": str(ubi or ""),
                "distrito": str(dist or ""),
                "filas": 0,
                "soles": 0.0,
                "mes_min": None,
                "mes_max": None,
                "_n": n,
            }
        m = mejor[key]
        m["filas"] += int(n)
        m["soles"] = round(m["soles"] + float(soles or 0.0), 2)
        m["mes_min"] = fmin if m["mes_min"] is None else min(m["mes_min"], fmin)
        m["mes_max"] = fmax if m["mes_max"] is None else max(m["mes_max"], fmax)
        if not m["id_ubigeo"] and ubi:
            m["id_ubigeo"] = str(ubi)
        if not m["distrito"] and dist:
            m["distrito"] = str(dist)
    out = []
    for m in mejor.values():
        del m["_n"]
        out.append(m)
    out.sort(key=lambda r: (r["id_cliente"], r["cod_sucursal"]))
    return out


def _cargar_sucursales_singleflight() -> list[dict]:
    import time as _t

    with _SUC_LOAD_LOCK:
        cache = _SUC_LIST_CACHE
        if cache["data"] is not None and (_t.time() - cache["ts"]) < _SUC_LIST_TTL:
            return cache["data"]
        for _ in range(3):
            epoch = _SUC_EPOCH["v"]
            out = _consultar_sucursales()
            if out is None:
                return cache["data"] or []
            if _SUC_EPOCH["v"] == epoch:
                cache["ts"] = _t.time()
                cache["data"] = out
                return out
        return cache["data"] or out or []


def refresh_sucursales_async() -> None:
    """Dispara un escaneo en background si no hay uno en curso."""
    global _SUC_REFRESH_BUSY
    if _SUC_REFRESH_BUSY:
        return
    _SUC_REFRESH_BUSY = True

    def worker():
        global _SUC_REFRESH_BUSY
        try:
            _cargar_sucursales_singleflight()
        finally:
            _SUC_REFRESH_BUSY = False

    threading.Thread(target=worker, daemon=True).start()


def distinct_sucursales(force: bool = False) -> list[dict]:
    """Catálogo de tiendas reales: una fila por (id_cliente, cod_sucursal).

    Cada fila: id_cliente, nom_cliente, cod_sucursal, nom_sucursal (moda
    del par), id_ubigeo, distrito, filas, soles, mes_min, mes_max.
    Incluye el par ('', 'ACUMULADO') de clientes sin código: es su
    principal o su única tienda (ver categoria_sucursal). Vacía si no
    hay DB. Con caché TTL + serve-stale (igual que líneas).
    """
    import time as _t

    if not db_exists():
        invalidate_sucursales_cache()
        return []
    cache = _SUC_LIST_CACHE
    data = cache["data"]
    if data is None or force:
        return _cargar_sucursales_singleflight()
    if (_t.time() - cache["ts"]) < _SUC_LIST_TTL:
        return data
    refresh_sucursales_async()
    return data


# Cache de _card_info(): los COUNT(DISTINCT) sobre 1.5M filas tardan ~10s y la
# UI lo pide varias veces (card + modal). Estrategia (igual que lineas):
#   1. TTL amplio + invalidacion en escrituras -> la siguiente lectura tras
#      un cambio recalcula una sola vez.
#   2. serve-stale: vencido pero con datos previos, se sirven al instante y
#      un refresco en background actualiza (el reset nunca bloquea).
#   3. el cache recuerda de que DB salio: si el data dir cambia (reset de
#      config, otro G360_DATA_DIR) los datos son de otra base y se descartan.
#      Sin esto, serve-stale + el thread de refresco servian KPIs de otra DB.
_CARD_INFO_CACHE: dict = {"ts": 0.0, "data": None, "path": None}
_CARD_INFO_TTL = 300.0
_CARD_INFO_REFRESH_BUSY = False


def invalidate_card_info_cache() -> None:
    # Marca vencido pero CONSERVA los datos para serve-stale.
    _CARD_INFO_CACHE["ts"] = 0.0


def refresh_card_info_async() -> None:
    """Dispara un recálculo en background si no hay uno en curso."""
    global _CARD_INFO_REFRESH_BUSY
    if _CARD_INFO_REFRESH_BUSY:
        return
    _CARD_INFO_REFRESH_BUSY = True

    def worker():
        global _CARD_INFO_REFRESH_BUSY
        try:
            _cargar_card_info()
        except Exception:
            pass
        finally:
            _CARD_INFO_REFRESH_BUSY = False

    threading.Thread(target=worker, daemon=True).start()


def _card_info(force: bool = False) -> dict:
    import time as _t

    if force:
        return _cargar_card_info()

    # Los datos cacheados son de otra base si el data dir cambio: hay que
    # descartarlos del todo. Invalidar solo marca vencido (serve-stale), asi que
    # sin este chequeo se servian KPIs de la DB anterior.
    if _CARD_INFO_CACHE["data"] is not None and _CARD_INFO_CACHE["path"] != str(db_path()):
        _CARD_INFO_CACHE["data"] = None
        _CARD_INFO_CACHE["path"] = None
        _CARD_INFO_CACHE["ts"] = 0.0

    data = _CARD_INFO_CACHE["data"]
    if data is not None:
        if (_t.time() - _CARD_INFO_CACHE["ts"]) < _CARD_INFO_TTL:
            return data
        refresh_card_info_async()
        return data
    return _cargar_card_info()


def _cargar_card_info() -> dict:
    import time as _t

    if not db_exists():
        return {
            "exists": False,
            "resumen": "Sin DB local — usa el portal de conexión para descargar",
        }
    kv = stats_cache_read()
    try:
        conn = connect(readonly=True)
        try:
            if kv.get("total_rows"):
                n_filas = int(kv["total_rows"])
            else:
                n_filas = conn.execute("SELECT COUNT(*) FROM ventas").fetchone()[0]
            # MIN/MAX por índice (idx_venta_fecha). NO consultar
            # MAX(capturado_en): sin índice obligaba a full scan (~29s frío).
            fmin = conn.execute("SELECT MIN(fecha_orig) FROM ventas").fetchone()[0]
            fmax = conn.execute("SELECT MAX(fecha_orig) FROM ventas").fetchone()[0]
        finally:
            conn.close()
    except Exception as e:
        return {"exists": True, "error": str(e), "resumen": f"DB con error: {e}"}
    if not n_filas:
        return {"exists": True, "filas": 0, "resumen": "DB local vacia"}

    meses = sorted({c["mes_ref"][:7] for c in months_coverage()})
    incompletos = incomplete_months()
    huecos: list[str] = []
    if meses:
        y, m = int(meses[0][:4]), int(meses[0][5:7])
        cur = date(y, m, 1)
        fin = date(int(meses[-1][:4]), int(meses[-1][5:7]), 1)
        set_m = set(meses)
        while cur <= fin:
            label = f"{cur.year}-{cur.month:02d}"
            if label not in set_m:
                huecos.append(label)
            cur = date(cur.year + (cur.month == 12), (cur.month % 12) + 1, 1)
    dias_ultimo = None
    if fmax:
        try:
            dias_ultimo = (date.today() - date.fromisoformat(fmax[:10])).days
        except ValueError:
            pass
    size_mb = db_path().stat().st_size / (1024 * 1024)

    # NC asociadas
    nc_count = 0
    try:
        conn = connect(readonly=True)
        try:
            nc_row = conn.execute("SELECT COUNT(*) FROM nc_asociadas").fetchone()
            nc_count = nc_row[0] if nc_row else 0
        finally:
            conn.close()
    except Exception:
        pass

    # Stats de entidades: del stats_cache cuando existe (evita los
    # COUNT(DISTINCT) caros), si no en vivo y se recalienta en background.
    clientes = 0
    vendedores = 0
    facturas = 0
    skus = 0  # COUNT(DISTINCT id_articulo) — articulos únicos
    lineas_prod = 0  # COUNT(DISTINCT LINEA) — lineas de producto únicas
    try:
        conn = connect(readonly=True)
        try:
            clientes = int(kv.get("n_clientes") or 0)
            skus = int(kv.get("n_articulos") or 0)
            lineas_prod = int(kv.get("n_lineas") or 0)
            vendedores = int(kv.get("n_vendedores") or 0)
            facturas = int(kv.get("n_facturas") or 0)
            if not clientes:
                c = conn.execute("SELECT COUNT(DISTINCT id_cliente) FROM ventas").fetchone()
                clientes = c[0] if c else 0
            if not skus:
                c = conn.execute("SELECT COUNT(DISTINCT id_articulo) FROM ventas").fetchone()
                skus = c[0] if c else 0
            if not lineas_prod:
                c = conn.execute(
                    "SELECT COUNT(DISTINCT id_linea) FROM ventas "
                    "WHERE id_linea IS NOT NULL AND LENGTH(TRIM(id_linea)) > 0"
                ).fetchone()
                lineas_prod = c[0] if c else 0
            if not vendedores:
                c = conn.execute("SELECT COUNT(DISTINCT id_vendedor) FROM ventas").fetchone()
                vendedores = c[0] if c else 0
            if not facturas:
                c = conn.execute(
                    "SELECT COUNT(DISTINCT folio_unico) FROM ventas WHERE tpo_doc LIKE 'F01%'"
                ).fetchone()
                facturas = c[0] if c else 0
        finally:
            conn.close()
    except Exception:
        pass
    if not kv.get("n_facturas"):
        refresh_stats_cache_async()

    partes = [
        f"{len(meses)} meses ({meses[0]} a {meses[-1]})" if meses else "sin meses",
        f"{n_filas:,} filas",
        f"ultimo dato {fmax}",
    ]
    if incompletos:
        partes.append(f"Incompletos: {', '.join(incompletos[:4])}")
    if huecos:
        partes.append(f"Huecos: {', '.join(huecos[:4])}")

    result = {
        "exists": True,
        "filas": n_filas,
        "fecha_min": fmin,
        "fecha_max": fmax,
        "meses": len(meses),
        "mes_primero": meses[0] if meses else None,
        "mes_ultimo": meses[-1] if meses else None,
        "size_mb": round(size_mb, 1),
        "dias_desde_ultimo": dias_ultimo,
        "incompletos": incompletos,
        "huecos": huecos,
        "nc_asociadas": nc_count,
        "clientes": clientes,
        "vendedores": vendedores,
        "facturas": facturas,
        "skus": skus,
        "lineas_prod": lineas_prod,
        "resumen": " · ".join(partes),
    }
    _CARD_INFO_CACHE["path"] = str(db_path())
    _CARD_INFO_CACHE["ts"] = _t.time()
    _CARD_INFO_CACHE["data"] = result
    return result


def db_card_info() -> dict:
    """Datos estructurados para la card de salud de la DB local en la UI."""
    return _card_info()


def nc_nd_huerfanas(conn: sqlite3.Connection) -> int:
    """NC/ND cuya factura referenciada NO existe en la DB (fuera del rango descargado).
    Se sanan solas al extender el rango hacia atras; se auditan tras cada captura."""
    row = conn.execute(
        "SELECT COUNT(*) FROM ventas n "
        "WHERE n.tipo_operacion IN ('devolucion','ajuste_valor','nota_debito') "
        "AND COALESCE(n.factura_ref_serie,'') != '' AND COALESCE(n.factura_ref_nro,'') != '' "
        "AND NOT EXISTS ("
        "  SELECT 1 FROM ventas f WHERE f.tpo_doc LIKE 'F01%'"
        "  AND f.serie_doc = n.factura_ref_serie AND f.nro_doc = n.factura_ref_nro)"
    ).fetchone()
    return row[0]


def record_month_checksum(conn: sqlite3.Connection, mes_ref: str) -> tuple[int, float]:
    """Guarda el checksum del mes tras cada captura (filas + S/ totales).
    Cuenta rows mensuales (mes_ref=label) Y diarios (mes_ref=label-*) —
    los meses capturados por fallback diario tienen filas con label diario.
    Es la 'garantia' de la primera pasada: permite detectar datos parciales
    o corrompidos sin re-descargar."""
    row = conn.execute(
        "SELECT COUNT(*), ROUND(COALESCE(SUM(soles),0),2) FROM ventas "
        "WHERE mes_ref = ? OR mes_ref LIKE ?",
        (mes_ref, mes_ref + "-%"),
    ).fetchone()
    n, tot = int(row[0]), float(row[1])
    with write_txn(conn):
        conn.execute(
            "INSERT INTO mes_checksums (mes_ref, checksum, total_filas, total_soles, calculado_en) "
            "VALUES (?,?,?,?,datetime('now')) "
            "ON CONFLICT(mes_ref) DO UPDATE SET checksum=excluded.checksum, "
            "total_filas=excluded.total_filas, total_soles=excluded.total_soles, "
            "calculado_en=excluded.calculado_en",
            (mes_ref, f"{n}:{tot}", n, tot),
        )
    return n, tot


def record_day_checksum(conn: sqlite3.Connection, dia: str) -> tuple[int, float]:
    """Guarda el checksum del dia tras cada captura (filas + S/ totales).
    Permite detectar si un dia cambio desde la ultima descarga."""
    row = conn.execute(
        "SELECT COUNT(*), ROUND(COALESCE(SUM(soles),0),2) FROM ventas "
        "WHERE substr(fecha_orig, 1, 10) = ?",
        (dia,),
    ).fetchone()
    n, tot = int(row[0]), float(row[1])
    checksum = f"{n}:{tot}"
    with write_txn(conn):
        conn.execute(
            "INSERT INTO day_checksums (dia, total_filas, total_soles, checksum, calculado_en) "
            "VALUES (?,?,?,?,datetime('now')) "
            "ON CONFLICT(dia) DO UPDATE SET total_filas=excluded.total_filas, "
            "total_soles=excluded.total_soles, checksum=excluded.checksum, "
            "calculado_en=excluded.calculado_en",
            (dia, n, tot, checksum),
        )
    return n, tot


def day_checksums_rango(conn: sqlite3.Connection, desde: str, hasta: str) -> dict[str, dict]:
    """Lee day_checksums en [desde, hasta]. Base del chequeo de superset (F5).

    Returns {dia: {filas, soles}}. Vive en historial.db: es metadata del
    archivo, no estado viajero (se reconstruye de ventas, nunca se transporta).
    """
    out = {}
    try:
        filas = conn.execute(
            "SELECT dia, total_filas, total_soles FROM day_checksums WHERE dia >= ? AND dia <= ?",
            (desde, hasta),
        ).fetchall()
    except sqlite3.Error:
        return out
    for dia, n, tot in filas:
        out[dia] = {"filas": int(n), "soles": float(tot)}
    return out


def record_day_capture(
    conn: sqlite3.Connection, dia: str, conn_estado: sqlite3.Connection
) -> tuple[int, float]:
    """Anota en ``day_state`` (sidecar) que el día se descargó (provisional).

    Lee los conteos de ``ventas`` (conn principal) y escribe el watermark en
    el sidecar (conn_estado). Un día ya 'cerrado' conserva su estado.
    """
    row = conn.execute(
        "SELECT COUNT(*), ROUND(COALESCE(SUM(soles), 0), 2) FROM ventas "
        "WHERE fecha_orig >= ? AND fecha_orig < date(?, '+1 day')",
        (dia, dia),
    ).fetchone()
    n, tot = int(row[0]), float(row[1])
    with write_txn(conn_estado):
        conn_estado.execute(
            "INSERT INTO day_state (dia, ultima_captura, filas, soles, estado) "
            "VALUES (?, datetime('now'), ?, ?, 'provisional') "
            "ON CONFLICT(dia) DO UPDATE SET ultima_captura = excluded.ultima_captura, "
            "filas = excluded.filas, soles = excluded.soles, "
            "estado = CASE WHEN day_state.estado = 'cerrado' "
            "THEN 'cerrado' ELSE 'provisional' END",
            (dia, n, tot),
        )
    return n, tot


def marcar_dias_cerrados(
    conn_estado: sqlite3.Connection, dias_inmadurez: int = 15, hoy: str | None = None
) -> int:
    """Pasa a 'cerrado' los días provisionales más viejos que el umbral.

    Opera sobre el sidecar. Un día solo se considera final cuando lleva
    ``dias_inmadurez`` días cerrado el calendario: la intranet puede devolver
    documentos con fecha vieja ingresados tarde, así que 'ayer' nunca está completo.
    """
    hoy = hoy or date.today().isoformat()
    with write_txn(conn_estado):
        cur = conn_estado.execute(
            "UPDATE day_state SET estado = 'cerrado', cerrado_en = datetime('now') "
            "WHERE estado = 'provisional' AND dia < date(?, '-' || ? || ' days')",
            (hoy, int(dias_inmadurez)),
        )
        return cur.rowcount


def dias_sin_cerrar(conn_estado: sqlite3.Connection, desde: str, hasta: str) -> list[str]:
    """Días de [desde, hasta] que no están 'cerrado' (hay que descargarlos)."""
    cerrados = {
        r[0]
        for r in conn_estado.execute(
            "SELECT dia FROM day_state WHERE dia >= ? AND dia <= ? AND estado = 'cerrado'",
            (desde, hasta),
        ).fetchall()
    }
    out, cur = [], date.fromisoformat(desde)
    fin = date.fromisoformat(hasta)
    while cur <= fin:
        iso = cur.isoformat()
        if iso not in cerrados:
            out.append(iso)
        cur += timedelta(days=1)
    return out


def resumen_captura(conn_estado: sqlite3.Connection | None = None) -> dict:
    """Estado de la captura para mostrar en UI: hasta dónde estoy y qué falta.

    Lee ``day_state`` del sidecar. Sin sidecar (fresh install) devuelve vacío.
    """
    cerrar = conn_estado is None
    if conn_estado is None:
        if not estado_exists():
            return {
                "completado_hasta": None,
                "abiertos": [],
                "n_abiertos": 0,
                "ultima_captura": None,
            }
        conn_estado = connect_estado(readonly=True)
    try:
        try:
            filas = conn_estado.execute(
                "SELECT dia, filas, soles, estado, ultima_captura FROM day_state ORDER BY dia"
            ).fetchall()
        except sqlite3.Error:
            return {
                "completado_hasta": None,
                "abiertos": [],
                "n_abiertos": 0,
                "ultima_captura": None,
            }
        cerrados = [r[0] for r in filas if r[3] == "cerrado"]
        abiertos = [
            {"dia": r[0], "filas": r[1], "soles": r[2], "ultima_captura": r[4]}
            for r in filas
            if r[3] != "cerrado"
        ]
        ult = max((r[4] for r in filas if r[4]), default=None)
        return {
            "completado_hasta": max(cerrados) if cerrados else None,
            "abiertos": abiertos,
            "n_abiertos": len(abiertos),
            "ultima_captura": ult,
        }
    finally:
        if cerrar:
            conn_estado.close()


def fecha_max_segura(conn: sqlite3.Connection) -> str | None:
    """MAX(fecha_orig) topado a hoy: una fila con fecha futura o errónea
    no debe congelar la captura incremental."""
    row = conn.execute(
        "SELECT MAX(fecha_orig) FROM ventas WHERE fecha_orig IS NOT NULL AND fecha_orig != ''"
    ).fetchone()
    fmax = (row[0] or "")[:10] if row else ""
    if not fmax:
        return None
    return min(fmax, date.today().isoformat())


def contar_duplicados_cruzados(conn: sqlite3.Connection, desde: str, hasta: str) -> int:
    """Pares (folio_unico, id_articulo) que viven en 2+ mes_ref en el rango.

    Solo lectura y solo cuenta: reemplazar el dedup destructivo por detección.
    Un par que cruza labels es o un solapamiento mensual/diario (normal) o una
    línea legítima repetida (no borrar jamás sin revisar).
    """
    row = conn.execute(
        "SELECT COUNT(*) FROM (SELECT folio_unico, id_articulo FROM ventas "
        "WHERE folio_unico <> '' AND fecha_orig >= ? AND fecha_orig <= ? "
        "GROUP BY 1, 2 HAVING COUNT(DISTINCT mes_ref) > 1)",
        (desde, hasta),
    ).fetchone()
    return int(row[0]) if row else 0


def oc_alias_upsert(
    conn_estado: sqlite3.Connection, id_cliente: str, oc_raw: str, oc_norm: str
) -> list[str]:
    """Registra el mapeo (cliente, crudo → canónico) en el sidecar.

    Devuelve otros crudos del mismo cliente que ya apuntan al mismo canónico
    (colisión). La colisión significa que quitar ceros fusionaría dos formas
    distintas; el llamador debe entonces NO fusionar y marcar 'pendiente'.
    """
    with write_txn(conn_estado):
        conn_estado.execute(
            "INSERT INTO oc_alias (id_cliente, oc_raw, oc_norm) VALUES (?,?,?) "
            "ON CONFLICT(id_cliente, oc_raw) DO UPDATE SET oc_norm = excluded.oc_norm",
            (id_cliente, oc_raw, oc_norm),
        )
        otros = [
            r[0]
            for r in conn_estado.execute(
                "SELECT oc_raw FROM oc_alias WHERE id_cliente = ? AND oc_norm = ? AND oc_raw <> ?",
                (id_cliente, oc_norm, oc_raw),
            ).fetchall()
        ]
        if otros:
            conn_estado.execute(
                "UPDATE oc_alias SET estado = 'pendiente' WHERE id_cliente = ? "
                "AND oc_norm = ? AND estado = 'auto'",
                (id_cliente, oc_norm),
            )
    return otros


def ocs_pendientes(conn: sqlite3.Connection, conn_estado: sqlite3.Connection) -> list[dict]:
    """Mapeos en colisión sin revisar, agrupados por (cliente, canónico).

    Lee el alias del sidecar y cuenta las filas afectadas en ventas (conn).
    """
    out = []
    for cid, norm in conn_estado.execute(
        "SELECT id_cliente, oc_norm FROM oc_alias WHERE estado = 'pendiente' "
        "GROUP BY 1, 2 ORDER BY 1, 2"
    ).fetchall():
        raws = [
            r[0]
            for r in conn_estado.execute(
                "SELECT oc_raw FROM oc_alias WHERE id_cliente = ? AND oc_norm = ? ORDER BY oc_raw",
                (cid, norm),
            ).fetchall()
        ]
        n = conn.execute(
            "SELECT COUNT(*) FROM ventas WHERE id_cliente = ? AND (ord_compra = ? "
            "OR ord_compra IN (%s))" % ",".join("?" * len(raws)),
            (cid, norm, *raws),
        ).fetchone()[0]
        out.append({"id_cliente": cid, "norm": norm, "raws": raws, "filas": int(n)})
    return out


def oc_resolver(
    conn: sqlite3.Connection,
    id_cliente: str,
    oc_norm: str,
    accion: str,
    conn_estado: sqlite3.Connection,
) -> int:
    """Resuelve una colisión pendiente: 'confirmado' fusiona a canónico,
    'separado' deja cada forma cruda. Retorna filas actualizadas en ventas.

    La decisión queda en el sidecar (conn_estado); el UPDATE va a ventas (conn).
    """
    if accion not in ("confirmado", "separado"):
        raise ValueError("accion debe ser 'confirmado' o 'separado'")
    raws = [
        r[0]
        for r in conn_estado.execute(
            "SELECT oc_raw FROM oc_alias WHERE id_cliente = ? AND oc_norm = ? "
            "AND estado = 'pendiente'",
            (id_cliente, oc_norm),
        ).fetchall()
    ]
    if not raws:
        return 0
    n = 0
    if accion == "confirmado":
        # Incluye el propio canónico: un archivo anterior pudo escribirlo
        # antes de que se descubriera la colisión.
        with write_txn(conn):
            cur = conn.execute(
                "UPDATE ventas SET ord_compra = ? WHERE id_cliente = ? "
                "AND (ord_compra = ? OR ord_compra IN (%s))" % ",".join("?" * len(raws)),
                (oc_norm, id_cliente, oc_norm, *raws),
            )
            n = cur.rowcount
    with write_txn(conn_estado):
        conn_estado.execute(
            "UPDATE oc_alias SET estado = ?, revisado_en = datetime('now') "
            "WHERE id_cliente = ? AND oc_norm = ?",
            (accion, id_cliente, oc_norm),
        )
    return int(n)


# ── Campos críticos: la red de seguridad (lección O/C) ─────────────────────
# Un campo que una captura deja de traer no avisa: el filtro devuelve 0
# filas y se lee como "no hay datos". Este auditor mide cobertura e índice
# de cada campo crítico para que la UI lo muestre antes de que importe.

#: (campo, etiqueta, índice esperado, cobertura mínima normal)
CAMPOS_CRITICOS: tuple[tuple[str, str, str, float], ...] = (
    ("ord_compra", "Orden de compra", "idx_venta_oc", 0.50),
    ("cod_sucursal", "Sucursal (código)", "idx_venta_sucursal", 0.50),
    ("nom_sucursal", "Sucursal (nombre)", "idx_venta_nom_sucursal", 0.95),
    ("division", "División", "idx_venta_division", 0.95),
    ("fecha_venc", "Vencimiento", "idx_venta_venc", 0.95),
    ("nom_condicion_pago", "Condición de pago", "idx_venta_condicion", 0.95),
)


def auditar_campos_criticos(conn: sqlite3.Connection) -> list[dict]:
    """Cobertura e índice de cada campo crítico. Solo lectura.

    Veredicto por campo: 'ok' | 'sin_indice' | 'degradado' | 'perdido'.
    'perdido' = la columna ni existe (el gate de contrato ya habría
    bloqueado, esto lo nombra). Costoso en frío (~6 escaneos): llamar
    desde un hilo, nunca del hilo UI.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(ventas)")}
    # Índices sobre ventas con su primera columna (la usable en igualdad).
    primera_col: dict[str, str] = {}
    for (iname,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'ventas'"
    ).fetchall():
        info = conn.execute(f"PRAGMA index_info({iname})").fetchall()
        if info:
            primera_col[iname] = info[0][2]
    total = conn.execute("SELECT COUNT(*) FROM ventas").fetchone()[0]
    out = []
    for campo, etiqueta, indice, umbral in CAMPOS_CRITICOS:
        if campo not in cols:
            out.append(
                {
                    "campo": campo,
                    "etiqueta": etiqueta,
                    "filas_informadas": 0,
                    "total": total,
                    "cobertura": 0.0,
                    "indice": indice,
                    "tiene_indice": False,
                    "umbral": umbral,
                    "veredicto": "perdido",
                }
            )
            continue
        n = conn.execute(
            f"SELECT COUNT(*) FROM ventas WHERE {campo} IS NOT NULL "
            f"AND TRIM(CAST({campo} AS TEXT)) <> ''"
        ).fetchone()[0]
        cob = (n / total) if total else 0.0
        tiene = primera_col.get(indice) == campo
        if cob < umbral:
            veredicto = "degradado"
        elif not tiene:
            veredicto = "sin_indice"
        else:
            veredicto = "ok"
        out.append(
            {
                "campo": campo,
                "etiqueta": etiqueta,
                "filas_informadas": int(n),
                "total": total,
                "cobertura": round(cob, 4),
                "indice": indice,
                "tiene_indice": bool(tiene),
                "umbral": umbral,
                "veredicto": veredicto,
            }
        )
    return out


def verify_integrity(conn: sqlite3.Connection) -> tuple[bool, list[tuple], list[tuple]]:
    """Revalida los checksums guardados vs el contenido real (agregando dias
    del mismo mes: mes_ref=label o label-*). Devuelve (ok, drifts, sin_checksum):
    drifts=(mes, filas_esperadas, filas_reales, soles).

    Compara contra ``total_filas``/``total_soles`` y no contra la cadena de
    ``checksum``: los meses que bajan del productor traen su formato propio
    (``printf('%08x-%08x-%08x-%08x', ...)``) y los que escribe
    ``record_month_checksum`` traen ``filas:soles``. Validar el string hacia que
    el productor marcara como drift un mes con las cifras correctas.
    """
    stored = {
        r[0]: (int(r[1]), float(r[2]))
        for r in conn.execute("SELECT mes_ref, total_filas, total_soles FROM mes_checksums")
    }
    drifts: list[tuple] = []
    sin: list[tuple] = []
    # Agregar por mes: label mensual o dias label-*

    reales: dict[str, tuple[int, float]] = {}
    for r in conn.execute(
        "SELECT mes_ref, COUNT(*), ROUND(COALESCE(SUM(soles),0),2) FROM ventas GROUP BY mes_ref"
    ):
        label = str(r[0])
        mes = label[:7] if len(label) == 10 else label
        n, tot = int(r[1]), float(r[2])
        if mes in reales:
            pn, pt = reales[mes]
            reales[mes] = (pn + n, round(pt + tot, 2))
        else:
            reales[mes] = (n, tot)
    for mes, (n, tot) in reales.items():
        if mes not in stored:
            sin.append((mes, n, tot))
            continue
        n_esperadas, tot_esperados = stored[mes]
        if n_esperadas != n or abs(tot_esperados - tot) > 0.01:
            drifts.append((mes, n_esperadas, n, tot))
    return (len(drifts) == 0), drifts, sin


def reset_connections() -> None:
    """Cierra las conexiones cacheadas del hilo actual (SOLO del hilo actual:
    threading.local). NO usar reemplazo de archivo mientras otros hilos
    tengan la DB abierta en Windows (WinError 32): import_db copia en
    caliente via backup API en vez de borrar/reemplazar el archivo."""
    conn_rw = getattr(_local, "conn_rw", None)
    if conn_rw:
        try:
            conn_rw[0].close()
        except Exception:
            pass
        _local.conn_rw = None
    conn_ro = getattr(_local, "conn_ro", None)
    if conn_ro:
        try:
            conn_ro[0].close()
        except Exception:
            pass
        _local.conn_ro = None


# ── Fechas por defecto (primera carga) ──────────────────────────────────────

FIRST_LOAD_FROM = date(2024, 1, 1)

# ── Backup / import (multi-PC): extraido a ventas_db_backup.py ─────────────
from src.core.ventas_db_backup import (  # noqa: F401
    backup_dir,
    backup_db,
    import_db,
    next_month_after_last,
)
