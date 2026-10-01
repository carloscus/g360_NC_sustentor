import re
import pandas as pd
from pathlib import Path
from src.core.utils import read_erp_file, build_doc_full
from src.core.doc_matcher import seleccionar_mejor_documento
from src.core.nc_reconciliation import (
    alertas_reconciliacion,
    normalizar_cliente,
    normalizar_sku,
    texto_auditoria_notas,
)
from src.domain import (
    ExpedienteComercial,
    RecognitionResult,
    BusinessAlert,
    generar_texto_alerta,
    CATALOGO_ALERTAS,
)
from src.strategies.allocation import AllocationEngine
from src.strategies.price_difference import PriceDifferenceStrategy


def _moda(valores) -> float:
    """Valor más frecuente (misma regla que el consolidado de DC/VRS)."""
    nums = [float(v) for v in valores if v is not None]
    if not nums:
        return 0.0
    frec: dict = {}
    for v in nums:
        frec[v] = frec.get(v, 0) + 1
    top = max(frec.values())
    return next(v for v in nums if frec[v] == top)


def _precio_por_notas(df: pd.DataFrame) -> pd.DataFrame:
    """PRECIO_UNITARIO = SOLES_CALCULO / CANTIDAD_FACTURADA en lo ajustado.

    Es el mismo criterio de VRS: una FAE exacta aplicada cambia el precio
    atendido que el AllocationEngine usa para valorar el sustento. Las líneas
    sin nota conservan su precio original.
    """
    if "CANTIDAD_FACTURADA" not in df.columns or "SOLES_CALCULO" not in df.columns:
        return df
    qty = pd.to_numeric(df["CANTIDAD_FACTURADA"], errors="coerce").fillna(0)
    soles = pd.to_numeric(df["SOLES_CALCULO"], errors="coerce").fillna(0)
    base = pd.to_numeric(df.get("PRECIO_UNITARIO"), errors="coerce")
    nuevo = (soles / qty).where(qty > 0)
    df["PRECIO_UNITARIO"] = nuevo.round(5).where(qty > 0, base)
    return df


def _cobertura_por_sku(df: pd.DataFrame, skus_req: set) -> tuple[dict, dict]:
    """(facturado, disponible) por SKU — misma convención de cobertura que VRS.

    Facturado es lo que dicen las facturas; disponible es lo que queda tras la
    reconciliación de NC/NDB (cantidad elegible para el cálculo).
    """
    vacio = ({}, {})
    if df is None or df.empty or "CODIGO" not in df.columns:
        return vacio
    objetivo = {normalizar_sku(s) for s in skus_req}
    claves = df["CODIGO"].map(normalizar_sku)
    d = df[claves.isin(objetivo)]
    if d.empty:
        return vacio
    g = d.groupby(claves[claves.isin(objetivo)], sort=False)
    fact = (
        g["CANTIDAD_FACTURADA"].sum().astype(float).to_dict()
        if "CANTIDAD_FACTURADA" in d.columns
        else {}
    )
    disp = g["CANTIDAD"].sum().astype(float).to_dict() if "CANTIDAD" in d.columns else {}
    return fact, disp


def _pares_historial(df: pd.DataFrame, skus_req: set, rec_map: dict) -> set:
    """Pares (cliente, factura, SKU) presentes, para filtrar AL12/AL13."""
    pares: set = set()
    if not rec_map:
        return pares
    con_cliente = any(len(k) == 3 for k in rec_map)
    objetivo = {normalizar_sku(s) for s in skus_req}
    for _, row in df.iterrows():
        sku = normalizar_sku(row.get("CODIGO"))
        if sku not in objetivo:
            continue
        invoice = str(row.get("DOC_ID", "") or "").strip()
        if con_cliente:
            pares.add((normalizar_cliente(row.get("COD_CLIENTE", "")), invoice, sku))
        else:
            pares.add((invoice, sku))
    return pares


def _cliente_por_sku(df: pd.DataFrame) -> dict:
    """Cliente normalizado por SKU: el reconciliador indexa con esa clave."""
    out: dict = {}
    if df is None or df.empty or "CODIGO" not in df.columns:
        return out
    for _, row in df.iterrows():
        clave = normalizar_sku(row.get("CODIGO"))
        if clave and clave not in out:
            out[clave] = normalizar_cliente(row.get("COD_CLIENTE", ""))
    return out


