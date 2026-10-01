"""Devolución física (DF): mercadería devuelta contra las facturas del mismo SKU.

Qué calcula
-----------
El cliente devuelve mercadería. Cada unidad devuelta se asigna **LIFO** (la
factura más reciente primero; el radio "orden de asignación" decide) contra las
líneas de factura del mismo artículo, y la NC reconoce el valor de lo devuelto
al **precio neto de esa factura**: si esas unidades ya tienen una NC previa, esa
NC se descuenta del precio antes de valorar (si no se estaría devolviendo dos
veces el mismo soles).

Individual : una fila por factura×SKU (habilita un expediente por factura).
Consolidado: una fila por SKU, precio por moda y monto por suma exacta, con el
detalle del corte en la auditoría (misma convención que DC/VRS/DO).

Insumos
-------
- `cantidad` (archivo de devoluciones): CODIGO_SKU + CANTIDAD_DEVUELTA
  (+ FECHA_DEVOLUCION opcional).
- `historico`: facturas para asignar + NC/NDB previas para el precio neto.
"""

import pandas as pd

from src.core.utils import build_doc_full, safe_float, split_doc_id
from src.domain import (
    BusinessAlert,
    ExpedienteComercial,
    RecognitionResult,
    generar_texto_alerta,
)
from src.core.nc_reconciliation import normalizar_sku

MOTOR = "DevolucionFisica"
PRECIO_DECIMALES = 5


def _doc_de_fila(row) -> str:
    try:
        tipo, serie, nro = split_doc_id(row.get("TIPO_DOC"), row.get("SERIE"), row.get("NUMERO"))
        return build_doc_full(tipo, serie, nro)
    except Exception:  # noqa: BLE001  (historial con columnas raras)
        return str(row.get("DOC_ID", "") or "").strip()


def _normalizar_indice_notas(notas: dict) -> dict:
    """Normaliza las claves de factura del resumen de NC previas.

    El resumen ya viene indexado por SKU normalizado (ver
    ``_resumen_notas_por_sku``); aquí solo se limpian los espacios de la factura
    para que el cruce con el DOC_ID de la línea no falle por mayúsculas.
    """
    return {str(f).strip(): info for f, info in (notas or {}).items()}


def _precio_neto_linea(
    factura: str, sku: str, cantidad_linea: float, soles_linea: float, notas: dict
) -> tuple[float, str]:
    """Precio unitario neto de la línea + auditoría de las NC previas.

    Estructura de `notas` (la arma ``_resumen_notas_por_sku``):

    ``{factura: {sku: {fae_qty, fae_soles, dev_qty, docs}}}``

    - FAE (ajuste de valor): se resta el importe de la nota, el precio de las
      unidades que se devuelven baja: ``(soles - fae_soles) / cantidad``.
    - devolución previa: si ya se devolvieron unidades, el precio unitario pasa
      a calcularse sobre las que quedan en la factura.
    """
    base = (soles_linea / cantidad_linea) if cantidad_linea > 0 else 0.0
    info = (notas or {}).get(factura, {}).get(sku)
    if not info:
        return base, ""
    fae_qty = float(info.get("fae_qty", 0) or 0)
    fae_soles = float(info.get("fae_soles", 0) or 0)
    dev_qty = float(info.get("dev_qty", 0) or 0)
    docs = sorted(str(d).strip() for d in (info.get("docs") or set()) if str(d).strip())
    if not docs:
        return base, ""
    notas_txt = ", ".join(docs)
    precio = base
    detalle = f"NC previa {notas_txt} sobre la factura"
    if fae_qty > 0 and cantidad_linea > 0:
        precio = (soles_linea - fae_soles) / cantidad_linea
        detalle += f" (FAE: {fae_qty:.0f} u, S/ {fae_soles:,.2f} descontados del precio)"
    if dev_qty > 0:
        restante = cantidad_linea - dev_qty
        if restante > 0:
            precio = (soles_linea - fae_soles) / restante
            detalle += f" ({dev_qty:.0f} u ya devueltas; precio sobre {restante:.0f} u)"
        else:
            detalle += " (devolución total previa: no queda precio de línea)"
    return precio, detalle


