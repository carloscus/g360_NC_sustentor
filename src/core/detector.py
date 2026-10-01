"""
Detección de NC/NDB existentes que referencian a facturas en el historial.
Permite identificar qué facturas ya tienen Notas de Crédito o Débito aplicadas,
por SKU, para evitar sobre-sustentar y mostrar alertas al usuario.
"""

import re
import pandas as pd
import logging
from typing import Dict

logger = logging.getLogger(__name__)


def _format_doc_id(tpo: str, serie: str, nro: str) -> str:
    """Formato consistente para IDs de documentos."""
    t = str(tpo).strip()[:1].upper() if pd.notna(tpo) else ""
    s = str(serie).strip().upper() if pd.notna(serie) else ""
    n = str(nro).strip() if pd.notna(nro) else ""
    if s and not s.startswith(t):
        s = f"{t}{s}"
    elif not s:
        s = t
    return f"{s}-{n}" if n else s


format_doc_id = _format_doc_id  # alias público


def _es_documento_nota(tpo_doc: str) -> bool:
    """Retorna True si el TIPO_DOC corresponde a NC, NDB, ND.
    Soporta tanto valores raw (NC, NDB) como normalizados (N)."""
    t = str(tpo_doc).strip().upper()
    return t.startswith("NC") or t in ("NDB", "ND", "NOTA CREDITO", "NOTA DEBITO") or t == "N"


def _es_factura(tpo_doc: str) -> bool:
    """Retorna True si el TIPO_DOC corresponde a Factura o Boleta."""
    t = str(tpo_doc).strip().upper()
    return t.startswith("F") or t.startswith("B") or t in ("FACTURA", "BOLETA")


def clasificar_nota(tipo_doc: str, cantidad: float, soles: float) -> dict:
    """
    Clasifica una NC/ND según su impacto real en ventas de producto.

    Returns:
        dict con: categoria, afecta_cantidad, afecta_valor, es_correccion_real
    """
    tpo = str(tipo_doc).strip().upper()

    # NDB/ND se considera ajuste de valor aunque el ERP informe cantidad: en
    # este flujo la FAE es la base para ponderar el cargo; no es devolución
    # física. La devolución física se identifica por una NCR/NC con cantidad.
    if tpo.startswith("ND") or tpo in ("NOTA DEBITO", "NOTA DÉBITO"):
        return {
            "categoria": "cargo",
            "afecta_cantidad": False,
            "afecta_valor": bool(soles),
            "es_correccion_real": bool(soles),
        }

    if cantidad != 0:
        return {
            "categoria": "devolucion",
            "afecta_cantidad": True,
            "afecta_valor": True,
            "es_correccion_real": True,
        }

    if tpo in ("NC", "NCR", "NOTA CREDITO"):
        if soles < 0:
            return {
                "categoria": "descuento",
                "afecta_cantidad": False,
                "afecta_valor": True,
                "es_correccion_real": True,
            }
        if soles > 0:
            return {
                "categoria": "cargo",
                "afecta_cantidad": False,
                "afecta_valor": True,
                "es_correccion_real": True,
            }

    # NDB, ND, etc. sin cantidad → administrativo/financiero
    return {
        "categoria": "descuento" if soles < 0 else "cargo",
        "afecta_cantidad": False,
        "afecta_valor": bool(soles),
        "es_correccion_real": False,
    }