def _mapa_doc_alias(df: pd.DataFrame) -> dict:
    """DOC_ID del historial ↔ documento que arma el AllocationEngine.

    El reconciliador indexa por ``DOC_ID`` (p.ej. ``FF201-100``) y el motor de
    asignación arma el doc sin el prefijo duplicado (``F201-100``). Sin este
    mapa las notas nunca se encontrarían por documento.
    """
    from src.core.utils import split_doc_id

    alias: dict = {}
    if df is None or df.empty:
        return alias
    for _, row in df.iterrows():
        doc_id = str(row.get("DOC_ID", "") or "").strip()
        if not doc_id:
            continue
        try:
            tipo, serie, nro = split_doc_id(
                row.get("TIPO_DOC"), row.get("SERIE"), row.get("NUMERO")
            )
            doc_full = build_doc_full(tipo, serie, nro)
        except Exception:
            continue
        if doc_full and doc_full != doc_id:
            alias.setdefault(doc_full, set()).add(doc_id)
    return alias


def _notas_de_documento(
    rec_map: dict, doc: str, sku: str, cliente: str = "", alias: dict | None = None
) -> dict:
    """Recuperación tolerante de la reconciliación de (factura, SKU)."""
    if not rec_map:
        return {}
    doc = str(doc or "").strip()
    sku = normalizar_sku(sku)
    rec = rec_map.get((normalizar_cliente(cliente), doc, sku)) or rec_map.get((doc, sku)) or {}
    if rec or not alias:
        return rec
    for doc_id in alias.get(doc, ()):  # el motor arma el doc sin prefijo
        rec = (
            rec_map.get((normalizar_cliente(cliente), doc_id, sku))
            or rec_map.get((doc_id, sku))
            or {}
        )
        if rec:
            return rec
    return {}


def _status_a_codigo_alerta(status: str, item=None) -> tuple:
    """Mapea STATUS del AllocationEngine a (codigo_AL, texto_detallado)."""
    s = status.upper().strip()

    if s == "OK":
        return "OK", "OK"

    if s.startswith("ERROR"):
        sku = item.CODIGO if item else ""
        return "AL06", generar_texto_alerta("AL06", sku=sku)

    if "⚠️" in status or "SE USARON" in s:
        sku = item.CODIGO if item else ""
        # El item trae los dos números exactos; el STATUS los menciona al revés
        # ("SE USARON <solicitado> ... Sustentadas <asignado>"), así que el
        # texto se arma desde el item y el regex queda como respaldo.
        if item is not None and (item.CANTIDAD_SOLICITADA or 0) > 0:
            # Con "forzar cantidad" el item reporta la cantidad SOLICITADA como
            # encontrada; lo realmente asignado son las unidades por documento.
            asignado = int(
                sum(
                    float(q or 0)
                    for q in (getattr(item, "DOCUMENTOS_CANTIDAD", None) or {}).values()
                )
            ) or int(item.CANTIDAD_REAL_ENCONTRADA or 0)
            solicitado = int(item.CANTIDAD_SOLICITADA or 0)
        else:
            asignado = 0
            solicitado = item.CANTIDAD_SOLICITADA if item else 0
            m = re.search(r"USARON (\d+) UNIDADES.*?(\d+)", status)
            if m:
                usado_str, pend_str = m.groups()
                asignado = int(usado_str)
                solicitado = int(pend_str)
        # Extraer documentos con cantidades
        docs = {}
        if item and hasattr(item, "DOCUMENTOS_CANTIDAD") and item.DOCUMENTOS_CANTIDAD:
            for doc_id, cant in item.DOCUMENTOS_CANTIDAD.items():
                docs[doc_id] = {"cantidad": int(cant), "monto": 0}
        return "AL09", generar_texto_alerta(
            "AL09",
            sku=sku,
            asignado=asignado,
            solicitado=solicitado,
            documentos=docs if docs else None,
        )

    if "PRECIO" in s and "VARIABLE" in s:
        sku = item.CODIGO if item else ""
        # Construir detalle por documento
        docs = {}
        if item and hasattr(item, "DOCUMENTOS_CANTIDAD") and item.DOCUMENTOS_CANTIDAD:
            for doc_id, cant in item.DOCUMENTOS_CANTIDAD.items():
                monto = (
                    item.DOCUMENTOS_MONTOS.get(doc_id, 0)
                    if hasattr(item, "DOCUMENTOS_MONTOS")
                    else 0
                )
                docs[doc_id] = {"cantidad": int(cant), "monto": float(monto)}
        return "AL03", generar_texto_alerta("AL03", sku=sku, documentos=docs if docs else None)

    if "CANTIDAD" in s and ("VACÍA" in s or "CERO" in s):
        return "AL10", generar_texto_alerta("AL10", detalle="Cantidad vacía o cero")

    if "DESCUENTO" in s and ("VACÍO" in s or "CERO" in s):
        return "AL10", generar_texto_alerta("AL10", detalle="Descuento vacío o cero")

    if "EXCEDE" in s:
        return "AL04", generar_texto_alerta("AL04", detalle="Descuento excede 100%")

    return "OK", "OK"


