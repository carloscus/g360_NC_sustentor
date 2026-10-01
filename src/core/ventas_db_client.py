"""Cliente de datos local SQLite — reemplazo 1:1 de SupabaseVentasClient.

Misma interfaz publica: fetch_historial, fetch_facturas_disponibles, test_connection,
fetch_vendedores, fetch_clientes, fetch_facturas_cliente, fetch_pedidos_cliente,
fetch_ordenes_cliente, to_expediente_historial.
Lee %APPDATA%/g360-erp-nc-sustentor/data/historial.db en modo read-only.
Filtros de fecha empujados a SQL (antes se filtraban en pandas). NC_ASOCIADAS
sale de la tabla materializada nc_asociadas (populada tras cada captura).
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

from src.core import ventas_db

log = logging.getLogger(__name__)

# Mapeo columna DB -> columna historial (identico al de supabase_client.py)
VENTAS_TO_HISTORIAL = {
    "id_articulo": "CODIGO",
    "nom_articulo": "ARTICULO",
    "id_linea": "COD_LINEA",
    "nom_linea": "LINEA",
    "id_grupo": "COD_GRUPO",
    "nom_grupo": "GRUPO",
    "id_tipo": "COD_TIPO",
    "nom_tipo": "TIPO",
    "id_familia": "COD_FAMILIA",
    "nom_familia": "FAMILIA",
    "id_cliente": "COD_CLIENTE",
    "nom_cliente": "CLIENTE",
    "doc_cliente": "DOC_CLIENTE",
    "tpo_doc": "TIPO_DOC",
    "serie_doc": "SERIE",
    "nro_doc": "NUMERO",
    "referencia": "REFERENCIA",
    "moneda": "MONEDA",
    "cantidad": "CANTIDAD",
    "cantidad_fae": "CANTIDAD_FAE",
    "soles": "SOLES",
    "dolares": "DOLARES",
    "precio_unitario": "PRECIO_UNITARIO",
    "anho": "ANHO",
    "mes": "MES",
    "fecha_orig": "FECHA",
    "fecha_ref": "FECHA_REF",
    "fecha_venc": "FECHA_VENC",
    "cod_sucursal": "COD_SUCURSAL",
    "nom_sucursal": "SUCURSAL",
    "departamento": "NOM_DEPARTAMENTO",
    "provincia": "NOM_PROVINCIA",
    "distrito": "NOM_DISTRITO",
    "id_vendedor": "COD_VENDEDOR",
    "nom_vendedor": "VENDEDOR",
    "id_pedido": "ID_PEDIDO",
    "ord_compra": "ORDEN_COMPRA",
    "tipo_operacion": "TIPO_OPERACION",
    "folio_unico": "FOLIO_UNICO",
}


def _allowlist_sql() -> str:
    """Predicado de líneas validadas (mismo criterio que ``fetch_historial``).

    - Facturas/boletas: solo líneas con ``id_linea`` validada.
    - NC/NDB: se conservan si SU línea es validada O su factura referenciada
      tiene al menos una línea validada (orphan-safe).
    """
    lin = ventas_db.active_line_sql("id_linea")
    lin_f = ventas_db.active_line_sql("f.id_linea")
    return (
        "((tpo_doc LIKE 'F%' OR tpo_doc LIKE 'B%' OR tpo_doc LIKE 'BO%')"
        f" AND {lin})"
        " OR (UPPER(tpo_doc) IN ('NCR','NDB','NC','ND') AND ("
        f"{lin}"
        " OR EXISTS (SELECT 1 FROM ventas f"
        " WHERE f.serie_doc = ventas.factura_ref_serie"
        " AND f.nro_doc = ventas.factura_ref_nro"
        " AND (f.tpo_doc LIKE 'F%' OR f.tpo_doc LIKE 'B%')"
        f" AND {lin_f})))"
    )


# ── depósitos componentizados del reporte de compras ──────────────────
# Facturas/boletas y notas por predicado de documento.
_FYB = "substr(tpo_doc,1,1) IN ('F','B')"
_NOTA = "UPPER(tpo_doc) IN ('NCR','NDB','NC','ND')"
# Línea canónica: el ERP a veces prefija el código con la sucursal
# ('0101' = suc 01 + línea 01) y a veces no ('01'); la identidad
# comercial es el sufijo de 2 (verificado en datos 2026-09-26: 73
# códigos → 53 sufijos, 0 colisiones de nombre; dim_linea usa pelados).
# Mismo criterio que el allowlist (active_line_sql).
_LIN_CANON = "UPPER(SUBSTR(id_linea,-2))"
# Vendedor canónico: idem con sufijo de 3 ('01177' y '177' son el mismo;
# verificado: sin colisiones de nombre). Rige dropdown y filtros de
# cartera para no partir vendedores por época del archivo.


def _vendedor_canon(vid: str) -> str:
    """Sufijo canónico de vendedor en Python ('01177'→'177')."""
    return str(vid or "").strip().upper()[-3:]


# Unidades físicas: solo venta y devolución mueven stock; descuento y NDB
# aportan 0 (sus soles sí suman).
_FISICA = (
    "SUM(CASE WHEN COALESCE(tipo_operacion,'') IN"
    " ('ajuste_valor','nota_debito') THEN 0 ELSE cantidad END)"
)
# Descuento "económico": ajuste_valor + facturas/boletas con soles<0
# (descuento embebido en factura; lo físico no cambia). La bruta excluye
# esas filas para que BRUTA+DEV+DESC+NDB = NETA se mantenga.
_BRUTA = f"SUM(CASE WHEN {_FYB} AND soles >= 0 THEN soles ELSE 0 END)"
_DEV_S = "SUM(CASE WHEN tipo_operacion = 'devolucion' THEN soles ELSE 0 END)"
_DESC_S = (
    f"SUM(CASE WHEN tipo_operacion = 'ajuste_valor'"
    f" OR ({_FYB} AND soles < 0) THEN soles ELSE 0 END)"
)
_NDB_S = "SUM(CASE WHEN tipo_operacion = 'nota_debito' THEN soles ELSE 0 END)"
# Factura referenciada por una nota existe en la DB (mismo cliente).
_FACTURA_EXISTE = (
    "EXISTS (SELECT 1 FROM ventas f"
    " WHERE f.id_cliente = ventas.id_cliente"
    " AND (f.tpo_doc LIKE 'F%' OR f.tpo_doc LIKE 'B%')"
    " AND f.serie_doc = ventas.factura_ref_serie"
    " AND f.nro_doc = ventas.factura_ref_nro)"
)
_DOC_FYB = "substr(tpo_doc,1,1) || serie_doc || '-' || nro_doc"
_DOC_NOTA = (
    "(CASE WHEN serie_doc LIKE substr(tpo_doc,1,1) || '%' THEN '' "
    "ELSE substr(tpo_doc,1,1) END) || serie_doc || '-' || nro_doc"
)
_REF_NOTA = "'F' || factura_ref_serie || '-' || factura_ref_nro"


class VentasDbClient:
    """Lectura read-only del SQLite local del sustentor."""

    def __init__(self, db_file: Optional[str] = None):
        self._db_file = db_file

    def _read_conn(self):
        if self._db_file:
            import sqlite3

            conn = sqlite3.connect(f"file:{self._db_file}?mode=ro", uri=True, timeout=30)
            conn.row_factory = None
            return conn
        return ventas_db.get_read_conn()

    # ── Mapeo a formato historial ────────────────────────────────────

    def _map_to_historial(self, df: pd.DataFrame) -> pd.DataFrame:
        """Port de _map_to_historial (supabase_client.py): derivadas UI/pipeline."""
        if df.empty:
            return df
        df = df.rename(columns=VENTAS_TO_HISTORIAL)
        df = df.rename(
            columns={
                "factura_ref_serie": "FACTURA_REF_SERIE",
                "factura_ref_nro": "FACTURA_REF_NRO",
            }
        )
        if "FECHA" in df.columns:
            df["FECHA"] = pd.to_datetime(df["FECHA"], errors="coerce")
        for col in ["CANTIDAD", "CANTIDAD_FAE", "SOLES", "DOLARES", "PRECIO_UNITARIO"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
        if "FECHA" in df.columns:
            df["ANHO"] = df["FECHA"].dt.year
            df["MES"] = df["FECHA"].dt.month
        if all(c in df.columns for c in ("TIPO_DOC", "SERIE", "NUMERO")):
            df["DOC_ID"] = df.apply(
                lambda r: (
                    f"{str(r.get('TIPO_DOC', ''))[:1]}{r.get('SERIE', '')}-{r.get('NUMERO', '')}"
                ),
                axis=1,
            )
        else:
            df["DOC_ID"] = ""
        # TIPO_CLASE (el pipeline de captura ya clasifica en tipo_operacion).
        # nota_debito = incremento de valor -> cargo: afecta valor, no cantidad
        # (igual que clasificar_nota en detector.py; antes quedaba sin_impacto
        # y el auditor/resumen la ignoraban por completo).
        df["TIPO_CLASE"] = (
            df["TIPO_OPERACION"]
            .map(
                {
                    "venta": "factura",
                    "devolucion": "devolucion",
                    "ajuste_valor": "descuento",
                    "nota_debito": "cargo",
                }
            )
            .fillna("sin_impacto")
        )

        # FACTURA_REF desde campos pre-calculados en captura
        def _build_factura_ref(r):
            serie = str(r.get("FACTURA_REF_SERIE", "") or "").strip()
            nro = str(r.get("FACTURA_REF_NRO", "") or "").strip()
            if serie and nro:
                return f"F{serie}-{nro}"
            ref = str(r.get("REFERENCIA", "") or "").strip()
            if ref and ref not in ("", "nan", "None"):
                from src.core.detector import _parsear_referencia_factura

                return _parsear_referencia_factura(ref)
            return ""

        df["FACTURA_REF"] = df.apply(_build_factura_ref, axis=1)
        df["AFECTA_CANTIDAD"] = df["TIPO_CLASE"].isin(["devolucion"])
        df["AFECTA_VALOR"] = df["TIPO_CLASE"].isin(["factura", "devolucion", "descuento", "cargo"])
        return df

    def _enriquecer_nc_asociadas(self, conn, df: pd.DataFrame) -> pd.DataFrame:
        """NC_ASOCIADAS desde la tabla materializada nc_asociadas."""
        if df.empty or "DOC_ID" not in df.columns:
            return df
        facturas = sorted(
            {
                str(x).strip()
                for x in df.loc[df["TIPO_CLASE"] != "factura", "FACTURA_REF"]
                if str(x).strip()
            }
            | {
                str(x).strip()
                for x in df.loc[df["TIPO_CLASE"] == "factura", "DOC_ID"]
                if str(x).strip()
            }
        )
        if not facturas:
            df["NC_ASOCIADAS"] = [[] for _ in range(len(df))]
            return df
        try:
            ph = ",".join("?" * len(facturas))
            rows = conn.execute(
                f"SELECT factura_doc_id, nc_doc_id FROM nc_asociadas WHERE factura_doc_id IN ({ph})",
                facturas,
            ).fetchall()
        except Exception:
            # Tabla ausente (DB muy vieja) — degradar a vacio
            df["NC_ASOCIADAS"] = [[] for _ in range(len(df))]
            return df
        mapping: dict[str, list[str]] = {}
        for fac_doc, nc_doc in rows:
            mapping.setdefault(fac_doc, []).append(nc_doc)
        df["NC_ASOCIADAS"] = (
            df["DOC_ID"].map(mapping).apply(lambda x: x if isinstance(x, list) else [])
        )
        return df

    # ── API publica (misma firma que SupabaseVentasClient) ───────────

    def fetch_historial(
        self,
        *,
        id_cliente: Optional[str] = None,
        id_articulo: Optional[str] = None,
        mes_ref: Optional[str] = None,
        serie_doc: Optional[str] = None,
        nro_doc: Optional[str] = None,
        fecha_desde: Optional[str] = None,
        fecha_hasta: Optional[str] = None,
        id_pedidos: Optional[list[str]] = None,
        ordenes: Optional[list[str]] = None,
        # Filtros por campos críticos (habilitados, sin UI todavía): todos
        # None por defecto = comportamiento idéntico al actual. Cada uno
        # tiene su índice (idx_venta_division/condicion/sucursal/venc).
        divisiones: Optional[list[str]] = None,
        condiciones_pago: Optional[list[str]] = None,
        sucursales: Optional[list[str]] = None,
        fecha_venc_desde: Optional[str] = None,
        fecha_venc_hasta: Optional[str] = None,
        # Tienda real (versión supermercados/TAI LOY): el par
        # (cliente, sucursal) es la unidad (el código solo se repite
        # entre clientes). Todos None = sin filtrar.
        sucursal_cliente: Optional[list[tuple[str, str]]] = None,
        nombres_sucursal: Optional[list[str]] = None,
        id_ubigeos: Optional[list[str]] = None,
        distritos: Optional[list[str]] = None,
        limit: int = 50000,
        offset: int = 0,
        use_cache: bool = True,
        solo_lineas_activas: bool = True,
    ) -> pd.DataFrame:
        """Historial filtrado. Fechas filtradas EN SQL (parametrizado).

        solo_lineas_activas: restrictivo al allowlist de lineas de producto
        (ventas_db.allowed_lines, default 24 lineas). Aplica a:
          - Facturas/boletas (tpo_doc F*/B*): solo lineas con id_linea activa.
          - NC/NDB: si SU linea es activa O la factura referenciada tiene al
            menos una linea activa (orphan-safe: NC cuya factura no esta en
            la DB se conserva si su propia linea es activa).
        id_pedidos: si se indica, filtra lineas cuyo id_pedido este en la
        lista. Restringe a lineas de factura/boleta (tpo_doc F*/B%): un
        pedido puede tener guias/notas/pendientes que no interesan aqui.
        ordenes: idem para ord_compra (OC del cliente, F2).
        divisiones/condiciones_pago/sucursales: idem para division,
        nom_condicion_pago y cod_sucursal (listas exactas).
        fecha_venc_desde/fecha_venc_hasta: rango sobre fecha_venc
        (antigüedad de cartera). Todos None = sin filtrar.
        sucursal_cliente: pares (id_cliente, cod_sucursal) = tienda real
        (usa idx_venta_cliente_sucursal). nombres_sucursal: por nombre
        normalizado (UPPER+TRIM, sin índice: solo listas cortas).
        id_ubigeos/distritos: dimensión geográfica exacta.
        """
        cols = ", ".join(VENTAS_TO_HISTORIAL.keys()) + ", factura_ref_serie, factura_ref_nro"
        where = []
        params: list = []
        if solo_lineas_activas:
            lin = ventas_db.active_line_sql("id_linea")
            lin_f = ventas_db.active_line_sql("f.id_linea")
            where.append(
                "("
                "((tpo_doc LIKE 'F%' OR tpo_doc LIKE 'B%' OR tpo_doc LIKE 'BO%')"
                f" AND {lin})"
                " OR (UPPER(tpo_doc) IN ('NCR','NDB','NC','ND') AND ("
                f"{lin}"
                " OR EXISTS (SELECT 1 FROM ventas f"
                " WHERE f.serie_doc = ventas.factura_ref_serie"
                " AND f.nro_doc = ventas.factura_ref_nro"
                " AND (f.tpo_doc LIKE 'F%' OR f.tpo_doc LIKE 'B%')"
                f" AND {lin_f}))))"
            )
        if id_cliente:
            where.append("id_cliente = ?")
            params.append(id_cliente)
        if id_articulo:
            where.append("id_articulo = ?")
            params.append(id_articulo)
        if mes_ref:
            where.append("mes_ref = ?")
            params.append(mes_ref)
        if serie_doc:
            where.append("serie_doc = ?")
            params.append(serie_doc)
        if nro_doc:
            where.append("nro_doc = ?")
            params.append(nro_doc)
        if fecha_desde:
            where.append("fecha_orig >= ?")
            params.append(fecha_desde)
        if fecha_hasta:
            where.append("fecha_orig <= ?")
            params.append(fecha_hasta)
        if id_pedidos:
            peds = sorted({str(p).strip() for p in id_pedidos if str(p).strip()})
            if peds:
                ph = ",".join("?" * len(peds))
                where.append(f"(id_pedido IN ({ph}) AND (tpo_doc LIKE 'F%' OR tpo_doc LIKE 'B%'))")
                params.extend(peds)
            else:
                where.append("1 = 0")
        if ordenes:
            ocs = sorted({str(o).strip() for o in ordenes if str(o).strip()})
            if ocs:
                oh = ",".join("?" * len(ocs))
                # Incluir facturas con OC directa O notas que referencian facturas con esa OC
                where.append(
                    "("
                    "(ord_compra IN (" + oh + ") AND (tpo_doc LIKE 'F%' OR tpo_doc LIKE 'B%'))"
                    " OR (UPPER(tpo_doc) IN ('NCR','NDB','NC','ND') AND EXISTS ("
                    "  SELECT 1 FROM ventas f"
                    "  WHERE f.serie_doc = ventas.factura_ref_serie"
                    "    AND f.nro_doc = ventas.factura_ref_nro"
                    "    AND f.ord_compra IN (" + oh + ")"
                    "))"
                    ")"
                )
                params.extend(
                    ocs * 2
                )  # dos veces: una para la condicion directa, otra para el EXISTS
            else:
                where.append("1 = 0")
        if divisiones:
            divs = sorted({str(d).strip() for d in divisiones if str(d).strip()})
            if divs:
                dh = ",".join("?" * len(divs))
                where.append(f"division IN ({dh})")
                params.extend(divs)
            else:
                where.append("1 = 0")
        if condiciones_pago:
            conds = sorted({str(x).strip() for x in condiciones_pago if str(x).strip()})
            if conds:
                ch = ",".join("?" * len(conds))
                where.append(f"nom_condicion_pago IN ({ch})")
                params.extend(conds)
            else:
                where.append("1 = 0")
        if sucursales:
            sucs = sorted({str(x).strip() for x in sucursales if str(x).strip()})
            if sucs:
                sh = ",".join("?" * len(sucs))
                where.append(f"cod_sucursal IN ({sh})")
                params.extend(sucs)
            else:
                where.append("1 = 0")
        if fecha_venc_desde:
            where.append("fecha_venc >= ?")
            params.append(fecha_venc_desde)
        if fecha_venc_hasta:
            where.append("fecha_venc <= ?")
            params.append(fecha_venc_hasta)
        if sucursal_cliente:
            pares = sorted(
                {
                    (str(c).strip(), str(s).strip())
                    for c, s in sucursal_cliente
                    if str(c).strip() or str(s).strip()
                }
            )
            if pares:
                ph = ",".join(["(?,?)"] * len(pares))
                where.append(f"(id_cliente, cod_sucursal) IN ({ph})")
                for c, s in pares:
                    params.extend([c, s])
            else:
                where.append("1 = 0")
        if nombres_sucursal:
            noms = sorted({str(x).strip().upper() for x in nombres_sucursal if str(x).strip()})
            if noms:
                nh = ",".join("?" * len(noms))
                where.append(f"UPPER(TRIM(IFNULL(nom_sucursal,''))) IN ({nh})")
                params.extend(noms)
            else:
                where.append("1 = 0")
        if id_ubigeos:
            ubis = sorted({str(x).strip() for x in id_ubigeos if str(x).strip()})
            if ubis:
                uh = ",".join("?" * len(ubis))
                where.append(f"id_ubigeo IN ({uh})")
                params.extend(ubis)
            else:
                where.append("1 = 0")
        if distritos:
            dists = sorted({str(x).strip() for x in distritos if str(x).strip()})
            if dists:
                dh = ",".join("?" * len(dists))
                where.append(f"distrito IN ({dh})")
                params.extend(dists)
            else:
                where.append("1 = 0")
        sql = f"SELECT {cols} FROM ventas"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY fecha_orig DESC LIMIT ? OFFSET ?"
        params.extend([int(limit), int(offset)])

        conn = self._read_conn()
        try:
            own = self._db_file is not None
            try:
                df = pd.read_sql_query(sql, conn, params=params)
                df = self._map_to_historial(df)
                if not df.empty:
                    facturas = sorted(
                        {
                            str(x).strip()
                            for x in df.loc[df["TIPO_CLASE"] != "factura", "FACTURA_REF"]
                            if str(x).strip()
                        }
                        | {
                            str(x).strip()
                            for x in df.loc[df["TIPO_CLASE"] == "factura", "DOC_ID"]
                            if str(x).strip()
                        }
                    )
                    if facturas:
                        ph = ",".join("?" * len(facturas))
                        rows = conn.execute(
                            f"SELECT factura_doc_id, nc_doc_id FROM nc_asociadas WHERE factura_doc_id IN ({ph})",
                            facturas,
                        ).fetchall()
                        mapping: dict[str, list[str]] = {}
                        for fac_doc, nc_doc in rows:
                            mapping.setdefault(fac_doc, []).append(nc_doc)
                        df["NC_ASOCIADAS"] = (
                            df["DOC_ID"]
                            .map(mapping)
                            .apply(lambda x: x if isinstance(x, list) else [])
                        )
                    else:
                        df["NC_ASOCIADAS"] = [[] for _ in range(len(df))]
                else:
                    df["NC_ASOCIADAS"] = []
                return df
            finally:
                if own:
                    conn.close()
        except Exception:
            log.exception("fetch_historial fallo")
            raise

    def distribucion_por_tienda(
        self,
        id_cliente: Optional[str] = None,
        fecha_desde: Optional[str] = None,
        fecha_hasta: Optional[str] = None,
        solo_lineas_activas: bool = True,
    ) -> pd.DataFrame:
        """% por tienda real (id_cliente, cod_sucursal) en un rango.

        La tienda es el par: el código solo se repite entre clientes.
        ACUMULADO sin código se clasifica 'principal' (su cliente tiene
        otras sucursales: es su matriz) o 'unica' (no tiene ninguna).
        PCT_CLIENTE suma 100% por cliente dentro del rango. Sin rango =
        toda la historia (mezcla aperturas y cierres: acotar si importa).
        """
        where = []
        params: list = []
        if solo_lineas_activas:
            where.append("(" + _allowlist_sql() + ")")
        if id_cliente:
            # Calificado: el JOIN con `tiene` también trae id_cliente.
            where.append("ventas.id_cliente = ?")
            params.append(id_cliente)
        if fecha_desde:
            where.append("fecha_orig >= ?")
            params.append(fecha_desde)
        if fecha_hasta:
            where.append("fecha_orig <= ?")
            params.append(fecha_hasta)
        wf = " AND ".join(where)
        # `tiene`: clientes CON sucursal real en el rango. El filtro de
        # código es propio del CTE (wf no lo trae: también filtra la base).
        wf_tiene = "TRIM(IFNULL(cod_sucursal, '')) <> ''" + (" AND " + wf if wf else "")
        sql = f"""
        WITH tiene AS (
            SELECT DISTINCT id_cliente FROM ventas
            WHERE {wf_tiene}
        )
        SELECT id_cliente AS ID_CLIENTE, MAX(nom_cliente) AS NOM_CLIENTE,
            cod AS COD_SUCURSAL, MAX(nom) AS NOM_SUCURSAL,
            CASE WHEN cod <> '' THEN 'sucursal'
                 WHEN MAX(nom) = 'ACUMULADO' AND MAX(es_tiene) = 1 THEN 'principal'
                 WHEN MAX(nom) = 'ACUMULADO' THEN 'unica'
                 ELSE 'sin_dato' END AS CATEGORIA,
            MAX(ubigeo) AS ID_UBIGEO, MAX(distrito) AS DISTRITO,
            COUNT(*) AS FILAS, ROUND(SUM(soles), 2) AS SOLES,
            ROUND(100.0 * SUM(soles)
                  / NULLIF(SUM(SUM(soles)) OVER (PARTITION BY id_cliente), 0), 2)
                AS PCT_CLIENTE
        FROM (
            SELECT ventas.id_cliente, ventas.nom_cliente,
                UPPER(TRIM(IFNULL(ventas.cod_sucursal, ''))) AS cod,
                UPPER(TRIM(IFNULL(ventas.nom_sucursal, ''))) AS nom,
                ventas.id_ubigeo AS ubigeo, ventas.distrito, ventas.soles,
                CASE WHEN t.id_cliente IS NULL THEN 0 ELSE 1 END AS es_tiene
            FROM ventas LEFT JOIN tiene t ON t.id_cliente = ventas.id_cliente
            {"WHERE " + wf if wf else ""}
        )
        GROUP BY id_cliente, cod
        ORDER BY id_cliente, SOLES DESC
        """
        # El rango va dos veces: base y subconsulta tiene.
        params = params + params
        conn = self._read_conn()
        try:
            own = self._db_file is not None
            try:
                return pd.read_sql_query(sql, conn, params=params)
            finally:
                if own:
                    conn.close()
        except Exception:
            log.exception("distribucion_por_tienda fallo")
            raise

    def _compras_where(
        self,
        id_cliente,
        fecha_desde,
        fecha_hasta,
        incluir_nc,
        solo_lineas_activas,
        excluir_devoluciones=False,
    ):
        docs = "(tpo_doc LIKE 'F%' OR tpo_doc LIKE 'B%')"
        if incluir_nc:
            docs = f"({docs} OR UPPER(tpo_doc) IN ('NCR','NDB','NC','ND'))"
        where = ["id_cliente = ?", docs]
        params: list = [id_cliente]
        if fecha_desde:
            where.append("fecha_orig >= ?")
            params.append(fecha_desde)
        if fecha_hasta:
            where.append("fecha_orig <= ?")
            params.append(fecha_hasta)
        if solo_lineas_activas:
            where.append("(" + _allowlist_sql() + ")")
        if excluir_devoluciones:
            where.append("(tipo_operacion IS NULL OR tipo_operacion != 'devolucion')")
        return where, params

    def fetch_analisis_sucursales_cliente(
        self,
        id_cliente: str,
        *,
        fecha_desde: Optional[str] = None,
        fecha_hasta: Optional[str] = None,
        incluir_nc: bool = True,
        solo_lineas_activas: bool = False,
    ) -> dict[str, pd.DataFrame]:
        """Pareto del rango + agregados mensuales por sucursal×línea/SKU.

        La tienda se identifica dentro del cliente: (id_cliente,cod_sucursal).
        Sin código, el nombre distingue buckets (ACUMULADO no se mezcla con
        otros vacíos). El resultado contiene:

        - ``pareto``: una fila por tienda, rank por bruta del rango y % acumulado.
        - ``linea_mes``: una fila por (mes, tienda, línea).
        - ``sku_mes``: una fila por (mes, tienda, SKU).

        Las tres salidas usan el mismo universo de documentos, rango, allowlist
        e incluir_nc que las otras hojas del reporte. Se calcula en pandas
        sobre las filas del cliente/rango, no escaneando la DB desde la UI.
        """
        columnas = {
            "pareto": [
                "RANK",
                "COD_SUCURSAL",
                "SUCURSAL",
                "TIPO_SUCURSAL",
                "ID_UBIGEO",
                "DISTRITO",
                "BRUTA",
                "DEV_S",
                "DESC_S",
                "NDB_S",
                "SOLES",
                "CANTIDAD",
                "N_DOCS",
                "PCT_BRUTA",
                "PCT_ACUMULADO",
            ],
            "linea_mes": [
                "MES_REF",
                "COD_SUCURSAL",
                "SUCURSAL",
                "TIPO_SUCURSAL",
                "COD_LINEA",
                "LINEA",
                "BRUTA",
                "DEV_S",
                "DESC_S",
                "NDB_S",
                "SOLES",
                "CANTIDAD",
                "N_DOCS",
                "N_SKUS",
            ],
            "sku_mes": [
                "MES_REF",
                "COD_SUCURSAL",
                "SUCURSAL",
                "TIPO_SUCURSAL",
                "COD_SKU",
                "SKU",
                "COD_LINEA",
                "LINEA",
                "BRUTA",
                "DEV_S",
                "DESC_S",
                "NDB_S",
                "SOLES",
                "CANTIDAD",
                "N_DOCS",
            ],
        }
        where, params = self._compras_where(
            id_cliente, fecha_desde, fecha_hasta, incluir_nc, solo_lineas_activas
        )
        sql = (
            "SELECT substr(fecha_orig,1,7) AS MES_REF, id_cliente, "
            "cod_sucursal, nom_sucursal, id_ubigeo, distrito, id_linea, "
            "nom_linea, id_articulo, nom_articulo, tpo_doc, serie_doc, "
            "nro_doc, soles, cantidad, tipo_operacion "
            "FROM ventas WHERE " + " AND ".join(where)
        )
        conn = self._read_conn()
        try:
            own = self._db_file is not None
            try:
                df = pd.read_sql_query(sql, conn, params=params)
            finally:
                if own:
                    conn.close()
        except Exception:
            log.exception("fetch_analisis_sucursales_cliente fallo")
            raise

        if df.empty:
            return {k: pd.DataFrame(columns=v) for k, v in columnas.items()}

        # Normalizar solo para agrupar/etiquetar; los datos fuente no se mutan.
        df["COD_SUCURSAL"] = df["cod_sucursal"].fillna("").astype(str).str.strip().str.upper()
        df["_NOM_RAW"] = df["nom_sucursal"].fillna("").astype(str).str.strip().str.upper()
        # Los códigos se repiten entre clientes; dentro de este libro el par
        # basta. Para filas sin código, conserva buckets por nombre.
        df["_SUC_KEY"] = df["COD_SUCURSAL"].where(df["COD_SUCURSAL"] != "", "N:" + df["_NOM_RAW"])
        df.loc[df["COD_SUCURSAL"] != "", "_SUC_KEY"] = (
            "C:" + df.loc[df["COD_SUCURSAL"] != "", "COD_SUCURSAL"]
        )
        df["COD_LINEA"] = df["id_linea"].fillna("").astype(str).str.strip().str.upper().str[-2:]
        df["LINEA"] = df["nom_linea"].fillna("").astype(str).str.strip()
        df["COD_SKU"] = df["id_articulo"].fillna("").astype(str).str.strip()
        df["SKU"] = df["nom_articulo"].fillna("").astype(str).str.strip()
        df["_SOLES"] = pd.to_numeric(df["soles"], errors="coerce").fillna(0.0)
        df["_CANT"] = pd.to_numeric(df["cantidad"], errors="coerce").fillna(0.0)
        df["_OP"] = df["tipo_operacion"].fillna("").astype(str).str.lower()
        fyb = df["tpo_doc"].fillna("").astype(str).str.upper().str.startswith(("F", "B"))
        if incluir_nc:
            df["BRUTA"] = df["_SOLES"].where(fyb & (df["_SOLES"] >= 0), 0.0)
            df["DEV_S"] = df["_SOLES"].where(df["_OP"] == "devolucion", 0.0)
            df["DESC_S"] = df["_SOLES"].where(
                (df["_OP"] == "ajuste_valor") | (fyb & (df["_SOLES"] < 0)), 0.0
            )
            df["NDB_S"] = df["_SOLES"].where(df["_OP"] == "nota_debito", 0.0)
        else:
            # El resto del reporte representa sin NC como bruto = facturas,
            # componentes de ajuste en cero.
            df["BRUTA"] = df["_SOLES"]
            df["DEV_S"] = 0.0
            df["DESC_S"] = 0.0
            df["NDB_S"] = 0.0
        df["SOLES"] = df["_SOLES"]
        df["CANTIDAD"] = df["_CANT"].where(~df["_OP"].isin(("ajuste_valor", "nota_debito")), 0.0)
        df["_DOC"] = (
            df["tpo_doc"].fillna("").astype(str)
            + "|"
            + df["serie_doc"].fillna("").astype(str)
            + "|"
            + df["nro_doc"].fillna("").astype(str)
        )

        # Etiquetas estables por tienda durante todo el rango: nombre/ubigeo
        # más frecuente, con desempate alfabético determinista.
        def _modo_por_tienda(col: str) -> pd.Series:
            counts = df.groupby(["_SUC_KEY", col], dropna=False).size().rename("_N").reset_index()
            counts[col] = counts[col].fillna("").astype(str)
            counts = counts.sort_values(["_SUC_KEY", "_N", col], ascending=[True, False, True])
            return counts.drop_duplicates("_SUC_KEY").set_index("_SUC_KEY")[col]

        refs = pd.DataFrame(index=sorted(df["_SUC_KEY"].unique()))
        refs.index.name = "_SUC_KEY"
        refs["COD_SUCURSAL"] = df.groupby("_SUC_KEY")["COD_SUCURSAL"].first()
        refs["SUCURSAL"] = _modo_por_tienda("_NOM_RAW")
        refs["ID_UBIGEO"] = _modo_por_tienda("id_ubigeo")
        refs["DISTRITO"] = _modo_por_tienda("distrito")
        tiene_otra = bool((df["COD_SUCURSAL"] != "").any())
        refs["TIPO_SUCURSAL"] = [
            (
                "sucursal"
                if cod
                else "principal"
                if nom == "ACUMULADO" and tiene_otra
                else "unica"
                if nom == "ACUMULADO"
                else "sin_dato"
            )
            for cod, nom in zip(refs["COD_SUCURSAL"], refs["SUCURSAL"])
        ]

        metricas = {
            "BRUTA": ("BRUTA", "sum"),
            "DEV_S": ("DEV_S", "sum"),
            "DESC_S": ("DESC_S", "sum"),
            "NDB_S": ("NDB_S", "sum"),
            "SOLES": ("SOLES", "sum"),
            "CANTIDAD": ("CANTIDAD", "sum"),
            "N_DOCS": ("_DOC", "nunique"),
        }

        def _agrupar(keys: list[str], contar_skus: bool = False) -> pd.DataFrame:
            agg = dict(metricas)
            if contar_skus:
                agg["N_SKUS"] = ("COD_SKU", "nunique")
            out = df.groupby(keys, dropna=False, sort=False).agg(**agg).reset_index()
            out = out.merge(refs.reset_index(), on="_SUC_KEY", how="left")
            for col in ("BRUTA", "DEV_S", "DESC_S", "NDB_S", "SOLES", "CANTIDAD"):
                out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0.0).round(2)
            out["N_DOCS"] = out["N_DOCS"].fillna(0).astype(int)
            if contar_skus:
                out["N_SKUS"] = out["N_SKUS"].fillna(0).astype(int)
            return out

        pareto = _agrupar(["_SUC_KEY"])
        pareto = pareto.sort_values(
            ["BRUTA", "SOLES", "COD_SUCURSAL", "SUCURSAL"], ascending=[False, False, True, True]
        ).reset_index(drop=True)
        total_bruta = float(pareto["BRUTA"].sum())
        if total_bruta > 0:
            pareto["PCT_BRUTA"] = pareto["BRUTA"] / total_bruta
            pareto["PCT_ACUMULADO"] = pareto["PCT_BRUTA"].cumsum()
        else:
            pareto["PCT_BRUTA"] = pd.NA
            pareto["PCT_ACUMULADO"] = pd.NA
        pareto.insert(0, "RANK", range(1, len(pareto) + 1))
        pareto = pareto[columnas["pareto"]]

        linea_keys = ["MES_REF", "_SUC_KEY", "COD_LINEA"]
        linea = _agrupar(linea_keys, contar_skus=True)
        linea_names = df.groupby(linea_keys)["LINEA"].max().rename("LINEA").reset_index()
        linea = linea.merge(linea_names, on=linea_keys, how="left")
        linea = linea.sort_values(["MES_REF", "COD_SUCURSAL", "SUCURSAL", "COD_LINEA"])
        linea = linea[columnas["linea_mes"]]

        sku = _agrupar(["MES_REF", "_SUC_KEY", "COD_SKU"])
        sku_names = (
            df.groupby(["MES_REF", "_SUC_KEY", "COD_SKU"])
            .agg(SKU=("SKU", "max"), COD_LINEA=("COD_LINEA", "max"), LINEA=("LINEA", "max"))
            .reset_index()
        )
        sku = sku.merge(sku_names, on=["MES_REF", "_SUC_KEY", "COD_SKU"], how="left")
        sku = sku.sort_values(
            ["MES_REF", "COD_SUCURSAL", "SUCURSAL", "SOLES"], ascending=[True, True, True, False]
        )
        sku = sku[columnas["sku_mes"]]

        return {
            "pareto": pareto.reset_index(drop=True),
            "linea_mes": linea.reset_index(drop=True),
            "sku_mes": sku.reset_index(drop=True),
        }

    def _compras_agg(
        self,
        grupo_col,
        extra_cols,
        *,
        id_cliente,
        fecha_desde=None,
        fecha_hasta=None,
        incluir_nc=True,
        solo_lineas_activas=False,
        excluir_devoluciones=False,
    ) -> pd.DataFrame:
        """Agrega compras por (mes, grupo_col). Compartido por linea/SKU.

        Regla física: solo venta y devolución mueven unidades; descuento y
        NDB aportan 0 a CANTIDAD (sus soles sí suman). Así un descuento con
        cantidad informada (legado) no corrompe las unidades netas.
        """
        where, params = self._compras_where(
            id_cliente,
            fecha_desde,
            fecha_hasta,
            incluir_nc,
            solo_lineas_activas,
            excluir_devoluciones,
        )
        cant_fisica = (
            "SUM(CASE WHEN COALESCE(tipo_operacion,'') IN "
            "('ajuste_valor','nota_debito') THEN 0"
            " ELSE cantidad END)"
        )
        sql = (
            "SELECT substr(fecha_orig,1,7) AS MES_REF, "
            + extra_cols
            + " SUM(soles) AS SOLES, "
            + cant_fisica
            + " AS CANTIDAD,"
            " COUNT(DISTINCT tpo_doc || serie_doc || nro_doc) AS N_DOCS"
            " FROM ventas WHERE "
            + " AND ".join(where)
            + f" GROUP BY MES_REF, {grupo_col} ORDER BY MES_REF, SOLES DESC"
        )
        conn = self._read_conn()
        try:
            own = self._db_file is not None
            try:
                df = pd.read_sql_query(sql, conn, params=params)
            finally:
                if own:
                    conn.close()
        except Exception:
            log.exception("fetch_compras fallo")
            raise
        for col in ("SOLES", "CANTIDAD"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
        df["ANHO"] = (
            pd.to_numeric(df["MES_REF"].str.slice(0, 4), errors="coerce").fillna(0).astype(int)
        )
        df["MES"] = (
            pd.to_numeric(df["MES_REF"].str.slice(5, 7), errors="coerce").fillna(0).astype(int)
        )
        return df

    def fetch_compras_cliente(
        self,
        id_cliente: str,
        *,
        fecha_desde: Optional[str] = None,
        fecha_hasta: Optional[str] = None,
        incluir_nc: bool = True,
        solo_lineas_activas: bool = False,
        excluir_devoluciones: bool = False,
    ) -> pd.DataFrame:
        """Compras agregadas por mes y linea (tidy) para un cliente.

        Columnas de salida: MES_REF (YYYY-MM), ANHO, MES, COD_LINEA, LINEA,
        SOLES, CANTIDAD, N_DOCS. Con ``incluir_nc=True`` suma facturas/boletas
        (tpo_doc F*/B*) + NC/ND; los signos ya vienen en la DB (devolucion y
        ajuste en negativo, nota de debito en positivo), por lo que SUM directo
        da el neto. Con ``incluir_nc=False`` solo facturas/boletas.
        Con ``excluir_devoluciones=True`` se quitan las devoluciones físicas
        (bloque "sin devoluciones" del reporte).

        ``solo_lineas_activas`` default False: un reporte de compras no debe
        ocultar lineas fuera del allowlist de captura. El pivote (mes x linea)
        lo arma el llamador. COD_LINEA es canónico (sufijo de 2).
        """
        return self._compras_agg(
            _LIN_CANON,
            _LIN_CANON + " AS COD_LINEA, MAX(nom_linea) AS LINEA, ",
            id_cliente=id_cliente,
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta,
            incluir_nc=incluir_nc,
            solo_lineas_activas=solo_lineas_activas,
            excluir_devoluciones=excluir_devoluciones,
        )

    def fetch_lineas_resumen_cliente(
        self,
        id_cliente: str,
        *,
        fecha_desde: Optional[str] = None,
        fecha_hasta: Optional[str] = None,
        incluir_nc: bool = True,
        solo_lineas_activas: bool = False,
    ) -> pd.DataFrame:
        """Consolidado por línea: neto + componentes (hoja Consolidado).

        Columnas: COD_LINEA (canónico: sufijo de 2, '01' y '0101' son la
        misma línea), LINEA, BRUTA, DEV_S, DESC_S, NDB_S, SOLES
        (neto), CANTIDAD (física), N_DOCS, N_SKUS. Buckets económicos y
        guarda física iguales que el histórico mensual; ordenado por neto
        descendente. Con ``incluir_nc=False`` BRUTA = neto y componentes
        en cero.
        """
        cols = [
            "COD_LINEA",
            "LINEA",
            "BRUTA",
            "DEV_S",
            "DESC_S",
            "NDB_S",
            "SOLES",
            "CANTIDAD",
            "N_DOCS",
            "N_SKUS",
        ]
        where, params = self._compras_where(
            id_cliente, fecha_desde, fecha_hasta, incluir_nc, solo_lineas_activas
        )
        if incluir_nc:
            bruta = f"ROUND({_BRUTA},2) AS BRUTA"
            dev = f"ROUND({_DEV_S},2) AS DEV_S"
            desc = f"ROUND({_DESC_S},2) AS DESC_S"
            ndb = f"ROUND({_NDB_S},2) AS NDB_S"
        else:
            bruta = "ROUND(SUM(soles),2) AS BRUTA"
            dev = "0.0 AS DEV_S"
            desc = "0.0 AS DESC_S"
            ndb = "0.0 AS NDB_S"
        sql = (
            f"SELECT {_LIN_CANON} AS COD_LINEA, MAX(nom_linea) AS LINEA,"
            f" {bruta}, {dev}, {desc}, {ndb},"
            " ROUND(SUM(soles),2) AS SOLES,"
            f" {_FISICA} AS CANTIDAD,"
            " COUNT(DISTINCT tpo_doc || serie_doc || nro_doc) AS N_DOCS,"
            " COUNT(DISTINCT id_articulo) AS N_SKUS"
            " FROM ventas WHERE " + " AND ".join(where) + " GROUP BY COD_LINEA ORDER BY SOLES DESC"
        )
        conn = self._read_conn()
        try:
            own = self._db_file is not None
            try:
                df = pd.read_sql_query(sql, conn, params=params)
            finally:
                if own:
                    conn.close()
        except Exception:
            log.exception("fetch_lineas_resumen fallo")
            raise
        if df.empty:
            return pd.DataFrame(columns=cols)
        for col in ("BRUTA", "DEV_S", "DESC_S", "NDB_S", "SOLES", "CANTIDAD"):
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
        for col in ("N_DOCS", "N_SKUS"):
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
        return df[cols]

    def fetch_skus_resumen_cliente(
        self,
        id_cliente: str,
        *,
        fecha_desde: Optional[str] = None,
        fecha_hasta: Optional[str] = None,
        incluir_nc: bool = True,
        solo_lineas_activas: bool = False,
    ) -> pd.DataFrame:
        """Top SKUs por neto (hoja Consolidado, bloque B).

        Columnas: COD_SKU, SKU, COD_LINEA, LINEA, SOLES (neto), CANTIDAD
        (física), N_DOCS. El SKU conserva sus ceros a la izquierda.
        Ordenado por neto descendente (el writer corta el top + Otros).
        """
        cols = ["COD_SKU", "SKU", "COD_LINEA", "LINEA", "SOLES", "CANTIDAD", "N_DOCS"]
        where, params = self._compras_where(
            id_cliente, fecha_desde, fecha_hasta, incluir_nc, solo_lineas_activas
        )
        sql = (
            "SELECT id_articulo AS COD_SKU, MAX(nom_articulo) AS SKU,"
            f" MAX({_LIN_CANON}) AS COD_LINEA, MAX(nom_linea) AS LINEA,"
            " ROUND(SUM(soles),2) AS SOLES,"
            f" {_FISICA} AS CANTIDAD,"
            " COUNT(DISTINCT tpo_doc || serie_doc || nro_doc) AS N_DOCS"
            " FROM ventas WHERE " + " AND ".join(where) + " GROUP BY COD_SKU ORDER BY SOLES DESC"
        )
        conn = self._read_conn()
        try:
            own = self._db_file is not None
            try:
                df = pd.read_sql_query(sql, conn, params=params)
            finally:
                if own:
                    conn.close()
        except Exception:
            log.exception("fetch_skus_resumen fallo")
            raise
        if df.empty:
            return pd.DataFrame(columns=cols)
        for col in ("SOLES", "CANTIDAD"):
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
        df["N_DOCS"] = pd.to_numeric(df["N_DOCS"], errors="coerce").fillna(0).astype(int)
        return df[cols]

    def fetch_compras_sku_mes_cliente(
        self,
        id_cliente: str,
        *,
        fecha_desde: Optional[str] = None,
        fecha_hasta: Optional[str] = None,
        incluir_nc: bool = True,
        solo_lineas_activas: bool = False,
    ) -> pd.DataFrame:
        """Mes × SKU × línea neto (hoja Comparativo, tabla fusionada).

        Columnas: MES_REF, COD_SKU, SKU, COD_LINEA (canónico), LINEA,
        SOLES (neto con toggle), CANTIDAD (física: misma guarda que el
        resto — descuento/NDB aportan 0). El SKU conserva ceros a la
        izquierda. Un SKU bajo 2 líneas en el mismo mes se parte en 2
        filas (sin MAX sobre la línea). Orden mes, línea, neto descendente.
        """
        cols = ["MES_REF", "COD_SKU", "SKU", "COD_LINEA", "LINEA", "SOLES", "CANTIDAD"]
        where, params = self._compras_where(
            id_cliente, fecha_desde, fecha_hasta, incluir_nc, solo_lineas_activas
        )
        sql = (
            "SELECT substr(fecha_orig,1,7) AS MES_REF,"
            " id_articulo AS COD_SKU, MAX(nom_articulo) AS SKU,"
            f" {_LIN_CANON} AS COD_LINEA, MAX(nom_linea) AS LINEA,"
            " ROUND(SUM(soles),2) AS SOLES,"
            f" {_FISICA} AS CANTIDAD"
            " FROM ventas WHERE "
            + " AND ".join(where)
            + " GROUP BY MES_REF, COD_LINEA, COD_SKU ORDER BY MES_REF, SOLES DESC"
        )
        conn = self._read_conn()
        try:
            own = self._db_file is not None
            try:
                df = pd.read_sql_query(sql, conn, params=params)
            finally:
                if own:
                    conn.close()
        except Exception:
            log.exception("fetch_compras_sku_mes fallo")
            raise
        if df.empty:
            return pd.DataFrame(columns=cols)
        for col in ("SOLES", "CANTIDAD"):
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
        return df[cols]

    def fetch_compras_lineas_cliente(
        self,
        id_cliente: str,
        *,
        fecha_desde: Optional[str] = None,
        fecha_hasta: Optional[str] = None,
        incluir_nc: bool = True,
        solo_lineas_activas: bool = False,
        excluir_devoluciones: bool = False,
    ) -> pd.DataFrame:
        """BD_Registro: una fila por línea de documento (hoja BD_Registro).

        Granularidad mínima del paquete: las hojas agregadas son vistas de
        estas filas. Mismos filtros que las compras agregadas
        (``_compras_where``) y la misma guarda física en CANTIDAD (ajuste y
        NDB aportan 0 unidades), de modo que SUM(CANTIDAD)/SUM(SOLES)
        coinciden con Unidades y Soles.

        Columnas: MES_REF (YYYY-MM), FECHA (YYYY-MM-DD), TPO_DOC, DOC
        (F204-67375, igual que la hoja Documentos), REF (factura que
        referencia la nota), COD_LINEA, LINEA, SUCURSAL, COD_SKU, ARTICULO,
        CANTIDAD (física), PU (precio unitario a 5 decimales), SOLES y
        OPERACION (tipo_operacion crudo; vacío en factura). Columnas
        analíticas: PEDIDO (id_pedido), FAE (|cantidad_fae|), CANT_DEV
        (unidades devueltas contra esa línea de factura, 0 en notas),
        BRUTO (soles de venta) y AJUSTE (soles de devolución/descuento/NDB;
        BRUTO+AJUSTE = SOLES por fila).
        """
        cols = [
            "MES_REF",
            "FECHA",
            "TPO_DOC",
            "DOC",
            "REF",
            "COD_LINEA",
            "LINEA",
            "SUCURSAL",
            "COD_SKU",
            "ARTICULO",
            "CANTIDAD",
            "PU",
            "SOLES",
            "OPERACION",
            "PEDIDO",
            "FAE",
            "CANT_DEV",
            "BRUTO",
            "AJUSTE",
        ]
        where, params = self._compras_where(
            id_cliente,
            fecha_desde,
            fecha_hasta,
            incluir_nc,
            solo_lineas_activas,
            excluir_devoluciones,
        )
        nota_ops = "('devolucion','ajuste_valor','nota_debito')"
        sql = (
            "SELECT substr(fecha_orig,1,7) AS MES_REF,"
            " substr(fecha_orig,1,10) AS FECHA,"
            " tpo_doc AS TPO_DOC,"
            f" {_DOC_FYB} AS DOC,"
            " CASE WHEN UPPER(tpo_doc) IN ('NCR','NDB','NC','ND')"
            " AND COALESCE(factura_ref_serie,'') != ''"
            " AND COALESCE(factura_ref_nro,'') != ''"
            f" THEN {_REF_NOTA}"
            " ELSE '' END AS REF,"
            f" {_LIN_CANON} AS COD_LINEA, nom_linea AS LINEA,"
            " COALESCE(nom_sucursal,'') AS SUCURSAL,"
            " id_articulo AS COD_SKU, nom_articulo AS ARTICULO,"
            " CASE WHEN COALESCE(tipo_operacion,'') IN"
            " ('ajuste_valor','nota_debito') THEN 0 ELSE cantidad END"
            " AS CANTIDAD,"
            " ROUND(ABS(COALESCE(precio_unitario,0)),5) AS PU,"
            " soles AS SOLES,"
            " COALESCE(tipo_operacion,'') AS OPERACION,"
            " COALESCE(id_pedido,'') AS PEDIDO,"
            " ABS(COALESCE(cantidad_fae,0)) AS FAE,"
            " COALESCE(dev.CANT_DEV,0) AS CANT_DEV,"
            f" CASE WHEN COALESCE(tipo_operacion,'') IN {nota_ops}"
            " THEN 0 ELSE soles END AS BRUTO,"
            f" CASE WHEN COALESCE(tipo_operacion,'') IN {nota_ops}"
            " THEN soles ELSE 0 END AS AJUSTE"
            " FROM ventas"
            # Unidades ya devueltas contra cada línea de factura (mismo
            # cliente; join por (serie, nro, SKU) nunca machea a la propia
            # nota porque su nro_doc difiere del referenciado).
            " LEFT JOIN (SELECT id_cliente AS cid,"
            " factura_ref_serie AS s, factura_ref_nro AS n,"
            " id_articulo AS sku, ROUND(ABS(SUM(cantidad)),2) AS CANT_DEV"
            " FROM ventas WHERE id_cliente = ?"
            " AND tipo_operacion = 'devolucion'"
            " AND COALESCE(factura_ref_serie,'') != ''"
            " AND COALESCE(factura_ref_nro,'') != ''"
            " GROUP BY cid, s, n, sku) dev"
            " ON dev.cid = ventas.id_cliente"
            " AND dev.s = ventas.serie_doc AND dev.n = ventas.nro_doc"
            " AND dev.sku = ventas.id_articulo"
            " WHERE "
            + " AND ".join(where)
            + " ORDER BY fecha_orig, tpo_doc, serie_doc, nro_doc, id_articulo"
        )
        params = [id_cliente, *params]
        conn = self._read_conn()
        try:
            own = self._db_file is not None
            try:
                df = pd.read_sql_query(sql, conn, params=params)
            finally:
                if own:
                    conn.close()
        except Exception:
            log.exception("fetch_compras_lineas fallo")
            raise
        if df.empty:
            return pd.DataFrame(columns=cols)
        for col in ("CANTIDAD", "PU", "SOLES", "FAE", "CANT_DEV", "BRUTO", "AJUSTE"):
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
        return df[cols]

    def fetch_facturas_sku_detalle_cliente(
        self,
        id_cliente: str,
        *,
        fecha_desde: Optional[str] = None,
        fecha_hasta: Optional[str] = None,
        solo_lineas_activas: bool = False,
    ) -> pd.DataFrame:
        """Bruto por (factura, SKU) + NC asociadas (hoja Facturas).

        Solo líneas de factura/boleta (el ajuste/NC lo aporta la hoja
        Ajustes vía SUMIFS). Una fila por documento F/B × SKU: FECHA,
        DOC, PEDIDO, OC, SUCURSAL, COD_LINEA, LINEA, SKU, ARTICULO, CANT
        (unidades físicas de la factura), BRUTO (soles) y NC (lista de
        NC asociadas históricas vía ``nc_asociadas``, orden cronológico).
        Sucursal/Pedido/OC/Línea son únicos por grupo en datos (0
        dispersión verificada). Sin ``incluir_nc``: por definición es
        mundo bruto.
        """
        cols = [
            "FECHA",
            "DOC",
            "PEDIDO",
            "OC",
            "SUCURSAL",
            "COD_LINEA",
            "LINEA",
            "SKU",
            "ARTICULO",
            "CANT",
            "BRUTO",
            "NC",
        ]
        where = ["id_cliente = ?", "(tpo_doc LIKE 'F%' OR tpo_doc LIKE 'B%')"]
        params: list = [id_cliente]
        if fecha_desde:
            where.append("fecha_orig >= ?")
            params.append(fecha_desde)
        if fecha_hasta:
            where.append("fecha_orig <= ?")
            params.append(fecha_hasta)
        if solo_lineas_activas:
            where.append("(" + _allowlist_sql() + ")")
        sql = (
            "SELECT MIN(fecha_orig) AS FECHA,"
            f" {_DOC_FYB} AS DOC,"
            " MAX(COALESCE(id_pedido,'')) AS PEDIDO,"
            " MAX(COALESCE(ord_compra,'')) AS OC,"
            " MAX(COALESCE(nom_sucursal,'')) AS SUCURSAL,"
            f" MAX({_LIN_CANON}) AS COD_LINEA, MAX(nom_linea) AS LINEA,"
            " id_articulo AS SKU, MAX(nom_articulo) AS ARTICULO,"
            " SUM(cantidad) AS CANT,"
            " ROUND(SUM(soles),2) AS BRUTO"
            " FROM ventas WHERE "
            + " AND ".join(where)
            + " GROUP BY tpo_doc, serie_doc, nro_doc, id_articulo"
            " ORDER BY FECHA, DOC, SKU"
        )
        conn = self._read_conn()
        try:
            own = self._db_file is not None
            try:
                df = pd.read_sql_query(sql, conn, params=params)
                mapa_nc: dict = {}
                if not df.empty:
                    docs = sorted({str(d) for d in df["DOC"].tolist()})
                    ph = ",".join("?" * len(docs))
                    try:
                        rows = conn.execute(
                            "SELECT factura_doc_id, nc_tpo, nc_serie, "
                            "nc_nro, fecha_orig"
                            f" FROM nc_asociadas WHERE factura_doc_id IN ({ph})"
                            " ORDER BY fecha_orig, nc_serie, nc_nro",
                            docs,
                        ).fetchall()
                        for fac_doc, tpo, serie, nro, _f in rows:
                            # nc_doc_id guarda el prefijo duplicado
                            # ('NN204-…'); se normaliza igual que DOC_NOTA.
                            pre = str(tpo or "")[:1]
                            if str(serie or "").startswith(pre):
                                pre = ""
                            nc = f"{pre}{serie}-{nro}"
                            lst = mapa_nc.setdefault(str(fac_doc), [])
                            if nc not in lst:
                                lst.append(nc)
                    except Exception:
                        mapa_nc = {}
            finally:
                if own:
                    conn.close()
        except Exception:
            log.exception("fetch_facturas_sku_detalle fallo")
            raise
        if df.empty:
            return pd.DataFrame(columns=cols)
        for col in ("CANT", "BRUTO"):
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
        df["NC"] = pd.Series(
            [list(mapa_nc.get(str(d), [])) for d in df["DOC"].tolist()], dtype=object
        )
        return df[cols]

    def fetch_historico_mensual(
        self,
        id_cliente: str,
        *,
        fecha_desde: Optional[str] = None,
        fecha_hasta: Optional[str] = None,
        incluir_nc: bool = True,
        solo_lineas_activas: bool = False,
    ) -> pd.DataFrame:
        """Una fila por mes: métricas económicas, físicas y de actividad.

        Columnas: MES (YYYY-MM), BRUTA, DEV_S, DESC_S, NDB_S, NETA, UFACT,
        UDEV (negativo), FACTURAS, NC, NDB_DOCS, SKUS, MAX_FECHA. Signos
        nativos de la DB (DEV/DESC en negativo, NDB en positivo). Unidades
        solo de venta/devolución (descuento/NDB aportan 0). DESC incluye
        facturas/boletas con soles<0 (descuento embebido); BRUTA las excluye
        para que BRUTA+DEV+DESC+NDB = NETA. Con ``incluir_nc=False`` solo
        facturas/boletas (componentes en cero). Sin filas → DataFrame vacío
        (con columnas).
        """
        cols = [
            "MES",
            "BRUTA",
            "DEV_S",
            "DESC_S",
            "NDB_S",
            "NETA",
            "UFACT",
            "UDEV",
            "FACTURAS",
            "NC",
            "NDB_DOCS",
            "SKUS",
            "MAX_FECHA",
        ]
        ndb = "UPPER(tpo_doc) = 'NDB'"
        ncr = "UPPER(tpo_doc) = 'NCR'"
        where = ["id_cliente = ?", f"({_FYB} OR {_NOTA})" if incluir_nc else _FYB]
        params: list = [id_cliente]
        if fecha_desde:
            where.append("fecha_orig >= ?")
            params.append(fecha_desde)
        if fecha_hasta:
            where.append("fecha_orig <= ?")
            params.append(fecha_hasta)
        if solo_lineas_activas:
            where.append("(" + _allowlist_sql() + ")")
        if incluir_nc:
            b_bruta = f"ROUND({_BRUTA},2) AS BRUTA"
            b_dev = f"ROUND({_DEV_S},2) AS DEV_S"
            b_desc = f"ROUND({_DESC_S},2) AS DESC_S"
            b_ndb = f"ROUND({_NDB_S},2) AS NDB_S"
        else:
            # Sin NC/ND: bruta = neto y componentes en cero (el toggle rige
            # todas las hojas; las F/B con soles<0 quedan en bruta).
            b_bruta = "ROUND(SUM(soles),2) AS BRUTA"
            b_dev = "0.0 AS DEV_S"
            b_desc = "0.0 AS DESC_S"
            b_ndb = "0.0 AS NDB_S"
        sql = (
            # Alias PERIODO (no MES): SQLite resuelve GROUP BY insensible a
            # mayúsculas y MES colisionaría con la columna entera `mes`.
            "SELECT substr(fecha_orig,1,7) AS PERIODO,"
            f" {b_bruta},"
            f" {b_dev},"
            f" {b_desc},"
            f" {b_ndb},"
            " ROUND(SUM(soles),2) AS NETA,"
            f" SUM(CASE WHEN {_FYB} THEN cantidad ELSE 0 END) AS UFACT,"
            " SUM(CASE WHEN tipo_operacion = 'devolucion' THEN cantidad ELSE 0 END) AS UDEV,"
            f" COUNT(DISTINCT CASE WHEN {_FYB} THEN tpo_doc || serie_doc || nro_doc END) AS FACTURAS,"
            f" COUNT(DISTINCT CASE WHEN {ncr} THEN tpo_doc || serie_doc || nro_doc END) AS NC,"
            f" COUNT(DISTINCT CASE WHEN {ndb} THEN tpo_doc || serie_doc || nro_doc END) AS NDB_DOCS,"
            f" COUNT(DISTINCT CASE WHEN {_FYB} THEN id_articulo END) AS SKUS,"
            " MAX(fecha_orig) AS MAX_FECHA"
            f" FROM ventas WHERE {' AND '.join(where)}"
            " GROUP BY PERIODO ORDER BY PERIODO"
        )
        conn = self._read_conn()
        try:
            own = self._db_file is not None
            try:
                df = pd.read_sql_query(sql, conn, params=params)
            finally:
                if own:
                    conn.close()
        except Exception:
            log.exception("fetch_historico_mensual fallo")
            raise
        if df.empty:
            return pd.DataFrame(columns=cols)
        df = df.rename(columns={"PERIODO": "MES"})
        for col in ("BRUTA", "DEV_S", "DESC_S", "NDB_S", "NETA", "UFACT", "UDEV"):
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
        for col in ("FACTURAS", "NC", "NDB_DOCS", "SKUS"):
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
        return df[cols]

    def fetch_historico_diario(
        self,
        id_cliente: str,
        *,
        fecha_desde: Optional[str] = None,
        fecha_hasta: Optional[str] = None,
        incluir_nc: bool = True,
        solo_lineas_activas: bool = False,
    ) -> pd.DataFrame:
        """Agregado diario para el periodo comparable (MTD).

        Columnas: FECHA (YYYY-MM-DD), BRUTA, DEV_S, DESC_S, NDB_S, UFACT,
        UDEV, FACT_N, NC_N. Los documentos son de un solo día, por lo que
        los conteos diarios sí se pueden sumar en el MTD. Mismos buckets
        que el mensual (F/B con soles<0 van a DESC). Con
        ``incluir_nc=False`` solo facturas/boletas.
        """
        cols = ["FECHA", "BRUTA", "DEV_S", "DESC_S", "NDB_S", "UFACT", "UDEV", "FACT_N", "NC_N"]
        where = ["id_cliente = ?", f"({_FYB} OR {_NOTA})" if incluir_nc else _FYB]
        params: list = [id_cliente]
        if fecha_desde:
            where.append("fecha_orig >= ?")
            params.append(fecha_desde)
        if fecha_hasta:
            where.append("fecha_orig <= ?")
            params.append(fecha_hasta)
        if solo_lineas_activas:
            where.append("(" + _allowlist_sql() + ")")
        if incluir_nc:
            b_bruta = f"ROUND({_BRUTA},2) AS BRUTA"
            b_dev = f"ROUND({_DEV_S},2) AS DEV_S"
            b_desc = f"ROUND({_DESC_S},2) AS DESC_S"
            b_ndb = f"ROUND({_NDB_S},2) AS NDB_S"
        else:
            b_bruta = "ROUND(SUM(soles),2) AS BRUTA"
            b_dev = "0.0 AS DEV_S"
            b_desc = "0.0 AS DESC_S"
            b_ndb = "0.0 AS NDB_S"
        sql = (
            "SELECT substr(fecha_orig,1,10) AS FECHA,"
            f" {b_bruta},"
            f" {b_dev},"
            f" {b_desc},"
            f" {b_ndb},"
            f" SUM(CASE WHEN {_FYB} THEN cantidad ELSE 0 END) AS UFACT,"
            " SUM(CASE WHEN tipo_operacion = 'devolucion' THEN cantidad ELSE 0 END) AS UDEV,"
            f" COUNT(DISTINCT CASE WHEN {_FYB} THEN tpo_doc || serie_doc || nro_doc END) AS FACT_N,"
            f" COUNT(DISTINCT CASE WHEN {_NOTA} THEN tpo_doc || serie_doc || nro_doc END) AS NC_N"
            f" FROM ventas WHERE {' AND '.join(where)}"
            " GROUP BY FECHA ORDER BY FECHA"
        )
        conn = self._read_conn()
        try:
            own = self._db_file is not None
            try:
                df = pd.read_sql_query(sql, conn, params=params)
            finally:
                if own:
                    conn.close()
        except Exception:
            log.exception("fetch_historico_diario fallo")
            raise
        if df.empty:
            return pd.DataFrame(columns=cols)
        for col in ("BRUTA", "DEV_S", "DESC_S", "NDB_S", "UFACT", "UDEV"):
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
        for col in ("FACT_N", "NC_N"):
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
        return df[cols]

    def fetch_notas_sku_cliente(
        self,
        id_cliente: str,
        *,
        fecha_desde: Optional[str] = None,
        fecha_hasta: Optional[str] = None,
        incluir_nc: bool = True,
        solo_lineas_activas: bool = False,
    ) -> pd.DataFrame:
        """Detalle de notas por (factura, SKU, documento) + datos completos de la factura.

        Bloque principal de la hoja Ajustes: solo notas cuya factura
        referenciada EXISTE en la DB (mismo cliente); las huérfanas van en
        ``fetch_notas_huerfanas_cliente`` (bloque final "NC sin factura").
        Una fila por documento de nota × SKU: devolución (cantidad y soles),
        descuento y NDB (solo soles). El rango de fechas filtra SOLO las
        notas (el hecho económico del periodo); las líneas de factura entran
        SIN filtro de fecha, por lo que CANT_FACT/F_FACT/PU_FACT siempre
        están completos. PU_NC = campo precio_unitario de la línea de la
        nota (valor real abonado); PU_FACT = soles/cantidad de la factura
        (5 decimales). FAE = base de la nota (|suma cantidad_fae|): en
        descuento/NDB sin existencias es la referencia para cantidad y
        P.U. (FAE = −1 → base no desagregada). SOLES_NC con signo nativo
        (DEV/DESC negativos, NDB positivo); SALDO = CANT_FACT − devuelto
        HISTÓRICO del grupo (factura, SKU) — sin filtro de fecha, el
        saldo físico no depende del rango. F_FACT a nivel factura (fecha
        aunque el SKU no esté en ella). Con ``solo_lineas_activas`` respeta
        el allowlist en ambas patas (igual que el sustento).
        """
        cols = [
            "FACTURA",
            "F_FACT",
            "SKU",
            "ARTICULO",
            "CANT_FACT",
            "PU_FACT",
            "SOLES_FACT",
            "NC",
            "TIPO",
            "FECHA_DOC",
            "CANT_NC",
            "PU_NC",
            "SOLES_NC",
            "SALDO",
            "FAE",
        ]
        if not incluir_nc:
            return pd.DataFrame(columns=cols)
        docs_fyb = "(tpo_doc LIKE 'F%' OR tpo_doc LIKE 'B%')"
        es_nota = "UPPER(tpo_doc) IN ('NCR','NDB','NC','ND')"
        doc_nota = (
            "(CASE WHEN serie_doc LIKE substr(tpo_doc,1,1) || '%' THEN '' "
            "ELSE substr(tpo_doc,1,1) END) || serie_doc || '-' || nro_doc"
        )
        doc_fact = "substr(tpo_doc,1,1) || serie_doc || '-' || nro_doc"
        ref_nota = "'F' || factura_ref_serie || '-' || factura_ref_nro"
        with_allow = f" AND ({_allowlist_sql()})" if solo_lineas_activas else ""
        conn = self._read_conn()
        try:
            own = self._db_file is not None
            try:
                # (a) Líneas de nota dentro del rango, agrupadas por documento.
                # Solo notas con factura existente (las huérfanas van al
                # bloque final vía fetch_notas_huerfanas_cliente).
                w_nota = [
                    f"id_cliente = ? AND ({es_nota})",
                    "COALESCE(factura_ref_serie,'') != ''",
                    "COALESCE(factura_ref_nro,'') != ''",
                    _FACTURA_EXISTE,
                ]
                p_nota: list = [id_cliente]
                if fecha_desde:
                    w_nota.append("fecha_orig >= ?")
                    p_nota.append(fecha_desde)
                if fecha_hasta:
                    w_nota.append("fecha_orig <= ?")
                    p_nota.append(fecha_hasta)
                df_n = pd.read_sql_query(
                    "SELECT"
                    f" {ref_nota} AS FACTURA,"
                    " id_articulo AS SKU, MAX(nom_articulo) AS ARTICULO,"
                    f" {doc_nota} AS NC, tipo_operacion AS TIPO,"
                    " MIN(substr(fecha_orig,1,10)) AS FECHA_DOC,"
                    " SUM(cantidad) AS CANT_NC, ROUND(SUM(soles),2) AS SOLES_NC,"
                    " MAX(ABS(COALESCE(precio_unitario,0))) AS PU_NC,"
                    " SUM(COALESCE(cantidad_fae,0)) AS FAE"
                    " FROM ventas WHERE "
                    + " AND ".join(w_nota)
                    + with_allow
                    + " GROUP BY FACTURA, SKU, NC, TIPO"
                    " ORDER BY FACTURA, SKU, FECHA_DOC, NC",
                    conn,
                    params=p_nota,
                )
                if df_n.empty:
                    return pd.DataFrame(columns=cols)
                # (b) Agregado de factura SIN filtro de fecha, solo para las
                # facturas referenciadas por las notas del punto (a).
                refs = sorted({str(f) for f in df_n["FACTURA"].tolist() if f})
                ph = ",".join("?" * len(refs))
                df_f = pd.read_sql_query(
                    "SELECT"
                    f" {doc_fact} AS FACTURA, id_articulo AS SKU,"
                    " SUM(cantidad) AS CANT_FACT,"
                    " ROUND(SUM(soles),2) AS SOLES_FACT,"
                    " MAX(substr(fecha_orig,1,10)) AS F_FACT"
                    f" FROM ventas WHERE id_cliente = ? AND ({docs_fyb})"
                    f" AND {doc_fact} IN ({ph})" + with_allow + " GROUP BY FACTURA, SKU",
                    conn,
                    params=[id_cliente, *refs],
                )
                # (c) Devuelto histórico por (factura, SKU): sin filtro de
                # fecha — el saldo físico no depende del rango.
                df_h = pd.read_sql_query(
                    "SELECT"
                    f" {ref_nota} AS FACTURA, id_articulo AS SKU,"
                    " SUM(cantidad) AS DEV_HIST"
                    " FROM ventas WHERE id_cliente = ?"
                    " AND tipo_operacion = 'devolucion'"
                    " AND COALESCE(factura_ref_serie,'') != ''"
                    " AND COALESCE(factura_ref_nro,'') != ''"
                    + with_allow
                    + " GROUP BY FACTURA, SKU",
                    conn,
                    params=[id_cliente],
                )
            finally:
                if own:
                    conn.close()
        except Exception:
            log.exception("fetch_notas_sku fallo")
            raise
        fag = {
            (str(r[0]), str(r[1])): (float(r[2] or 0), float(r[3] or 0), str(r[4] or "") or None)
            for r in df_f.itertuples(index=False)
        }
        for col in ("CANT_NC", "PU_NC", "FAE"):
            df_n[col] = pd.to_numeric(df_n[col], errors="coerce").fillna(0.0).abs()
        # SOLES_NC conserva el signo nativo (DEV/DESC negativos, NDB
        # positivo): el TOTAL del detalle suma el neto de ajustes.
        df_n["SOLES_NC"] = pd.to_numeric(df_n["SOLES_NC"], errors="coerce").fillna(0.0)
        dev_hist = {
            (str(r[0]), str(r[1])): abs(float(r[2] or 0)) for r in df_h.itertuples(index=False)
        }

        def _fag(key):
            return fag.get((str(key[0]), str(key[1])), (0.0, 0.0, None))

        df_n["CANT_FACT"] = [float(_fag(k)[0]) for k in zip(df_n["FACTURA"], df_n["SKU"])]
        # None si la factura no trae ese SKU (flag "SKU no facturado").
        df_n["SOLES_FACT"] = pd.Series(
            [
                float(fag[(str(f), str(s))][1]) if (str(f), str(s)) in fag else None
                for f, s in zip(df_n["FACTURA"], df_n["SKU"])
            ],
            dtype=object,
        )
        # F_FACT a nivel factura (fecha de la factura aunque el SKU no
        # esté en ella): None solo si no hay líneas válidas.
        inv_fechas = df_f.groupby("FACTURA")["F_FACT"].max().to_dict()

        def _inv_fecha(factura, sku):
            key = (str(factura), str(sku))
            if key in fag:
                return fag[key][2]
            return inv_fechas.get(str(factura))

        df_n["F_FACT"] = [_inv_fecha(f, s) for f, s in zip(df_n["FACTURA"], df_n["SKU"])]
        df_n["PU_FACT"] = df_n.apply(
            lambda r: round(r["SOLES_FACT"] / r["CANT_FACT"], 5) if r["CANT_FACT"] else 0.0, axis=1
        )
        saldos = {}
        for k in zip(df_n["FACTURA"], df_n["SKU"]):
            key = (str(k[0]), str(k[1]))
            if key not in saldos:
                cf = float(fag.get(key, (0.0,))[0])
                saldos[key] = round(cf - float(dev_hist.get(key, 0.0)), 2)
        primera = ~df_n.duplicated(subset=["FACTURA", "SKU"], keep="first")
        # dtype=object: el None se conserva (en float64 mutaría a NaN y
        # rompería las sumas del TOTAL con `nan or 0` → nan).
        df_n["SALDO"] = pd.Series(
            [
                saldos[(str(f), str(s))] if p else None
                for f, s, p in zip(df_n["FACTURA"], df_n["SKU"], primera)
            ],
            dtype=object,
        )
        return df_n[cols]

    def fetch_notas_huerfanas_cliente(
        self,
        id_cliente: str,
        *,
        fecha_desde: Optional[str] = None,
        fecha_hasta: Optional[str] = None,
        incluir_nc: bool = True,
        solo_lineas_activas: bool = False,
    ) -> pd.DataFrame:
        """NC/NDB sin factura (hoja Ajustes, bloque final "NC sin factura").

        Complemento exacto del bloque principal: notas del rango cuya
        referencia está vacía O cuya factura no existe en la DB (mismo
        cliente). Una fila por (documento NC/NDB, SKU). SOLES_NC con signo
        nativo; CANT_NC y FAE en magnitud. La hoja deriva Obs.: "Sin
        factura ref." (referencia vacía) o "Factura no encontrada".
        Con ``incluir_nc=False`` retorna vacío.
        """
        cols = [
            "FACTURA",
            "NC",
            "FECHA_DOC",
            "TIPO",
            "SKU",
            "ARTICULO",
            "CANT_NC",
            "SOLES_NC",
            "FAE",
        ]
        if not incluir_nc:
            return pd.DataFrame(columns=cols)
        es_nota = "UPPER(tpo_doc) IN ('NCR','NDB','NC','ND')"
        doc_nota = (
            "(CASE WHEN serie_doc LIKE substr(tpo_doc,1,1) || '%' THEN '' "
            "ELSE substr(tpo_doc,1,1) END) || serie_doc || '-' || nro_doc"
        )
        w_nota = [
            f"id_cliente = ? AND ({es_nota})",
            "(COALESCE(factura_ref_serie,'') = ''"
            " OR COALESCE(factura_ref_nro,'') = ''"
            f" OR NOT ({_FACTURA_EXISTE}))",
        ]
        p_nota: list = [id_cliente]
        if fecha_desde:
            w_nota.append("fecha_orig >= ?")
            p_nota.append(fecha_desde)
        if fecha_hasta:
            w_nota.append("fecha_orig <= ?")
            p_nota.append(fecha_hasta)
        with_allow = f" AND ({_allowlist_sql()})" if solo_lineas_activas else ""
        conn = self._read_conn()
        try:
            own = self._db_file is not None
            try:
                df_n = pd.read_sql_query(
                    "SELECT"
                    f" {_REF_NOTA} AS FACTURA,"
                    f" {doc_nota} AS NC, tipo_operacion AS TIPO,"
                    " id_articulo AS SKU, MAX(nom_articulo) AS ARTICULO,"
                    " MIN(substr(fecha_orig,1,10)) AS FECHA_DOC,"
                    " SUM(cantidad) AS CANT_NC, ROUND(SUM(soles),2) AS SOLES_NC,"
                    " SUM(COALESCE(cantidad_fae,0)) AS FAE"
                    " FROM ventas WHERE "
                    + " AND ".join(w_nota)
                    + with_allow
                    + " GROUP BY FACTURA, SKU, NC, TIPO"
                    " ORDER BY FECHA_DOC, NC",
                    conn,
                    params=p_nota,
                )
            finally:
                if own:
                    conn.close()
        except Exception:
            log.exception("fetch_notas_huerfanas fallo")
            raise
        if df_n.empty:
            return pd.DataFrame(columns=cols)
        # La referencia vacía ('F-') equivale a sin referencia.
        df_n["FACTURA"] = df_n["FACTURA"].apply(
            lambda f: "" if str(f).strip() in ("", "F-") else str(f)
        )
        for col in ("CANT_NC", "FAE"):
            df_n[col] = pd.to_numeric(df_n[col], errors="coerce").fillna(0.0).abs()
        df_n["SOLES_NC"] = pd.to_numeric(df_n["SOLES_NC"], errors="coerce").fillna(0.0)
        return df_n[cols]

    def fetch_documento(self, serie_doc: str, nro_doc: str) -> Optional[dict]:
        """Consulta integral de un documento (factura/NC/ND):
        - cabecera: tipo, cliente, fechas, totales, referencia
        - lineas: detalle por SKU (cantidad, montos, precio)
        - asociados: cada NC/ND que referencia este doc (devolucion/descuento/NDB)
        - impacto: por SKU de factura — devuelto, descuento, cargo, saldo y precio neto
        Base para calcular futuras notas de credito (fuente de verdad: historial)."""
        conn = self._read_conn()
        own = self._db_file is not None
        try:
            cab = conn.execute(
                "SELECT tpo_doc, serie_doc, nro_doc, fecha_orig, mes_ref, "
                "id_cliente, doc_cliente, nom_cliente, n_lineas, cantidad, "
                "total_soles, total_dolares, referencia_factura "
                "FROM vw_documento WHERE serie_doc = ? AND nro_doc = ?",
                (serie_doc, nro_doc),
            ).fetchone()
            if not cab:
                return None
            lineas = conn.execute(
                "SELECT id_articulo, nom_articulo, id_linea, nom_linea, "
                "cantidad, cantidad_fae, soles, dolares, precio_unitario, "
                "fecha_orig, id_vendedor, nom_vendedor "
                "FROM ventas WHERE serie_doc = ? AND nro_doc = ? ORDER BY id_articulo",
                (serie_doc, nro_doc),
            ).fetchall()
            asociados = conn.execute(
                "SELECT "
                "printf('%s%s-%s', substr(tpo_doc,1,1), serie_doc, nro_doc) AS doc_id, "
                "tpo_doc, serie_doc, nro_doc, tipo_operacion, fecha_orig, "
                "id_articulo, nom_articulo, cantidad, cantidad_fae, soles, dolares, "
                "precio_unitario, folio_unico "
                "FROM ventas "
                "WHERE factura_ref_serie = ? AND factura_ref_nro = ? "
                "AND tipo_operacion IN ('devolucion','ajuste_valor','nota_debito') "
                "ORDER BY fecha_orig, id_articulo",
                (serie_doc, nro_doc),
            ).fetchall()
            impacto = conn.execute(
                "SELECT id_articulo, nom_articulo, cant_vendida, soles_vendidos, "
                "precio_original, cant_devuelta, soles_devueltos, n_devoluciones, "
                "soles_descuento, fae_descuento, n_ajustes, soles_nota_debito, "
                "n_notas_debito, saldo_disponible, precio_neto "
                "FROM vw_impacto_documento WHERE serie_doc = ? AND nro_doc = ? "
                "ORDER BY id_articulo",
                (serie_doc, nro_doc),
            ).fetchall()
            return {
                "cabecera": dict(
                    zip(
                        (
                            "tpo_doc",
                            "serie_doc",
                            "nro_doc",
                            "fecha_orig",
                            "mes_ref",
                            "id_cliente",
                            "doc_cliente",
                            "nom_cliente",
                            "n_lineas",
                            "cantidad",
                            "total_soles",
                            "total_dolares",
                            "referencia_factura",
                        ),
                        cab,
                    )
                ),
                "lineas": [
                    dict(
                        zip(
                            (
                                "id_articulo",
                                "nom_articulo",
                                "id_linea",
                                "nom_linea",
                                "cantidad",
                                "cantidad_fae",
                                "soles",
                                "dolares",
                                "precio_unitario",
                                "fecha_orig",
                                "id_vendedor",
                                "nom_vendedor",
                            ),
                            r,
                        )
                    )
                    for r in lineas
                ],
                "asociados": [
                    dict(
                        zip(
                            (
                                "doc_id",
                                "tpo_doc",
                                "serie_doc",
                                "nro_doc",
                                "tipo_operacion",
                                "fecha_orig",
                                "id_articulo",
                                "nom_articulo",
                                "cantidad",
                                "cantidad_fae",
                                "soles",
                                "dolares",
                                "precio_unitario",
                                "folio_unico",
                            ),
                            r,
                        )
                    )
                    for r in asociados
                ],
                "impacto": [
                    dict(
                        zip(
                            (
                                "id_articulo",
                                "nom_articulo",
                                "cant_vendida",
                                "soles_vendidos",
                                "precio_original",
                                "cant_devuelta",
                                "soles_devueltos",
                                "n_devoluciones",
                                "soles_descuento",
                                "fae_descuento",
                                "n_ajustes",
                                "soles_nota_debito",
                                "n_notas_debito",
                                "saldo_disponible",
                                "precio_neto",
                            ),
                            r,
                        )
                    )
                    for r in impacto
                ],
            }
        except Exception:
            log.exception("fetch_documento fallo (%s-%s)", serie_doc, nro_doc)
            raise
        finally:
            if own:
                conn.close()

    def fetch_facturas_disponibles(
        self,
        id_cliente: str,
        id_articulo: str,
        limit: int = 100,
    ) -> pd.DataFrame:
        """Vista vw_facturas_disponibles (saldo LIFO + precio neto)."""
        conn = self._read_conn()
        own = self._db_file is not None
        try:
            return pd.read_sql_query(
                "SELECT * FROM vw_facturas_disponibles WHERE id_cliente = ? AND id_articulo = ? "
                "ORDER BY fecha_orig DESC LIMIT ?",
                conn,
                params=(id_cliente, id_articulo, int(limit)),
            )
        except Exception:
            # Fallback: historial base (igual que el cliente Supabase)
            return self.fetch_historial(
                id_cliente=id_cliente, id_articulo=id_articulo, limit=limit * 5
            )
        finally:
            if own:
                conn.close()

    def test_connection(self) -> tuple[bool, str]:
        """Verifica DB local, tabla ventas y tabla nc_asociadas."""
        if not ventas_db.db_exists():
            return False, (
                f"DB local no existe: {ventas_db.db_path()}. "
                "Usa 'Primera carga' para descargar el historial desde la intranet."
            )
        try:
            conn = self._read_conn()
            own = self._db_file is not None
            try:
                n = conn.execute("SELECT COUNT(*) FROM ventas").fetchone()[0]
                if n == 0:
                    return False, "DB local vacia — ejecuta 'Actualizar datos'."
                # Check nc_asociadas table
                has_tbl = conn.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='nc_asociadas'"
                ).fetchone()[0]
                nc_count = 0
                if has_tbl:
                    nc_count = conn.execute("SELECT COUNT(*) FROM nc_asociadas").fetchone()[0]
                fmax = conn.execute("SELECT MAX(fecha_orig) FROM ventas").fetchone()[0]
                msg = f"OK — {n} filas, {nc_count} NC asociadas, ultimo dato {fmax}"
                return True, msg
            finally:
                if own:
                    conn.close()
        except Exception as e:
            return False, str(e)

    def data_freshness(self) -> dict:
        """Info de frescura para la UI (MAX capturado_en / fecha_max)."""
        health = ventas_db.db_health()
        return health if health.get("exists") else {}

    def fetch_vendedores(
        self,
        min_docs: int = 0,
        solo_lineas_activas: bool = True,
    ) -> list[dict]:
        """Vendedores con VENTAS válidas (facturas/boletas de líneas activas).

        Solo aparece el vendedor si tiene al menos una factura/boleta de línea
        activa (mismo allowlist que ``fetch_clientes``). Los vendedores con solo
        NC/ND o líneas inactivas NO aparecen — a menos que estén anclados (ver
        ``fetch_vendedor_by_id``). El id es canónico (sufijo de 3: '01177' y
        '177' salen como un solo '177' con docs sumados).

        min_docs: umbral mínimo de documentos distintos (F*/B*) por vendedor.
        """
        from src.core.xls_processor import vendedor_corto

        conn = self._read_conn()
        own = self._db_file is not None
        try:
            docs = "(tpo_doc LIKE 'F%' OR tpo_doc LIKE 'B%')"
            where_parts = ["id_vendedor != ''", docs]
            params: list = []
            if solo_lineas_activas:
                where_parts.append(ventas_db.active_line_sql("v.id_linea"))
            sql = (
                "SELECT UPPER(SUBSTR(v.id_vendedor,-3)) AS vid,"
                " MAX(v.nom_vendedor) FROM ventas v "
                "WHERE " + " AND ".join(where_parts)
            )
            sql += " GROUP BY vid"
            if min_docs > 0:
                sql += " HAVING COUNT(DISTINCT v.serie_doc || '/' || v.nro_doc) >= ?"
                params.append(int(min_docs))
            sql += " ORDER BY 2"
            rows = conn.execute(sql, params).fetchall()
            return [
                {"id": vid, "codigo": vendedor_corto(vid), "nombre": (nom or vid).strip()}
                for vid, nom in rows
            ]
        except Exception:
            return []
        finally:
            if own:
                conn.close()

    def fetch_vendedor_by_id(self, id_vendedor: str) -> Optional[dict]:
        """Vendedor por id (cualquiera de sus formas), sin filtro de min_ventas.

        Acepta '01177' o '177' (matchea por sufijo canónico) y retorna el id
        canónico. Permite que un vendedor anclado siga apareciendo en el
        dropdown aunque quede por debajo del umbral de actividad tras una
        importación/recorte.
        """
        if not id_vendedor:
            return None
        from src.core.xls_processor import vendedor_corto

        conn = self._read_conn()
        own = self._db_file is not None
        try:
            row = conn.execute(
                "SELECT UPPER(SUBSTR(id_vendedor,-3)), MAX(nom_vendedor)"
                " FROM ventas WHERE UPPER(SUBSTR(id_vendedor,-3)) = ?"
                " GROUP BY 1",
                (_vendedor_canon(id_vendedor),),
            ).fetchone()
        except Exception:
            return None
        finally:
            if own:
                conn.close()
        if not row:
            return None
        vid, nom = row
        return {"id": vid, "codigo": vendedor_corto(vid), "nombre": (nom or vid).strip()}

    def fetch_client_by_id(self, id_cliente: str) -> Optional[dict]:
        """Cliente por id exacto, SIN filtros de rango/vendedor/líneas.

        Usado para que los clientes anclados (pinned) siempre se muestren en
        el picker aunque no tengan ventas en el rango/filtro actual.
        """
        if not id_cliente:
            return None
        conn = self._read_conn()
        own = self._db_file is not None
        try:
            if self._agg_cliente_mes_ready(conn):
                row = conn.execute(
                    "SELECT MAX(nom_cliente), MAX(doc_cliente), SUM(n_docs) "
                    "FROM agg_cliente_mes WHERE id_cliente = ?",
                    (id_cliente,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT MAX(nom_cliente), MAX(doc_cliente), "
                    "COUNT(DISTINCT serie_doc || '-' || nro_doc) "
                    "FROM ventas WHERE id_cliente = ?",
                    (id_cliente,),
                ).fetchone()
        except Exception:
            return None
        finally:
            if own:
                conn.close()
        if row is None or (row[0] is None and not row[1]):
            return None
        nom, doc, n = row
        return {
            "id": id_cliente,
            "nombre": (nom or id_cliente).strip(),
            "doc": (doc or "").strip(),
            "docs": n or 0,
        }

    def fetch_rucs_partidos(self) -> dict:
        """{RUC: nº de ids_cliente} para RUCs repartidos en varios códigos.

        Históricamente el ERP reasignó el código interno de un cliente
        (mismo RUC, 2 ids) — caso legacy 2010-2022. El picker usa este
        mapa para marcar esas entradas con "N códigos". Solo RUCs reales
        (≥8 chars): el basura '00' compartido por decenas de clientes no
        es un RUC y no se marca. Consulta pequeña (~23 filas).
        """
        conn = self._read_conn()
        own = self._db_file is not None
        try:
            rows = conn.execute(
                "SELECT TRIM(doc_cliente), COUNT(DISTINCT id_cliente)"
                " FROM ventas"
                " WHERE TRIM(COALESCE(doc_cliente,'')) <> ''"
                " AND LENGTH(TRIM(doc_cliente)) >= 8"
                " GROUP BY TRIM(doc_cliente)"
                " HAVING COUNT(DISTINCT id_cliente) > 1",
            ).fetchall()
            return {str(d).strip(): int(n) for d, n in rows}
        except Exception:
            log.exception("fetch_rucs_partidos fallo")
            return {}
        finally:
            if own:
                conn.close()

    def _agg_cliente_mes_ready(self, conn) -> bool:
        """agg_cliente_mes existe y tiene filas -> apto como fast-path."""
        try:
            return conn.execute("SELECT COUNT(*) FROM agg_cliente_mes").fetchone()[0] > 0
        except Exception:
            return False

    def fetch_clientes(
        self,
        vendedor_id: Optional[str] = None,
        fecha_desde: Optional[str] = None,
        fecha_hasta: Optional[str] = None,
        limit: int = 400,
        solo_lineas_activas: bool = True,
        search: Optional[str] = None,
        offset: int = 0,
    ) -> list[dict]:
        """Clientes con VENTAS (facturas/boletas de lineas activas) en el rango, por actividad.

        Solo lineas con `tpo_doc LIKE 'F%' OR 'B%'` y el allowlist de 24
        lineas (mismo criterio que fetch_historial): un cliente que solo tiene
        NC/NDB o productos fuera del allowlist NO aparece (daria "Sin
        resultados"). Orden: facturas/boletas distintas DESC.

        Fast-path: si agg_cliente_mes esta poblado, consulta ese resumen
        (cliente x vendedor x mes, ~27K filas) en vez de escanear 1.5M ->
        rangos largos caen de ~3.3s a <20ms. Fallback al detalle si la tabla
        resumen no existe o esta vacia (p.ej. fixtures de test).

        search: subcadena por nombre/id/doc (LIKE, case-insensitive). Permite
        encontrar cualquier cliente (no solo los top-`limit`) escribiendo.
        offset: para paginacion (picker modal con "cargar mas").
        """
        conn = self._read_conn()
        own = self._db_file is not None
        try:
            use_agg = solo_lineas_activas and self._agg_cliente_mes_ready(conn)
            q = (search or "").strip()
            like = f"%{q}%"
            # Ranking as-you-type: lo que calza exacto o por prefijo
            # (en forma corta o canónica) sube primero; el resto por docs.
            # Así tipear '68414' (o '68') trae su cliente al tope sin
            # esperar la forma completa '00068414'.
            rank_params = []
            if q:

                def _rank(col):
                    return (
                        f"CASE WHEN {col}id_cliente = ?"
                        f" OR {col}id_cliente = ?"
                        f" OR MAX({col}doc_cliente) = ? THEN 0"
                        f" WHEN {col}id_cliente LIKE ?"
                        f" OR LTRIM({col}id_cliente,'0') LIKE ?"
                        f" OR MAX({col}doc_cliente) LIKE ?"
                        f" OR MAX({col}nom_cliente) LIKE ?"
                        " THEN 1 ELSE 2 END, "
                    )

                rank_params = [q, q.zfill(8), q, q + "%", q + "%", q + "%", q + "%"]
            if use_agg:
                where = ["1=1"]
                params: list = []
                if vendedor_id:
                    where.append("UPPER(SUBSTR(id_vendedor,-3)) = ?")
                    params.append(_vendedor_canon(vendedor_id))
                # mes es 'YYYY-MM' (7 chars); truncamos el rango a mes.
                if fecha_desde:
                    where.append("mes >= ?")
                    params.append(str(fecha_desde)[:7])
                if fecha_hasta:
                    where.append("mes <= ?")
                    params.append(str(fecha_hasta)[:7])
                if q:
                    where.append("(nom_cliente LIKE ? OR id_cliente LIKE ? OR doc_cliente LIKE ?)")
                    params += [like, like, like]
                sql = (
                    "SELECT id_cliente, MAX(nom_cliente), MAX(doc_cliente), "
                    "SUM(n_docs) AS n_docs FROM agg_cliente_mes WHERE "
                    + " AND ".join(where)
                    + " GROUP BY id_cliente "
                    "ORDER BY "
                    + (_rank("") if q else "")
                    + "n_docs DESC, id_cliente LIMIT ? OFFSET ?"
                )
                params = params + rank_params
            else:
                where = ["v.id_cliente != ''", "(v.tpo_doc LIKE 'F%' OR v.tpo_doc LIKE 'B%')"]
                params = []
                if solo_lineas_activas:
                    where.append(ventas_db.active_line_sql("v.id_linea"))
                if vendedor_id:
                    where.append("UPPER(SUBSTR(v.id_vendedor,-3)) = ?")
                    params.append(_vendedor_canon(vendedor_id))
                if fecha_desde:
                    where.append("v.fecha_orig >= ?")
                    params.append(fecha_desde)
                if fecha_hasta:
                    where.append("v.fecha_orig <= ?")
                    params.append(fecha_hasta)
                if q:
                    where.append(
                        "(v.nom_cliente LIKE ? OR v.id_cliente LIKE ? OR v.doc_cliente LIKE ?)"
                    )
                    params += [like, like, like]
                sql = (
                    "SELECT v.id_cliente, MAX(v.nom_cliente), MAX(v.doc_cliente), "
                    "COUNT(DISTINCT v.serie_doc || '-' || v.nro_doc) AS n_docs "
                    "FROM ventas v WHERE "
                    + " AND ".join(where)
                    + " GROUP BY v.id_cliente ORDER BY "
                    + (_rank("v.") if q else "")
                    + "n_docs DESC, v.id_cliente LIMIT ? OFFSET ?"
                )
                params = params + rank_params
            params.append(int(limit))
            params.append(int(offset))
            rows = conn.execute(sql, params).fetchall()
            return [
                {"id": cid, "nombre": (nom or cid).strip(), "doc": (doc or "").strip(), "docs": n}
                for cid, nom, doc, n in rows
            ]
        except Exception:
            log.exception("fetch_clientes fallo")
            return []
        finally:
            if own:
                conn.close()

    def fetch_facturas_cliente(
        self,
        id_cliente: str,
        solo_lineas_activas: bool = True,
        fecha_desde: Optional[str] = None,
        fecha_hasta: Optional[str] = None,
    ) -> list[dict]:
        """Facturas de un cliente (TIPO_DOC empieza con F), mas recientes primero.

        solo_lineas_activas: solo facturas con al menos una linea en el
        allowlist de lineas activas (mismo criterio que fetch_historial).
        fecha_desde/fecha_hasta: 'YYYY-MM-DD', filtra por fecha_orig (MAX por
        factura). Sin fechas trae todo el historial del cliente.
        """
        conn = self._read_conn()
        own = self._db_file is not None
        try:
            activo = ""
            if solo_lineas_activas:
                activo = (
                    " AND EXISTS (SELECT 1 FROM ventas f"
                    " WHERE f.serie_doc = ventas.serie_doc AND f.nro_doc = ventas.nro_doc"
                    " AND (f.tpo_doc LIKE 'F%' OR f.tpo_doc LIKE 'B%')"
                    f" AND {ventas_db.active_line_sql('f.id_linea')})"
                )
            where = ["id_cliente = ?", "tpo_doc LIKE 'F%'"]
            params: list = [id_cliente]
            if fecha_desde:
                where.append("fecha_orig >= ?")
                params.append(fecha_desde)
            if fecha_hasta:
                where.append("fecha_orig <= ?")
                params.append(fecha_hasta)
            rows = conn.execute(
                "SELECT tpo_doc, serie_doc, nro_doc, MAX(fecha_orig), SUM(soles) "
                "FROM ventas WHERE " + " AND ".join(where) + activo + " "
                "GROUP BY tpo_doc, serie_doc, nro_doc ORDER BY 4 DESC LIMIT 200",
                params,
            ).fetchall()
            return [
                {
                    "id": f"{tpo}-{serie}-{nro}",
                    "tipo": tpo,
                    "serie": serie,
                    "nro": nro,
                    "fecha": (fecha or "")[:10],
                    "soles": soles or 0,
                }
                for tpo, serie, nro, fecha, soles in rows
            ]
        except Exception:
            return []
        finally:
            if own:
                conn.close()

    def fetch_pedidos_cliente(
        self,
        id_cliente: str,
        solo_lineas_activas: bool = True,
        fecha_desde: Optional[str] = None,
        fecha_hasta: Optional[str] = None,
    ) -> list[dict]:
        """Pedidos unicos (id_pedido) con facturacion de un cliente, mas recientes primero.

        Solo lineas de factura (tpo_doc LIKE 'F%') con id_pedido informado.
        solo_lineas_activas: mismo criterio que fetch_facturas_cliente.
        fecha_desde/fecha_hasta: 'YYYY-MM-DD', filtra por fecha_orig.
        """
        conn = self._read_conn()
        own = self._db_file is not None
        try:
            activo = ""
            if solo_lineas_activas:
                activo = (
                    " AND EXISTS (SELECT 1 FROM ventas f"
                    " WHERE f.serie_doc = ventas.serie_doc AND f.nro_doc = ventas.nro_doc"
                    " AND (f.tpo_doc LIKE 'F%' OR f.tpo_doc LIKE 'B%')"
                    f" AND {ventas_db.active_line_sql('f.id_linea')})"
                )
            where = ["id_cliente = ?", "tpo_doc LIKE 'F%'", "id_pedido != ''"]
            params: list = [id_cliente]
            if fecha_desde:
                where.append("fecha_orig >= ?")
                params.append(fecha_desde)
            if fecha_hasta:
                where.append("fecha_orig <= ?")
                params.append(fecha_hasta)
            rows = conn.execute(
                "SELECT id_pedido, MAX(fecha_orig), SUM(soles), "
                "COUNT(DISTINCT serie_doc || '-' || nro_doc) "
                "FROM ventas WHERE " + " AND ".join(where) + activo + " "
                "GROUP BY id_pedido ORDER BY 2 DESC LIMIT 200",
                params,
            ).fetchall()
            return [
                {
                    "id": ped,
                    "fecha": (fecha or "")[:10],
                    "soles": soles or 0,
                    "n_facturas": int(n) if n else 0,
                }
                for ped, fecha, soles, n in rows
            ]
        except Exception:
            return []
        finally:
            if own:
                conn.close()

    def fetch_ordenes_cliente(
        self,
        id_cliente: str,
        solo_lineas_activas: bool = True,
        fecha_desde: Optional[str] = None,
        fecha_hasta: Optional[str] = None,
    ) -> list[dict]:
        """Ordenes de compra unicas con facturacion de un cliente, mas recientes primero.

        Solo lineas de factura (tpo_doc LIKE 'F%') con ord_compra informado (F2).
        solo_lineas_activas: mismo criterio que fetch_facturas_cliente.
        fecha_desde/fecha_hasta: 'YYYY-MM-DD', filtra por fecha_orig.
        """
        conn = self._read_conn()
        own = self._db_file is not None
        try:
            activo = ""
            if solo_lineas_activas:
                activo = (
                    " AND EXISTS (SELECT 1 FROM ventas f"
                    " WHERE f.serie_doc = ventas.serie_doc AND f.nro_doc = ventas.nro_doc"
                    " AND (f.tpo_doc LIKE 'F%' OR f.tpo_doc LIKE 'B%')"
                    f" AND {ventas_db.active_line_sql('f.id_linea')})"
                )
            where = ["id_cliente = ?", "tpo_doc LIKE 'F%'", "ord_compra != ''"]
            params: list = [id_cliente]
            if fecha_desde:
                where.append("fecha_orig >= ?")
                params.append(fecha_desde)
            if fecha_hasta:
                where.append("fecha_orig <= ?")
                params.append(fecha_hasta)
            rows = conn.execute(
                "SELECT ord_compra, MAX(fecha_orig), SUM(soles), "
                "COUNT(DISTINCT serie_doc || '-' || nro_doc) "
                "FROM ventas WHERE " + " AND ".join(where) + activo + " "
                "GROUP BY ord_compra ORDER BY 2 DESC LIMIT 200",
                params,
            ).fetchall()
            return [
                {
                    "id": oc,
                    "fecha": (fecha or "")[:10],
                    "soles": soles or 0,
                    "n_facturas": int(n) if n else 0,
                }
                for oc, fecha, soles, n in rows
            ]
        except Exception:
            return []
        finally:
            if own:
                conn.close()

    # ── Helper para el pipeline ──────────────────────────────────────

    def to_expediente_historial(self, df: pd.DataFrame) -> pd.DataFrame:
        """Asegura columnas minimas que espera el pipeline."""
        required = [
            "CODIGO",
            "ARTICULO",
            "COD_CLIENTE",
            "CLIENTE",
            "TIPO_DOC",
            "SERIE",
            "NUMERO",
            "FECHA",
            "CANTIDAD",
            "SOLES",
        ]
        for col in required:
            if col not in df.columns:
                df[col] = "" if df[col].dtype == object else 0
        return df


# Alias de compatibilidad durante la migracion
SupabaseVentasClient = VentasDbClient