def clasificar_notas_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Agrega columnas CATEGORIA, AFECTA_CANTIDAD, AFECTA_VALOR, ES_CORRECION_REAL
    a un DataFrame que contiene filas de NC/ND.
    """
    if df.empty:
        return df.copy()
    df = df.copy()
    clasif = df.apply(
        lambda r: clasificar_nota(
            str(r.get("TIPO_DOC", "")),
            float(r.get("CANTIDAD", 0)),
            float(r.get("SOLES", 0)),
        ),
        axis=1,
        result_type="expand",
    )
    for col in ["categoria", "afecta_cantidad", "afecta_valor", "es_correccion_real"]:
        df[col.upper()] = clasif[col].values
    return df


def detectar_notas_en_historial(df: pd.DataFrame) -> pd.DataFrame:
    """
    Identifica filas del historial que son NC/NDB y extrae qué factura referencian.

    Returns:
        DataFrame con columnas: DOC_NOTA, TPO_NOTA, SERIE_NOTA, NRO_NOTA,
        FACTURA_REF, CODIGO, CANTIDAD, CANTIDAD_FAE, SOLES
    """
    if df.empty:
        return pd.DataFrame()

    resultados = []
    for _, row in df.iterrows():
        tpo = str(row.get("TIPO_DOC", "")).strip()
        if not _es_documento_nota(tpo):
            continue

        # VentasDbClient already exposes the canonical reference from the
        # factura_ref_serie/factura_ref_nro columns. Prefer it; reparsing the
        # free-text REFERENCIA can disagree for non-standard ERP strings.
        factura_ref = str(row.get("FACTURA_REF", "") or "").strip()
        if not factura_ref:
            serie_ref = str(row.get("FACTURA_REF_SERIE", "") or "").strip()
            nro_ref = str(row.get("FACTURA_REF_NRO", "") or "").strip()
            if serie_ref and nro_ref:
                factura_ref = _format_doc_id("F", serie_ref, nro_ref)
        if not factura_ref:
            ref_raw = str(row.get("REFERENCIA", ""))
            factura_ref = _parsear_referencia_factura(ref_raw)

        nota_doc = _format_doc_id(
            row.get("TIPO_DOC", ""),
            row.get("SERIE", ""),
            row.get("NUMERO", ""),
        )
        try:
            fae = float(row.get("CANTIDAD_FAE", 0) or 0)
        except (TypeError, ValueError):
            fae = 0.0

        resultados.append(
            {
                "DOC_NOTA": nota_doc,
                "TPO_NOTA": tpo,
                "SERIE_NOTA": str(row.get("SERIE", "")),
                "NRO_NOTA": str(row.get("NUMERO", "")),
                "FACTURA_REF": factura_ref,
                "COD_CLIENTE": str(row.get("COD_CLIENTE", "") or "").strip(),
                "DOC_CLIENTE": str(row.get("DOC_CLIENTE", "") or "").strip(),
                "CODIGO": str(row.get("CODIGO", "")),
                "ARTICULO": str(row.get("ARTICULO", "")),
                "CANTIDAD": float(row.get("CANTIDAD", 0)),
                "CANTIDAD_FAE": fae,
                "SOLES": float(row.get("SOLES", 0)),
            }
        )

    df_notas = pd.DataFrame(resultados)
    if not df_notas.empty:
        df_notas = clasificar_notas_df(df_notas)
        logger.info(f"Detectadas {len(df_notas)} filas de NC/NDB en el historial")
    return df_notas


def _parsear_referencia_factura(ref: str) -> str:
    """
    Parsea la columna REFERENCIA para extraer el ID de factura original.
    Formatos esperados: F026/001-1234567, F001-100, 001-1234567
    """
    if not ref or ref in ("nan", "None", ""):
        return ""
    ref = str(ref).strip()

    # Patrón: F026/001-1234567
    m = re.search(r"([A-Za-z]?\d+)[/\-](\d+)\-(\d+)", ref)
    if m:
        prefijo = m.group(1)
        serie = m.group(2)
        nro = m.group(3)
        return _format_doc_id(prefijo[0] if prefijo else "F", serie, nro)

    # Patrón simple: F001-100
    m = re.search(r"([A-Za-z]?\d+)\-(\d+)", ref)
    if m:
        return _format_doc_id(m.group(1)[0] if m.group(1) else "F", m.group(1)[1:], m.group(2))

    return ref


parsear_referencia_factura = _parsear_referencia_factura  # alias público


def _cantidad_nota_fae(row) -> tuple:
    """Cantidad calculable de una nota: FAE si CANTIDAD es 0.

    En NC/NDB la CANTIDAD es 0 y el FAE trae el valor calculable.
    Retorna (qty, desde_fae: bool).
    """
    try:
        cant = float(row.get("CANTIDAD", 0) or 0)
    except (TypeError, ValueError):
        cant = 0.0
    try:
        fae = float(row.get("CANTIDAD_FAE", 0) or 0)
    except (TypeError, ValueError):
        fae = 0.0
    if abs(cant) > 0:
        return abs(cant), False
    return abs(fae), True


def _tipo_nota_corto(tpo: str) -> str:
    """Etiqueta corta del tipo de nota: NC o NDB."""
    t = str(tpo or "").strip().upper()
    if t.startswith("NC") or t in ("NOTA CREDITO",):
        return "NC"
    return "NDB"


def resumen_notas_por_factura(df_notas: pd.DataFrame) -> Dict[str, dict]:
    """
    Agrupa las NC/NDB detectadas por factura referenciada.

    Returns:
        Dict[factura_id, {total_notas, total_soles, skus: {sku: {cantidad, soles,
        docs: [{doc, tipo, qty, fae}]}}}]
    """
    if df_notas.empty:
        return {}

    resumen = {}
    for _, row in df_notas.iterrows():
        factura = row["FACTURA_REF"]
        if not factura:
            continue
        if factura not in resumen:
            resumen[factura] = {"total_notas": 0, "total_soles": 0.0, "skus": {}}
        resumen[factura]["total_notas"] += 1
        resumen[factura]["total_soles"] += abs(row["SOLES"])

        sku = row["CODIGO"]
        if sku not in resumen[factura]["skus"]:
            resumen[factura]["skus"][sku] = {
                "cantidad": 0.0,
                "soles": 0.0,
                "nombres": set(),
                "docs": [],
            }
        qty, desde_fae = _cantidad_nota_fae(row)
        resumen[factura]["skus"][sku]["cantidad"] += abs(row["CANTIDAD"])
        resumen[factura]["skus"][sku]["soles"] += abs(row["SOLES"])
        resumen[factura]["skus"][sku]["nombres"].add(str(row.get("ARTICULO", "")))
        resumen[factura]["skus"][sku]["docs"].append(
            {
                "doc": str(row.get("DOC_NOTA", "")),
                "tipo": _tipo_nota_corto(row.get("TPO_NOTA", "")),
                "qty": qty,
                "fae": desde_fae,
            }
        )

    return resumen


def obtener_notas_de_factura(df_notas: pd.DataFrame, factura_id: str) -> pd.DataFrame:
    """
    Filtra las NC/NDB que referencian a una factura específica.
    """
    if df_notas.empty:
        return pd.DataFrame()
    return df_notas[df_notas["FACTURA_REF"] == factura_id].copy()


def separar_inventario(df: pd.DataFrame) -> dict:
    """
    Separa el historial en: facturas (para FIFO) y notas (NC/NDB).

    Returns:
        {"facturas": DataFrame solo con facturas,
         "notas": DataFrame solo con NC/NDB,
         "resumen_notas": dict con resumen por factura}
    """
    if df.empty:
        return {"facturas": pd.DataFrame(), "notas": pd.DataFrame(), "resumen_notas": {}}

    mask_notas = (
        df["TIPO_DOC"]
        .astype(str)
        .str.strip()
        .str.upper()
        .apply(
            lambda t: (
                t.startswith("NC") or t in ("NDB", "ND", "NOTA CREDITO", "NOTA DEBITO") or t == "N"
            )
        )
    )
    df_notas = df[mask_notas].copy()
    df_notas = clasificar_notas_df(df_notas)
    df_facturas = df[~mask_notas].copy()

    logger.info(f"Separación: {len(df_facturas)} facturas, {len(df_notas)} NC/NDB en historial")

    notas_detectadas = detectar_notas_en_historial(df)
    resumen = resumen_notas_por_factura(notas_detectadas)

    return {
        "facturas": df_facturas,
        "notas": df_notas,
        "resumen_notas": resumen,
    }
