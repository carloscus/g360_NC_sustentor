import pandas as pd
import logging

from src.core.utils import split_doc_id

logger = logging.getLogger(__name__)


def update_inventory_balances(df_h: pd.DataFrame, items: list) -> pd.DataFrame:
    """Descuenta las cantidades procesadas del historial actual por cada documento utilizado."""
    for item in items:
        if item.DOCUMENTOS_CANTIDAD:
            for doc_str, cant_tomada in item.DOCUMENTOS_CANTIDAD.items():
                if "-" not in doc_str:
                    continue
                tipo_serie, nro_target = doc_str.rsplit("-", 1)
                tipo_target = tipo_serie[0] if tipo_serie else "F"
                serie_target = tipo_serie[1:] if len(tipo_serie) > 1 else ""

                mask_match = False
                indices_art = df_h.index[df_h["CODIGO"] == item.CODIGO]
                for idx in indices_art:
                    row = df_h.loc[idx]
                    _, serie_h, nro_h = split_doc_id(
                        row["TIPO_DOC"] if "TIPO_DOC" in df_h.columns else tipo_target,
                        row["SERIE"],
                        row["NUMERO"],
                    )

                    if serie_h == serie_target and nro_h == nro_target:
                        precio_u = float(df_h.at[idx, "PRECIO_UNITARIO"])
                        nuevo_v = max(
                            0.0, float(df_h.at[idx, "CANTIDAD"]) - float(cant_tomada or 0)
                        )
                        logger.debug(
                            f"Descontando {cant_tomada} de '{item.CODIGO}' Doc({doc_str}). Nuevo saldo: {nuevo_v}"
                        )
                        df_h.at[idx, "CANTIDAD"] = nuevo_v
                        df_h.at[idx, "SOLES"] = round(nuevo_v * precio_u, 4)
                        mask_match = True
                        break

                if not mask_match:
                    logger.warning(
                        f"No se encontró documento {doc_str} para {item.CODIGO} en el historial."
                    )
        else:
            mask = df_h["CODIGO"] == item.CODIGO
            nro_item = str(item.NUMERO).strip()
            serie_item = str(item.SERIE).strip()
            for idx in df_h[mask].index:
                if df_h.at[idx, "CODIGO"] == item.CODIGO:
                    match_found = False
                    try:
                        if nro_item in str(df_h.at[idx, "NUMERO"]) and serie_item in str(
                            df_h.at[idx, "SERIE"]
                        ):
                            match_found = True
                    except Exception:
                        pass

                    if match_found:
                        precio_u = float(df_h.at[idx, "PRECIO_UNITARIO"])
                        nuevo_v = max(
                            0.0,
                            float(df_h.at[idx, "CANTIDAD"])
                            - float(item.CANTIDAD_REAL_ENCONTRADA or 0),
                        )
                        logger.debug(
                            f"Descontando {item.CANTIDAD_REAL_ENCONTRADA} de '{item.CODIGO}' (Doc: {item.NUMERO}). Nuevo saldo: {nuevo_v}"
                        )
                        df_h.at[idx, "CANTIDAD"] = nuevo_v
                        df_h.at[idx, "SOLES"] = round(nuevo_v * precio_u, 4)
                        break

    return df_h[pd.to_numeric(df_h["CANTIDAD"], errors="coerce").fillna(0) > 0].reset_index(
        drop=True
    )
