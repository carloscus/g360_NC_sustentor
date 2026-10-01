import pandas as pd
from src.domain import RecognitionResult, BusinessAlert, generar_texto_alerta
from src.core.utils import normalizar_porcentaje


class DescuentoFacturaStrategy:
    """
    Aplica % de descuento a una factura específica.
    - Sin archivo SKU: usa un solo % global (descuento_pct) para todos los SKU.
    - Con archivo SKU (CODIGO_SKU + DESCUENTO_PORCENTAJE): cada SKU tiene su propio %.
    NC = PRECIO_UNITARIO × fraccion × CANTIDAD
    Los porcentajes pasan por normalizar_porcentaje (0.05 = 5%, acepta
    ademas '5%' y puntos de porcentaje con alerta AL04).
    """

    def process(self, expediente) -> RecognitionResult:
        config = expediente.contexto.config
        descuento_pct, cod_pct = normalizar_porcentaje(config.get("descuento_pct", 0))
        factura_id = config.get("factura_id", "")
        sku_filter = config.get("sku_filter", None)

        df = expediente.datos.copy() if expediente.datos is not None else pd.DataFrame()

        if df.empty:
            return RecognitionResult(
                alertas=[
                    BusinessAlert(
                        tipo="error",
                        severidad="alta",
                        mensaje="No hay datos de factura para procesar",
                        motor="DescuentoFactura",
                    )
                ],
                resumen={"total_nc": 0, "skus_afectados": 0},
            )

        if sku_filter:
            df = df[df["CODIGO"].astype(str).isin(sku_filter.keys())].copy()
        else:
            if descuento_pct <= 0:
                return RecognitionResult(
                    alertas=[
                        BusinessAlert(
                            tipo="error",
                            severidad="alta",
                            mensaje="El porcentaje de descuento debe ser mayor a 0",
                            motor="DescuentoFactura",
                        )
                    ],
                    resumen={"total_nc": 0, "skus_afectados": 0},
                )

        if df.empty:
            return RecognitionResult(
                alertas=[
                    BusinessAlert(
                        tipo="info",
                        severidad="baja",
                        mensaje="Ningún SKU de la factura coincide con el filtro",
                        motor="DescuentoFactura",
                    )
                ],
                resumen={"total_nc": 0, "skus_afectados": 0},
            )

        alertas = []
        if cod_pct == "AL04":
            alertas.append(
                BusinessAlert(
                    codigo="AL04",
                    tipo="warning",
                    severidad="media",
                    mensaje=(
                        f"AL04 - Porcentaje global fuera de rango: se "
                        f"interpreto como puntos de porcentaje "
                        f"({descuento_pct * 100:g}%)."
                    ),
                    motor="DescuentoFactura",
                )
            )
        elif cod_pct == "AL10":
            alertas.append(
                BusinessAlert(
                    codigo="AL10",
                    tipo="info",
                    severidad="baja",
                    mensaje="AL10 - Porcentaje global no interpretable; se tomo 0.",
                    motor="DescuentoFactura",
                )
            )
        result_rows = []
        for _, row in df.iterrows():
            sku = str(row.get("CODIGO", ""))
            articulo = str(row.get("ARTICULO", ""))
            cantidad = float(row.get("CANTIDAD", 0))
            precio = float(row.get("PRECIO_UNITARIO", 0))

            if sku_filter and sku in sku_filter:
                pct, cod_sku = normalizar_porcentaje(sku_filter[sku])
                if cod_sku == "AL04":
                    alertas.append(
                        BusinessAlert(
                            codigo="AL04",
                            tipo="warning",
                            severidad="media",
                            sku=sku,
                            mensaje=(
                                f"AL04 - Porcentaje del SKU {sku} fuera de "
                                f"rango: se interpreto como puntos de "
                                f"porcentaje ({pct * 100:g}%)."
                            ),
                            motor="DescuentoFactura",
                        )
                    )
            else:
                pct = descuento_pct

            # pct ya es fraccion (0.05 = 5%): antes se dividia otra vez /100
            # y un 0.05 capturado daba 100x menos de NC.
            monto_nc = round(precio * pct * cantidad, 2)

            result_rows.append(
                {
                    "SKU": sku,
                    "ARTICULO": articulo,
                    "CANTIDAD": cantidad,
                    "PRECIO_UNITARIO": precio,
                    "%_DESCUENTO": pct,
                    "MONTO_NC": monto_nc,
                    "FACTURA": factura_id,
                    "ALERTA": generar_texto_alerta("OK"),
                }
            )

        df_result = pd.DataFrame(result_rows) if result_rows else pd.DataFrame()
        total_nc = df_result["MONTO_NC"].sum() if "MONTO_NC" in df_result.columns else 0

        if sku_filter:
            pct_label = "per-SKU"
        else:
            pct_label = f"{descuento_pct * 100:g}%"
        return RecognitionResult(
            dataframe=df_result,
            resumen={
                "total_nc": total_nc,
                "skus_afectados": len(result_rows),
                "doc_ref": factura_id,
            },
            alertas=alertas,
            trazabilidad=[
                f"Descuento Factura: {pct_label} en {len(result_rows)} SKU, factura {factura_id}",
            ],
        )
