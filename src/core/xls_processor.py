"""Procesador del reporte Estadistica11 — port de src/processor/parser.rs (g360-ventas-db).

Acepta el export D0 (XLS binario via xlrd) o el grid L0 (HTML -> matriz) y produce
filas `ventas` con campos derivados: tipo_operacion, factura_ref_serie/nro,
folio_unico, precio_unitario. Aplica allowlist de lineas y resuelve NC/ND
cross-mes contra la DB (igual que el Rust).
"""

from __future__ import annotations

import io
import logging
import sqlite3
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# ── Utilidades numericas (port de parse_f64) ────────────────────────────────


def parse_f64(s: str, is_quantity: bool) -> float:
    s = (s or "").strip()
    if not s:
        return 0.0
    if is_quantity:
        # Cantidad: punto siempre decimal, coma se elimina ("1,000" -> 1000? no:
        # en el Rust is_quantity elimina comas y parsea => "25.056" -> 25.056)
        return _to_float(s.replace(",", ""))
    if "." in s and "," in s:
        # Desambiguar por posicion del ultimo separador (el decimal es el ULTIMO):
        #   "1,234.56" (US, grid web L0) -> punto despues de coma -> coma=miles
        #   "1.000,50" (latino, XLS D0)  -> coma despues de punto -> punto=miles
        if s.rfind(".") > s.rfind(","):
            return _to_float(s.replace(",", ""))
        return _to_float(s.replace(".", "").replace(",", "."))
    if "," in s:
        pos = s.find(",")
        decimals = len(s) - pos - 1
        if decimals >= 3:
            return _to_float(s.replace(",", ""))
        return _to_float(s.replace(",", "."))
    if "." in s:
        pos = s.find(".")
        after = s[pos + 1 :]
        if len(after) == 3 and after == "000":
            return _to_float(s.replace(".", ""))
        return _to_float(s)
    return _to_float(s)


def _to_float(s: str) -> float:
    try:
        return float(s)
    except ValueError:
        return 0.0


def parse_f64_ctx(s: str, col_name: str) -> float:
    up = (col_name or "").strip().upper()
    is_quantity = up in ("CANTIDAD", "CANTIDAD FAE") or "UNIDADES" in up or "PRECIO UNITARIO" in up
    return parse_f64(s, is_quantity)


# ── Utilidades de texto (port de clean_str/clean_id/normalizers) ────────────

_ACCENTS = str.maketrans("ÁÉÍÓÚÑáéíóúñ", "AEIOUNaeioun")


def clean_str(s: str) -> str:
    t = (s or "").replace("\u00a0", " ").replace("  ", " ").strip()
    return t.translate(_ACCENTS)


def clean_id(s: str) -> str:
    t = (s or "").replace("\ufeff", "").strip()
    if not t:
        return ""
    if t.lower() in ("nan", "none", "null", "<na>"):
        return ""
    while t.endswith(".0"):
        cut = len(t)
        while cut > 0 and t[cut - 1] == "0":
            cut -= 1
        if cut > 0 and t[cut - 1] == ".":
            t = t[: cut - 1]
            continue
        break
    return t


def normalize_client_id(s: str) -> str:
    t = clean_id(s)
    if not t:
        return ""
    return t.rjust(8, "0") if len(t) <= 8 else t.rjust(11, "0")


def normalize_line_id(s: str) -> str:
    """Canónico pelado ('0101'→'01', '01AD'→'AD'; espejo de g360-ventas-db).

    El ERP emite con o sin prefijo empresa '01' según la época del archivo;
    se normaliza a pelado para no duplicar líneas en la DB.
    """
    t = clean_id(s).upper()
    if t.startswith(PREFIJO_EMPRESA) and len(t) > 2:
        return t[2:]
    return t


def normalize_seller_id(s: str) -> str:
    """Canónico pelado ('01177'→'177'; espejo de g360-ventas-db)."""
    t = clean_id(s).upper()
    if t.startswith(PREFIJO_EMPRESA) and len(t) > 3:
        return t[2:]
    return t


