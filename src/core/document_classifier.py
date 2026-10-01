"""
Clasifica todas las filas del historial en una sola pasada:
- DOC_ID único por documento
- TIPO_CLASE (factura, devolucion, descuento, cargo)
- AFECTA_CANTIDAD / AFECTA_VALOR
- FACTURA_REF (referencia parseada)
- NC_ASOCIADAS (lista de NC/ND que referencian a esta factura)
"""

import pandas as pd
import logging
from src.core.detector import (
    _es_factura,
    _es_documento_nota,
    clasificar_nota,
    format_doc_id,
    _parsear_referencia_factura,
)

logger = logging.getLogger(__name__)


class DocumentClassifier:
    """Clasificador universal de documentos del historial."""

    def classify(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Agrega columnas de clasificación al DataFrame.

        Returns:
            DataFrame con nuevas columnas: DOC_ID, TIPO_CLASE,
            AFECTA_CANTIDAD, AFECTA_VALOR, FACTURA_REF, NC_ASOCIADAS
        """
        if df is None or df.empty:
            return df

        df = df.copy()
        n = len(df)

        col_tpo = "TIPO_DOC" if "TIPO_DOC" in df.columns else None
        col_serie = "SERIE" if "SERIE" in df.columns else None
        col_num = "NUMERO" if "NUMERO" in df.columns else None
        col_cant = "CANTIDAD" if "CANTIDAD" in df.columns else None
        col_soles = "SOLES" if "SOLES" in df.columns else None
        col_ref = "REFERENCIA" if "REFERENCIA" in df.columns else None

        # DOC_ID
        if col_tpo and col_serie and col_num:
            df["DOC_ID"] = df.apply(
                lambda r: format_doc_id(
                    r.get(col_tpo, ""), r.get(col_serie, ""), r.get(col_num, "")
                ),
                axis=1,
            )
        else:
            df["DOC_ID"] = ""

        # TIPO_CLASE
        def _classify_row(r):
            tpo = str(r.get(col_tpo, "")).strip().upper() if col_tpo else ""
            if not tpo:
                return "sin_impacto"
            if _es_factura(tpo):
                return "factura"
            if _es_documento_nota(tpo):
                cant = float(r.get(col_cant, 0))
                soles = float(r.get(col_soles, 0))
                return clasificar_nota(tpo, cant, soles)["categoria"]
            return "sin_impacto"

        df["TIPO_CLASE"] = df.apply(_classify_row, axis=1)

        # AFECTA_CANTIDAD / AFECTA_VALOR
        es_factura_mask = df["TIPO_CLASE"] == "factura" if n > 0 else pd.Series([False] * n)

        def _afecta(r):
            if r["TIPO_CLASE"] == "factura":
                return pd.Series([True, True])
            if r["TIPO_CLASE"] == "sin_impacto":
                return pd.Series([False, False])
            tpo = str(r.get(col_tpo, "")).strip().upper()
            cant = float(r.get(col_cant, 0))
            soles = float(r.get(col_soles, 0))
            c = clasificar_nota(tpo, cant, soles)
            return pd.Series([c["afecta_cantidad"], c["afecta_valor"]])

        if n > 0:
            df[["AFECTA_CANTIDAD", "AFECTA_VALOR"]] = df.apply(
                _afecta, axis=1, result_type="expand"
            )
        else:
            df["AFECTA_CANTIDAD"] = False
            df["AFECTA_VALOR"] = False

        # FACTURA_REF
        if col_ref:
            df["FACTURA_REF"] = df[col_ref].apply(
                lambda v: (
                    _parsear_referencia_factura(str(v)) if pd.notna(v) and str(v).strip() else ""
                )
            )
        else:
            df["FACTURA_REF"] = ""

        # NC_ASOCIADAS: doc_id → lista de NC/ND
        mapping = {}
        if n > 0 and not df["FACTURA_REF"].empty:
            notas = df[df["TIPO_CLASE"] != "factura"]
            for _, r in notas.iterrows():
                ref = str(r.get("FACTURA_REF", "")).strip()
                if ref and r.get("DOC_ID", ""):
                    mapping.setdefault(ref, []).append(r["DOC_ID"])
        df["NC_ASOCIADAS"] = df["DOC_ID"].map(mapping)
        df["NC_ASOCIADAS"] = df["NC_ASOCIADAS"].apply(lambda x: x if isinstance(x, list) else [])

        logger.info(
            f"Clasificación: {int(es_factura_mask.sum())} facturas, "
            f"{int((df['TIPO_CLASE'] == 'devolucion').sum())} devoluciones, "
            f"{int((df['TIPO_CLASE'] == 'descuento').sum())} descuentos, "
            f"{int((df['TIPO_CLASE'] == 'cargo').sum())} cargos, "
            f"{int((df['TIPO_CLASE'] == 'sin_impacto').sum())} sin impacto"
        )
        return df


def resumen_global(df_clasificado: pd.DataFrame) -> str:
    """Genera resumen de todo el historial clasificado."""
    if df_clasificado is None or df_clasificado.empty:
        return ""
    labels = {
        "factura": "Facturas",
        "devolucion": "Devoluciones",
        "descuento": "Descuentos",
        "cargo": "Cargos",
    }
    partes = []
    for cat, label in labels.items():
        mask = df_clasificado["TIPO_CLASE"] == cat
        count = mask.sum()
        if count == 0:
            continue
        total = float(df_clasificado.loc[mask, "SOLES"].sum())
        partes.append(f"{label}: {count:02d} S/{total:,.2f}")
    return " | ".join(partes) if partes else ""
