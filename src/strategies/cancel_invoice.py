from src.domain import (
    ExpedienteComercial,
    RecognitionResult,
    BusinessAlert,
    ReconocimientoPorCondicion,
    generar_texto_alerta,
)


class CancelInvoiceStrategy:
    """
    Genera NC por el 100% del valor de una factura específica.
    No necesita lista de precios ni SKU — solo historial + selección de factura.
    """

    def process(self, expediente: ExpedienteComercial) -> RecognitionResult:
        df = expediente.datos
        config = expediente.contexto.config
        alertas = []
        trazabilidad = []

        factura_id = config.get("factura_id", "")
        if not factura_id:
            return RecognitionResult(
                alertas=[
                    BusinessAlert(
                        tipo="error",
                        severidad="alta",
                        mensaje="No se seleccionó ninguna factura",
                        motor="CancelInvoice",
                    )
                ],
                resumen={"total_nc": 0, "skus_afectados": 0},
            )

        trazabilidad.append(f"Anulando factura: {factura_id}")

        # Parsear factura seleccionada
        if "-" in factura_id:
            serie_nro, nro_part = factura_id.rsplit("-", 1)
            tipo_prefix = serie_nro[0]  # "F" for "F01204"
        else:
            tipo_prefix, nro_part = "F", factura_id

        # Filtrar historial por esa factura (usar startswith + numeric nro)
        mask_tipo = df["TIPO_DOC"].astype(str).str.strip().str.upper().str.startswith(tipo_prefix)
        mask_serie = df["SERIE"].astype(str).str.strip() != ""
        mask_nro = df["NUMERO"].astype(str).str.strip() == nro_part
        mask = mask_tipo & mask_serie & mask_nro

        factura_df = df[mask].copy()
        if factura_df.empty:
            return RecognitionResult(
                alertas=[
                    BusinessAlert(
                        tipo="error",
                        severidad="alta",
                        mensaje=f"Factura {factura_id} no encontrada en historial",
                        motor="CancelInvoice",
                    )
                ],
                resumen={"total_nc": 0, "skus_afectados": 0},
            )

        # Calcular total
        total_factura = factura_df["SOLES"].sum()
        total_cantidad = factura_df["CANTIDAD"].sum()
        skus = factura_df["CODIGO"].nunique()
        factura_label = factura_id

        trazabilidad.append(
            f"Factura {factura_label}: {skus} SKU, {total_cantidad:.0f} unid, S/ {total_factura:.2f}"
        )

        # Construir resultado: cada línea de factura (sin agrupar)
        cols_base = ["CODIGO", "ARTICULO", "CANTIDAD", "PRECIO_UNITARIO", "SOLES"]
        cols_disponibles = [c for c in cols_base if c in factura_df.columns]
        resultado = factura_df[cols_disponibles].copy()
        if "LINEA" in factura_df.columns:
            resultado["LINEA"] = factura_df["LINEA"]
        resultado = resultado.rename(
            columns={"CODIGO": "SKU", "PRECIO_UNITARIO": "PRECIO_HIST", "SOLES": "MONTO_FACTURA"}
        )
        resultado["FACTURA"] = factura_label
        resultado["MONTO_NC"] = resultado["MONTO_FACTURA"]  # 100% del valor
        resultado["ALERTA"] = generar_texto_alerta("OK")

        # Extraer datos para QR (referencia a factura original)
        fecha_factura = (
            str(factura_df["FECHA"].iloc[0].date()) if "FECHA" in factura_df.columns else ""
        )
        ruc_cliente = (
            str(factura_df["DOC_CLIENTE"].iloc[0]).strip()
            if "DOC_CLIENTE" in factura_df.columns
            else ""
        )
        igv_factura = round(total_factura * 0.18 / 1.18, 2)  # IGV incluido en SOLES

        rec = ReconocimientoPorCondicion(
            condicion_id="anular_factura",
            fuente=factura_label,
            estrategia="CancelInvoice",
            monto_reconocido=float(total_factura),
            cantidad_aplicada=float(total_cantidad),
            skus=factura_df["CODIGO"].unique().tolist(),
            documentos=[factura_label],
        )

        return RecognitionResult(
            dataframe=resultado,
            por_condicion=[rec],
            resumen={"total_nc": float(total_factura), "skus_afectados": skus},
            metricas={
                "total_cantidad": float(total_cantidad),
                "factura": factura_label,
                "fecha_factura": fecha_factura,
                "ruc_cliente": ruc_cliente,
                "igv_factura": igv_factura,
            },
            alertas=alertas,
            trazabilidad=trazabilidad,
        )