def normalize_orden_compra(s: str) -> str:
    """Canónico de orden de compra para agrupar y filtrar.

    El ERP la emite con padding y mayúsculas inconsistentes
    (' 001561 '→'1561', 'oc2020245992'→'OC2020245992'). Los ceros a la
    izquierda solo se quitan si es todo numérica; lo alfanumérico se
    conserva tal cual     (puede ser significativo: 'P01275' vs 'P1275').
    También se eliminan caracteres de control (ej. '\x1f' que a veces
    prefija el valor en el export).
    """
    t = clean_id(s).upper()
    if not t:
        return ""
    t = "".join(c for c in t.split())
    t = "".join(c for c in t if c.isprintable())
    if not t:
        return ""
    if t.isdigit():
        t = t.lstrip("0") or "0"
    return t


# Prefijo de empresa en los codigos del ERP: linea '0102' = empresa(01) + codigo(02);
# vendedor '01177' = empresa(01) + codigo(177). El allowlist compara el CODIGO (sufijo).
PREFIJO_EMPRESA = "01"


def linea_corta(id_linea: str) -> str:
    """'0102' -> '02', '01AD' -> 'AD' (omite el prefijo de empresa)."""
    t = normalize_line_id(id_linea)
    if len(t) == 4 and t.startswith(PREFIJO_EMPRESA):
        return t[2:]
    return t


def vendedor_corto(id_vendedor: str) -> str:
    """'01052' -> '052', '01M17' -> 'M17' (omite el prefijo de empresa)."""
    t = normalize_seller_id(id_vendedor)
    if len(t) == 5 and t.startswith(PREFIJO_EMPRESA):
        return t[2:]
    return t


def parse_date(s: str) -> str:
    """Normaliza cualquier fecha a ``yyyy-mm-dd`` ('' si no se puede).

    Delega en `src.core.fechas` para no tener un segundo parser: antes este
    solo aceptaba dd/mm/yyyy y dd-mm-yyyy, y una fila con fecha ISO devolvía
    '' — que el llamador de la línea 483 convierte en `continue`, o sea la fila
    entera se perdía en silencio. Ahora acepta ISO, dd/mm/yyyy, dd-mm-yyyy,
    yyyy/mm/dd y cualquiera de esos con hora pegada.
    """
    from src.core.fechas import fecha_iso

    return fecha_iso(s)


# ── Derivados (port de derivar_campos) ──────────────────────────────────────


def derivar_campos(v: dict) -> None:
    tpo = v["tpo_doc"]
    cant = v["cantidad"]
    has_ref = bool((v.get("referencia") or "").strip())
    if tpo == "NCR" and cant < 0:
        v["tipo_operacion"] = "devolucion"
    elif tpo == "NCR":
        v["tipo_operacion"] = "ajuste_valor"
    elif tpo == "NDB":
        v["tipo_operacion"] = "nota_debito"
    else:
        v["tipo_operacion"] = "venta"
    v["factura_ref_serie"] = ""
    v["factura_ref_nro"] = ""
    if has_ref:
        rest = v["referencia"].rsplit("/", 1)[-1]
        if "-" in rest:
            serie, nro = rest.split("-", 1)
            v["factura_ref_serie"] = clean_id(serie).upper()
            v["factura_ref_nro"] = clean_id(nro)
    v["folio_unico"] = f"{v['tpo_doc']}/{v['serie_doc']}/{v['nro_doc']}"


# ── Lectura de fuentes (XLS binario / HTML grid / CSV) ──────────────────────

_HEADER_MARKERS = ("ANHO", "ID_CLIENTE", "TPO_DOC", "ID_ARTICULO")


def _is_header_row(row: list[str]) -> bool:
    up = [c.strip().upper() for c in row]
    return any(marker in up for marker in _HEADER_MARKERS)


# Tokens de la fila de TIPOS que el grid del navegador expone bajo el header
# ('texto', '99', 'no va', 'numero decimal'...). No es un dato: se descarta.
_TOKENS_FILA_TIPOS = frozenset(
    {
        "texto",
        "no va",
        "numero",
        "numero decimal",
        "nova",
        "folio texto",
        "folio",
        "fecha",
        "fecha-mes",
        "fecha año",
    }
)


def read_xls_bytes(payload: bytes) -> list[list[str]]:
    """XLS OLE binario -> matriz de strings (equivale a xls_to_csv de calamine)."""
    import xlrd

    wb = xlrd.open_workbook(file_contents=payload)
    sheet = wb.sheet_by_index(0)
    rows: list[list[str]] = []
    for r in range(sheet.nrows):
        record: list[str] = []
        for cell in sheet.row(r):
            if cell.ctype == 0:  # EMPTY
                record.append("")
            elif cell.ctype == 2:  # NUMBER
                if float(cell.value).is_integer():
                    record.append(str(int(cell.value)))
                else:
                    record.append(repr(cell.value))
            elif cell.ctype in (3, 4):  # DATE / BOOL
                record.append(str(cell.value))
            else:
                record.append(str(cell.value).strip())
        if any(record):
            rows.append(record)
    if not rows:
        raise ValueError("xls sin filas")
    return rows


