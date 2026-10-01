import re
import pandas as pd
import numpy as np
from src.domain import (
    ExpedienteComercial,
    RecognitionResult,
    BusinessAlert,
    generar_texto_alerta,
)
from src.validation.normalization import NormalizationEngine, PRECIO_DECIMALES
from src.core.utils import normalizar_porcentaje, normalizar_porcentaje_serie
from src.core.nc_reconciliation import (
    normalizar_cliente,
    normalizar_sku,
    texto_auditoria_notas,
)


# ── Tolerancia de redondeo acumulada (dos niveles + proporcional) ────────────
# Unidad: hasta este valor de dif_unitaria se considera ruido de redondeo ERP.
TOLERANCIA_REDONDEO_UNITARIA = 0.01
# Total acumulado: hasta este monto se considera tolerable sin generar NC.
TOLERANCIA_REDONDEO_TOTAL = 1.00


def _build_factura(r):
    doc = f"{str(r.get('TIPO_DOC', '')).strip()[0]}{str(r.get('SERIE', '')).strip()}-{str(r.get('NUMERO', '')).strip()}"
    return doc.strip("-")


def _moda_precio(valores) -> float:
    """Precio modal de una serie; en empate gana el primero (orden de corte).

    El llamador ya ordena las líneas por ``sort_mode``, así que "el primero"
    es el más reciente con fecha_desc y el más antiguo con fecha_asc.
    """
    precios = [float(v) for v in valores if v is not None]
    if not precios:
        return 0.0
    frec: dict = {}
    for p in precios:
        frec[p] = frec.get(p, 0) + 1
    top = max(frec.values())
    return next(p for p in precios if frec[p] == top)


def _alertar_pct(codigo: str, origen: str, alertas: list, *, skus=()) -> None:
    """Traduce el codigo de normalizar_porcentaje a BusinessAlert.

    AL04 = captura fuera de rango interpretada como puntos de porcentaje;
    AL10 = texto no interpretable (se tomo 0). Un solo lugar para el texto
    de estas dos alertas en toda la app.
    """
    if not codigo:
        return
    muestra = ", ".join(sorted({str(s).strip() for s in skus})[:5])
    quien = f" en SKU {muestra}" if muestra else ""
    if codigo == "AL04":
        alertas.append(
            BusinessAlert(
                codigo="AL04",
                tipo="warning",
                severidad="media",
                mensaje=(
                    f"AL04 - Porcentaje fuera de rango en {origen}{quien}: "
                    "se interpreto como puntos de porcentaje (5 = 5%, no 500%). "
                    "Confirmar el dato de origen."
                ),
                motor="PriceDifference",
            )
        )
    else:
        alertas.append(
            BusinessAlert(
                codigo="AL10",
                tipo="info",
                severidad="baja",
                mensaje=(
                    f"AL10 - Porcentaje no interpretable en {origen}{quien}: "
                    "se tomo 0. Revisar el formato de la celda."
                ),
                motor="PriceDifference",
            )
        )