class FeriaPreventaStrategy:
    def process(self, expediente: ExpedienteComercial) -> RecognitionResult:
        config = expediente.contexto.config
        rutas = config.get("ruta_requerimientos", [])
        if isinstance(rutas, str):
            rutas = [rutas]
        if not rutas:
            return RecognitionResult(
                alertas=[
                    BusinessAlert(
                        tipo="error",
                        severidad="alta",
                        mensaje="No se cargó archivo de requerimientos",
                        motor="FeriaPreventa",
                    )
                ],
                resumen={
                    "total_nc": 0,
                    "skus_afectados": 0,
                    "lotes_procesados": 0,
                    "filas_requerimiento": 0,
                },
            )

        columnas_req = config.get("columnas_requerimientos", {})
        codigo_col = columnas_req.get("codigo", "CODIGO")
        cantidad_col = columnas_req.get("cantidad", "CANTIDAD_NC")
        descuento_col = columnas_req.get("descuento", "PORCENTAJE_DESC")

        header_map = {}
        try:
            from src.pipeline import CatalogoCargador

            schema = CatalogoCargador().obtener_schema("feria_preventa")
            header_map = schema.get("header_map", {})
        except Exception:
            pass

        sort_mode = config.get("sort_mode", "fecha_desc")
        forzar_cantidad = config.get("forzar_cantidad", True)
        fecha_desde = config.get("fecha_desde")
        fecha_hasta = config.get("fecha_hasta")
        modalidad = str(config.get("modalidad", "individual"))

        df_hist = expediente.datos.copy()

        # FPE comparte la política factura×SKU de DC/DO/VRS: en individual las
        # NC/NDB exactas habilitadas ajustan el precio atendido y la cantidad;
        # en consolidado el reconciliador es informativo (solo se detalla).
        pd_helper = PriceDifferenceStrategy()
        df_hist = _precio_por_notas(pd_helper._aplicar_reconciliacion_notas(df_hist, config))

        engine = AllocationEngine(
            sort_mode=sort_mode,
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta,
            forzar_cantidad=forzar_cantidad,
        )

        cliente_nombre = config.get("cliente", "")
        todos_items = []
        todos_docs = set()
        total_alertas = []
        lote_idx = 0
        total_filas_req = 0

        # Paso 0: Seleccionar el mejor documento de referencia (sustento) del cliente
        # usando todos los requerimientos combinados para el matching de SKUs.
        df_req_combinado = []
        for ruta in rutas:
            rp = Path(ruta)
            if not rp.exists():
                continue
            try:
                df_single = read_erp_file(ruta)
                if df_single.empty:
                    continue
                for old, new in header_map.items():
                    if old in df_single.columns and new not in df_single.columns:
                        df_single = df_single.rename(columns={old: new})
                df_single = df_single.rename(
                    columns={
                        codigo_col: "CODIGO",
                        cantidad_col: "CANTIDAD_NC",
                        descuento_col: "PORCENTAJE_DESC",
                    }
                )
                df_req_combinado.append(df_single)
            except Exception:
                continue

        doc_referencia = ""
        if df_req_combinado:
            df_all_req = pd.concat(df_req_combinado, ignore_index=True)
            doc_referencia = seleccionar_mejor_documento(
                df_hist,
                df_all_req,
                cliente=cliente_nombre,
            )

        for ruta in rutas:
            lote_idx += 1
            rp = Path(ruta)
            if not rp.exists():
                total_alertas.append(
                    BusinessAlert(
                        tipo="error",
                        severidad="alta",
                        mensaje=f"Archivo no encontrado: {rp.name}",
                        motor="FeriaPreventa",
                    )
                )
                continue
            try:
                df_req = read_erp_file(ruta)
            except Exception as ex:
                total_alertas.append(
                    BusinessAlert(
                        tipo="error",
                        severidad="alta",
                        mensaje=f"Error al leer {rp.name}: {ex}",
                        motor="FeriaPreventa",
                    )
                )
                continue

            if df_req.empty:
                total_alertas.append(
                    BusinessAlert(
                        tipo="info",
                        severidad="baja",
                        mensaje=f"Archivo vacío: {rp.name}",
                        motor="FeriaPreventa",
                    )
                )
                continue

            for old, new in header_map.items():
                if old in df_req.columns and new not in df_req.columns:
                    df_req = df_req.rename(columns={old: new})
            df_req = df_req.rename(
                columns={
                    codigo_col: "CODIGO",
                    cantidad_col: "CANTIDAD_NC",
                    descuento_col: "PORCENTAJE_DESC",
                }
            )

            total_filas_req += len(df_req)

            items, docs = engine.assign(df_req, df_hist)
            for item in items:
                item.NOMBRE_LOTE = rp.stem
            todos_items.extend(items)
            todos_docs.update(docs)

        # Detectar SKUs repetidos con diferente descuento (AL08)
        sku_descuento_set = {}
        for item in todos_items:
            if item.CODIGO not in sku_descuento_set:
                sku_descuento_set[item.CODIGO] = set()
            sku_descuento_set[item.CODIGO].add(round(item.PORCENTAJE_APLICADO, 4))

        for sku, pcts in sku_descuento_set.items():
            if len(pcts) > 1:
                texto = generar_texto_alerta("AL08", sku=sku, cant_desc=len(pcts))
                total_alertas.append(
                    BusinessAlert(
                        codigo="AL08",
                        tipo="info",
                        severidad="baja",
                        sku=sku,
                        mensaje=texto,
                        motor="FeriaPreventa",
                    )
                )

        # ── notas reconciliadas y cobertura por SKU ──────────────────────
        rec_map = config.get("reconciliacion_nc") or {}
        skus_req = {str(i.CODIGO) for i in todos_items}
        facturado_por_sku, disponible_por_sku = _cobertura_por_sku(df_hist, skus_req)
        doc_alias = _mapa_doc_alias(df_hist)
        cliente_por_sku = _cliente_por_sku(df_hist)
        if rec_map and modalidad != "consolidado":
            # Igual que DC/VRS: solo en individual las notas priorizadas se
            # replican como alerta de resultado (AL12/AL13).
            total_alertas.extend(
                alertas_reconciliacion(
                    rec_map,
                    pares=_pares_historial(df_hist, skus_req, rec_map),
                    motor="FeriaPreventa",
                )
            )

        def _cobertura_de(sku: str) -> tuple:
            """(facturado, disponible, % restante) repetidos en cada fila del SKU."""
            f = float(facturado_por_sku.get(normalizar_sku(sku), 0.0))
            d = float(disponible_por_sku.get(normalizar_sku(sku), 0.0))
            return f, d

        def _notas_de(item, doc: str) -> tuple:
            """(alertas, auditoría) de las notas que tocan ese documento."""
            rec = _notas_de_documento(
                rec_map,
                doc,
                item.CODIGO,
                cliente=cliente_por_sku.get(normalizar_sku(item.CODIGO), ""),
                alias=doc_alias,
            )
            if not rec:
                return "", ""
            alertas_txt = " | ".join(
                dict.fromkeys(str(a).strip() for a in rec.get("alerts", []) if str(a).strip())
            )
            audit = texto_auditoria_notas(rec.get("details", []))
            return alertas_txt, audit

        # Dos dataframes: vista previa (ligera) + reporte Excel (detallado).
        # Modalidad individual: una fila por factura×SKU (se explota el reparto
        # del AllocationEngine). Consolidado: una fila por SKU con precio por
        # moda y el detalle en la auditoría.
        vista_previa_data: list = []
        reporte_data: list = []

        def _glosa_de(item, extra: str = "") -> str:
            partes: list = []
            if "VARIABLE" in str(item.STATUS or "").upper():
                partes.append("Precios variables en historial")
            gap = (item.CANTIDAD_SOLICITADA or 0) - (item.CANTIDAD_REAL_ENCONTRADA or 0)
            if gap > 0:
                partes.append(f"Sin sustento: {gap} unid.")
            if extra:
                partes.append(extra)
            return " | ".join(partes)

        if modalidad == "consolidado":
            grupos: dict = {}
            for item in todos_items:
                grupos.setdefault(str(item.CODIGO), []).append(item)
            for codigo, items in grupos.items():
                codigo_al, texto_al = _status_a_codigo_alerta(items[0].STATUS, items[0])
                cant_sol = sum(i.CANTIDAD_SOLICITADA or 0 for i in items)
                cant_sus = sum(i.CANTIDAD_REAL_ENCONTRADA or 0 for i in items)
                pct_cump = (cant_sus / cant_sol * 100) if cant_sol > 0 else 0.0
                # Precio consolidado = MODA de los precios de línea que
                # realmente sustentan (el mismo corte por documento que se
                # lista en "Cortes"), no del precio de referencia del item.
                docs_qty: dict = {}
                docs_montos: dict = {}
                for i in items:
                    for doc, q in (i.DOCUMENTOS_CANTIDAD or {}).items():
                        docs_qty[doc] = docs_qty.get(doc, 0.0) + float(q or 0)
                    for doc, m in (i.DOCUMENTOS_MONTOS or {}).items():
                        docs_montos[doc] = docs_montos.get(doc, 0.0) + float(m or 0)
                precio_doc = {
                    doc: (docs_montos.get(doc, 0.0) / q if q > 0 else 0.0)
                    for doc, q in docs_qty.items()
                }
                precios = [precio_doc[d] for d, q in docs_qty.items() if q > 0]
                precios = precios or [i.PRECIO_UNITARIO or 0 for i in items]
                pu = _moda(precios)
                pcts = [i.PORCENTAJE_APLICADO or 0 for i in items]
                pct = _moda(pcts)
                desc_unit = round(pu * pct, 5)
                pu_res = round(pu - desc_unit, 5)
                tot_sus = sum(i.VALOR_SOPORTE_TOTAL or 0 for i in items)
                subtotal = sum(i.SUBTOTAL_DESCUENTO or 0 for i in items)
                docs_str = "; ".join(
                    f"{doc} ({q:.0f} unid)" for doc, q in docs_qty.items() if q > 0
                )
                fac_principal = (
                    max(docs_montos, key=docs_montos.get)
                    if docs_montos
                    else (next(iter(docs_qty)) if docs_qty else "")
                )
                alertas_txt, audits = [], []
                for doc in docs_qty:
                    a_txt, a_aud = _notas_de(items[0], doc)
                    if a_txt:
                        alertas_txt.append(a_txt)
                    if a_aud:
                        audits.append(a_aud)
                alertas_txt = " | ".join(dict.fromkeys(alertas_txt))
                audit = (
                    f"Consolidado {len(items)} lote(s) de "
                    f"{len([d for d, q in docs_qty.items() if q > 0])} factura(s) "
                    f"({', '.join(docs_qty)}); moda S/ {pu:.5f}"
                )
                distintos = sorted({round(float(p), 5) for p in precios})
                if len(distintos) > 1:
                    audit += (
                        f"; rango S/ {min(distintos):.5f}–{max(distintos):.5f} "
                        "(monto por suma exacta de líneas)"
                    )
                audit += f"; desc. aplicado {pct * 100:.2f}%"
                cortes = "; ".join(
                    f"{doc} {q:,.0f}u ({precio_doc.get(doc, 0.0):,.2f})"
                    for doc, q in docs_qty.items()
                    if q > 0
                )
                audit += f" Cortes: {cortes}."
                if audits:
                    audit += " | Notas previas: " + " || ".join(dict.fromkeys(audits))
                fact, disp = _cobertura_de(codigo)
                vista_previa_data.append(
                    {
                        "SKU": codigo,
                        "SKU - ARTICULO": f"{codigo} - {items[0].ARTICULO}",
                        "CANT. SOLICITADA": cant_sol,
                        "CANT. SUSTENTAR": cant_sus,
                        "PRECIO NETO": pu_res,
                        "SUBTOTAL (SIN IGV)": round(subtotal, 2),
                        "ALERTA": " | ".join(x for x in (texto_al, alertas_txt) if x),
                    }
                )
                reporte_data.append(
                    {
                        "SKU": codigo,
                        "ARTICULO": items[0].ARTICULO,
                        "LINEA": getattr(items[0], "LINEA", "")
                        or getattr(items[0], "COD_LINEA", ""),
                        "Cant. Solicitada": cant_sol,
                        "Cant. Sustentada": cant_sus,
                        "% Cumplimiento": round(pct_cump, 1),
                        "P.U. Hist.": round(pu, 5),
                        "Desc. (%) Aplicado": round(pct * 100, 2),
                        "Desc. Unit. (S/)": desc_unit,
                        "P.U. Result.": pu_res,
                        "Tot. Sustento (S/)": round(tot_sus, 2),
                        "Subtotal NC (S/)": round(subtotal, 2),
                        "Facturas (qty)": docs_str,
                        "FACTURA": fac_principal,
                        "Glosa": " | ".join(
                            x
                            for x in (_glosa_de(items[0]), *[_glosa_de(i) for i in items[1:]])
                            if x
                        ),
                        "Alerta": " | ".join(x for x in (texto_al, alertas_txt) if x),
                        "AUDITORIA_NC": audit,
                        "Cant. Facturada": fact,
                        "Cant. Disponible": disp,
                        "% Stock Restante": round(cant_sol / disp * 100, 1) if disp > 0 else 0.0,
                    }
                )
        else:
            for item in todos_items:
                codigo_al, texto_al = _status_a_codigo_alerta(item.STATUS, item)
                cant_sol = item.CANTIDAD_SOLICITADA or 0
                cant_sus = item.CANTIDAD_REAL_ENCONTRADA or 0
                fact, disp = _cobertura_de(item.CODIGO)
                vista_previa_data.append(
                    {
                        "SKU": item.CODIGO,
                        "SKU - ARTICULO": f"{item.CODIGO} - {item.ARTICULO}",
                        "CANT. SOLICITADA": cant_sol,
                        "CANT. SUSTENTAR": cant_sus,
                        "PRECIO NETO": item.PRECIO_NETO_FINAL,
                        "SUBTOTAL (SIN IGV)": item.SUBTOTAL_DESCUENTO,
                        "ALERTA": texto_al,
                    }
                )
                # Una fila por factura: la cantidad de la fila es la que entra
                # al % de cumplimiento y al monto. Con "forzar cantidad" (por
                # defecto) lo que se reconoce es lo SOLICITADO, así que el
                # faltante se reparte entre los documentos en proporción a su
                # soporte real; lo efectivamente sustentado queda en
                # Tot. Sustento y en la glosa "Sin sustento". Sin forzar, cada
                # fila lleva solo lo que el documento realmente aporta.
                docs = [d for d in (item.DOCUMENTOS or [])] or [""]
                real_total = sum(
                    float((item.DOCUMENTOS_CANTIDAD or {}).get(d, 0) or 0)
                    for d in (item.DOCUMENTOS or [])
                )
                faltante = float(cant_sol) - real_total
                forzar_falta = bool(forzar_cantidad) and faltante > 0
                for doc in docs:
                    qty_real = float((item.DOCUMENTOS_CANTIDAD or {}).get(doc, 0) or 0)
                    if not doc:
                        qty_doc = float(cant_sus)
                    elif not forzar_falta:
                        qty_doc = qty_real
                    elif real_total > 0:
                        qty_doc = qty_real * float(cant_sol) / real_total
                    else:
                        qty_doc = float(cant_sol) if doc == docs[0] else 0.0
                    pct_cump = (qty_doc / cant_sol * 100) if cant_sol > 0 else 0.0
                    monto_doc = float((item.DOCUMENTOS_MONTOS or {}).get(doc, 0) or 0)
                    alertas_txt, audit_notas = _notas_de(item, doc)
                    extra = f"Asignado: {doc} ({qty_doc:.0f})" if doc else ""
                    reporte_data.append(
                        {
                            "SKU": item.CODIGO,
                            "ARTICULO": item.ARTICULO,
                            "LINEA": getattr(item, "LINEA", "") or getattr(item, "COD_LINEA", ""),
                            "Cant. Solicitada": cant_sol,
                            "Cant. Sustentada": round(qty_doc, 2),
                            "% Cumplimiento": round(pct_cump, 1),
                            "P.U. Hist.": round(item.PRECIO_UNITARIO, 5)
                            if item.PRECIO_UNITARIO is not None
                            else 0.0,
                            "Desc. (%) Aplicado": round(item.PORCENTAJE_APLICADO * 100, 2),
                            "Desc. Unit. (S/)": round(item.MONTO_DESCUENTO_UNITARIO, 5)
                            if item.MONTO_DESCUENTO_UNITARIO is not None
                            else 0.0,
                            "P.U. Result.": round(item.PRECIO_NETO_FINAL, 5)
                            if item.PRECIO_NETO_FINAL is not None
                            else 0.0,
                            "Tot. Sustento (S/)": round(monto_doc, 2),
                            "Subtotal NC (S/)": round(
                                (item.MONTO_DESCUENTO_UNITARIO or 0) * qty_doc, 2
                            ),
                            "FACTURA": doc,
                            "Facturas (qty)": f"{doc} ({qty_doc:.0f} unid)" if doc else "",
                            "Glosa": _glosa_de(item, extra),
                            "Alerta": " | ".join(x for x in (texto_al, alertas_txt) if x),
                            "AUDITORIA_NC": audit_notas,
                            "Cant. Facturada": fact,
                            "Cant. Disponible": disp,
                            "% Stock Restante": round(cant_sol / disp * 100, 1)
                            if disp > 0
                            else 0.0,
                        }
                    )

        df_vista_previa = pd.DataFrame(vista_previa_data) if vista_previa_data else pd.DataFrame()
        df_reporte = pd.DataFrame(reporte_data) if reporte_data else pd.DataFrame()

        total_nc = (
            df_reporte["Subtotal NC (S/)"].sum() if "Subtotal NC (S/)" in df_reporte.columns else 0
        )

        alertas = list(total_alertas)
        for item in todos_items:
            codigo_al, texto_al = _status_a_codigo_alerta(item.STATUS, item)
            if codigo_al != "OK":
                info_al = CATALOGO_ALERTAS.get(codigo_al, {"tipo": "info", "severidad": "baja"})
                alertas.append(
                    BusinessAlert(
                        codigo=codigo_al,
                        tipo=info_al["tipo"],
                        severidad=info_al["severidad"],
                        sku=item.CODIGO,
                        mensaje=texto_al,
                        motor="FeriaPreventa",
                    )
                )

        archivo_nombre = ""
        for r in rutas:
            rp = Path(r)
            if rp.exists():
                archivo_nombre = rp.name
                break

        resumen = {
            "total_nc": total_nc,
            "skus_afectados": len(skus_req),
            "lotes_procesados": len([r for r in rutas if Path(r).exists()]),
            "filas_requerimiento": total_filas_req,
            "documento_referencia": doc_referencia,
            "archivo": archivo_nombre,
        }
        # Solo consolidado lista las facturas comprometidas: en individual cada
        # fila ya trae su documento (misma convención que DC/VRS).
        if modalidad == "consolidado":
            docs_unicos = sorted({str(d).strip() for d in todos_docs if str(d).strip()})
            if docs_unicos:
                resumen["documentos_unicos"] = docs_unicos
                resumen["titulo_documentos"] = "FACTURAS COMPROMETIDAS"

        return RecognitionResult(
            dataframe=df_vista_previa,
            dataframe_excel=df_reporte,
            resumen=resumen,
            alertas=alertas,
            trazabilidad=[
                f"FeriaPreventa: {len(todos_items)} items procesados, {len(todos_docs)} documentos únicos, {len(rutas)} lotes, ref: {doc_referencia or 'N/A'}"
            ],
        )