def read_html_bytes(payload: bytes) -> list[list[str]]:
    """Grid HTML (L0 o export disfrazado) -> matriz de la tabla mayor."""
    from src.core.intranet_client import extract_largest_table_html

    rows = extract_largest_table_html(payload.decode("utf-8", "ignore"))
    if len(rows) < 2:
        raise ValueError("HTML sin tabla de datos")
    return rows


def read_csv_bytes(payload: bytes) -> list[list[str]]:
    import csv as _csv

    text = payload.decode("utf-8-sig", "ignore")
    return [row for row in _csv.reader(io.StringIO(text)) if any(row)]


def load_report_source(payload: bytes, kind: str) -> list[list[str]]:
    """kind: 'xls' | 'html' | 'csv'. Auto-detecta si el binario es HTML disfrazado."""
    if kind == "xls":
        if payload[:1] == b"<":
            return read_html_bytes(payload)
        if payload[:2] == b"PK":
            return read_xlsx_bytes(payload)
        return read_xls_bytes(payload)
    if kind == "html":
        return read_html_bytes(payload)
    return read_csv_bytes(payload)


def read_xlsx_bytes(payload: bytes) -> list[list[str]]:
    """XLSX (zip) -> matriz. Requiere openpyxl (ya en deps)."""
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(payload), read_only=True, data_only=True)
    ws = wb.worksheets[0]
    rows: list[list[str]] = []
    for row in ws.iter_rows(values_only=True):
        record = [
            ""
            if c is None
            else (str(int(c)) if isinstance(c, float) and c.is_integer() else str(c).strip())
            for c in row
        ]
        if any(record):
            rows.append(record)
    wb.close()
    return rows


# ── Parseo principal ────────────────────────────────────────────────────────


@dataclass
class ParseOutput:
    ventas: list[dict] = field(default_factory=list)
    nc_nd_pendientes: list[dict] = field(default_factory=list)


# Mapeo: marcador de header (contains) -> campo destino
COLUMN_MAP = [
    ("ANHO", "anho_str"),
    ("MES", "_mes"),
    ("ID_CLIENTE", "id_cliente_raw"),
    ("DOC_CLIENTE", "doc_cliente"),
    ("NOM_CLIENTE", "nom_cliente"),
    ("NOM_DEPARTAMENTO", "departamento"),
    ("NOM_PROVINCIA", "provincia"),
    ("NOM_DISTRITO", "distrito"),
    ("ID_LINEA", "id_linea_raw"),
    ("NOM_LINEA", "nom_linea"),
    ("ID_GRUPO", "id_grupo"),
    ("NOM_GRUPO", "nom_grupo"),
    ("ID_TIPO", "id_tipo"),
    ("NOM_TIPO", "nom_tipo"),
    ("ID_FAMILIA", "id_familia"),
    ("NOM_FAMILIA", "nom_familia"),
    ("ID_ARTICULO", "id_articulo_raw"),
    ("NOM_ARTICULO", "nom_articulo"),
    ("ID_VENDEDOR", "id_vendedor_raw"),
    ("NOM_VENDEDOR", "nom_vendedor"),
    ("COD_SUCURSAL", "cod_sucursal"),
    ("NOM_SUCURSAL", "nom_sucursal"),
    ("TPO_DOC", "tpo_doc"),
    ("SERIE_DOC", "serie_doc"),
    ("NRO_DOC", "nro_doc"),
    ("REFERENCIA", "referencia"),
    ("FECHA_ORIG", "fecha_orig_str"),
    ("FECHA_REF", "fecha_ref"),
    ("MONEDA", "moneda"),
    ("CANTIDAD FAE", "cantidad_fae_str"),
    ("CANTIDAD", "cantidad_str"),
    ("SOLES", "soles_str"),
    ("DOLARES", "dolares_str"),
    ("ID_PEDIDO", "id_pedido"),
    ("ORD_COMPRA", "ord_compra"),
    ("FECHA_VENC", "fecha_venc"),
    ("ID_LOCALIDAD_UBIGEO", "id_ubigeo"),
    ("ESTADO_LINEA", "estado_linea"),
    ("CANAL DE DISTRIBUCION", "canal_distribucion"),
    ("ID_GUIA", "id_guia"),
    ("NOM_CONDICION_PAGO", "nom_condicion_pago"),
    ("DIVISION", "division"),
    ("FEC_CARGO", "fec_cargo"),
]


