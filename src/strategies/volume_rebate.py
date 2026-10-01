import pandas as pd
from src.domain import ExpedienteComercial, RecognitionResult, BusinessAlert
from src.core.utils import normalizar_porcentaje


class VolumeRebateStrategy:
    """
    Aplica % de rebate sobre el total vendido si se alcanza la meta.
    Agrupa por línea de producto y distribuye el rebate proporcionalmente.
    """

    def process(self, expediente: ExpedienteComercial) -> RecognitionResult:
        df = expediente.datos
        config = expediente.contexto.config
        alertas = []

        if df is None or df.empty:
            return RecognitionResult(
                alertas=[
                    BusinessAlert(
                        tipo="error",
                        severidad="alta",
                        mensaje="Historial vacío",
                        motor="VolumeRebate",
                    )
                ],
                resumen={"total_nc": 0, "skus_afectados": 0},
            )

        meta_monto = float(config.get("meta_monto", 0))
        # pct_rebate se conserva en puntos para resumen y %_REBATE (se
        # imprime con formato '0.00' sin signo: 5.00 = 5%); la fraccion
        # para el calculo sale del helper canónico.
        bruto = config.get("porcentaje_rebate", 0)
        rebate_rate, cod_pct = normalizar_porcentaje(bruto)
        try:
            pct_rebate = float(str(bruto).replace("%", "").strip())
        except (TypeError, ValueError):
            pct_rebate = rebate_rate * 100
        if cod_pct == "AL04":
            alertas.append(
                BusinessAlert(
                    codigo="AL04",
                    tipo="warning",
                    severidad="media",
                    mensaje=(
                        f"AL04 - Porcentaje de rebate fuera de rango "
                        f"({bruto}): se interpreto como puntos de "
                        f"porcentaje ({rebate_rate * 100:g}%)."
                    ),
                    motor="VolumeRebate",
                )
            )

        lineas = config.get("lineas", [])
        if lineas and "LINEA" in df.columns:
            df = df[df["LINEA"].astype(str).str.strip().isin(lineas)]
        elif "skus" in config:
            skus = config.get("skus", [])
            if skus and "CODIGO" in df.columns:
                df = df[df["CODIGO"].astype(str).isin(skus)]

        if df.empty:
            return RecognitionResult(
                alertas=[
                    BusinessAlert(
                        tipo="warning",
                        severidad="media",
                        mensaje="No hay datos para las líneas seleccionadas",
                        motor="VolumeRebate",
                    )
                ],
                resumen={"total_nc": 0, "skus_afectados": 0},
            )

        factura_ref = ""
        cols_factura = all(c in df.columns for c in ("TIPO_DOC", "SERIE", "NUMERO"))
        if cols_factura and "SOLES" in df.columns:
            idx_max = df["SOLES"].idxmax()
            row_max = df.loc[idx_max]
            factura_ref = (
                f"{str(row_max['TIPO_DOC']).strip()[0]}"
                f"{str(row_max['SERIE']).strip()}-"
                f"{str(row_max['NUMERO']).strip().replace('.0', '')}"
            )

        grupo_cols = ["LINEA"] if "LINEA" in df.columns else ["CODIGO"]

        agg_dict = {"CANTIDAD": ("CANTIDAD", "sum")}
        if "SOLES" in df.columns:
            monto_col = "SOLES"
            agg_dict["SOLES"] = ("SOLES", "sum")
        elif "CANTIDAD" in df.columns and "PRECIO_UNITARIO" in df.columns:
            monto_col = "SOLES"
            df["_MONTO"] = df["CANTIDAD"] * df["PRECIO_UNITARIO"]
            agg_dict["SOLES"] = ("_MONTO", "sum")
        else:
            monto_col = None
            agg_dict = {"REGISTROS": ("size", "sum")}

        if "CODIGO" in df.columns:
            agg_dict["SKUS"] = ("CODIGO", lambda x: ", ".join(sorted(x.dropna().unique())))

        grouped = df.groupby(grupo_cols, as_index=False).agg(**agg_dict)
        if monto_col is None:
            grouped = grouped.rename(columns={"REGISTROS": "CANTIDAD"})

        if "LINEA" not in grouped.columns:
            grouped = grouped.rename(columns={"CODIGO": "LINEA"})

        total_compra = float(grouped[monto_col].sum()) if monto_col else 0

        nc_por_linea = config.get("nc_por_linea", {})
        if nc_por_linea:
            grouped["MONTO_NC_ND"] = grouped[grupo_cols[0]].map(nc_por_linea).fillna(0).round(2)
        else:
            grouped["MONTO_NC_ND"] = 0.0

        if meta_monto > 0 and total_compra < meta_monto:
            alertas.append(
                BusinessAlert(
                    tipo="warning",
                    severidad="media",
                    mensaje=f"No se alcanzó la meta de S/ {meta_monto:,.2f} (total: S/ {total_compra:,.2f}). Revisar.",
                    motor="VolumeRebate",
                )
            )
            return RecognitionResult(
                dataframe=pd.DataFrame(),
                alertas=alertas,
                resumen={"total_nc": 0, "skus_afectados": 0, "doc_ref": factura_ref},
                metricas={
                    "total_venta": total_compra,
                    "meta": meta_monto,
                    "porcentaje_rebate": pct_rebate,
                    "factura_ref": factura_ref,
                },
            )

        if monto_col:
            grouped["MONTO_NC"] = (grouped[monto_col] * rebate_rate).round(2)
            grouped["%_DEL_TOTAL"] = (grouped[monto_col] / total_compra * 100).round(2)
        else:
            grouped["MONTO_NC"] = 0.0
            grouped["%_DEL_TOTAL"] = 0.0

        grouped["%_REBATE"] = pct_rebate
        total_nc = float(grouped["MONTO_NC"].sum())

        label_meta = "Meta alcanzada" if total_compra >= meta_monto else "Meta no alcanzada"
        alertas.append(
            BusinessAlert(
                tipo="success" if total_compra >= meta_monto else "warning",
                severidad="baja",
                mensaje=f"{label_meta}: S/ {total_compra:,.2f} {'≥' if total_compra >= meta_monto else '<'} S/ {meta_monto:,.2f}",
                motor="VolumeRebate",
            )
        )

        return RecognitionResult(
            dataframe=grouped,
            resumen={"total_nc": total_nc, "skus_afectados": len(grouped), "doc_ref": factura_ref},
            metricas={
                "total_venta": total_compra,
                "meta": meta_monto,
                "porcentaje_rebate": pct_rebate,
                "factura_ref": factura_ref,
            },
            alertas=alertas,
        )
