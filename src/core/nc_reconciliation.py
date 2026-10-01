"""Reconcile previous NC/NDB against the exact invoice + SKU they reference.

The result is descriptive and safe to pass through ``PipelineContext.config``:
individual DC can apply only exact, explicitly enabled matches; partial and
excess quantities remain visible for manual review; consolidated DC is always
informational and does not apply note adjustments.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import pandas as pd

from src.core.detector import detectar_notas_en_historial
from src.domain import BusinessAlert

_EPS = 1e-6


def normalizar_sku(value: Any) -> str:
    """Normalize numeric/string ERP SKUs without losing zero-padding equality."""
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in ("", "nan", "none"):
        return ""
    if text.endswith(".0"):
        text = text[:-2]
    return text.lstrip("0") or "0"


def normalizar_cliente(value: Any) -> str:
    """Stable client key; keep leading zeroes because ERP ids are identifiers."""
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in ("", "nan", "none"):
        return ""
    return text[:-2] if text.endswith(".0") else text


def _num(value: Any) -> float:
    try:
        n = float(value or 0)
        return n if n == n else 0.0
    except (TypeError, ValueError):
        return 0.0


def _doc_kind(tipo: Any) -> str:
    t = str(tipo or "").strip().upper()
    return "ndb" if t.startswith("ND") else "nc"


def _options(documentos: dict | None, doc: str) -> tuple[bool, bool]:
    """Return (show, use) from the normalized historical-document policy."""
    if not documentos:
        return (True, doc == "facturas")
    opt = documentos.get(doc)
    if isinstance(opt, dict):
        return bool(opt.get("mostrar", True)), bool(opt.get("usar", False))
    if isinstance(opt, (list, tuple)) and len(opt) >= 2:
        return bool(opt[0]), bool(opt[1])
    return (True, doc == "facturas")


def reconciliar_notas(
    historial: pd.DataFrame | None,
    *,
    documentos: dict | None = None,
    modalidad: str = "individual",
) -> dict[tuple[str, ...], dict]:
    """Build invoice-SKU note decisions for DC.

    Quantity comparison is per individual note document after summing duplicate
    rows within that note. Multiple partial note documents are not combined to
    trigger automatic application. Their total is still checked for over-credit.

    Returns a mapping keyed by ``(client, DOC_ID, normalized SKU)`` when client
    identity is present, otherwise ``(DOC_ID, normalized SKU)``. Each value
    contains the invoice baseline, policy-permitted adjustments, and rendered
    detail/alert strings.
    """
    if historial is None or historial.empty:
        return {}
    required = {"TIPO_CLASE", "CODIGO", "DOC_ID", "CANTIDAD", "SOLES"}
    if not required.issubset(historial.columns):
        return {}

    facturas = historial[historial["TIPO_CLASE"].astype(str).str.lower() == "factura"]
    client_col = next(
        (c for c in ("COD_CLIENTE", "DOC_CLIENTE", "CLIENTE") if c in historial.columns), None
    )
    invoice_base: dict[tuple[str, ...], dict[str, float]] = {}
    group_cols = []
    if client_col:
        group_cols.append(facturas[client_col].map(normalizar_cliente))
    group_cols.extend(
        [
            facturas["DOC_ID"].astype(str).str.strip(),
            facturas["CODIGO"].map(normalizar_sku),
        ]
    )
    for grouped_key, group in facturas.groupby(group_cols, sort=False):
        if not isinstance(grouped_key, tuple):
            grouped_key = (grouped_key,)
        if client_col:
            client, doc, sku = grouped_key
            key = (normalizar_cliente(client), str(doc), str(sku))
        else:
            doc, sku = grouped_key
            key = (str(doc), str(sku))
        if not doc or not sku:
            continue
        invoice_base[key] = {
            "invoice_qty": sum(max(0.0, _num(v)) for v in group["CANTIDAD"]),
            "invoice_soles": sum(_num(v) for v in group["SOLES"]),
        }

    notes = detectar_notas_en_historial(historial)
    if notes is None or notes.empty:
        return {}

    # Keep original signed SOLES and the original note id; detector output has
    # both, but the amount must be read from the source rows (not abs(summary)).
    if "SOLES" not in notes.columns:
        return {}

    grouped: dict[tuple[str, ...], dict] = {}
    for _, note in notes.iterrows():
        invoice = str(note.get("FACTURA_REF", "") or "").strip()
        sku = normalizar_sku(note.get("CODIGO"))
        doc_note = str(note.get("DOC_NOTA", "") or "").strip()
        client = normalizar_cliente(note.get(client_col)) if client_col else ""
        key_base = (client, invoice, sku) if client_col else (invoice, sku)
        if not invoice or not sku or key_base not in invoice_base:
            continue
        source = _doc_kind(note.get("TPO_NOTA"))
        category = str(note.get("CATEGORIA", "") or "").strip().lower()
        # Physical quantity belongs to returns. Value corrections (NC/NDB)
        # are eligible only against FAE; an unrelated FAE is never treated as
        # quantity sold/returned.
        effect = "devolucion" if source == "nc" and category == "devolucion" else "valor"
        if effect == "devolucion":
            qty = abs(_num(note.get("CANTIDAD")))
        else:
            qty = abs(_num(note.get("CANTIDAD_FAE")))
        key = (*key_base, source, doc_note)
        g = grouped.setdefault(
            key,
            {
                "qty": 0.0,
                "soles": 0.0,
                "effect": effect,
                "source": source,
                "doc": doc_note,
                "category": category,
            },
        )
        g["qty"] += qty
        g["soles"] += _num(note.get("SOLES"))

    by_invoice_sku: dict[tuple[str, ...], list[dict]] = defaultdict(list)
    for grouped_key, item in grouped.items():
        base_key = grouped_key[:-2]
        by_invoice_sku[base_key].append(item)

    result: dict[tuple[str, ...], dict] = {}
    for key, base in invoice_base.items():
        invoice_qty = base["invoice_qty"]
        item = {
            **base,
            "qty_return": 0.0,
            "delta_soles": 0.0,
            "details": [],
            "alerts": [],
            "applied": False,
        }
        for source in ("nc", "ndb"):
            source_notes = [n for n in by_invoice_sku.get(key, []) if n["source"] == source]
            if not source_notes:
                continue
            show, use = _options(documentos, source)
            total_qty = sum(n["qty"] for n in source_notes)
            doc_count = len({n["doc"] for n in source_notes if n["doc"]})
            if invoice_qty <= _EPS or total_qty <= _EPS:
                status = "sin_cantidad"
            elif total_qty > invoice_qty + _EPS:
                status = "exceso"
            elif doc_count > 1:
                # Several partial NCs are deliberately not auto-combined into
                # an exact match; manual review decides their relationship.
                status = "varias_notas_manual"
            elif abs(total_qty - invoice_qty) <= _EPS:
                status = "exacta"
            else:
                status = "parcial"

            can_apply = modalidad == "individual" and use and status == "exacta"
            if status == "exacta" and not use:
                status = "exacta_no_usada"
            elif status == "exacta" and modalidad == "consolidado":
                status = "exacta_solo_informativa"

            details = []
            for n in source_notes:
                q = n["qty"]
                unit = abs(n["soles"]) / q if q > _EPS else None
                decision = status
                details.append(
                    {
                        "doc": n["doc"],
                        "tipo": source.upper(),
                        "categoria": n["category"],
                        "cantidad": q,
                        "importe": n["soles"],
                        "valor_unitario": unit,
                        "mostrar": show,
                        "usar": use,
                        "estado": decision,
                        "aplicada": can_apply,
                        "efecto": n["effect"],
                    }
                )

            if can_apply:
                item["applied"] = True
                if source_notes[0]["effect"] == "devolucion":
                    item["qty_return"] += total_qty
                else:
                    item["delta_soles"] += sum(n["soles"] for n in source_notes)

            # Only notes requested for display (or actually applied) are
            # surfaced. Applied notes stay auditable even if the user hides
            # other historical notes.
            visible_details = [d for d in details if d["mostrar"] or d["aplicada"]]
            item["details"].extend(visible_details)
            if show and visible_details:
                label = "NC" if source == "nc" else "NDB"
                refs = ", ".join(d["doc"] or "(sin número)" for d in visible_details)
                qty_txt = f"{total_qty:,.2f}/{invoice_qty:,.2f} unid"
                amount = sum(n["soles"] for n in source_notes)
                if status == "exceso":
                    item["alerts"].append(
                        f"AL13 - {label} EXCESO — {refs}: {qty_txt}; "
                        f"importe S/ {amount:,.2f}; no aplicado, revisar manualmente"
                    )
                elif status in ("parcial", "varias_notas_manual", "sin_cantidad"):
                    item["alerts"].append(
                        f"AL12 - {label} REVISIÓN MANUAL — {refs}: {qty_txt}; "
                        f"importe S/ {amount:,.2f}; no aplicado"
                    )
                elif status == "exacta_solo_informativa":
                    item["alerts"].append(
                        f"AL12 - {label} EXACTA — {refs}: {qty_txt}; "
                        f"importe S/ {amount:,.2f}; en consolidado no aplicado"
                    )
                elif status == "exacta_no_usada":
                    item["alerts"].append(
                        f"AL12 - {label} EXACTA — {refs}: {qty_txt}; "
                        f"importe S/ {amount:,.2f}; check Usar desactivado"
                    )
                elif status == "exacta":
                    action = (
                        "cantidad reducida"
                        if source_notes[0]["effect"] == "devolucion"
                        else "valor aplicado al precio"
                    )
                    item["alerts"].append(
                        f"AL12 - {label} APLICADA — {refs}: {qty_txt}; "
                        f"importe S/ {amount:,.2f}; {action}"
                    )
        result[key] = item
    return result


def texto_auditoria_notas(details: list[dict]) -> str:
    """Compact, deterministic note trace for ALERTA/AUDITORIA cells."""
    parts = []
    for d in details or []:
        unit = d.get("valor_unitario")
        unit_txt = f"S/ {unit:,.5f}/u" if unit is not None else "P.U. no disponible"
        parts.append(
            f"{d.get('tipo', 'NC')} {d.get('doc') or '(sin número)'}: "
            f"{d.get('cantidad', 0):,.2f} unid, importe S/ {d.get('importe', 0):,.2f}, "
            f"{unit_txt}; {d.get('estado', 'revisar')}"
        )
    return " | ".join(parts)


def alertas_reconciliacion(
    reconciliaciones: dict,
    *,
    pares: set[tuple[str, ...]] | None = None,
    motor: str = "",
) -> list[BusinessAlert]:
    """Turn visible note statuses into deduplicated UI/business alerts."""
    out = []
    vistas = set()
    for key, rec in (reconciliaciones or {}).items():
        if pares is not None and key not in pares:
            continue
        for mensaje in rec.get("alerts", []):
            if mensaje in vistas:
                continue
            vistas.add(mensaje)
            codigo = "AL13" if str(mensaje).startswith("AL13") else "AL12"
            severidad = "media" if codigo == "AL13" else "baja"
            tipo = "warning" if codigo == "AL13" or "REVISIÓN MANUAL" in mensaje else "info"
            out.append(
                BusinessAlert(
                    codigo=codigo,
                    tipo=tipo,
                    severidad=severidad,
                    sku=str(key[-1]),
                    mensaje=mensaje,
                    motor=motor,
                )
            )
    return out
