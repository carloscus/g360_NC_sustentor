import pandas as pd
import logging
from typing import Optional
from src.core.utils import format_doc_id

logger = logging.getLogger(__name__)


def seleccionar_mejor_documento(
    df_historial: pd.DataFrame,
    df_requerimientos: pd.DataFrame,
    *,
    cliente: Optional[str] = None,
) -> str:
    """
    Selecciona la mejor factura del historial como documento de referencia
    (sustento) para un requerimiento de Feria/Preventa.

    Criterios:
    1. Coincidencia de SKUs: cuantos más SKUs del requerimiento aparezcan en
       la factura, mejor.
    2. Monto suficiente: el total de la factura (SOLES) debe ser >= monto
       calculado del requerimiento (suma de cantidad * precio * descuento).
    3. Si ninguna factura cumple el monto, se elige la que tenga más SKUs
       coincidentes (fallback).
    4. Desempate: fecha más reciente.

    Args:
        df_historial: DataFrame del ERP pre-filtrado por cliente (o vacío).
        df_requerimientos: DataFrame con columnas ``CODIGO``, ``CANTIDAD_NC``,
            ``PORCENTAJE_DESC``.
        cliente: Nombre del cliente (solo para logging).

    Returns:
        ``DOC_ID_UNI`` de la mejor factura, o cadena vacía si no se encontró
        ninguna.
    """
    if df_historial is None or df_historial.empty:
        logger.warning("Historial vacío, no se puede seleccionar documento")
        return ""

    if df_requerimientos is None or df_requerimientos.empty:
        logger.warning("Requerimientos vacíos, no se puede seleccionar documento")
        return ""

    req = df_requerimientos.copy()
    req.columns = [str(c).strip().upper() for c in req.columns]

    skus_req = (
        set(req["CODIGO"].astype(str).str.strip().unique()) if "CODIGO" in req.columns else set()
    )
    if not skus_req:
        logger.warning("Requerimientos sin columna CODIGO")
        return ""

    # 1. Calcular monto total del requerimiento
    monto_req_total = 0.0
    for _, row in req.iterrows():
        sku = str(row.get("CODIGO", "")).strip()
        if not sku:
            continue
        cant = float(pd.to_numeric(row.get("CANTIDAD_NC", 0), errors="coerce") or 0)
        porc = _convertir_porcentaje(row.get("PORCENTAJE_DESC", 0))

        hist_art = df_historial[df_historial["CODIGO"].astype(str).str.strip() == sku]
        if not hist_art.empty:
            p_unit = float(
                pd.to_numeric(hist_art.iloc[0].get("PRECIO_UNITARIO", 0), errors="coerce") or 0
            )
            monto_req_total += cant * p_unit * (1 - porc)

    logger.info(
        "Monto requerido calculado: S/ %.2f para %d SKUs (%s)",
        monto_req_total,
        len(skus_req),
        cliente or "sin cliente",
    )

    # 2. Construir DOC_ID_UNI por fila
    hist = df_historial.copy()
    hist["DOC_ID_UNI"] = hist.apply(
        lambda x: format_doc_id(x.get("TIPO_DOC", ""), x.get("SERIE", ""), x.get("NUMERO", "")),
        axis=1,
    )

    # 3. Agrupar por documento
    docs_stats = (
        hist.groupby("DOC_ID_UNI")
        .agg(
            SOLES=("SOLES", "sum"),
            SKUS=("CODIGO", lambda x: set(x.astype(str).str.strip())),
            FECHA=("FECHA", "max"),
        )
        .reset_index()
    )

    if docs_stats.empty:
        logger.warning("No se encontraron documentos en el historial filtrado")
        return ""

    # 4. Calcular score de coincidencia
    docs_stats["SCORE_SKUS"] = docs_stats["SKUS"].apply(lambda x: len(x.intersection(skus_req)))

    # 5. Identificar mejor documento
    docs_solventes = docs_stats[docs_stats["SOLES"] >= monto_req_total].copy()

    if not docs_solventes.empty:
        mejor = docs_solventes.sort_values(
            by=["SCORE_SKUS", "SOLES", "FECHA"],
            ascending=[False, False, False],
        ).iloc[0]
        logger.info(
            "Documento de referencia: %s | %d SKUs coinciden | S/ %.2f >= S/ %.2f (solvente)",
            mejor["DOC_ID_UNI"],
            mejor["SCORE_SKUS"],
            mejor["SOLES"],
            monto_req_total,
        )
        return str(mejor["DOC_ID_UNI"])

    # Fallback: el que más SKUs coincida, aunque no cubra el monto
    mejor = docs_stats.sort_values(
        by=["SCORE_SKUS", "SOLES", "FECHA"],
        ascending=[False, False, False],
    ).iloc[0]
    logger.info(
        "Documento de referencia: %s | %d SKUs coinciden | S/ %.2f (fallback, sin solvente)",
        mejor["DOC_ID_UNI"],
        mejor["SCORE_SKUS"],
        mejor["SOLES"],
    )
    return str(mejor["DOC_ID_UNI"])


def _convertir_porcentaje(valor) -> float:
    """Convierte un valor de porcentaje a FRACCION (0.05 = 5%).

    Delegado al helper canónico: el corte anterior (``>= 0.5``) partía a la
    mitad cualquier descuento real de 50% o más.
    """
    # Guard against array-like inputs that cause ambiguous truth value errors
    if hasattr(valor, "__len__") and not isinstance(valor, (str, bytes)):
        return 0.0
    from src.core.utils import normalizar_porcentaje

    return normalizar_porcentaje(valor)[0]
