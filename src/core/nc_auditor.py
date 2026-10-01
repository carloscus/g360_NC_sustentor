"""
Auditoría de Notas de Crédito/Débito contra facturas del historial.

Regla de negocio (exact-match):
  NCR solo AFECTA PRECIO si SUM(cantidad_NC por folio+factura+SKU) == cantidad
  facturada (eps 1e-6). NDB con documento+SKU directo AUMENTA el precio
  (su cantidad fisica es 0). Todo lo demas es INFORMATIVO y no modifica
  precio_neto (parciales, excesos, consolidados de feria, refs erroneas).

Niveles de alerta:
  1. SKU DIRECTO: match exacto -> cruza contra precio ([SKU]).
  2. INFORMATIVO: sin evidencia para tocar precio ([INF], [GRAL], [CON]).
  3. ERROR probable: SKU inexistente en folio de 1 linea, sin ref ([ERR]/[?]).

Las alertas son informativas (no bloquean el pipeline).
"""

import pandas as pd
from dataclasses import dataclass
from typing import List


EPS_QTY = 1e-6  # igualdad de cantidades con tolerancia float


@dataclass
class NcAlert:
    nivel: str  # "sku" | "documento"
    tipo: str  # "match_directo" | "nc_informativa" | "nc_consolidada"
    # | "nc_general" | "sku_no_en_factura" | "sin_referencia"
    factura_id: str
    nc_id: str
    sku: str
    nc_monto: float
    base_monto: float
    pct: float
    nivel_detalle: str  # "DIRECTO" | "INFORMATIVO" | "CONSOLIDADO" | "GENERAL"
    # | "DESCUENTO" | "DEVOLUCION" | "ERROR" | "SIN_REF"
    mensaje: str
    cant_factura: float = 0.0  # cantidad facturada del SKU (auditable)
    cant_nc: float = 0.0  # cantidad NC acumulada por folio+factura+SKU
    n_docs: int = 0  # documentos NC acumulados en el grupo
    id_cliente: str = ""  # desambigua documentos repetidos entre clientes