def _moda(valores) -> float:
    nums = [float(v) for v in valores if v is not None]
    if not nums:
        return 0.0
    frec: dict = {}
    for v in nums:
        frec[v] = frec.get(v, 0) + 1
    top = max(frec.values())
    return next(v for v in nums if frec[v] == top)


class DevolucionFisicaStrategy:
    """Asigna las unidades devueltas a las facturas del mismo SKU (LIFO)."""

    def process(self, expediente: ExpedienteComercial) -> RecognitionResult:
        config = expediente.contexto.config or {}
        devoluciones = config.get("devoluciones") or {}
        notas = _normalizar_indice_notas(config.get("notas_por_factura"))
        modalidad = str(config.get("modalidad", "individual"))
        sort_mode = str(config.get("sort_mode", "fecha_desc"))
        df_hist = expediente.datos

        if df_hist is None or df_hist.empty:
            return RecognitionResult(
                alertas=[
                    BusinessAlert(
                        tipo="error", severidad="alta", mensaje="Historial vacío", motor=MOTOR
                    )
                ],
                resumen={"total_nc": 0, "skus_afectados": 0},
            )
        if not devoluciones:
            return RecognitionResult(
                alertas=[
                    BusinessAlert(
                        tipo="error",
                        severidad="alta",
                        motor=MOTOR,
                        mensaje="Sin archivo de devoluciones: cargue SKU y cantidad devuelta",
                    )
                ],
                resumen={"total_nc": 0, "skus_afectados": 0},
            )

        df = df_hist.copy()
        if "TIPO_CLASE" in df.columns:
            df = df[df["TIPO_CLASE"].astype(str).str.lower() == "factura"].copy()
        df["CODIGO"] = df["CODIGO"].map(normalizar_sku) if "CODIGO" in df.columns else ""
        df["_codigo_original"] = df["CODIGO"].astype(str) if "CODIGO" in df.columns else ""
        df["CANTIDAD"] = pd.to_numeric(df.get("CANTIDAD", 0), errors="coerce").fillna(0)
        df["SOLES"] = pd.to_numeric(df.get("SOLES", 0), errors="coerce").fillna(0)
        df["_doc"] = df.apply(_doc_de_fila, axis=1)
        if "FECHA" in df.columns:
            df["_f"] = pd.to_datetime(df["FECHA"], errors="coerce")
        else:
            df["_f"] = pd.NaT
        por_sku = {str(k): v for k, v in df[df["CANTIDAD"] > 0].groupby("CODIGO", sort=False)}

        alertas: list = []
        trazabilidad: list = []
        # Una fila por (factura, SKU) con la cantidad asignada y su precio neto.
        filas_ind: list = []
        faltantes: list = []

        for sku_raw, info in devoluciones.items():
            sku = normalizar_sku(sku_raw)
            solicitud = safe_float(info.get("cantidad", 0))
            nombre_req = str(info.get("articulo", "") or "")
            fecha_min = info.get("fecha") or ""
            if solicitud <= 0:
                alertas.append(
                    BusinessAlert(
                        codigo="AL10",
                        tipo="info",
                        severidad="baja",
                        sku=sku,
                        mensaje=generar_texto_alerta("AL10", detalle="Cantidad devuelta en cero"),
                        motor=MOTOR,
                    )
                )
                continue
            hist_sku = por_sku.get(sku)
            if hist_sku is None or hist_sku.empty:
                alertas.append(
                    BusinessAlert(
                        codigo="AL06",
                        tipo="error",
                        severidad="alta",
                        sku=sku,
                        mensaje=generar_texto_alerta("AL06", sku=sku),
                        motor=MOTOR,
                    )
                )
                continue
            # El informe muestra el SKU como lo trae el ERP (con ceros), no el
            # normalizado que se usa para emparejar.
            sku_mostrar = str(info.get("sku_original") or "").strip() or str(
                hist_sku["_codigo_original"].iloc[0]
            )
            hist_sku = hist_sku.copy()
            if fecha_min and hist_sku["_f"].notna().any():
                limite = pd.to_datetime(fecha_min, errors="coerce")
                if pd.notna(limite):
                    hist_sku = hist_sku[hist_sku["_f"] >= limite]
            if hist_sku.empty:
                alertas.append(
                    BusinessAlert(
                        codigo="AL06",
                        tipo="warning",
                        severidad="media",
                        sku=sku,
                        mensaje=generar_texto_alerta("AL06", sku=sku),
                        motor=MOTOR,
                    )
                )
                continue
            asc = sort_mode == "fecha_asc"
            hist_sku = hist_sku.sort_values(
                "_f", ascending=asc, na_position="last", kind="mergesort"
            )

            restante = solicitud
            cortes: list = []
            for _, linea in hist_sku.iterrows():
                if restante <= 0:
                    break
                cant_linea = float(linea["CANTIDAD"])
                tomar = min(cant_linea, restante)
                if tomar <= 0:
                    continue
                doc = str(linea["_doc"] or linea.get("DOC_ID", "") or "").strip()
                precio, detalle_nc = _precio_neto_linea(
                    doc, sku, cant_linea, float(linea["SOLES"]), notas
                )
                monto = round(tomar * precio, 2)
                cortes.append((doc, tomar, precio, monto, detalle_nc))
                filas_ind.append(
                    {
                        "SKU": sku_mostrar,
                        "ARTICULO": nombre_req or str(linea.get("ARTICULO", "")),
                        "FACTURA": doc,
                        "CANTIDAD": round(tomar, 2),
                        "CANTIDAD_DEVUELTA": round(tomar, 2),
                        "PRECIO_HIST": round(precio, PRECIO_DECIMALES),
                        "MONTO_FACTURA": monto,
                        "MONTO_NC": monto,
                        "AUDITORIA_NC": detalle_nc,
                    }
                )
                restante -= tomar
            asignado = solicitud - restante
            if restante > 0:
                faltantes.append((sku, asignado, solicitud))
                alertas.append(
                    BusinessAlert(
                        codigo="AL09",
                        tipo="warning",
                        severidad="media",
                        sku=sku,
                        mensaje=generar_texto_alerta(
                            "AL09", sku=sku, asignado=asignado, solicitado=solicitud
                        ),
                        motor=MOTOR,
                    )
                )
            if not cortes:
                continue
            precios = [c[2] for c in cortes]
            if len({round(p, PRECIO_DECIMALES) for p in precios}) > 1:
                alertas.append(
                    BusinessAlert(
                        codigo="AL03",
                        tipo="info",
                        severidad="baja",
                        sku=sku,
                        mensaje=generar_texto_alerta("AL03", sku=sku),
                        motor=MOTOR,
                    )
                )
            # AL12: la factura ya tenía NC previa y el precio se descontó.
            for doc, _c, _p, _m, detalle in cortes:
                if detalle:
                    alertas.append(
                        BusinessAlert(
                            codigo="AL12",
                            tipo="warning",
                            severidad="media",
                            sku=sku,
                            mensaje=("AL12 - " + detalle),
                            motor=MOTOR,
                        )
                    )
                    break
            trazabilidad.append(
                f"{sku}: {asignado:.0f}/{solicitud:.0f} u asignadas a {len(cortes)} factura(s)"
            )

        if not filas_ind:
            return RecognitionResult(
                dataframe=pd.DataFrame(),
                alertas=alertas,
                trazabilidad=trazabilidad,
                resumen={"total_nc": 0, "skus_afectados": 0, "lotes_procesados": 1},
            )

        df_ind = pd.DataFrame(filas_ind)
        # Los documentos únicos se toman de las filas individuales: la celda
        # FACTURAS del consolidado ya viene unida en un solo texto.
        docs_unicos = sorted({str(f).strip() for f in df_ind["FACTURA"] if str(f).strip()})
        if modalidad == "consolidado":
            df_out = self._consolidar(df_ind)
            resumen = {
                "total_nc": float(df_out["MONTO_NC"].sum()),
                "skus_afectados": int(df_out["SKU"].nunique()),
                "documentos_unicos": docs_unicos,
                "titulo_documentos": "FACTURAS DE SUSTENTO",
            }
        else:
            df_out = df_ind.sort_values(["FACTURA", "SKU"]).reset_index(drop=True)
            resumen = {
                "total_nc": float(df_out["MONTO_NC"].sum()),
                "skus_afectados": int(df_out["SKU"].nunique()),
            }
        if faltantes:
            resumen["unidades_sin_sustento"] = sum(f[2] - f[1] for f in faltantes)
        # doc_ref del expediente: la factura que más valor aporta.
        por_doc = df_out.groupby("FACTURA" if "FACTURA" in df_out.columns else "FACTURAS")[
            "MONTO_NC"
        ].sum()
        if len(por_doc):
            resumen["doc_ref"] = str(por_doc.idxmax())
        return RecognitionResult(
            dataframe=df_out,
            dataframe_excel=df_out.copy(),
            resumen=resumen,
            alertas=alertas,
            trazabilidad=trazabilidad,
        )

    def _consolidar(self, df_ind: pd.DataFrame) -> pd.DataFrame:
        """Una fila por SKU: precio por moda, monto por suma exacta, con cortes."""
        filas = []
        for sku, g in df_ind.groupby("SKU", sort=False):
            precios = g["PRECIO_HIST"].tolist()
            pu = _moda(precios)
            monto = round(float(g["MONTO_NC"].sum()), 2)
            facturas = sorted({str(f).strip() for f in g["FACTURA"] if str(f).strip()})
            alertas_txt = []
            if g["CANTIDAD"].sum() <= 0:
                alertas_txt.append("OK")
            elif monto > 0:
                alertas_txt.append(
                    f"Devolución de {g['CANTIDAD'].sum():,.0f} u en {len(facturas)} factura(s)"
                )
            distintos = sorted({round(float(p), PRECIO_DECIMALES) for p in precios})
            audit = [
                f"Consolidado {len(g)} línea(s) de {len(facturas)} factura(s) "
                f"({', '.join(facturas)}); moda S/ {pu:.5f}"
            ]
            if len(distintos) > 1:
                audit.append(f"rango S/ {min(distintos):.5f}–{max(distintos):.5f}")
            cortes = "; ".join(
                f"{r['FACTURA']} {r['CANTIDAD']:,.0f}u ({r['PRECIO_HIST']:,.2f})"
                for _, r in g.sort_values("FACTURA").iterrows()
            )
            audit.append(f"Cortes: {cortes}.")
            notas = [
                str(v).strip()
                for v in g.get("AUDITORIA_NC", pd.Series(dtype=str))
                if str(v).strip()
            ]
            if notas:
                audit.append("Notas previas: " + " || ".join(dict.fromkeys(notas)))
            fac_principal = str(g.groupby("FACTURA")["MONTO_NC"].sum().idxmax())
            filas.append(
                {
                    "SKU": sku,
                    "ARTICULO": str(g["ARTICULO"].iloc[0]),
                    "CANTIDAD": round(float(g["CANTIDAD"].sum()), 2),
                    "CANTIDAD_DEVUELTA": round(float(g["CANTIDAD"].sum()), 2),
                    "PRECIO_HIST": round(float(pu), PRECIO_DECIMALES),
                    "MONTO_FACTURA": monto,
                    "MONTO_NC": monto,
                    "MONTO_EXACTO": monto,
                    "FACTURA": fac_principal,
                    "FACTURAS": ", ".join(facturas),
                    "ALERTA": " | ".join(dict.fromkeys(alertas_txt)),
                    "AUDITORIA_NC": "; ".join(audit),
                }
            )
        return pd.DataFrame(filas).reset_index(drop=True)


PRECIO_DECIMALES = 5