def _map_headers(header_row: list[str]) -> dict[str, int]:
    headers = [h.strip().upper() for h in header_row]
    mapping: dict[str, int] = {}
    for marker, field_name in COLUMN_MAP:
        # CANTIDAD FAE debe matchear antes que CANTIDAD; SOLES antes que otros
        for idx, h in enumerate(headers):
            if marker in h and field_name not in mapping:
                # Evitar colision: CANTIDAD no debe capturar 'CANTIDAD FAE'
                if marker == "CANTIDAD" and "FAE" in h:
                    continue
                if marker == "SOLES" and "DOLARES" in h:
                    continue
                if marker == "MES" and "MES_REF" in h:
                    continue
                mapping[field_name] = idx
                break
    return mapping


def parse_report_rows(rows: list[list[str]], label: str, file_source: str = "") -> ParseOutput:
    """Port de parse_export_csv_inner: matriz -> ParseOutput(ventas, pendientes)."""
    header_idx = next((i for i, r in enumerate(rows) if _is_header_row(r)), None)
    if header_idx is None:
        raise ValueError("reporte sin fila de encabezado reconocible")
    colmap = _map_headers(rows[header_idx])
    out = ParseOutput()

    def g(record: list[str], name: str) -> str:
        idx = colmap.get(name)
        return clean_str(record[idx]) if idx is not None and idx < len(record) else ""

    mes_num = int(label[5:7]) if len(label) >= 7 and label[5:7].isdigit() else 0
    sin_fecha = 0
    _log = logging.getLogger(__name__)
    raw_rows: list[dict] = []
    for record in rows[header_idx + 1 :]:
        if not any(record):
            continue
        # Headers repetidos (partes concatenadas por split) se descartan
        if _is_header_row(record):
            continue
        id_articulo = clean_id(g(record, "id_articulo_raw"))
        id_cliente = normalize_client_id(g(record, "id_cliente_raw"))
        if not id_articulo or not id_cliente:
            continue
        # Fila de tipos del grid (no es un dato): se descarta.
        if (
            g(record, "id_articulo_raw").strip().lower() in _TOKENS_FILA_TIPOS
            or g(record, "id_cliente_raw").strip().lower() in _TOKENS_FILA_TIPOS
        ):
            continue
        cantidad = parse_f64_ctx(g(record, "cantidad_str"), "CANTIDAD")
        cantidad_fae = parse_f64_ctx(g(record, "cantidad_fae_str"), "CANTIDAD FAE")
        soles = round(parse_f64_ctx(g(record, "soles_str"), "SOLES") * 100.0) / 100.0
        dolares = round(parse_f64_ctx(g(record, "dolares_str"), "DOLARES") * 100.0) / 100.0
        base_qty = cantidad if cantidad != 0.0 else cantidad_fae
        precio_raw = soles / base_qty if base_qty != 0.0 else 0.0
        precio_unitario = round(precio_raw * 10_000.0) / 10_000.0
        anho = int(clean_id(g(record, "anho_str")) or 0) or (
            int(label[:4]) if label[:4].isdigit() else 0
        )
        moneda = g(record, "moneda") or "Soles"
        v = {
            "id_articulo": id_articulo,
            "original_sku": g(record, "id_articulo_raw"),
            "nom_articulo": g(record, "nom_articulo"),
            "id_linea": normalize_line_id(g(record, "id_linea_raw")),
            "nom_linea": g(record, "nom_linea"),
            "id_grupo": g(record, "id_grupo"),
            "nom_grupo": g(record, "nom_grupo"),
            "id_tipo": g(record, "id_tipo"),
            "nom_tipo": g(record, "nom_tipo"),
            "id_familia": g(record, "id_familia"),
            "nom_familia": g(record, "nom_familia"),
            "id_cliente": id_cliente,
            "doc_cliente": g(record, "doc_cliente"),
            "nom_cliente": g(record, "nom_cliente"),
            "tpo_doc": g(record, "tpo_doc").upper(),
            "serie_doc": clean_id(g(record, "serie_doc")).upper(),
            "nro_doc": clean_id(g(record, "nro_doc")),
            "referencia": g(record, "referencia"),
            "moneda": moneda,
            "cantidad": cantidad,
            "cantidad_fae": cantidad_fae,
            "soles": soles,
            "dolares": dolares,
            "precio_unitario": precio_unitario,
            "anho": anho,
            "mes": mes_num,
            "fecha_orig": parse_date(g(record, "fecha_orig_str")),
            "fecha_ref": g(record, "fecha_ref") or None,
            "fecha_venc": g(record, "fecha_venc") or None,
            "cod_sucursal": g(record, "cod_sucursal"),
            "nom_sucursal": g(record, "nom_sucursal"),
            "departamento": g(record, "departamento"),
            "provincia": g(record, "provincia"),
            "distrito": g(record, "distrito"),
            "id_vendedor": normalize_seller_id(g(record, "id_vendedor_raw")),
            "nom_vendedor": g(record, "nom_vendedor"),
            "id_pedido": "_".join(str(g(record, "id_pedido") or "").split()),
            "ord_compra": normalize_orden_compra(g(record, "ord_compra")),
            "id_ubigeo": g(record, "id_ubigeo"),
            "estado_linea": g(record, "estado_linea"),
            "canal_distribucion": g(record, "canal_distribucion"),
            "id_guia": g(record, "id_guia"),
            "nom_condicion_pago": g(record, "nom_condicion_pago"),
            "division": g(record, "division"),
            "fec_cargo": g(record, "fec_cargo"),
            "file_source": file_source,
            # F6/C1: mes_ref siempre mensual canónico (YYYY-MM), aunque el
            # chunk sea diario. El día vive en fecha_orig; el watermark diario
            # vive en day_state. Un label diario acá rompería el contrato.
            "mes_ref": label[:7] if len(label) >= 7 else label,
            "tipo_operacion": "",
            "factura_ref_serie": "",
            "factura_ref_nro": "",
            "folio_unico": "",
        }
        if not v["fecha_orig"]:
            # Descartar la fila es correcto (sin fecha no hay nada que
            # indexar), pero no puede ser silencioso: antes se perdían filas
            # sin dejar rastro. Se cuentan y se avisa por log.
            sin_fecha += 1
            continue
        derivar_campos(v)
        raw_rows.append(v)

    # F6 (espejo completo): se guarda TODO, sin filtro de allowlist.
    # El allowlist es filtro de pantalla (consultas), no de guardado: dos PCs
    # con líneas distintas guardan archivos idénticos y el cartucho viaja.
    # Las NC/ND van directo a ventas (su factura está o llegará, y una NCR
    # sin factura igual es un documento real que afecta totales).
    for v in raw_rows:
        out.ventas.append(v)
    if sin_fecha:
        # Antes el descarte era invisible. Una fila sin fecha no se puede
        # indexar, pero hay que saber cuantas son: si aparecen de golpe, el
        # origen cambio de formato de fecha.
        _log.warning(
            "parse_report_rows: %d fila(s) descartada(s) por fecha ilegible", sin_fecha
        )

    return out


# ── Cross-month NC/ND (port de resolve_nc_nd_cross) ─────────────────────────


def resolve_nc_nd_cross(
    conn: sqlite3.Connection, pendientes: list[dict]
) -> tuple[list[dict], list[dict]]:
    """Resuelve NC/ND cuya factura no esta en el mismo archivo contra la DB.
    Devuelve (resueltas, sin_resolver)."""
    if not pendientes:
        return [], []
    resueltas: list[dict] = []
    sin_resolver: list[dict] = []
    for v in pendientes:
        serie, nro = v["factura_ref_serie"], v["factura_ref_nro"]
        if not serie or not nro:
            sin_resolver.append(v)
            continue
        row = conn.execute(
            "SELECT COUNT(*) FROM ventas WHERE serie_doc = ? AND nro_doc = ? AND tpo_doc LIKE 'F01%'",
            (serie, nro),
        ).fetchone()
        (resueltas if row[0] > 0 else sin_resolver).append(v)
    if resueltas:
        log.info(
            "cross-month: %d NC/ND resueltas, %d sin factura en BD",
            len(resueltas),
            len(sin_resolver),
        )
    return resueltas, sin_resolver
