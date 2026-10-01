import pandas as pd
import numpy as np
import re
from src.core.utils import format_doc_id
from src.domain import (
    ExpedienteComercial,
    RecognitionResult,
    BusinessAlert,
    ReconocimientoPorCondicion,
    generar_texto_alerta,
)


def _seleccionar_mejor_documento_promo(df: pd.DataFrame) -> str:
    """Elige el documento con mayor SOLES del historial filtrado."""
    if df is None or df.empty:
        return ""
    tmp = df.copy()
    tmp["DOC_ID_UNI"] = tmp.apply(
        lambda x: format_doc_id(x.get("TIPO_DOC", ""), x.get("SERIE", ""), x.get("NUMERO", "")),
        axis=1,
    )
    agrupado = tmp.groupby("DOC_ID_UNI")["SOLES"].sum().reset_index()
    if agrupado.empty:
        return ""
    mejor = agrupado.sort_values("SOLES", ascending=False).iloc[0]
    return str(mejor["DOC_ID_UNI"])


class PromotionBonusStrategy:
    """
    Calcula unidades bonificadas según mecánica promocional (12+1, 48+1, etc.)
    Agrupa ventas del SKU en el periodo y aplica la regla.
    """

    def process(self, expediente: ExpedienteComercial) -> RecognitionResult:
        df = expediente.datos
        alertas = []
        trazabilidad = []

        if df.empty:
            return RecognitionResult(
                alertas=[
                    BusinessAlert(
                        tipo="error",
                        severidad="alta",
                        mensaje="Historial vacío",
                        motor="PromotionBonus",
                    )
                ],
                resumen={"total_nc": 0, "skus_afectados": 0},
            )

        # Obtener configuración
        config = expediente.contexto.config
        mecanica = config.get("mecanica", "12+1")
        nivel = config.get("nivel_calculo", "sku")
        sku_seleccionados = config.get("skus", [])
        fecha_desde = config.get("fecha_desde")
        fecha_hasta = config.get("fecha_hasta")

        # Parsear mecánica
        match = re.match(r"(\d+)\s*\+\s*(\d+)", mecanica)
        if not match:
            return RecognitionResult(
                alertas=[
                    BusinessAlert(
                        tipo="error",
                        severidad="alta",
                        mensaje=f"Mecánica inválida: {mecanica}",
                        motor="PromotionBonus",
                    )
                ],
                resumen={"total_nc": 0, "skus_afectados": 0},
            )
        comprar = int(match.group(1))
        regalar = int(match.group(2))
        trazabilidad.append(f"Mecánica: {mecanica} ({comprar}+{regalar})")

        # Filtrar por SKU
        if sku_seleccionados:
            df = df[df["CODIGO"].isin(sku_seleccionados)]
            if df.empty:
                return RecognitionResult(
                    alertas=[
                        BusinessAlert(
                            tipo="warning",
                            severidad="media",
                            mensaje="Ningún SKU seleccionado tiene ventas",
                        )
                    ],
                    resumen={"total_nc": 0, "skus_afectados": 0},
                )

        # Filtrar por fecha
        if fecha_desde and "FECHA" in df.columns:
            df = df[df["FECHA"] >= fecha_desde]
        if fecha_hasta and "FECHA" in df.columns:
            df = df[df["FECHA"] <= fecha_hasta]

        # Seleccionar el mejor documento de referencia del historial filtrado
        doc_referencia = _seleccionar_mejor_documento_promo(df)

        # Si hay condición comercial, usarla para precios
        precio_unitario = None
        if expediente.condiciones:
            cond = expediente.condiciones[0]
            if isinstance(cond, pd.DataFrame) and "PRECIO_UNITARIO" in cond.columns:
                precio_unitario = cond["PRECIO_UNITARIO"].iloc[0] if not cond.empty else None

        grupos = ["CODIGO", "ARTICULO"]
        if nivel == "linea" and "LINEA" in df.columns:
            grupos.append("LINEA")

        # Agrupar por SKU y agregar cantidades
        grouped = df.groupby(grupos, as_index=False).agg(
            CANTIDAD_TOTAL=("CANTIDAD", "sum"),
            SOLES_TOTAL=("SOLES", "sum"),
            FACTURAS=("NUMERO", lambda x: "; ".join(x.dropna().unique())),
        )

        # Calcular bonificación
        if precio_unitario:
            grouped["PRECIO_UNITARIO"] = precio_unitario
        else:
            grouped["PRECIO_UNITARIO"] = np.where(
                grouped["CANTIDAD_TOTAL"] > 0,
                grouped["SOLES_TOTAL"] / grouped["CANTIDAD_TOTAL"],
                0,
            )

        grouped["COMPRAR"] = comprar
        grouped["CICLOS"] = grouped["CANTIDAD_TOTAL"] // comprar
        grouped["UNIDADES_BONIFICADAS"] = grouped["CICLOS"] * regalar
        grouped["MONTO_NC"] = (grouped["UNIDADES_BONIFICADAS"] * grouped["PRECIO_UNITARIO"]).round(
            2
        )

        # Alertas
        sin_bonif = grouped[grouped["UNIDADES_BONIFICADAS"] == 0]
        for _, row in sin_bonif.iterrows():
            sku = str(row.get("CODIGO", ""))
            total = float(row.get("CANTIDAD_TOTAL", 0))
            meta = float(comprar + regalar)
            texto = generar_texto_alerta("AL05", sku=sku, total=total, meta=meta)
            alertas.append(
                BusinessAlert(
                    codigo="AL05",
                    tipo="info",
                    severidad="baja",
                    sku=sku,
                    mensaje=texto,
                    motor="PromotionBonus",
                )
            )

        con_bonif = grouped[grouped["UNIDADES_BONIFICADAS"] > 0]
        total_monto = float(con_bonif["MONTO_NC"].sum())
        total_skus = len(con_bonif)

        # Construir resultado
        resultado_cols = [
            "CODIGO",
            "ARTICULO",
            "CANTIDAD_TOTAL",
            "UNIDADES_BONIFICADAS",
            "PRECIO_UNITARIO",
            "MONTO_NC",
            "FACTURAS",
            "CICLOS",
        ]
        if nivel == "linea" and "LINEA" in grouped.columns:
            resultado_cols.insert(1, "LINEA")
        cols_existentes = [c for c in resultado_cols if c in grouped.columns]

        df_result = (
            grouped[cols_existentes]
            .copy()
            .rename(
                columns={
                    "CODIGO": "SKU",
                    "CANTIDAD_TOTAL": "CANTIDAD",
                    "UNIDADES_BONIFICADAS": "BONIFICACION",
                }
            )
        )
        df_result["ALERTA"] = df_result["SKU"].apply(
            lambda s: (
                generar_texto_alerta("OK")
                if s in con_bonif["CODIGO"].values
                else generar_texto_alerta("AL05", sku=s, total=0, meta=0)
            )
        )

        rec = ReconocimientoPorCondicion(
            condicion_id="promo_main",
            fuente=f"promotion_{mecanica}",
            estrategia="PromotionBonus",
            cantidad_aplicada=float(con_bonif["CANTIDAD_TOTAL"].sum()),
            monto_reconocido=total_monto,
            skus=con_bonif["CODIGO"].unique().tolist(),
        )

        trazabilidad.append(
            f"Promoción {mecanica}: {total_skus} SKU bonificados, total S/ {total_monto:.2f}"
        )

        return RecognitionResult(
            dataframe=df_result,
            por_condicion=[rec],
            resumen={
                "total_nc": total_monto,
                "skus_afectados": total_skus,
                "documento_referencia": doc_referencia,
            },
            metricas={
                "unidades_bonificadas": int(con_bonif["UNIDADES_BONIFICADAS"].sum()),
                "mecanica": mecanica,
            },
            alertas=alertas,
            trazabilidad=trazabilidad,
        )