class CreditNoteAuditor:
    """Audita NC/ND del historial contra sus facturas referenciadas."""

    @staticmethod
    def _client_key(value) -> str:
        if value is None:
            return ""
        text = str(value).strip()
        if text.lower() in ("", "nan", "none"):
            return ""
        return text[:-2] if text.endswith(".0") else text

    @staticmethod
    def _nc_qty(nc: pd.Series) -> float:
        """Cantidad comparable de una fila NC según su clase.

        descuento (ajuste_valor) -> CANTIDAD_FAE (su base real; CANTIDAD=0);
        devolucion -> CANTIDAD; cargo (NDB) -> 0.0 (sin prueba de cantidad:
        es incremento economico, no movimiento fisico).
        """

        def _num(v) -> float:
            try:
                f = abs(float(v))
                return f if f == f else 0.0  # NaN -> 0
            except (TypeError, ValueError):
                return 0.0

        clase = str(nc.get("TIPO_CLASE", "")).strip()
        if clase == "descuento":
            fae = _num(nc.get("CANTIDAD_FAE", None))
            if fae > 0:
                return fae
            return _num(nc.get("CANTIDAD", 0))
        if clase == "devolucion":
            return _num(nc.get("CANTIDAD", 0))
        if clase == "cargo":
            # En NDB/ND la FAE es evidencia de la base del ajuste de valor.
            # No es movimiento físico ni se infiere cantidad si FAE falta.
            return _num(nc.get("CANTIDAD_FAE", 0))
        return 0.0

    def auditar(
        self,
        df_historial: pd.DataFrame,
        df_resultado: pd.DataFrame = None,
        *,
        documentos_historial: dict | None = None,
        modalidad: str = "individual",
    ) -> List[NcAlert]:
        if df_historial is None or df_historial.empty:
            return []

        df = df_historial.copy()
        if "TIPO_CLASE" not in df.columns:
            from src.core.document_classifier import DocumentClassifier

            df = DocumentClassifier().classify(df)
        client_col = next(
            (c for c in ("COD_CLIENTE", "id_cliente", "DOC_CLIENTE") if c in df.columns), None
        )

        # Si se pasa un df_resultado, solo auditar NCs referenciadas a facturas
        # efectivamente procesadas en este ejecucion (evita ruido de fuera del segmento).
        # Las celdas pueden traer listas ("F1, F2" / "F1; F2" / "F1 (5 unid)"):
        # se parte cada una para no perder el cruce en consolidado.
        facturas_procesadas = None
        if df_resultado is not None and not df_resultado.empty:
            cols_docs = [
                c for c in ("FACTURAS", "FACTURA", "Facturas (qty)") if c in df_resultado.columns
            ]
            if cols_docs:
                # Consolidado trae FACTURA (principal) y FACTURAS (todas las
                # contribuyentes). El conjunto debe usar la lista completa.
                facturas_procesadas = set()
                result_client_col = next(
                    (
                        c
                        for c in ("COD_CLIENTE", "id_cliente", "DOC_CLIENTE")
                        if c in df_resultado.columns
                    ),
                    None,
                )
                for _, result_row in df_resultado.iterrows():
                    client = (
                        self._client_key(result_row.get(result_client_col, ""))
                        if result_client_col
                        else ""
                    )
                    for col in cols_docs:
                        v = result_row.get(col, "")
                        for p in str(v).replace(";", ",").split(","):
                            p = p.strip().split(" (")[0].strip()
                            if p and p.lower() != "nan":
                                facturas_procesadas.add((client, p) if client else p)

        invoices = self._build_invoice_lines(df)
        nc_rows = self._extract_nc_rows(df)

        # Agregado por (factura_ref, codigo, folio): la comparacion es a nivel
        # de operacion (varias NC pueden atender conjuntamente una factura).
        folios: dict = {}
        for _, nc in nc_rows.iterrows():
            factura_ref = str(nc.get("FACTURA_REF", "")).strip()
            codigo = str(nc.get("CODIGO", "")).strip()
            doc_id = str(nc.get("DOC_ID", "")).strip()
            clase = str(nc.get("TIPO_CLASE", "")).strip()
            client = self._client_key(nc.get(client_col, "")) if client_col else ""
            key = (client, factura_ref, codigo, doc_id)
            g = folios.setdefault(
                key,
                {
                    "qty": 0.0,
                    "soles": 0.0,
                    "n": 0,
                    "clases": set(),
                    "tpo": "",
                    "id_cliente": client,
                },
            )
            g["qty"] += self._nc_qty(nc)
            try:
                g["soles"] += abs(float(nc.get("SOLES", 0) or 0))
            except (TypeError, ValueError):
                pass
            g["n"] += 1
            if clase:
                g["clases"].add(clase)
            if not g["tpo"]:
                g["tpo"] = str(nc.get("TIPO_DOC", "")).strip()

        alertas: List[NcAlert] = []
        # Folios con lineas sin match: si un folio trae >=3 lineas y ninguna
        # es directa, se emite UNA alerta consolidada en vez de N errores.
        unmatched_por_folio: dict = {}
        for (client, factura_ref, codigo, doc_id), g in folios.items():
            if not factura_ref:
                alertas.append(self._audit_no_reference(doc_id, g, cliente_id=client))
                continue
            key_procesado = (client, factura_ref) if client else factura_ref
            if facturas_procesadas and key_procesado not in facturas_procesadas:
                continue
            invoice_key = (client, factura_ref) if client else factura_ref
            if invoice_key not in invoices:
                alertas.append(self._audit_invalid_ref(doc_id, factura_ref, g, cliente_id=client))
                continue
            inv = invoices[invoice_key]
            if not codigo:
                alertas.append(self._audit_general(doc_id, factura_ref, inv, g, cliente_id=client))
                continue
            if codigo not in inv["lineas"]:
                unmatched_por_folio.setdefault((client, doc_id), []).append(
                    (factura_ref, codigo, g)
                )
                continue
            tpo = str(g.get("tpo", "")).strip().upper()
            tipo_doc = "ndb" if tpo.startswith("ND") else "nc"
            options = (documentos_historial or {}).get(tipo_doc, {})
            if isinstance(options, dict):
                usar = bool(options.get("usar", True))
            elif isinstance(options, (list, tuple)) and len(options) >= 2:
                usar = bool(options[1])
            else:
                usar = documentos_historial is None
            aplicar = usar and modalidad != "consolidado"
            alertas.extend(
                self._audit_grupo(
                    factura_ref, codigo, doc_id, g, inv, aplicar=aplicar, cliente_id=client
                )
            )

        for (client, doc_id), grupos in unmatched_por_folio.items():
            factura_ref = grupos[0][0]
            inv = invoices.get((client, factura_ref) if client else factura_ref, {})
            directas_en_folio = sum(
                1
                for (_cid, fr, cd, _d), g in folios.items()
                if _cid == client and _d == doc_id and cd in inv.get("lineas", {})
            )
            if len(grupos) >= 3 and directas_en_folio == 0:
                soles = round(sum(g["soles"] for _, _, g in grupos), 2)
                base = round(inv.get("total_soles", 0.0), 2)
                advertencia = ""
                if soles > base:
                    advertencia = (
                        f" | ADVERTENCIA: descuento S/{soles:,.2f} "
                        f"supera el total de la factura S/{base:,.2f}"
                    )
                alertas.append(
                    NcAlert(
                        nivel="documento",
                        tipo="nc_consolidada",
                        factura_id=factura_ref,
                        nc_id=doc_id,
                        sku="",
                        id_cliente=client,
                        nc_monto=soles,
                        base_monto=base,
                        pct=0,
                        nivel_detalle="CONSOLIDADO",
                        n_docs=len(grupos),
                        mensaje=(
                            f"Descuento consolidado (posible feria/acuerdo): "
                            f"S/{soles:,.2f} en {len(grupos)} lineas sobre "
                            f"factura {factura_ref} (S/{base:,.2f}) -- "
                            f"informativo, no afecta precio{advertencia}"
                        ),
                    )
                )
            else:
                for factura_ref, codigo, g in grupos:
                    alertas.append(
                        self._audit_sku_not_found(doc_id, factura_ref, codigo, g, cliente_id=client)
                    )

        return alertas

    def _build_invoice_lines(self, df: pd.DataFrame) -> dict:
        inv_mask = df["TIPO_CLASE"] == "factura"
        facturas = {}
        client_col = next(
            (c for c in ("COD_CLIENTE", "id_cliente", "DOC_CLIENTE") if c in df.columns), None
        )
        for _, r in df[inv_mask].iterrows():
            doc_id = str(r.get("DOC_ID", "")).strip()
            if not doc_id:
                continue
            client = self._client_key(r.get(client_col, "")) if client_col else ""
            key = (client, doc_id) if client else doc_id
            if key not in facturas:
                facturas[key] = {"total_soles": 0.0, "total_qty": 0.0, "lineas": {}}
            soles = abs(float(r.get("SOLES", 0)))
            qty = abs(float(r.get("CANTIDAD", 0)))
            facturas[key]["total_soles"] += soles
            facturas[key]["total_qty"] += qty
            sku = str(r.get("CODIGO", "")).strip()
            if sku:
                if sku not in facturas[key]["lineas"]:
                    facturas[key]["lineas"][sku] = {"soles": 0.0, "qty": 0.0}
                facturas[key]["lineas"][sku]["soles"] += soles
                facturas[key]["lineas"][sku]["qty"] += qty
        return facturas

    def _extract_nc_rows(self, df: pd.DataFrame) -> pd.DataFrame:
        nc_mask = df["TIPO_CLASE"].isin(["devolucion", "descuento", "cargo"])
        return df[nc_mask].copy()

    def _audit_grupo(
        self,
        factura_id: str,
        sku: str,
        nc_id: str,
        g: dict,
        inv: dict,
        *,
        aplicar: bool = True,
        cliente_id: str = "",
    ) -> List[NcAlert]:
        """Evalua un grupo (folio+factura+SKU) contra la regla exact-match.

        NCR: SUM(cantidad) == cantidad facturada (eps) -> DIRECTO (disminuye).
        NDB (cargo): documento+SKU directo -> DIRECTO (aumenta, sin prueba
        de cantidad). Lo demas -> INFORMATIVO con numeros auditables.
        """
        linea = inv["lineas"][sku]
        linea_soles = linea["soles"]
        linea_qty = linea["qty"]
        precio_unit = linea_soles / linea_qty if linea_qty > 0 else 0
        qty, soles, n = g["qty"], g["soles"], g["n"]
        clases = g["clases"]
        es_cargo = clases == {"cargo"} and str(g.get("tpo", "")).strip().upper().startswith("ND")
        alertas = []

        if es_cargo:
            # NDB solo afecta precio si FAE coincide exactamente y el check
            # Usar está activo (y el modo no es consolidado).
            if not aplicar or linea_qty <= 0 or abs(qty - linea_qty) >= EPS_QTY:
                motivo = "no aplicado por FAE no exacta, check Usar o modalidad consolidada"
                return [
                    self._audit_informativa(
                        factura_id,
                        sku,
                        nc_id,
                        qty,
                        soles,
                        n,
                        linea_qty,
                        linea_soles,
                        motivo,
                        cliente_id=cliente_id,
                    )
                ]
            advertencia = ""
            if soles > linea_soles:
                advertencia = (
                    f" | ADVERTENCIA: cargo S/{soles:,.2f} "
                    f"supera el valor de la linea "
                    f"S/{linea_soles:,.2f}"
                )
            alertas.append(
                NcAlert(
                    nivel="sku",
                    tipo="match_directo",
                    factura_id=factura_id,
                    nc_id=nc_id,
                    sku=sku,
                    nc_monto=round(soles, 2),
                    base_monto=round(linea_soles, 2),
                    pct=(soles / linea_soles if linea_soles > 0 else 0),
                    nivel_detalle="DIRECTO",
                    cant_factura=round(linea_qty, 4),
                    cant_nc=0.0,
                    n_docs=n,
                    id_cliente=cliente_id,
                    mensaje=(
                        f"SKU {sku}: cargo directo (NDB) "
                        f"+S/{soles:,.2f} sobre {linea_qty:.0f} uds "
                        f"-- aumenta precio{advertencia}"
                    ),
                )
            )
            return alertas

        if len(clases) > 1:
            alertas.append(
                self._audit_informativa(
                    factura_id,
                    sku,
                    nc_id,
                    qty,
                    soles,
                    n,
                    linea_qty,
                    linea_soles,
                    "clases mixtas en el folio",
                    cliente_id=cliente_id,
                )
            )
            return alertas

        if linea_qty > 0 and abs(qty - linea_qty) < EPS_QTY:
            es_dev = "devolucion" in clases
            if not aplicar:
                return [
                    self._audit_informativa(
                        factura_id,
                        sku,
                        nc_id,
                        qty,
                        soles,
                        n,
                        linea_qty,
                        linea_soles,
                        "coincidencia exacta, no aplicada por configuración/modalidad",
                        cliente_id=cliente_id,
                    )
                ]
            if es_dev:
                monto = round(qty * precio_unit, 2)
                detalle = (
                    f"SKU {sku}: devolucion total {qty:.0f} de "
                    f"{linea_qty:.0f} uds -- mueve saldo, no precio"
                )
            else:
                monto = round(soles, 2)
                detalle = (
                    f"SKU {sku}: atiende exactamente {qty:.0f} de "
                    f"{linea_qty:.0f} uds ({n} doc) "
                    f"-- S/{monto:,.2f} afecta precio"
                )
            if monto > linea_soles:
                detalle += (
                    f" | ADVERTENCIA: descuento S/{monto:,.2f} "
                    f"supera el valor de la linea S/{linea_soles:,.2f}"
                )
            alertas.append(
                NcAlert(
                    nivel="sku",
                    tipo="match_directo",
                    factura_id=factura_id,
                    nc_id=nc_id,
                    sku=sku,
                    nc_monto=monto,
                    base_monto=round(linea_soles, 2),
                    pct=(qty / linea_qty if linea_qty > 0 else 0),
                    nivel_detalle="DIRECTO",
                    cant_factura=round(linea_qty, 4),
                    cant_nc=round(qty, 4),
                    n_docs=n,
                    id_cliente=cliente_id,
                    mensaje=detalle,
                )
            )
        else:
            motivo = "devolucion parcial" if "devolucion" in clases else "cantidad no exacta"
            alertas.append(
                self._audit_informativa(
                    factura_id,
                    sku,
                    nc_id,
                    qty,
                    soles,
                    n,
                    linea_qty,
                    linea_soles,
                    motivo,
                    cliente_id=cliente_id,
                )
            )
        return alertas

    def _audit_informativa(
        self,
        factura_id: str,
        sku: str,
        nc_id: str,
        qty: float,
        soles: float,
        n: int,
        linea_qty: float,
        linea_soles: float,
        motivo: str,
        *,
        cliente_id: str = "",
    ) -> NcAlert:
        return NcAlert(
            nivel="sku",
            tipo="nc_informativa",
            factura_id=factura_id,
            nc_id=nc_id,
            sku=sku,
            nc_monto=round(soles, 2),
            base_monto=round(linea_soles, 2),
            pct=0,
            nivel_detalle="INFORMATIVO",
            cant_factura=round(linea_qty, 4),
            cant_nc=round(qty, 4),
            n_docs=n,
            id_cliente=cliente_id,
            mensaje=(
                f"SKU {sku}: NC acumula {qty:.2f} vs factura "
                f"{linea_qty:.2f} ({n} doc, {motivo}) -- informativo, "
                f"no afecta precio"
            ),
        )

    def _audit_general(
        self, nc_id: str, factura_id: str, inv: dict, g: dict, *, cliente_id: str = ""
    ) -> NcAlert:
        nc_monto = round(g["soles"], 2)
        total = inv["total_soles"]
        pct = nc_monto / total if total > 0 else 0
        return NcAlert(
            nivel="documento",
            tipo="nc_general",
            factura_id=factura_id,
            nc_id=nc_id,
            sku="",
            nc_monto=nc_monto,
            base_monto=round(total, 2),
            pct=pct,
            nivel_detalle="GENERAL",
            n_docs=g["n"],
            id_cliente=cliente_id,
            mensaje=(
                f"NC/ND general sin SKU ({g['n']} doc): S/{nc_monto:,.2f} "
                f"sobre factura S/{total:,.2f} ({pct:.0%}) -- informativo"
            ),
        )

    def _audit_sku_not_found(
        self, nc_id: str, factura_id: str, sku: str, g: dict, *, cliente_id: str = ""
    ) -> NcAlert:
        nc_monto = round(g["soles"], 2)
        return NcAlert(
            nivel="documento",
            tipo="sku_no_en_factura",
            factura_id=factura_id,
            nc_id=nc_id,
            sku=sku,
            nc_monto=nc_monto,
            base_monto=0,
            pct=0,
            nivel_detalle="ERROR",
            cant_nc=round(g["qty"], 4),
            n_docs=g["n"],
            id_cliente=cliente_id,
            mensaje=(
                f"SKU {sku} en NC no existe en factura {factura_id} "
                f"-- S/{nc_monto:,.2f}. Verificar documento referenciado"
            ),
        )

    def _audit_no_reference(self, nc_id: str, g: dict, *, cliente_id: str = "") -> NcAlert:
        nc_monto = round(g["soles"], 2)
        return NcAlert(
            nivel="documento",
            tipo="sin_referencia",
            factura_id="",
            nc_id=nc_id,
            sku="",
            nc_monto=nc_monto,
            base_monto=0,
            pct=0,
            nivel_detalle="SIN_REF",
            n_docs=g["n"],
            id_cliente=cliente_id,
            mensaje=f"NC {nc_id} sin FACTURA_REF parseable. No se puede auditar",
        )

    def _audit_invalid_ref(
        self, nc_id: str, factura_ref: str, g: dict, *, cliente_id: str = ""
    ) -> NcAlert:
        nc_monto = round(g["soles"], 2)
        return NcAlert(
            nivel="documento",
            tipo="sin_referencia",
            factura_id=factura_ref,
            nc_id=nc_id,
            sku="",
            nc_monto=nc_monto,
            base_monto=0,
            pct=0,
            nivel_detalle="SIN_REF",
            n_docs=g["n"],
            id_cliente=cliente_id,
            mensaje=(f"NC {nc_id} referencia {factura_ref} que no esta en el historial. Verificar"),
        )

    @staticmethod
    def _norm_sku(v) -> str:
        """Normaliza un codigo de SKU para comparar fila vs alerta.

        Tolera la coercion numerica de pandas (09009 -> '9009.0') y mantiene
        los codigos de ancho fijo a 5 digitos al quitar ceros a la izquierda."""
        if v is None:
            return ""
        s = str(v).strip()
        if s.lower() in ("", "nan", "none"):
            return ""
        if s.endswith(".0"):
            s = s[:-2].strip()
        s = s.lstrip("0")
        return s

    @staticmethod
    def build_audit_column(df_res: pd.DataFrame, nc_alertas: List[NcAlert]) -> pd.Series:
        if not nc_alertas:
            return pd.Series([""] * len(df_res), index=df_res.index)

        # Indice por (cliente, factura, SKU). Solo las alertas con SKU se
        # pintan en filas (vista previa limpia); las de nivel documento
        # (general, consolidada, sin_referencia, invalid_ref) viven en el
        # panel global. Si el cliente no se conoce en uno de los dos lados,
        # se indexa tambien sin cliente como fallback.
        by_celda = {}
        for a in nc_alertas:
            if not a.factura_id or not a.sku:
                continue
            sku = CreditNoteAuditor._norm_sku(a.sku)
            cid = CreditNoteAuditor._client_key(a.id_cliente)
            by_celda.setdefault((cid, a.factura_id, sku), []).append(a)
            if not cid:
                by_celda.setdefault(("", a.factura_id, sku), []).append(a)

        def _prefix(a: NcAlert) -> str:
            if a.tipo == "match_directo":
                return "[SKU]"
            if a.tipo == "nc_informativa":
                return "[INF]"
            if a.tipo == "sku_no_en_factura":
                return "[ERR]"
            return "[?]"

        def _build_cell(row):
            sku = ""
            for col in ("SKU", "CODIGO"):
                if col in row:
                    sku = CreditNoteAuditor._norm_sku(row.get(col))
                    if sku:
                        break
            if not sku:
                return ""
            facturas = set()
            for col in ("FACTURA", "FACTURAS", "FACTURA_REF"):
                v = str(row.get(col, "")).strip()
                if v and v not in ("", "nan", "None"):
                    # La celda puede traer lista ("F1, F2" / "F1; F2"): cada
                    # documento matchea por SKU si está en la lista.
                    for part in v.replace(";", ",").split(","):
                        part = part.strip().split(" (")[0].strip()
                        if part:
                            facturas.add(part)
            cell_alerts = []
            row_client = ""
            for col in ("COD_CLIENTE", "id_cliente", "DOC_CLIENTE"):
                row_client = CreditNoteAuditor._client_key(row.get(col)) if col in row.index else ""
                if row_client:
                    break
            for fid in facturas:
                # 1) match con cliente (evita pintar NC de otro cliente con
                #    el mismo folio). 2) fallback sin cliente para filas o
                #    alertas que no traen COD_CLIENTE.
                matches = list(by_celda.get((row_client, fid, sku), [])) if row_client else []
                if not matches:
                    matches = by_celda.get(("", fid, sku), [])
                for a in matches:
                    cell_alerts.append(f"{_prefix(a)} {a.mensaje}")
            # dedup conservando orden (un folio puede repetir el mismo aviso)
            return "\n".join(dict.fromkeys(cell_alerts)) if cell_alerts else ""

        return df_res.apply(_build_cell, axis=1)

    @staticmethod
    def combinar_auditoria(df_res: pd.DataFrame, nc_alertas: List[NcAlert]) -> None:
        """Append NC audit findings without losing strategy calculation audit.

        Mutates the given dataframe so the UI preview and the Excel payload can
        each receive the same merged audit column.
        """
        if df_res is None or df_res.empty:
            return
        previous = (
            df_res["AUDITORIA_NC"].fillna("").astype(str)
            if "AUDITORIA_NC" in df_res.columns
            else pd.Series("", index=df_res.index)
        )
        generated = CreditNoteAuditor.build_audit_column(df_res, nc_alertas)

        def _join(a, b):
            parts = [
                str(x).strip() for x in (a, b) if str(x).strip() and str(x).strip().lower() != "nan"
            ]
            return " | ".join(dict.fromkeys(parts))

        df_res["AUDITORIA_NC"] = [
            _join(old, new) for old, new in zip(previous.tolist(), generated.tolist())
        ]

    @staticmethod
    def resumen_comprometidas(df_historial: pd.DataFrame) -> pd.DataFrame:
        """Resume facturas con NC/ND existentes (comprometidas).

        Returns:
            DataFrame con columnas: FACTURA, TOTAL_FACTURA, NC_CANTIDAD,
            NC_MONTO, ESTADO ('comprometida' o 'disponible')
        """
        if df_historial is None or df_historial.empty:
            return pd.DataFrame()

        # Facturas (ventas)
        facturas = df_historial[df_historial.get("TIPO_CLASE", pd.Series()) == "factura"].copy()
        if facturas.empty:
            return pd.DataFrame()

        # Agrupar facturas por DOC_ID
        facturas_agg = (
            facturas.groupby("DOC_ID")
            .agg(
                TOTAL_FACTURA=("SOLES", "sum"),
                CANTIDAD_VENDIDA=("CANTIDAD", "sum"),
                SKUS=("CODIGO", "nunique"),
                CLIENTE=("CLIENTE", "first"),
            )
            .reset_index()
        )

        # NC/ND existentes
        nc_rows = df_historial[
            df_historial.get("TIPO_CLASE", pd.Series()).isin(["devolucion", "descuento", "cargo"])
        ]

        nc_por_factura = {}
        for _, nc in nc_rows.iterrows():
            ref = str(nc.get("FACTURA_REF", "")).strip()
            if ref:
                # Normalizar: "F01-204-48049" -> "F204-48049" (matchear con DOC_ID)
                parts = ref.split("-", 2)
                if len(parts) == 3:
                    ref_key = f"{parts[0][:1]}{parts[1]}-{parts[2]}"
                else:
                    ref_key = ref
                if ref_key not in nc_por_factura:
                    nc_por_factura[ref_key] = {"cantidad": 0, "monto": 0.0, "docs": []}
                nc_por_factura[ref_key]["cantidad"] += 1
                nc_por_factura[ref_key]["monto"] += abs(float(nc.get("SOLES", 0)))
                nc_por_factura[ref_key]["docs"].append(str(nc.get("DOC_ID", "")))

        # Construir resultado
        rows = []
        for _, f in facturas_agg.iterrows():
            fid = str(f["DOC_ID"])
            nc_info = nc_por_factura.get(fid, {"cantidad": 0, "monto": 0.0, "docs": []})
            total = float(f["TOTAL_FACTURA"])
            nc_monto = nc_info["monto"]
            estado = "comprometida" if nc_monto > 0 else "disponible"
            pct_nc = nc_monto / total if total > 0 else 0

            rows.append(
                {
                    "FACTURA": fid,
                    "CLIENTE": f.get("CLIENTE", ""),
                    "TOTAL_FACTURA": round(total, 2),
                    "CANTIDAD_VENDIDA": int(f["CANTIDAD_VENDIDA"]),
                    "SKUS": int(f["SKUS"]),
                    "NC_CANTIDAD": nc_info["cantidad"],
                    "NC_MONTO": round(nc_monto, 2),
                    "NC_DOCS": ", ".join(nc_info["docs"][:5]),
                    "%_NC": round(pct_nc, 4),
                    "ESTADO": estado,
                }
            )

        df = pd.DataFrame(rows)
        if not df.empty:
            df = df.sort_values("NC_MONTO", ascending=False).reset_index(drop=True)
        return df