class PriceDifferenceStrategy:
    """
    Estrategia unificada para comparación de precios.

    Archivos:
    - historial: datos de ventas (PRECIO_UNITARIO)
    - lista_precios: PRECIO_BASE + cadena DESC (opcional)
    - requerimiento: SKU + CANTIDAD + DESC extra (opcional)

    Modos:
    - comparar: PRECIO_NETO = PRECIO_BASE × DESC_lista (usa lista_precios)
    - adicional: PRECIO_NETO = PRECIO_BASE × DESC_lista × (1 - DESC_req)
                 CANTIDAD = del historial (no del requerimiento)
    - ferias: PRECIO_NETO = PRECIO_UNITARIO × (1 - DESC_req)
             CANTIDAD = del requerimiento
    """

    def process(self, expediente: ExpedienteComercial) -> RecognitionResult:
        df_hist = expediente.datos
        config = expediente.contexto.config
        alertas = []
        trazabilidad = []

        if df_hist.empty:
            return RecognitionResult(
                dataframe=pd.DataFrame(),
                alertas=[BusinessAlert(tipo="error", severidad="alta", mensaje="Historial vacío")],
                resumen={"total_nc": 0, "skus_afectados": 0},
            )

        lista_precios = expediente.condiciones[0] if len(expediente.condiciones) > 0 else None
        requerimiento = expediente.condiciones[1] if len(expediente.condiciones) > 1 else None

        # La plantilla de descuentos llega con encabezados raw (CODIGO_SKU,
        # DESCUENTO_PORCENTAJE): sin normalizar, _determinar_modo la clasifica
        # como "comparar" y el merge se vacia (DO sale sin resultados). Se
        # renombra a DESCUENTO/CODIGO para que caiga en descuento_simple.
        if (
            lista_precios is not None
            and "PRECIO_BASE" not in lista_precios.columns
            and "DESCUENTO" not in lista_precios.columns
        ):
            ren = {}
            for c in ("DESCUENTO_PORCENTAJE", "DESCUENTO %", "DESCUENTO%", "% DESCUENTO", "DESC"):
                if c in lista_precios.columns:
                    ren[c] = "DESCUENTO"
                    break
            if "CODIGO" not in lista_precios.columns and "CODIGO_SKU" in lista_precios.columns:
                ren["CODIGO_SKU"] = "CODIGO"
            if ren:
                lista_precios = lista_precios.rename(columns=ren)

        # Filtro SKU (insumo "sku" / archivo por SKU): restringe el historial a
        # esos CODIGO y, si trae % por SKU, los aplica como tabla de descuento.
        # Flujo DO: historial × SKUs con descuento → precio atendido × (1-%) × cantidad.
        sku_filter = config.get("sku_filter") or {}
        sku_keys = {str(k).strip() for k in sku_filter.keys() if str(k).strip()}
        if sku_keys and "CODIGO" in df_hist.columns:
            df_hist = df_hist[df_hist["CODIGO"].astype(str).str.strip().isin(sku_keys)].copy()
            if df_hist.empty:
                return RecognitionResult(
                    dataframe=pd.DataFrame(),
                    alertas=[
                        BusinessAlert(
                            tipo="warning",
                            severidad="media",
                            mensaje="Ningún SKU del filtro coincide con el historial",
                        )
                    ],
                    resumen={"total_nc": 0, "skus_afectados": 0},
                )
            if lista_precios is None:
                pcts = {}
                malos = []
                for k, v in sku_filter.items():
                    num, cod = normalizar_porcentaje(v)
                    pcts[str(k).strip()] = num
                    if cod:
                        malos.append(str(k).strip())
                if malos:
                    _alertar_pct("AL04", "filtro SKU", alertas, skus=malos)
                if any(p > 0 for p in pcts.values()):
                    lista_precios = pd.DataFrame(
                        {"CODIGO": list(pcts.keys()), "DESCUENTO": list(pcts.values())}
                    )
                    trazabilidad.append(f"Descuento por SKU: {len(pcts)} SKU del filtro")
                elif not float(config.get("descuento_pct", 0) or 0) > 0:
                    return RecognitionResult(
                        dataframe=pd.DataFrame(),
                        alertas=[
                            BusinessAlert(
                                tipo="error",
                                severidad="alta",
                                mensaje="Filtro SKU sin %: cargue archivo con DESCUENTO o indique % global",
                            )
                        ],
                        resumen={"total_nc": 0, "skus_afectados": 0},
                    )

        modo = config.get("modo", self._determinar_modo(lista_precios, requerimiento, config))

        trazabilidad.append(f"Modo: {modo}")
        if lista_precios is not None:
            trazabilidad.append(f"Lista precios: {len(lista_precios)} SKUs")
        if requerimiento is not None:
            trazabilidad.append(f"Requerimiento: {len(requerimiento)} SKUs")

        df_full = None
        if modo == "ferias":
            if requerimiento is None:
                return RecognitionResult(
                    dataframe=pd.DataFrame(),
                    alertas=[
                        BusinessAlert(
                            tipo="error",
                            severidad="alta",
                            mensaje="Modo ferias requiere requerimiento",
                        )
                    ],
                    resumen={"total_nc": 0, "skus_afectados": 0},
                )
            df_result, res = self._procesar_ferias(df_hist, requerimiento, alertas)
            alertas.extend(res)
        elif modo == "adicional":
            if lista_precios is None or requerimiento is None:
                return RecognitionResult(
                    dataframe=pd.DataFrame(),
                    alertas=[
                        BusinessAlert(
                            tipo="error",
                            severidad="alta",
                            mensaje="Modo adicional requiere lista + requerimiento",
                        )
                    ],
                    resumen={"total_nc": 0, "skus_afectados": 0},
                )
            df_result, res = self._procesar_adicional(
                df_hist, lista_precios, requerimiento, alertas
            )
            alertas.extend(res)
        elif modo == "descuento_global":
            # El campo UI viene en % (5 = 5%); la formula usa fraccion.
            pct, cod_pct = normalizar_porcentaje(config.get("descuento_pct", 0))
            _alertar_pct(cod_pct, "descuento global", alertas)
            if pct <= 0:
                return RecognitionResult(
                    dataframe=pd.DataFrame(),
                    alertas=[
                        BusinessAlert(
                            tipo="error",
                            severidad="alta",
                            mensaje="Porcentaje de descuento debe ser mayor a 0",
                        )
                    ],
                    resumen={"total_nc": 0, "skus_afectados": 0},
                )
            df_result, res = self._procesar_descuento_global(df_hist, pct, alertas, config=config)
            alertas.extend(res)
        elif modo == "descuento_simple":
            if lista_precios is None:
                return RecognitionResult(
                    dataframe=pd.DataFrame(),
                    alertas=[
                        BusinessAlert(
                            tipo="error",
                            severidad="alta",
                            mensaje="Modo descuento_simple requiere archivo SKU+DESC",
                        )
                    ],
                    resumen={"total_nc": 0, "skus_afectados": 0},
                )
            df_result, res = self._procesar_descuento_simple(
                df_hist, lista_precios, alertas, config=config
            )
            alertas.extend(res)
        else:
            if lista_precios is None:
                return RecognitionResult(
                    dataframe=pd.DataFrame(),
                    alertas=[
                        BusinessAlert(
                            tipo="error",
                            severidad="alta",
                            mensaje="Comparar requiere lista de precios",
                        )
                    ],
                    resumen={"total_nc": 0, "skus_afectados": 0},
                )
            df_result, res, df_full = self._procesar_comparar(
                df_hist, lista_precios, alertas, config=config, trazabilidad=trazabilidad
            )
            alertas.extend(res)

        if df_result.empty:
            return RecognitionResult(
                dataframe=pd.DataFrame(),
                alertas=alertas
                + [BusinessAlert(tipo="warning", severidad="media", mensaje="Sin resultados")],
                resumen={"total_nc": 0, "skus_afectados": 0},
                trazabilidad=trazabilidad,
            )

        total_nc = df_result["MONTO_NC"].sum() if "MONTO_NC" in df_result.columns else 0
        skus = df_result["SKU"].nunique() if "SKU" in df_result.columns else 0
        doc_ref = self._get_doc_ref_masivo(df_result)

        resumen = {"total_nc": total_nc, "skus_afectados": skus, "doc_ref": doc_ref}
        # La lista sin repetir al pie solo va en consolidado: en individual
        # cada expediente es de una factura y la lista sería ruido.
        if (config or {}).get("modalidad", "individual") == "consolidado":
            docs_unicos = self._facturas_unicas(df_result)
            if docs_unicos:
                resumen["documentos_unicos"] = docs_unicos
                resumen["titulo_documentos"] = "FACTURAS COMPROMETIDAS"

        return RecognitionResult(
            dataframe=df_result,
            dataframe_excel=df_full,
            resumen=resumen,
            alertas=alertas,
            trazabilidad=trazabilidad,
        )

    @staticmethod
    def _facturas_unicas(df_result) -> list:
        """Facturas únicas sin repetir (lee FACTURAS y FACTURA).

        Va al pie de las tablas como lista de facturas comprometidas.
        """
        docs = set()
        for col in ("FACTURAS", "FACTURA"):
            if col not in df_result.columns:
                continue
            for v in df_result[col].dropna():
                for p in str(v).replace(";", ",").split(","):
                    p = p.strip().split(" (")[0].strip()
                    if p and p.lower() not in ("nan", "none"):
                        docs.add(p)
        return sorted(docs)

    def _determinar_modo(self, lista_precios, requerimiento, config=None) -> str:
        cfg = config or {}
        if lista_precios is not None and requerimiento is not None:
            return "adicional"
        elif lista_precios is not None:
            if "DESCUENTO" in lista_precios.columns and "PRECIO_BASE" not in lista_precios.columns:
                return "descuento_simple"
            return "comparar"
        elif requerimiento is not None:
            return "ferias"
        elif cfg.get("descuento_pct", 0) > 0:
            return "descuento_global"
        return "comparar"

    def _dentro_de_tol_redondeo(
        self, dif_unitaria: float, cantidad: float, precio_base: float
    ) -> bool:
        """Retorna True si la diferencia está dentro de tolerancia acumulada.

        Regla de negocio (dos niveles):
          1. dif_unitaria <= TOLERANCIA_REDONDEO_UNITARIA  (ruido de redondeo
             al convertir PRECIO_CALCULADO a PRECIO_HIST o viceversa)
          2. dif_unitaria × cantidad <= TOLERANCIA_REDONDEO_TOTAL
             (impacto total asumible)
        """
        tol_u = TOLERANCIA_REDONDEO_UNITARIA
        tol_t = TOLERANCIA_REDONDEO_TOTAL
        dif_abs = abs(dif_unitaria)
        if dif_abs > tol_u:
            return False
        dif_total = dif_abs * cantidad
        if dif_total > tol_t:
            return False
        return True

    @staticmethod
    def _get_doc_ref_masivo(df_result: pd.DataFrame) -> str:
        """Retorna la factura de mayor SOLES del resultado masivo."""
        if df_result.empty or "FACTURA" not in df_result.columns:
            return "NC"
        factura_totales = df_result.groupby("FACTURA")["SOLES"].sum()
        if factura_totales.empty:
            return str(df_result["FACTURA"].iloc[0])
        return factura_totales.idxmax()

    @staticmethod
    def _aplicar_reconciliacion_notas(merged: pd.DataFrame, config: dict) -> pd.DataFrame:
        """Keep invoice values intact and apply only permitted exact note matches.

        ``SOLES``/``CANTIDAD_FACTURADA`` remain the ERP invoice baseline.
        ``SOLES_CALCULO``/``CANTIDAD_CALCULO`` drive the new DC amount. In a
        consolidated run the reconciler marks notes informational, so these
        calculation fields remain equal to the invoice baseline.
        """
        df = merged.copy()
        qty = pd.to_numeric(df.get("CANTIDAD", 0), errors="coerce")
        soles = pd.to_numeric(df.get("SOLES", 0), errors="coerce")
        df["CANTIDAD_FACTURADA"] = qty.fillna(0).astype(float)
        df["CANTIDAD_CALCULO"] = df["CANTIDAD_FACTURADA"]
        df["SOLES_FACTURA"] = soles.fillna(0).astype(float)
        df["SOLES_CALCULO"] = df["SOLES_FACTURA"]
        df["PRECIO_HIST_FACTURA"] = np.where(
            df["CANTIDAD_FACTURADA"] > 0,
            df["SOLES_FACTURA"] / df["CANTIDAD_FACTURADA"],
            0,
        ).round(PRECIO_DECIMALES)
        df["TOTAL_FACTURA_EXACTO"] = df["SOLES_FACTURA"].round(2)
        df["ALERTA_NOTAS"] = ""
        df["AUDITORIA_NOTAS"] = ""

        reconciliaciones = config.get("reconciliacion_nc") or {}
        if not reconciliaciones:
            return df

        for idx, row in df.iterrows():
            invoice = str(row.get("DOC_ID", "") or _build_factura(row)).strip()
            sku = normalizar_sku(row.get("CODIGO", ""))
            client = normalizar_cliente(row.get("COD_CLIENTE", ""))
            rec = reconciliaciones.get((client, invoice, sku)) or reconciliaciones.get(
                (invoice, sku)
            )
            if not rec:
                continue
            q_line = float(df.at[idx, "CANTIDAD_FACTURADA"])
            q_invoice = float(rec.get("invoice_qty", 0) or 0)
            ratio = q_line / q_invoice if q_invoice > 0 else 0.0
            q_retorno = float(rec.get("qty_return", 0) or 0) * ratio
            delta_soles = float(rec.get("delta_soles", 0) or 0) * ratio
            df.at[idx, "CANTIDAD_CALCULO"] = max(0.0, q_line - q_retorno)
            df.at[idx, "SOLES_CALCULO"] = float(df.at[idx, "SOLES_FACTURA"]) + delta_soles
            alerts = list(dict.fromkeys(rec.get("alerts", [])))
            details = rec.get("details", [])
            if alerts:
                df.at[idx, "ALERTA_NOTAS"] = " | ".join(alerts)
            if details:
                df.at[idx, "AUDITORIA_NOTAS"] = texto_auditoria_notas(details)

        # CANTIDAD is the amount-eligible quantity consumed by the calculation;
        # the original stays available in CANTIDAD_FACTURADA for the invoice table.
        df["CANTIDAD"] = df["CANTIDAD_CALCULO"]
        return df

    @staticmethod
    def _unir_texto(base, nota) -> str:
        """Une el texto base de la celda con el detalle de notas (sin vacíos)."""
        parts = []
        for x in (base, nota):
            s = "" if x is None else str(x).strip()
            if s and s.lower() != "nan":
                parts.append(s)
        return " | ".join(dict.fromkeys(parts))

    @staticmethod
    def _alertas_por_notas(merged: pd.DataFrame, modalidad: str) -> list:
        """AL12/AL13 desde las decisiones de reconciliación (ALERTA_NOTAS).

        Solo en individual: en consolidado las notas se detallan en la fila
        (ALERTA/AUDITORIA_NC) pero no se replican como alertas de resultado.
        """
        if modalidad == "consolidado" or "ALERTA_NOTAS" not in merged.columns:
            return []
        out = []
        for msg in dict.fromkeys(str(v).strip() for v in merged["ALERTA_NOTAS"] if str(v).strip()):
            exceso = "EXCESO" in msg
            out.append(
                BusinessAlert(
                    codigo="AL13" if exceso else "AL12",
                    tipo="warning" if exceso or "REVISIÓN MANUAL" in msg else "info",
                    severidad="media" if exceso else "baja",
                    mensaje=msg,
                    motor="PriceDifference",
                )
            )
        return out

    def _procesar_comparar(self, df_hist, lista_precios, alertas, config=None, trazabilidad=None):
        """Modo Comparar: historial vs lista_precios.
        PRECIO_NETO = PRECIO_BASE × DESC_lista.
        CANTIDAD = del historial por línea (las cantidades siempre se toman
        de las facturas; una eventual columna CANTIDAD en la lista se ignora).

        Sin agrupación: una fila por línea del historial (si un SKU se repite
        en un documento, sale en filas independientes). La modalidad
        "consolidado" agrupa por SKU al final (ver _consolidar_por_sku).
        """
        if "SKU" not in lista_precios.columns or "PRECIO_BASE" not in lista_precios.columns:
            alertas.append(
                BusinessAlert(tipo="error", severidad="alta", mensaje="Lista sin SKU o PRECIO_BASE")
            )
            return pd.DataFrame(), [], pd.DataFrame()

        cols_desc = sorted(
            [c for c in lista_precios.columns if re.match(r"^DESC\d+$", c, re.IGNORECASE)]
        )
        precio_col = "PRECIO_BASE"
        lista_norm = lista_precios.copy()
        if cols_desc:
            schema = {"columnas_descuento": {"pattern": "^DESC\\d+$"}}
            norm = NormalizationEngine(schema)
            lista_norm = norm.aplicar_cadena_descuentos(lista_precios)
            alertas.extend(norm.alertas)  # AL04/AL10 de la cadena
            if "PRECIO_CALCULADO" in lista_norm.columns:
                precio_col = "PRECIO_CALCULADO"

        # DC: las cantidades siempre se toman de las facturas (la plantilla
        # oficial no incluye CANTIDAD). Si la lista la trae, se ignora.
        sort_mode = (config or {}).get("sort_mode", "fecha_desc")
        if "CANTIDAD" in lista_norm.columns:
            try:
                n_con_cant = int(
                    (pd.to_numeric(lista_norm["CANTIDAD"], errors="coerce").fillna(0) > 0).sum()
                )
            except Exception:
                n_con_cant = 0
            if n_con_cant:
                alertas.append(
                    BusinessAlert(
                        tipo="info",
                        severidad="baja",
                        mensaje=(
                            f"Columna CANTIDAD en lista de precios ignorada ({n_con_cant} SKUs): "
                            "las cantidades se toman de las facturas."
                        ),
                        motor="PriceDifference",
                    )
                )
            lista_norm = lista_norm.drop(columns=["CANTIDAD"])

        cols_merge = list(dict.fromkeys(["SKU", "PRECIO_BASE", precio_col] + cols_desc))
        merged = df_hist.merge(
            lista_norm[cols_merge].rename(columns={"SKU": "CODIGO"}),
            on="CODIGO",
            how="inner",
        )
        if merged.empty:
            alertas.append(
                BusinessAlert(tipo="warning", severidad="media", mensaje="Sin SKUs coincidentes")
            )
            return pd.DataFrame(), [], pd.DataFrame()

        merged = self._aplicar_reconciliacion_notas(merged, config or {})
        merged["PRECIO_HIST"] = np.where(
            merged["CANTIDAD_FACTURADA"] > 0,
            merged["SOLES_CALCULO"] / merged["CANTIDAD_FACTURADA"],
            0,
        ).round(PRECIO_DECIMALES)
        merged["PRECIO_NETO"] = merged[precio_col]
        merged["DESCUENTO_COMPUESTO"] = np.where(
            merged["PRECIO_BASE"] > 0,
            (1 - merged["PRECIO_NETO"] / merged["PRECIO_BASE"]).round(4),
            0,
        )
        merged["DIFERENCIA"] = (merged["PRECIO_HIST"] - merged["PRECIO_NETO"]).round(
            PRECIO_DECIMALES
        )
        merged["MONTO_NC"] = (merged["DIFERENCIA"].clip(lower=0) * merged["CANTIDAD"]).round(2)

        merged["FACTURA"] = merged.apply(_build_factura, axis=1)

        modalidad = (config or {}).get("modalidad", "individual")
        alertas_resultado = []
        if modalidad != "consolidado":
            for _, row in merged.iterrows():
                dif = row.get("DIFERENCIA", 0)
                sku = str(row.get("CODIGO", ""))
                monto_nc = float(row.get("MONTO_NC", 0))
                cant = float(row.get("CANTIDAD", 0))
                precio_base = float(row.get("PRECIO_BASE", 0))
                if cant <= 0:
                    continue
                if dif > 0 and not self._dentro_de_tol_redondeo(dif, cant, precio_base):
                    alertas_resultado.append(
                        BusinessAlert(
                            codigo="AL01",
                            tipo="info",
                            severidad="baja",
                            sku=sku,
                            mensaje=generar_texto_alerta(
                                "AL01", sku=sku, diferencia=dif, cantidad=cant
                            ),
                            impacto=monto_nc,
                            motor="PriceDifference",
                        )
                    )
                elif dif > 0 and self._dentro_de_tol_redondeo(dif, cant, precio_base):
                    alertas_resultado.append(
                        BusinessAlert(
                            codigo="AL11",
                            tipo="info",
                            severidad="baja",
                            sku=sku,
                            mensaje=generar_texto_alerta(
                                "AL11",
                                sku=sku,
                                diferencia_unitaria=dif,
                                diferencia_total=dif * cant,
                                cantidad=cant,
                            ),
                            motor="PriceDifference",
                        )
                    )

        def _clase_alerta(r):
            dif = r.get("DIFERENCIA", 0)
            sku = str(r.get("CODIGO", ""))
            cant = float(r.get("CANTIDAD", 0))
            precio_base = float(r.get("PRECIO_BASE", 0))
            if cant <= 0:
                # Una devolución exacta puede dejar cero unidades elegibles.
                # No etiquetarla como redondeo (AL11) por total 0; el detalle
                # de la devolución ya queda en ALERTA_NOTAS/AUDITORIA_NC.
                return "OK"
            if dif > 0 and not self._dentro_de_tol_redondeo(dif, cant, precio_base):
                return generar_texto_alerta("AL01", sku=sku, diferencia=dif, cantidad=cant)
            if dif > 0 and self._dentro_de_tol_redondeo(dif, cant, precio_base):
                return generar_texto_alerta(
                    "AL11",
                    sku=sku,
                    diferencia_unitaria=dif,
                    diferencia_total=dif * cant,
                    cantidad=cant,
                )
            return generar_texto_alerta("AL02", sku=sku, coincide=(abs(dif) < 0.001))

        merged["ALERTA"] = merged.apply(_clase_alerta, axis=1)
        merged["ALERTA"] = [
            " | ".join(x for x in (str(base or "").strip(), str(note or "").strip()) if x)
            for base, note in zip(merged["ALERTA"], merged["ALERTA_NOTAS"])
        ]
        merged["AUDITORIA_NC"] = merged["AUDITORIA_NOTAS"]
        alertas_resultado.extend(self._alertas_por_notas(merged, modalidad))

        resultado_cols = [
            "CODIGO",
            "ARTICULO",
            "CANTIDAD",
            "SOLES",
            "PRECIO_HIST",
            "PRECIO_BASE",
            "DESCUENTO_COMPUESTO",
            "PRECIO_NETO",
            "DIFERENCIA",
            "MONTO_NC",
            "FACTURA",
            "ALERTA",
            "AUDITORIA_NC",
            "CANTIDAD_FACTURADA",
            "CANTIDAD_CALCULO",
            "SOLES_CALCULO",
            "PRECIO_HIST_FACTURA",
            "TOTAL_FACTURA_EXACTO",
            "COD_CLIENTE",
            "DOC_CLIENTE",
        ]
        df_result = merged[[c for c in resultado_cols if c in merged.columns]].copy()
        df_result = df_result.rename(columns={"CODIGO": "SKU"})
        df_result = df_result.sort_values("FACTURA").reset_index(drop=True)
        df_full = merged.copy()
        if "CODIGO" in df_full.columns:
            df_full = df_full.rename(columns={"CODIGO": "SKU"})
        if modalidad == "consolidado" and not df_result.empty:
            df_result = self._consolidar_por_sku(
                merged, sort_mode=sort_mode, alertas=alertas_resultado
            )
            df_full = df_result.copy()
            if trazabilidad is not None:
                # El resumen de consolidación es trazabilidad, no alerta de
                # negocio: no entra al panel de alertas.
                trazabilidad.append(
                    f"Consolidado por SKU: {len(df_result)} SKUs "
                    f"de {len(merged)} líneas (monto por suma exacta)."
                )
        return df_result, alertas_resultado, df_full

    def _consolidar_por_sku(self, merged, sort_mode="fecha_desc", alertas=None, alertas_notas=True):
        """Consolida el resultado comparar por SKU (modalidad consolidada).

        Una fila por SKU: CANTIDAD y SOLES sumados, PRECIO_HIST y PRECIO_NETO =
        moda de los precios de línea (desempate por sort_mode: fecha_desc → el
        más reciente primero), FACTURAS = lista de documentos, FACTURA = la de
        mayor SOLES (naming/doc_ref), MONTO_NC = suma exacta de las líneas y
        DIFERENCIA = moda − moda (referencial; el monto manda).

        Es la convención de consolidado de toda la familia de precio (DC, VRS,
        DO): sirve también para motores sin lista de precios (DO), donde no
        existe PRECIO_BASE — la tolerancia de redondeo usa 0 como base.

        `alertas_notas=False` deja las notas previas solo en la fila (ALERTA y
        AUDITORIA_NC) sin sumar AL12/AL13 al panel: es la convención de DO en
        consolidado, donde la nota se revisa en la fila y no genera alerta.
        """
        df = merged.copy()
        fechas = pd.to_datetime(df.get("FECHA"), errors="coerce")
        df["_orden_fecha"] = fechas
        asc = str(sort_mode or "") == "fecha_asc"
        df = df.sort_values("_orden_fecha", ascending=asc, na_position="last", kind="mergesort")

        desc_cols = [c for c in df.columns if re.match(r"^DESC\d+$", str(c), re.IGNORECASE)]

        filas = []
        for sku, g in df.groupby("CODIGO", sort=False):
            precios = [float(p) for p in g["PRECIO_HIST"].tolist()]
            moda = _moda_precio(precios)
            distintos = sorted(set(precios))
            # FACTURAS/FACTURA solo con facturas: las notas reconciliadas
            # (NC/NDB) ajustan cantidades y precios pero no son facturas
            # comprometidas (el titulo de la lista es "FACTURAS COMPROMETIDAS").
            g_fac = (
                g[g["TIPO_CLASE"].astype(str).str.lower() == "factura"]
                if "TIPO_CLASE" in g.columns
                else g
            )
            facturas = sorted({str(f).strip() for f in g_fac["FACTURA"].tolist() if str(f).strip()})
            por_fac = g.groupby("FACTURA")["SOLES"].sum()
            fac_principal = str(por_fac.idxmax())
            cant_total = float(g["CANTIDAD"].sum())
            soles_total = float(g["SOLES"].sum())
            # DO (descuento comercial) no trae lista de precios: sin base no
            # hay tolerancia de redondeo, la base 0 la desactiva.
            base = float(g["PRECIO_BASE"].iloc[0]) if "PRECIO_BASE" in g.columns else 0.0
            # Neto por moda también: la DIFERENCIA de la fila es la del precio
            # representativo (si no, emparejaría la moda con el neto de otra
            # línea y la diferencia no cuadraría con la hoja).
            neto = _moda_precio(g["PRECIO_NETO"].tolist())
            desc_comp = (
                float(g["DESCUENTO_COMPUESTO"].iloc[0])
                if "DESCUENTO_COMPUESTO" in g.columns
                else 0.0
            )
            precios_distintos = (
                "PRECIO_BASE" in g.columns and g["PRECIO_BASE"].nunique() > 1
            ) or g["PRECIO_NETO"].nunique() > 1
            if alertas is not None and precios_distintos:
                alertas.append(
                    BusinessAlert(
                        codigo="AL03",
                        tipo="warning",
                        severidad="media",
                        sku=str(sku),
                        mensaje=(
                            f"SKU {sku} con precios netos distintos en el "
                            "consolidado; se usó la moda."
                        ),
                        motor="PriceDifference",
                    )
                )
            dif = round(float(moda) - float(neto), PRECIO_DECIMALES)
            monto = round(float(g["MONTO_NC"].sum()), 2)
            tol = self._dentro_de_tol_redondeo(dif, cant_total, base)
            if cant_total <= 0:
                alerta_txt = "OK"
            elif dif > 0 and not tol:
                alerta_txt = (
                    f"AL01 - Diferencia positiva S/ {dif:.5f} (total S/ {monto:,.2f}) (SKU {sku})"
                )
                if len(facturas) > 1:
                    alerta_txt += f" · {len(facturas)} facturas"
                if alertas is not None:
                    alertas.append(
                        BusinessAlert(
                            codigo="AL01",
                            tipo="info",
                            severidad="baja",
                            sku=str(sku),
                            mensaje=alerta_txt,
                            impacto=monto,
                            motor="PriceDifference",
                        )
                    )
            elif dif > 0 and tol:
                alerta_txt = generar_texto_alerta(
                    "AL11",
                    sku=str(sku),
                    diferencia_unitaria=dif,
                    diferencia_total=monto,
                    cantidad=cant_total,
                )
                if alertas is not None:
                    alertas.append(
                        BusinessAlert(
                            codigo="AL11",
                            tipo="info",
                            severidad="baja",
                            sku=str(sku),
                            mensaje=alerta_txt,
                            motor="PriceDifference",
                        )
                    )
            else:
                alerta_txt = generar_texto_alerta("AL02", sku=str(sku), coincide=(abs(dif) < 0.001))
            audit = (
                f"Consolidado {len(g)} líneas de {len(facturas)} factura(s): "
                f"{', '.join(facturas)}; moda S/ {float(moda):.5f}"
            )
            if len(distintos) > 1:
                audit += (
                    f"; rango S/ {min(distintos):.5f}–{max(distintos):.5f} "
                    "(monto por suma exacta de líneas)"
                )
            # DO: el % aplicado también queda en auditoría (con aviso si el
            # mismo SKU llevara descuentos distintos por línea).
            pcts_desc = sorted(
                {round(float(p), 6) for p in g.get("%_DESCUENTO", pd.Series(dtype=float)).dropna()}
            )
            if pcts_desc:
                audit += "; desc. aplicado " + " / ".join(f"{p * 100:.2f}%" for p in pcts_desc)
                if len(pcts_desc) > 1:
                    audit += " (descuentos distintos por línea; neto por el primero)"
                audit += "."
            cortes = []
            for _, _lr in g.sort_values("FACTURA").iterrows():
                try:
                    _cc = float(_lr.get("CANTIDAD", 0) or 0)
                except (TypeError, ValueError):
                    _cc = 0.0
                try:
                    _cp = float(_lr.get("PRECIO_HIST", 0) or 0)
                except (TypeError, ValueError):
                    _cp = 0.0
                cortes.append(f"{str(_lr.get('FACTURA', '')).strip()} {_cc:,.0f}u ({_cp:,.2f})")
            detalle = "; ".join(cortes[:6])
            if len(cortes) > 6:
                detalle += f" (+{len(cortes) - 6} más)"
            audit += f" Cortes: {detalle}."
            note_alerts = list(
                dict.fromkeys(
                    str(v).strip()
                    for v in g.get("ALERTA_NOTAS", pd.Series(dtype=str)).dropna()
                    if str(v).strip()
                )
            )
            note_audits = list(
                dict.fromkeys(
                    str(v).strip()
                    for v in g.get("AUDITORIA_NOTAS", pd.Series(dtype=str)).dropna()
                    if str(v).strip()
                )
            )
            if note_audits:
                audit += " | Notas previas: " + " || ".join(note_audits)
            if alertas is not None and alertas_notas:
                for note_msg in note_alerts:
                    exceso = "EXCESO" in note_msg
                    alertas.append(
                        BusinessAlert(
                            codigo="AL13" if exceso else "AL12",
                            tipo="warning" if exceso or "REVISIÓN MANUAL" in note_msg else "info",
                            severidad="media" if exceso else "baja",
                            sku=str(sku),
                            mensaje=note_msg,
                            motor="PriceDifference",
                        )
                    )
            fila = {
                "CODIGO": sku,
                "ARTICULO": str(g["ARTICULO"].iloc[0]),
                "CANTIDAD": cant_total,
                "CANTIDAD_CALCULO": cant_total,
                "CANTIDAD_FACTURADA": float(g.get("CANTIDAD_FACTURADA", g["CANTIDAD"]).sum()),
                "SOLES": round(soles_total, 2),
                "SOLES_CALCULO": round(float(g.get("SOLES_CALCULO", g["SOLES"]).sum()), 2),
                "PRECIO_HIST": float(moda),
                "PRECIO_HIST_FACTURA": (
                    round(soles_total / float(g["CANTIDAD_FACTURADA"].sum()), PRECIO_DECIMALES)
                    if "CANTIDAD_FACTURADA" in g and float(g["CANTIDAD_FACTURADA"].sum()) > 0
                    else float(moda)
                ),
                "PRECIO_NETO": neto,
                "DIFERENCIA": dif,
                "MONTO_NC": monto,
                "MONTO_EXACTO": monto,
                "TOTAL_FACTURA_EXACTO": round(soles_total, 2),
                "FACTURA": fac_principal,
                "FACTURAS": ", ".join(facturas),
                "ALERTA": " | ".join([alerta_txt, *note_alerts]) if note_alerts else alerta_txt,
                "AUDITORIA_NC": audit,
                "ALERTA_NOTAS": " | ".join(note_alerts),
                "AUDITORIA_NOTAS": " | ".join(note_audits),
            }
            # Solo las columnas que el motor de origen tenía: DO no tiene lista
            # de precios, así que no se inventan PRECIO_BASE/compuesto en 0.
            if "PRECIO_BASE" in g.columns:
                fila["PRECIO_BASE"] = base
            if "DESCUENTO_COMPUESTO" in g.columns:
                fila["DESCUENTO_COMPUESTO"] = desc_comp
            for col in ("COD_CLIENTE", "DOC_CLIENTE", "CLIENTE"):
                if col in g.columns:
                    fila[col] = g[col].iloc[0]
            # DO: el % del archivo (o el global) y la línea viajan a la fila
            # consolidada; sin esto la columna % DESC. del Excel salía vacía.
            if "%_DESCUENTO" in g.columns:
                fila["%_DESCUENTO"] = float(g["%_DESCUENTO"].iloc[0])
            if "LINEA" in g.columns:
                fila["LINEA"] = g["LINEA"].iloc[0]
            for dc in desc_cols:
                fila[dc] = g[dc].iloc[0]
            filas.append(fila)

        df_cons = pd.DataFrame(filas)
        if "CODIGO" in df_cons.columns:
            df_cons = df_cons.rename(columns={"CODIGO": "SKU"})
        return df_cons.reset_index(drop=True)

    def _procesar_adicional(self, df_hist, lista_precios, requerimiento, alertas):
        """Modo Adicional: lista_precios + descuento adicional del requerimiento.
        PRECIO_NETO = PRECIO_BASE × DESC_lista × (1 - DESC_req).
        CANTIDAD = del historial (por factura), no del requerimiento."""
        if "SKU" not in lista_precios.columns or "PRECIO_BASE" not in lista_precios.columns:
            alertas.append(
                BusinessAlert(tipo="error", severidad="alta", mensaje="Lista sin SKU o PRECIO_BASE")
            )
            return pd.DataFrame(), []
        if "SKU" not in requerimiento.columns or "DESCUENTO" not in requerimiento.columns:
            alertas.append(
                BusinessAlert(
                    tipo="error", severidad="alta", mensaje="Requerimiento sin SKU o DESCUENTO"
                )
            )
            return pd.DataFrame(), []

        cols_desc = sorted(
            [c for c in lista_precios.columns if re.match(r"^DESC\d+$", c, re.IGNORECASE)]
        )
        precio_col = "PRECIO_BASE"
        lista_norm = lista_precios.copy()
        if cols_desc:
            schema = {"columnas_descuento": {"pattern": "^DESC\\d+$"}}
            norm = NormalizationEngine(schema)
            lista_norm = norm.aplicar_cadena_descuentos(lista_precios)
            alertas.extend(norm.alertas)  # AL04/AL10 de la cadena
            if "PRECIO_CALCULADO" in lista_norm.columns:
                precio_col = "PRECIO_CALCULADO"

        req_clean = (
            requerimiento[["SKU", "DESCUENTO", "CANTIDAD"]].copy()
            if "CANTIDAD" in requerimiento.columns
            else requerimiento[["SKU", "DESCUENTO"]].copy()
        )
        req_clean.columns = ["SKU", "DESC_REQ", "CANTIDAD_REQ"]
        req_clean["DESC_REQ"], cod_req = normalizar_porcentaje_serie(req_clean["DESC_REQ"])
        for _cod in ("AL04", "AL10"):
            _mask = cod_req == _cod
            if _mask.any():
                _alertar_pct(
                    _cod, "descuento del requerimiento", alertas, skus=req_clean.loc[_mask, "SKU"]
                )

        cols_merge = list(dict.fromkeys(["SKU", "PRECIO_BASE", precio_col]))
        merged = df_hist.merge(
            lista_norm[cols_merge].rename(columns={"SKU": "CODIGO"}), on="CODIGO", how="inner"
        )
        merged = merged.merge(req_clean.rename(columns={"SKU": "CODIGO"}), on="CODIGO", how="left")
        merged["DESC_REQ"] = merged["DESC_REQ"].fillna(0)

        merged["PRECIO_NETO"] = (merged[precio_col] * (1 - merged["DESC_REQ"])).round(
            PRECIO_DECIMALES
        )
        merged["PRECIO_HIST"] = np.where(
            merged["CANTIDAD"] > 0, merged["SOLES"] / merged["CANTIDAD"], 0
        ).round(PRECIO_DECIMALES)
        merged["DESCUENTO_COMPUESTO"] = np.where(
            merged["PRECIO_BASE"] > 0,
            (1 - merged["PRECIO_NETO"] / merged["PRECIO_BASE"]).round(4),
            0,
        )
        merged["DIFERENCIA"] = (merged["PRECIO_HIST"] - merged["PRECIO_NETO"]).round(
            PRECIO_DECIMALES
        )
        merged["MONTO_NC"] = (merged["DIFERENCIA"].clip(lower=0) * merged["CANTIDAD"]).round(2)

        merged["FACTURA"] = merged.apply(_build_factura, axis=1)

        def _clase_adicional(r):
            dif = r.get("DIFERENCIA", 0)
            sku = str(r.get("CODIGO", ""))
            cant = float(r.get("CANTIDAD", 0))
            precio_base = float(r.get("PRECIO_BASE", 0))
            if dif > 0 and not self._dentro_de_tol_redondeo(dif, cant, precio_base):
                return generar_texto_alerta("AL01", sku=sku, diferencia=dif, cantidad=cant)
            if dif > 0 and self._dentro_de_tol_redondeo(dif, cant, precio_base):
                return generar_texto_alerta(
                    "AL11",
                    sku=sku,
                    diferencia_unitaria=dif,
                    diferencia_total=dif * cant,
                    cantidad=cant,
                )
            return generar_texto_alerta("AL02", sku=sku, coincide=(abs(dif) < 0.001))

        merged["ALERTA"] = merged.apply(_clase_adicional, axis=1)
        merged["CANTIDAD"] = merged["CANTIDAD_REQ"]

        resultado_cols = [
            "CODIGO",
            "ARTICULO",
            "CANTIDAD",
            "SOLES",
            "PRECIO_HIST",
            "PRECIO_BASE",
            "DESCUENTO_COMPUESTO",
            "PRECIO_NETO",
            "DIFERENCIA",
            "MONTO_NC",
            "FACTURA",
            "ALERTA",
            "COD_CLIENTE",
            "DOC_CLIENTE",
        ]
        df_result = merged[[c for c in resultado_cols if c in merged.columns]].copy()
        df_result = df_result.rename(columns={"CODIGO": "SKU"})
        df_result = df_result.sort_values("FACTURA").reset_index(drop=True)
        return df_result, []

    def _procesar_descuento_simple(self, df_hist, condiciones_desc, alertas, config=None):
        """Modo Descuento Simple: archivo CODIGO + DESCUENTO sobre historial.
        PRECIO_NETO = PRECIO_UNITARIO × (1 - DESC).
        CANTIDAD = del historial (cantidad elegible tras reconciliación
        NC/NDB; el precio atendido usa el total de factura ajustado).
        Una fila por SKU por factura."""
        if "CODIGO" not in condiciones_desc.columns and "DESCUENTO" not in condiciones_desc.columns:
            if "SKU" in condiciones_desc.columns:
                condiciones_desc = condiciones_desc.rename(columns={"SKU": "CODIGO"})
            if "DESC" in condiciones_desc.columns:
                condiciones_desc = condiciones_desc.rename(columns={"DESC": "DESCUENTO"})
        if "CODIGO" not in condiciones_desc.columns or "DESCUENTO" not in condiciones_desc.columns:
            alertas.append(
                BusinessAlert(
                    tipo="error", severidad="alta", mensaje="Archivo DESC sin CODIGO o DESCUENTO"
                )
            )
            return pd.DataFrame(), []

        desc = condiciones_desc[["CODIGO", "DESCUENTO"]].copy()
        desc["DESCUENTO"], cod_desc = normalizar_porcentaje_serie(desc["DESCUENTO"])
        for _cod in ("AL04", "AL10"):
            _mask = cod_desc == _cod
            if _mask.any():
                _alertar_pct(_cod, "archivo DESC", alertas, skus=desc.loc[_mask, "CODIGO"])

        merged = df_hist.merge(desc, on="CODIGO", how="inner")
        if merged.empty:
            alertas.append(
                BusinessAlert(
                    tipo="warning",
                    severidad="media",
                    mensaje="Sin SKUs coincidentes entre historial y archivo DESC",
                )
            )
            return pd.DataFrame(), []

        merged = self._aplicar_reconciliacion_notas(merged, config or {})
        merged["PRECIO_HIST"] = np.where(
            merged["CANTIDAD_FACTURADA"] > 0,
            merged["SOLES_CALCULO"] / merged["CANTIDAD_FACTURADA"],
            0,
        ).round(PRECIO_DECIMALES)
        merged["PRECIO_NETO"] = (merged["PRECIO_HIST"] * (1 - merged["DESCUENTO"])).round(
            PRECIO_DECIMALES
        )
        merged["DIFERENCIA"] = (merged["PRECIO_HIST"] - merged["PRECIO_NETO"]).round(
            PRECIO_DECIMALES
        )
        merged["MONTO_NC"] = (merged["DIFERENCIA"].clip(lower=0) * merged["CANTIDAD"]).round(2)

        merged["FACTURA"] = merged.apply(_build_factura, axis=1)
        merged["%_DESCUENTO"] = merged["DESCUENTO"]
        desc_aud = merged.apply(
            lambda r: (
                f"DESC {float(r.get('DESCUENTO', 0)) * 100:.2f}% s/precio atendido "
                f"S/ {float(r.get('PRECIO_HIST', 0)):.5f} (archivo SKU+DESC; sin lista de precios)"
            ),
            axis=1,
        )
        merged["AUDITORIA_NC"] = [
            self._unir_texto(a, b) for a, b in zip(desc_aud, merged["AUDITORIA_NOTAS"])
        ]

        def _clase_simple(r):
            dif = r.get("DIFERENCIA", 0)
            sku = str(r.get("CODIGO", ""))
            cant = float(r.get("CANTIDAD", 0))
            precio_base = float(r.get("PRECIO_BASE", 0))
            if cant <= 0:
                # Devolución exacta: sin unidades elegibles; el detalle de la
                # nota queda en ALERTA_NOTAS/AUDITORIA_NC.
                return "OK"
            if dif > 0 and not self._dentro_de_tol_redondeo(dif, cant, precio_base):
                return generar_texto_alerta("AL01", sku=sku, diferencia=dif, cantidad=cant)
            if dif > 0 and self._dentro_de_tol_redondeo(dif, cant, precio_base):
                return generar_texto_alerta(
                    "AL11",
                    sku=sku,
                    diferencia_unitaria=dif,
                    diferencia_total=dif * cant,
                    cantidad=cant,
                )
            return generar_texto_alerta("AL02", sku=sku, coincide=(abs(dif) < 0.001))

        merged["ALERTA"] = [
            self._unir_texto(base, nota)
            for base, nota in zip(merged.apply(_clase_simple, axis=1), merged["ALERTA_NOTAS"])
        ]
        modalidad = (config or {}).get("modalidad", "individual")

        resultado_cols = [
            "CODIGO",
            "ARTICULO",
            "LINEA",
            "CANTIDAD",
            "SOLES",
            "PRECIO_HIST",
            "%_DESCUENTO",
            "PRECIO_NETO",
            "DIFERENCIA",
            "MONTO_NC",
            "FACTURA",
            "ALERTA",
            "AUDITORIA_NC",
            "CANTIDAD_FACTURADA",
            "CANTIDAD_CALCULO",
            "SOLES_CALCULO",
            "PRECIO_HIST_FACTURA",
            "TOTAL_FACTURA_EXACTO",
            "COD_CLIENTE",
            "DOC_CLIENTE",
        ]
        df_result = merged[[c for c in resultado_cols if c in merged.columns]].copy()
        df_result = df_result.rename(columns={"CODIGO": "SKU"})
        df_result = df_result.sort_values("FACTURA").reset_index(drop=True)

        alertas_resultado = self._alertas_por_notas(merged, modalidad)
        if modalidad == "consolidado" and not df_result.empty:
            # Misma regla que DC/VRS: una fila por SKU, precio por moda y monto
            # por suma exacta de las líneas (auditoría con el detalle del corte).
            df_result = self._consolidar_por_sku(
                merged,
                sort_mode=(config or {}).get("sort_mode", "fecha_desc"),
                alertas=alertas_resultado,
                alertas_notas=False,
            )
        for _, row in df_result.iterrows():
            dif = row.get("DIFERENCIA", 0)
            sku = str(row.get("SKU", ""))
            monto_nc = float(row.get("MONTO_NC", 0))
            cant = float(row.get("CANTIDAD", 0))
            precio_base = float(row.get("PRECIO_BASE", 0))
            if cant <= 0:
                continue
            if dif > 0 and not self._dentro_de_tol_redondeo(dif, cant, precio_base):
                alertas_resultado.append(
                    BusinessAlert(
                        codigo="AL01",
                        tipo="info",
                        severidad="baja",
                        sku=sku,
                        mensaje=generar_texto_alerta(
                            "AL01", sku=sku, diferencia=dif, cantidad=cant
                        ),
                        impacto=monto_nc,
                        motor="PriceDifference",
                    )
                )
            elif dif > 0 and self._dentro_de_tol_redondeo(dif, cant, precio_base):
                alertas_resultado.append(
                    BusinessAlert(
                        codigo="AL11",
                        tipo="info",
                        severidad="baja",
                        sku=sku,
                        mensaje=generar_texto_alerta(
                            "AL11",
                            sku=sku,
                            diferencia_unitaria=dif,
                            diferencia_total=dif * cant,
                            cantidad=cant,
                        ),
                        motor="PriceDifference",
                    )
                )

        return df_result, alertas_resultado

    def _procesar_descuento_global(self, df_hist, pct, alertas, config=None):
        """Modo Descuento Global: aplica un % fijo a TODAS las filas del historial.
        PRECIO_NETO = PRECIO_HIST × (1 - pct).
        CANTIDAD y FACTURA provienen del historial (cantidad elegible tras
        reconciliación NC/NDB; el precio atendido usa el total ajustado).
        Sin archivo — el % se ingresa como campo numérico en la UI."""
        df = df_hist.copy()
        if df.empty:
            return pd.DataFrame(), []

        df = self._aplicar_reconciliacion_notas(df, config or {})
        df["PRECIO_HIST"] = np.where(
            df["CANTIDAD_FACTURADA"] > 0,
            df["SOLES_CALCULO"] / df["CANTIDAD_FACTURADA"],
            0,
        ).round(PRECIO_DECIMALES)
        df["PRECIO_NETO"] = (df["PRECIO_HIST"] * (1 - pct)).round(PRECIO_DECIMALES)
        df["DIFERENCIA"] = (df["PRECIO_HIST"] - df["PRECIO_NETO"]).round(PRECIO_DECIMALES)
        df["MONTO_NC"] = (df["DIFERENCIA"].clip(lower=0) * df["CANTIDAD"]).round(2)
        df["FACTURA"] = df.apply(_build_factura, axis=1)
        df["%_DESCUENTO"] = pct
        desc_aud = df.apply(
            lambda r: (
                f"Descuento global {pct * 100:.2f}% s/precio atendido "
                f"S/ {float(r.get('PRECIO_HIST', 0)):.5f} (sin archivo; % ingresado en UI)"
            ),
            axis=1,
        )
        df["AUDITORIA_NC"] = [
            self._unir_texto(a, b) for a, b in zip(desc_aud, df["AUDITORIA_NOTAS"])
        ]

        def _clase_global(r):
            dif = r.get("DIFERENCIA", 0)
            sku = str(r.get("CODIGO", ""))
            cant = float(r.get("CANTIDAD", 0))
            if cant <= 0:
                # Devolución exacta: sin unidades elegibles; el detalle de la
                # nota queda en ALERTA_NOTAS/AUDITORIA_NC.
                return "OK"
            if dif > 0:
                return generar_texto_alerta("AL01", sku=sku, diferencia=dif, cantidad=cant)
            return generar_texto_alerta("AL02", sku=sku, coincide=(abs(dif) < 0.001))

        df["ALERTA"] = [
            self._unir_texto(base, nota)
            for base, nota in zip(df.apply(_clase_global, axis=1), df["ALERTA_NOTAS"])
        ]
        modalidad = (config or {}).get("modalidad", "individual")

        cols = [
            "CODIGO",
            "ARTICULO",
            "LINEA",
            "CANTIDAD",
            "SOLES",
            "PRECIO_HIST",
            "%_DESCUENTO",
            "PRECIO_NETO",
            "DIFERENCIA",
            "MONTO_NC",
            "FACTURA",
            "ALERTA",
            "AUDITORIA_NC",
            "CANTIDAD_FACTURADA",
            "CANTIDAD_CALCULO",
            "SOLES_CALCULO",
            "PRECIO_HIST_FACTURA",
            "TOTAL_FACTURA_EXACTO",
            "COD_CLIENTE",
            "DOC_CLIENTE",
        ]
        df_result = df[[c for c in cols if c in df.columns]].copy()
        df_result = df_result.rename(columns={"CODIGO": "SKU"})
        df_result = df_result.sort_values("FACTURA").reset_index(drop=True)

        alertas_resultado = self._alertas_por_notas(df, modalidad)
        if modalidad == "consolidado" and not df_result.empty:
            df_result = self._consolidar_por_sku(
                df,
                sort_mode=(config or {}).get("sort_mode", "fecha_desc"),
                alertas=alertas_resultado,
                alertas_notas=False,
            )
        for _, row in df_result.iterrows():
            dif = row.get("DIFERENCIA", 0)
            sku = str(row.get("SKU", ""))
            monto_nc = float(row.get("MONTO_NC", 0))
            cant = float(row.get("CANTIDAD", 0))
            if cant <= 0:
                continue
            if dif > 0:
                alertas_resultado.append(
                    BusinessAlert(
                        codigo="AL01",
                        tipo="info",
                        severidad="baja",
                        sku=sku,
                        mensaje=generar_texto_alerta(
                            "AL01", sku=sku, diferencia=dif, cantidad=cant
                        ),
                        impacto=monto_nc,
                        motor="PriceDifference",
                    )
                )

        return df_result, alertas_resultado

    def _procesar_ferias(self, df_hist, requerimiento, alertas):
        """Modo Ferias: historial × descuento del requerimiento.
        PRECIO_NETO = PRECIO_UNITARIO × (1 - DESC_req).
        CANTIDAD = del requerimiento."""
        if "SKU" not in requerimiento.columns or "DESCUENTO" not in requerimiento.columns:
            alertas.append(
                BusinessAlert(
                    tipo="error", severidad="alta", mensaje="Requerimiento sin SKU o DESCUENTO"
                )
            )
            return pd.DataFrame(), []

        req_clean = requerimiento[["SKU", "DESCUENTO"]].copy()
        if "CANTIDAD" in requerimiento.columns:
            req_clean["CANTIDAD"] = requerimiento["CANTIDAD"]
        req_clean.columns = ["SKU", "DESC_REQ", "CANTIDAD_REQ"]
        req_clean["DESC_REQ"], cod_fer = normalizar_porcentaje_serie(req_clean["DESC_REQ"])
        for _cod in ("AL04", "AL10"):
            _mask = cod_fer == _cod
            if _mask.any():
                _alertar_pct(_cod, "descuento de ferias", alertas, skus=req_clean.loc[_mask, "SKU"])
        if "CANTIDAD_REQ" not in req_clean.columns:
            req_clean["CANTIDAD_REQ"] = 0

        merged = df_hist.merge(
            req_clean.rename(columns={"SKU": "CODIGO"}), on="CODIGO", how="inner"
        )
        if merged.empty:
            alertas.append(
                BusinessAlert(tipo="warning", severidad="media", mensaje="Sin SKUs coincidentes")
            )
            return pd.DataFrame(), []

        merged["PRECIO_HIST"] = np.where(
            merged["CANTIDAD"] > 0, merged["SOLES"] / merged["CANTIDAD"], 0
        ).round(PRECIO_DECIMALES)
        merged["PRECIO_NETO"] = (merged["PRECIO_HIST"] * (1 - merged["DESC_REQ"])).round(
            PRECIO_DECIMALES
        )
        merged["DIFERENCIA"] = (merged["PRECIO_HIST"] - merged["PRECIO_NETO"]).round(
            PRECIO_DECIMALES
        )
        merged["MONTO_NC"] = (merged["DIFERENCIA"].clip(lower=0) * merged["CANTIDAD_REQ"]).round(2)

        merged["FACTURA"] = merged.apply(_build_factura, axis=1)

        def _clase_ferias(r):
            dif = r.get("DIFERENCIA", 0)
            sku = str(r.get("CODIGO", ""))
            cant = float(r.get("CANTIDAD", 0))
            precio_base = float(r.get("PRECIO_BASE", r.get("PRECIO_HIST", 0)))
            if dif > 0 and not self._dentro_de_tol_redondeo(dif, cant, precio_base):
                return generar_texto_alerta("AL01", sku=sku, diferencia=dif, cantidad=cant)
            if dif > 0 and self._dentro_de_tol_redondeo(dif, cant, precio_base):
                return generar_texto_alerta(
                    "AL11",
                    sku=sku,
                    diferencia_unitaria=dif,
                    diferencia_total=dif * cant,
                    cantidad=cant,
                )
            return generar_texto_alerta("AL02", sku=sku, coincide=(abs(dif) < 0.001))

        merged["ALERTA"] = merged.apply(_clase_ferias, axis=1)

        resultado_cols = [
            "CODIGO",
            "ARTICULO",
            "CANTIDAD",
            "SOLES",
            "PRECIO_HIST",
            "PRECIO_NETO",
            "DIFERENCIA",
            "MONTO_NC",
            "FACTURA",
            "ALERTA",
            "COD_CLIENTE",
            "DOC_CLIENTE",
        ]
        df_result = merged[[c for c in resultado_cols if c in merged.columns]].copy()
        df_result = df_result.rename(columns={"CODIGO": "SKU"})
        return df_result, []
