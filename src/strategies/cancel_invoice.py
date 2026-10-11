from src.domain import (
    ExpedienteComercial,
    RecognitionResult,
    BusinessAlert,
    ReconocimientoPorCondicion,
    generar_texto_alerta,
)

import pandas as pd


def _parse_factura_id(factura_id) -> tuple[str, str]:
    """(tipo_prefix, nro) desde "TIPO-SERIE-NRO" (tolerante)."""
    fid = str(factura_id).strip()
    if "-" in fid:
        serie_nro, nro_part = fid.rsplit("-", 1)
        tipo_prefix = serie_nro[0].upper() if serie_nro else "F"
    else:
        tipo_prefix, nro_part = "F", fid
    nro_norm = nro_part.strip().lstrip("0") or "0"
    return tipo_prefix, nro_norm


class CancelInvoiceStrategy:
    """
    Genera NC por el 100% del valor de una o varias facturas específicas,
    de uno o varios clientes. No necesita lista de precios ni SKU —
    solo historial + selección de factura(s).
    """

    def process(self, expediente: ExpedienteComercial) -> RecognitionResult:
        df = expediente.datos
        config = expediente.contexto.config
        alertas = []
        trazabilidad = []

        factura_ids = config.get("factura_ids") or []
        if isinstance(factura_ids, str):
            factura_ids = [factura_ids]
        if not factura_ids and config.get("factura_id"):
            factura_ids = [config.get("factura_id")]
        factura_ids = [str(f).strip() for f in factura_ids if str(f).strip()]
        if not factura_ids:
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

        trazabilidad.append(f"Anulando {len(factura_ids)} factura(s): {', '.join(factura_ids)}")

        # Una o varias facturas (quizas de varios clientes): se filtran por
        # separado y se concatenan, etiquetando cada fila con su factura.
        # El numero se compara sin ceros ("067375" y "67375" son el mismo).
        partes = []
        for fid in factura_ids:
            tipo_prefix, nro_norm = _parse_factura_id(fid)
            mask = (
                df["TIPO_DOC"].astype(str).str.strip().str.upper().str.startswith(tipo_prefix)
                & (df["SERIE"].astype(str).str.strip() != "")
                & (
                    df["NUMERO"].astype(str).str.strip().str.lstrip("0").replace("", "0")
                    == nro_norm
                )
            )
            sub = df[mask].copy()
            if sub.empty:
                alertas.append(
                    BusinessAlert(
                        tipo="warning",
                        severidad="media",
                        mensaje=f"Factura {fid} no encontrada en historial",
                        motor="CancelInvoice",
                    )
                )
                continue
            sub["_factura_label"] = fid
            partes.append(sub)

        if not partes:
            detalle = factura_ids[0] if len(factura_ids) == 1 else ", ".join(factura_ids)
            return RecognitionResult(
                alertas=[
                    BusinessAlert(
                        tipo="error",
                        severidad="alta",
                        mensaje=f"Factura {detalle} no encontrada en historial",
                        motor="CancelInvoice",
                    )
                ],
                resumen={"total_nc": 0, "skus_afectados": 0},
            )

        factura_df = pd.concat(partes, ignore_index=True)

        # Calcular total
        total_factura = factura_df["SOLES"].sum()
        total_cantidad = factura_df["CANTIDAD"].sum()
        skus = factura_df["CODIGO"].nunique()

        for fid in sorted(factura_df["_factura_label"].unique()):
            sub = factura_df[factura_df["_factura_label"] == fid]
            trazabilidad.append(
                f"Factura {fid}: {sub['CODIGO'].nunique()} SKU, "
                f"{sub['CANTIDAD'].sum():.0f} unid, S/ {sub['SOLES'].sum():.2f}"
            )

        # Construir resultado: cada línea de factura (sin agrupar)
        cols_base = ["CODIGO", "ARTICULO", "CANTIDAD", "PRECIO_UNITARIO", "SOLES"]
        cols_disponibles = [c for c in cols_base if c in factura_df.columns]
        resultado = factura_df[cols_disponibles].copy()
        if "LINEA" in factura_df.columns:
            resultado["LINEA"] = factura_df["LINEA"]
        # Cliente por fila (el historial trae ambas variantes de nombre):
        # habilita tabs por cliente y expedientes separados.
        for dst, srcs in (
            ("CLIENTE", ("nom_cliente", "CLIENTE")),
            ("COD_CLIENTE", ("id_cliente", "COD_CLIENTE")),
            ("DOC_CLIENTE", ("doc_cliente", "DOC_CLIENTE")),
        ):
            for s in srcs:
                if s in factura_df.columns:
                    resultado[dst] = factura_df[s]
                    break
        resultado = resultado.rename(
            columns={"CODIGO": "SKU", "PRECIO_UNITARIO": "PRECIO_HIST", "SOLES": "MONTO_FACTURA"}
        )
        resultado["FACTURA"] = factura_df["_factura_label"]
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

        recs = []
        encontradas = [f for f in factura_ids if f in set(factura_df["_factura_label"])]
        for fid in encontradas:
            sub = factura_df[factura_df["_factura_label"] == fid]
            recs.append(
                ReconocimientoPorCondicion(
                    condicion_id="anular_factura",
                    fuente=fid,
                    estrategia="CancelInvoice",
                    monto_reconocido=float(sub["SOLES"].sum()),
                    cantidad_aplicada=float(sub["CANTIDAD"].sum()),
                    skus=sub["CODIGO"].unique().tolist(),
                    documentos=[fid],
                )
            )

        return RecognitionResult(
            dataframe=resultado,
            por_condicion=recs,
            resumen={"total_nc": float(total_factura), "skus_afectados": skus},
            metricas={
                "total_cantidad": float(total_cantidad),
                "factura": encontradas[0]
                if len(encontradas) == 1
                else f"{len(encontradas)} facturas",
                "fecha_factura": fecha_factura,
                "ruc_cliente": ruc_cliente,
                "igv_factura": igv_factura,
            },
            alertas=alertas,
            trazabilidad=trazabilidad,
        )
