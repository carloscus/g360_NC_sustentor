"""Reporte de compras: lógica pura (lecturas, pivotes, tabla y Excel).

La UI vive en src/ui/reporte_panel.py (card colapsable con estado propio).
Este módulo queda sin estado: se le pasa la conexión, los parámetros y los
DataFrames, y devuelve datos u hojas escritas — por eso es testeable sin Flet.

Neto por defecto: facturas/boletas + NC/ND con los signos ya almacenados en
la DB (devolucion/ajuste en negativo, nota de debito en positivo).
"""

from __future__ import annotations

import re

import flet as ft
import pandas as pd

from src.core.g360_theme import G360Theme
from src.core.utils import EXCEL_FMT_UNIT_PRICE, cliente_visible


_RE_FN = re.compile(r"[^\w\s().&-]", re.UNICODE)

# ── Geometría horizontal (px) ────────────────────────────────────────
# Presupuesto con la ventana MÍNIMA (main.py: min_width=960):
#   960 − 64 (padding vista 32+32) − 32 (card 16+16) − 16 (cuerpo 8+8)
ANCHO_UTIL_MIN = 848
# Anchos aproximados de la tabla mes×línea (DataTable dimensiona por contenido)
_ANCHO_COL_MES = 60
_ANCHO_COL_LINEA = 70
_ANCHO_COL_TOTAL = 80
_COL_SPACING = 10


def ancho_estimado_tabla(n_lineas: int) -> int:
    """Ancho aproximado de la tabla resumen mes × línea, en px."""
    cols = 2 + max(0, n_lineas)  # Mes + líneas + Total
    return (
        _ANCHO_COL_MES
        + max(0, n_lineas) * _ANCHO_COL_LINEA
        + _ANCHO_COL_TOTAL
        + (cols - 1) * _COL_SPACING
    )


def nombre_archivo_compras(nombre: str, cid: str, fecha: str = "") -> str:
    """Nombre legible del Excel: 'Compras - CLIENTE (56101) - 2026-09-26.xlsx'.

    Solo lleva la fecha de generación (el rango vive en la nota del
    Resumen). Sanitiza lo que Windows no admite en nombres de archivo y
    acota el cliente a 40 caracteres para no rebasar el límite de ruta.
    """
    from datetime import date

    base = " ".join(str(nombre or "").split())[:48].strip()
    base = _RE_FN.sub("", base).strip()
    cid_txt = str(cid or "").strip()
    cliente_txt = f"{base} ({cid_txt})".strip() if cid_txt else base
    f = str(fecha or "").strip() or date.today().isoformat()
    partes = ["Compras"]
    if cliente_txt:
        partes.append(cliente_txt)
    partes.append(f)
    return " - ".join(partes) + ".xlsx"


def _mes_label(mes_ref: str) -> str:
    """Mes como 'YYYY-MM' (texto ordenable, cero-padded)."""
    try:
        anho, mes = str(mes_ref).split("-")[:2]
        return f"{anho}-{int(mes):02d}"
    except Exception:
        return str(mes_ref)


def _fecha_corta(fecha) -> str:
    """'YYYY-MM-DD…' → 'dd-mm-aaaa' para mostrar en el reporte.

    Convención: interno siempre ISO (YYYY-MM-DD); display con guiones.
    """
    s = str(fecha or "").strip()[:10]
    try:
        anho, mes, dia = s.split("-")
        return f"{dia}-{mes}-{anho}"
    except Exception:
        return s


def _mes_display(mm: str, anio: str) -> str:
    """Mes × año en formato display: 'MM-AAAA' (conducido de _fecha_corta)."""
    try:
        return f"{int(mm):02d}-{anio}"
    except Exception:
        return mm


def _fecha_obj(fecha):
    """'YYYY-MM-DD…' → date real (Excel la filtra y ordena); None si no hay."""
    from datetime import datetime

    try:
        return datetime.strptime(str(fecha or "").strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _c_visible(cid: str) -> str:
    """Id de cliente SIN ceros a la izquierda (alias de cliente_visible)."""
    return cliente_visible(cid)


def _sufijo(cod: str) -> str:
    """Sufijo de 2 caracteres de una línea (01, 78…; conserva código completo si <2 chars)."""
    s = str(cod).strip()
    return s[-2:] if len(s) >= 2 else s


# ── pivotes ──────────────────────────────────────────────────────────


def _pivot_data(df):
    """Devuelve dict con meses, lineas (cod->nombre), soles, cant y totales."""
    df = df.copy()
    df["COD_LINEA"] = df["COD_LINEA"].astype(str).str.strip()
    meses = sorted(df["MES_REF"].unique())
    orden = df.groupby("COD_LINEA")["SOLES"].sum().sort_values(ascending=False).index.tolist()
    lineas = [str(c) for c in orden]
    nombres = df.groupby("COD_LINEA")["LINEA"].max().to_dict()

    soles = df.pivot_table(
        index="MES_REF", columns="COD_LINEA", values="SOLES", aggfunc="sum", fill_value=0.0
    )
    cant = df.pivot_table(
        index="MES_REF", columns="COD_LINEA", values="CANTIDAD", aggfunc="sum", fill_value=0.0
    )
    soles = soles.reindex(index=meses, columns=lineas, fill_value=0.0)
    cant = cant.reindex(index=meses, columns=lineas, fill_value=0.0)
    return {
        "meses": meses,
        "lineas": lineas,
        "nombres": nombres,
        "soles": soles,
        "cant": cant,
        "total_soles": float(df["SOLES"].sum()),
        "total_cant": float(df["CANTIDAD"].sum()),
    }


# ── tabla de preview ────────────────────────────────────────────────


def _construir_tabla(df) -> ft.DataTable:
    p = _pivot_data(df)
    meses, lineas = p["meses"], p["lineas"]

    def _hdr(cod):
        # Nombre de línea acotado: sin truncar, un nombre largo ensancha la
        # columna y desborda el presupuesto horizontal de la card.
        nom = str(p["nombres"].get(cod, ""))[:12]
        suf = _sufijo(cod)
        return ft.DataColumn(
            ft.Column(
                [
                    ft.Text(
                        suf, size=10, weight=ft.FontWeight.W_700, text_align=ft.TextAlign.CENTER
                    ),
                    ft.Text(
                        nom,
                        size=8,
                        color=G360Theme.text_muted_color(),
                        text_align=ft.TextAlign.CENTER,
                    ),
                ],
                spacing=0,
                alignment=ft.MainAxisAlignment.CENTER,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            )
        )

    columns = [ft.DataColumn(ft.Text("Mes", size=10, weight=ft.FontWeight.W_700))]
    columns += [_hdr(c) for c in lineas]
    columns.append(
        ft.DataColumn(
            ft.Text("Total", size=10, weight=ft.FontWeight.W_700, text_align=ft.TextAlign.RIGHT)
        )
    )

    rows = []
    for m in meses:
        cells = [ft.DataCell(ft.Text(_mes_label(m), size=10, weight=ft.FontWeight.W_600))]
        for c in lineas:
            s = float(p["soles"].loc[m, c])
            q = float(p["cant"].loc[m, c])
            cells.append(
                ft.DataCell(
                    ft.Column(
                        [
                            ft.Text(
                                f"{s:,.2f}" if s else "—",
                                size=10,
                                color=ft.Colors.ON_SURFACE,
                                text_align=ft.TextAlign.RIGHT,
                            ),
                            ft.Text(
                                f"{q:,.0f}",
                                size=8,
                                color=G360Theme.text_muted_color(),
                                text_align=ft.TextAlign.RIGHT,
                            ),
                        ],
                        spacing=0,
                        alignment=ft.MainAxisAlignment.CENTER,
                        horizontal_alignment=ft.CrossAxisAlignment.END,
                    )
                )
            )
        ts = float(p["soles"].loc[m].sum())
        tq = float(p["cant"].loc[m].sum())
        cells.append(
            ft.DataCell(
                ft.Column(
                    [
                        ft.Text(
                            f"{ts:,.2f}",
                            size=10,
                            weight=ft.FontWeight.W_600,
                            color=G360Theme.accent_text_color(),
                            text_align=ft.TextAlign.RIGHT,
                        ),
                        ft.Text(
                            f"{tq:,.0f}",
                            size=8,
                            color=G360Theme.text_muted_color(),
                            text_align=ft.TextAlign.RIGHT,
                        ),
                    ],
                    spacing=0,
                    horizontal_alignment=ft.CrossAxisAlignment.END,
                )
            )
        )
        rows.append(ft.DataRow(cells=cells))

    total_cells = [
        ft.DataCell(
            ft.Text(
                "TOTAL", size=10, weight=ft.FontWeight.W_700, color=G360Theme.accent_text_color()
            )
        )
    ]
    for c in lineas:
        ts = float(p["soles"][c].sum())
        tq = float(p["cant"][c].sum())
        total_cells.append(
            ft.DataCell(
                ft.Column(
                    [
                        ft.Text(
                            f"{ts:,.2f}",
                            size=10,
                            weight=ft.FontWeight.W_700,
                            color=G360Theme.accent_text_color(),
                            text_align=ft.TextAlign.RIGHT,
                        ),
                        ft.Text(
                            f"{tq:,.0f}",
                            size=8,
                            color=G360Theme.text_muted_color(),
                            text_align=ft.TextAlign.RIGHT,
                        ),
                    ],
                    spacing=0,
                    horizontal_alignment=ft.CrossAxisAlignment.END,
                )
            )
        )
    total_cells.append(
        ft.DataCell(
            ft.Column(
                [
                    ft.Text(
                        f"{p['total_soles']:,.2f}",
                        size=10,
                        weight=ft.FontWeight.W_700,
                        color=G360Theme.accent_text_color(),
                        text_align=ft.TextAlign.RIGHT,
                    ),
                    ft.Text(
                        f"{p['total_cant']:,.0f}",
                        size=8,
                        weight=ft.FontWeight.W_600,
                        color=G360Theme.text_muted_color(),
                        text_align=ft.TextAlign.RIGHT,
                    ),
                ],
                spacing=0,
                horizontal_alignment=ft.CrossAxisAlignment.END,
            )
        )
    )
    rows.append(ft.DataRow(cells=total_cells, color=G360Theme.with_opacity(0.06, G360Theme.ACCENT)))

    return ft.DataTable(
        columns=columns,
        rows=rows,
        border=ft.border.all(1, G360Theme.border_subtle_color()),
        border_radius=G360Theme.BTN_RADIUS,
        heading_row_color=G360Theme.with_opacity(0.08, G360Theme.ACCENT),
        column_spacing=_COL_SPACING,
        heading_row_height=44,
        data_row_min_height=34,
        data_row_max_height=40,
        horizontal_lines=ft.BorderSide(1, G360Theme.border_subtle_color()),
    )


# ── lecturas para el paquete de export ──────────────────────────────


def _paquete_export(
    cli, cid_raw, fd, fh, incluir_nc, hojas=None, solo_lineas_activas=False, corte_hasta=""
) -> dict:
    """Ejecuta las lecturas del paquete de export.

    ``hojas`` (set de claves) limita las lecturas a lo que se va a escribir:
    None = todas. Claves: 'resumen', 'consolidado', 'comparativo',
    'ajustes', 'bd', 'facturas' (incluye el detallado por doc×SKU),
    'sucursales' (Pareto + mes×línea + mes×SKU).
    ``solo_lineas_activas`` restringe al allowlist de líneas validadas
    (facturas por línea; NC/NDB por línea propia o factura referenciada),
    igual que el generador de sustento. Cada fetch se protege
    individualmente: un fallo omite su hoja sin tirar el paquete completo.
    Retorna DataFrames (o None si vacío/falló/no pedido):
      df_hist | df_lineas | df_skus | df_comp | df_comp_sku
      df_dev | df_huerf | df_hechos | df_det (con NC asociadas)
    El Comparativo usa un rango retrocedido 24 meses (``_fd_comparativo``)
    para habilitar la comparación vs año(s) previo(s) y filtra las filas
    a los meses del rango original (``_meses_rango``).
    ``incluir_nc`` rige todas las hojas: con False no hay notas en ningún
    fetch (Ajustes vacío, bloque B de Facturas vacío, N_NC en cero,
    histórico con componentes en cero, comparativo en bruta).
    """
    kw = {"fecha_desde": fd, "fecha_hasta": fh}
    kw_cmp = {"fecha_desde": _fd_comparativo(corte_hasta), "fecha_hasta": fh}

    def _sel(*keys):
        return hojas is None or any(k in hojas for k in keys)

    def _df(fn, kwd, **extra):
        try:
            df = fn(cid_raw, **kwd, **extra)
            return None if df is None or df.empty else df
        except Exception:
            return None

    def _dfc(fn, **extra):
        """Comparativo: trae la ventana extendida (3 años completos).
        Sin filtrado por rango: cada año muestra sus meses completos
        (el más reciente hasta el mes de corte).
        """
        return _df(fn, kw_cmp, **extra)

    suc = {"pareto": None, "linea_mes": None, "sku_mes": None}
    if _sel("sucursales"):
        try:
            suc = cli.fetch_analisis_sucursales_cliente(
                cid_raw,
                fecha_desde=fd,
                fecha_hasta=fh,
                incluir_nc=incluir_nc,
                solo_lineas_activas=solo_lineas_activas,
            )
        except Exception:
            # Igual que el resto de hojas, una consulta fallida no tira el libro.
            pass

    ln = {"solo_lineas_activas": solo_lineas_activas}
    nc = {"incluir_nc": incluir_nc}
    return {
        "df_hist": (_df(cli.fetch_historico_mensual, kw, **nc, **ln) if _sel("resumen") else None),
        "df_lineas": (
            _df(cli.fetch_lineas_resumen_cliente, kw, **nc, **ln) if _sel("consolidado") else None
        ),
        "df_skus": (
            _df(cli.fetch_skus_resumen_cliente, kw, **nc, **ln) if _sel("consolidado") else None
        ),
        "df_comp_sku": (
            _dfc(cli.fetch_compras_sku_mes_cliente, **nc, **ln) if _sel("comparativo") else None
        ),
        "df_dev": (_df(cli.fetch_notas_sku_cliente, kw, **nc, **ln) if _sel("ajustes") else None),
        "df_huerf": (
            _df(cli.fetch_notas_huerfanas_cliente, kw, **nc, **ln) if _sel("ajustes") else None
        ),
        "df_hechos": (
            _df(cli.fetch_compras_lineas_cliente, kw, **nc, **ln) if _sel("bd") else None
        ),
        "df_det": (
            _df(cli.fetch_facturas_sku_detalle_cliente, kw, **ln) if _sel("facturas") else None
        ),
        "df_suc_pareto": suc["pareto"],
        "df_suc_linea_mes": suc["linea_mes"],
        "df_suc_sku_mes": suc["sku_mes"],
    }


# ── conciliación ─────────────────────────────────────────────────


def reconciliar_mensual(df) -> list:
    """Verifica por mes: BRUTA+DEV+DESC+NDB = NETA (tol S/ 0.01).

    Retorna lista de alertas (vacía = concilia). No altera datos.
    """
    alertas = []
    if df is None or df.empty:
        return ["sin datos mensuales"]
    for _, r in df.iterrows():
        try:
            calc = float(r["BRUTA"]) + float(r["DEV_S"]) + float(r["DESC_S"]) + float(r["NDB_S"])
            if abs(calc - float(r["NETA"])) > 0.01:
                alertas.append(f"{r['MES']}: bruta+ajustes={calc:,.2f} != neta={r['NETA']}")
        except Exception:
            alertas.append(f"{r.get('MES', '?')}: fila no numérica")
    for col in (
        "BRUTA",
        "DEV_S",
        "DESC_S",
        "NDB_S",
        "NETA",
        "UFACT",
        "UDEV",
        "FACTURAS",
        "NC",
        "SKUS",
    ):
        try:
            if col in df.columns and bool(pd.isna(df[col]).any()):
                alertas.append(f"columna {col} con vacíos")
        except Exception:
            pass
    return alertas


# ── escritura del Excel (6 hojas estándar + análisis sucursales opcional) ─


def _estilos():
    """Estilos compartidos por todas las hojas del paquete."""
    from openpyxl.styles import Border, Font, PatternFill, Side

    return {
        "header": PatternFill(start_color="0D2B4E", end_color="0D2B4E", fill_type="solid"),
        "total": PatternFill(start_color="E4EBF3", end_color="E4EBF3", fill_type="solid"),
        "thin": Border(*[Side(style="thin", color="D0D0D0")] * 4),
        "muted": Font(size=9, color="666666"),
    }


def _x_bloque(ws, matriz, unidad_fmt, start_row, st, subtitulo=None):
    """Escribe una matriz (encabezado + filas) desde start_row.

    Retorna (fila_fin, fila_encabezado) para congelar/filtrar después.
    """
    from openpyxl.styles import Alignment, Font

    r = start_row
    if subtitulo:
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=len(matriz[0]))
        c = ws.cell(row=r, column=1, value=subtitulo)
        c.font = Font(bold=True, size=10, color="0D2B4E")
        c.fill = st["total"]
        r += 1
    r_hdr = r
    for ri, row in enumerate(matriz):
        for ci, val in enumerate(row, 1):
            c = ws.cell(row=r, column=ci, value=val)
            c.border = st["thin"]
            if ri == 0:
                c.font = Font(bold=True, size=9, color="FFFFFF")
                c.fill = st["header"]
                c.alignment = Alignment(horizontal="center", wrap_text=True)
            else:
                c.font = Font(size=9)
                if ci > 1 and isinstance(val, (int, float)):
                    c.number_format = unidad_fmt
                    c.alignment = Alignment(horizontal="right")
        r += 1
    return r, r_hdr


def _x_encabezado(ws, titulo, st, nombre, cid, ncols=10):
    """Título + cliente en las dos primeras filas de la hoja.

    Ambas filas se combinan al ancho de la hoja: el cliente va alineado
    a la derecha y sin combinar se recorta contra el borde (el texto
    desborda a la izquierda fuera de la vista). La fila 2 lleva el chip
    "importes sin IGV": los soles de la DB ya vienen convertidos y el
    ERP no incluye IGV (verificado: sin ×/÷1.18 en el pipeline).
    """
    from openpyxl.styles import Alignment, Font
    from openpyxl.utils import get_column_letter

    ultima = get_column_letter(max(2, ncols))
    ws.sheet_view.showGridLines = False
    ws.merge_cells(f"A1:{ultima}1")
    ws.cell(row=1, column=1, value=titulo)
    ws.cell(row=1, column=1).font = Font(bold=True, size=13, color="FFFFFF")
    ws.cell(row=1, column=1).fill = st["header"]
    ws.row_dimensions[1].height = 24
    ws.merge_cells(f"A2:{ultima}2")
    ws.cell(row=2, column=1, value=f"{nombre} ({cliente_visible(cid)}) · importes sin IGV")
    ws.cell(row=2, column=1).font = st["muted"]
    ws.cell(row=2, column=1).alignment = Alignment(horizontal="right")


def _x_anchos(ws, ncols, primero=12, resto=14):
    from openpyxl.utils import get_column_letter

    ws.column_dimensions["A"].width = primero
    for i in range(2, ncols + 1):
        ws.column_dimensions[get_column_letter(i)].width = resto


def _x_hoja(ws, matriz, titulo, st, nombre, cid, unidad_fmt, ncols=None, banner=True):
    """Hoja simple: encabezado + una matriz (la tabla nativa filtra sola).

    ``banner`` controla si se escribe título+cliente en filas 1-2 (True,
    valor por defecto) o si se van directo a fila 1 y el banner va al
    print-header (False). En ambos casos la columna A siempre cabe.
    """
    if banner:
        _x_encabezado(ws, titulo, st, nombre, cid, ncols or max(2, len(matriz[0])))
        r, r_hdr = _x_bloque(ws, matriz, unidad_fmt, 4, st)
    else:
        r, r_hdr = _x_bloque(ws, matriz, unidad_fmt, 1, st)
    _x_anchos(ws, len(matriz[0]))
    return r


# ── hoja Consolidado ─────────────────────────────────────────────

_TOP_SKUS = 15


def _x_consolidado(wb, df_lineas, df_skus, st, nombre, cid, fd="", fh=""):
    """2. Consolidado: por línea (componentes + Neto con fórmula) + top SKUs.

    Bloque A: una fila por línea (código canónico: 01, 78) con
    Bruta/Dev/Desc/NDB (valores) y Neto =SUM(fila) con fórmula; % neto
    como valor al exportar. Bloque B: top 15 SKUs por neto + fila
    "Otros (N)" residual; TOTAL con =SUM en ambos. Sin ciclos: los %
    son valores y los totales fórmulas sobre valores. Suma de
    componentes redondeados puede diferir del neto en S/ 0.01
    (tolerancia de paridad del paquete).
    Banner movido a print-header: la tabla arranca en fila 1.
    """
    if (df_lineas is None or df_lineas.empty) and (df_skus is None or df_skus.empty):
        return
    ws = wb.create_sheet("Consolidado")
    r = 1
    ancho_nota = 6
    if df_lineas is not None and not df_lineas.empty:
        mat = [
            [
                "Línea",
                "Bruta S/",
                "Dev. S/",
                "Desc. S/",
                "NDB S/",
                "Neto S/",
                "Unid.",
                "N° docs",
                "% neto",
            ]
        ]
        tot_neto = round(float(df_lineas["SOLES"].sum()), 2)
        for _, ln in df_lineas.iterrows():
            neto = round(float(ln["SOLES"]), 2)
            pct = round(neto / tot_neto, 4) if tot_neto else "—"
            # Sin columna de artículo: el código solo sería críptico →
            # identidad "CÓD - Nombre" en la misma celda Línea.
            cod = str(ln["COD_LINEA"])
            nom = str(ln["LINEA"] or "").strip()
            linea = f"{cod} - {nom}" if nom else cod
            mat.append(
                [
                    linea,
                    round(float(ln["BRUTA"]), 2),
                    round(float(ln["DEV_S"]), 2),
                    round(float(ln["DESC_S"]), 2),
                    round(float(ln["NDB_S"]), 2),
                    None,
                    float(ln["CANTIDAD"]),
                    int(ln["N_DOCS"]),
                    pct,
                ]
            )
        n = len(mat) - 1
        r0, rlast, rtot = r + 2, r + 1 + n, r + 2 + n
        for i in range(n):
            mat[1 + i][5] = f"=SUM(B{r0 + i}:E{r0 + i})"
        mat.append(["TOTAL"] + [f"=SUBTOTAL(109,{c}{r0}:{c}{rlast})" for c in "BCDEFGHI"])
        r, r_hdr = _x_bloque(ws, mat, "#,##0.00", r, st, subtitulo="POR LÍNEA (S/)")
        for rr in range(r0, rtot + 1):
            ws.cell(row=rr, column=7).number_format = "#,##0"
            ws.cell(row=rr, column=8).number_format = "#,##0"
            ws.cell(row=rr, column=9).number_format = "0.0%"
        for col, w in zip("ABCDEFGHI", (20, 13, 12, 12, 12, 13, 11, 10, 9)):
            ws.column_dimensions[col].width = w
        _tabla_excel(ws, "ConsolidadoLineas", r_hdr, rlast, len(mat[0]))
        _x_fmt_total(ws, rtot, len(mat[0]), st)
        _x_cf(ws, f"F{r0}:F{rtot}", "lessThan")
        ancho_nota = 9
        r += 1
    if df_skus is not None and not df_skus.empty:
        tot_s = round(float(df_skus["SOLES"].sum()), 2)
        mat = [["SKU", "Artículo", "Línea", "Neto S/", "Unid.", "% neto"]]
        top = df_skus.head(_TOP_SKUS)
        resto = df_skus.iloc[_TOP_SKUS:]

        def _fila(s):
            neto = round(float(s["SOLES"]), 2)
            pct = round(neto / tot_s, 4) if tot_s else "—"
            return [
                str(s["COD_SKU"]),
                str(s["SKU"]),
                str(s["COD_LINEA"]),
                neto,
                float(s["CANTIDAD"]),
                pct,
            ]

        for _, s in top.iterrows():
            mat.append(_fila(s))
        if not resto.empty:
            neto_r = round(float(resto["SOLES"].sum()), 2)
            pct_r = round(neto_r / tot_s, 4) if tot_s else "—"
            mat.append(
                [f"Otros ({len(resto)})", "", "", neto_r, float(resto["CANTIDAD"].sum()), pct_r]
            )
        n = len(mat) - 1
        r0, rlast, rtot = r + 2, r + 1 + n, r + 2 + n
        mat.append(["TOTAL", "", ""] + [f"=SUBTOTAL(109,{c}{r0}:{c}{rlast})" for c in "DEF"])
        r, _rh = _x_bloque(
            ws,
            mat,
            "#,##0.00",
            r,
            st,
            subtitulo=f"TOP SKUs POR NETO (S/) — top {_TOP_SKUS} + Otros",
        )
        for rr in range(r0, rtot + 1):
            ws.cell(row=rr, column=5).number_format = "#,##0"
            ws.cell(row=rr, column=6).number_format = "0.0%"
        ws.column_dimensions["A"].width = 20  # bloque A: "01 - PELOTAS"
        ws.column_dimensions["B"].width = 30
        ws.column_dimensions["C"].width = 20
        _tabla_excel(ws, "ConsolidadoSKUs", _rh, r - 2, len(mat[0]))
        _x_fmt_total(ws, rtot, len(mat[0]), st)
        _x_cf(ws, f"D{r0}:D{rtot}", "lessThan")
    _x_print_header(
        ws, "COMPRAS NETAS — CONSOLIDADO", nombre, cid, ncols=9, rango_desde=fd, rango_hasta=fh
    )
    _x_nota(
        ws,
        r + 1,
        ancho_nota,
        "Neto = Bruta + Dev. + Desc. + NDB (fórmula por fila). "
        "% neto calculado al exportar. Actualizar = re-exportar el "
        "reporte. Importes en Soles sin IGV.",
    )


# ── hoja Comparativo ────────────────────────────────────────────

_CMP_ANIOS = 3  # años más recientes mostrados (2 con dif + 1 base)


def _fd_comparativo(corte_hasta: str) -> str:
    """Inicio de la ventana comparativa: enero 1 del año (corte - 2).

    Siempre arranca el 1 de enero para que los años anteriores salgan
    completos (12 meses). El año más reciente se limita al mes de corte.
    """
    try:
        y = int(str(corte_hasta or "")[:4])
        if y >= 2000:
            return f"{y - 2:04d}-01-01"
    except Exception:
        pass
    return str(corte_hasta or "")


def _meses_rango(fd: str, fh: str) -> set:
    """Meses (MM) cubiertos por el rango original del reporte.

    Solo se retorna el conjunto de MM para compatibilidad con
    ``_meses_parciales``. El filtrado año-aware se hace con
    ``_meses_rango_year_aware``.
    """
    try:
        y1, m1 = int(str(fd)[:4]), int(str(fd)[5:7])
        y2, m2 = int(str(fh)[:4]), int(str(fh)[5:7])
    except Exception:
        return set()
    out = set()
    y, m = y1, m1
    while (y, m) <= (y2, m2) and len(out) <= 120:
        out.add(f"{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def _meses_rango_year_aware(fd: str, fh: str) -> set:
    """Pareja (YYYY, MM) de cada mes dentro del rango original."""
    try:
        y1, m1 = int(str(fd)[:4]), int(str(fd)[5:7])
        y2, m2 = int(str(fh)[:4]), int(str(fh)[5:7])
    except Exception:
        return set()
    out = set()
    y, m = y1, m1
    while (y, m) <= (y2, m2) and len(out) <= 120:
        out.add((y, m))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def _cmp_totales_rango(corte_hasta, anios):
    """Meses (MM) que suman los dos TOTALs del bloque.

    ``comparables`` = intersección de meses presentes en todos los años
    (meses que tienen datos en los 3 años).
    ``por_anio`` = para cada año, los meses que deben sumarse en el
    TOTAL DEL RANGO (año más reciente hasta el mes de corte, anteriores
    completos).
    """
    if not anios:
        return {"comparables": set(), "por_anio": {}}
    anio_ref = anios[0]
    comparables = None
    por_anio = {}
    for anio in anios:
        if anio == anio_ref and corte_hasta:
            try:
                cm = int(str(corte_hasta)[5:7])
                set_mm = {f"{m:02d}" for m in range(1, cm + 1)}
            except Exception:
                set_mm = set()
        else:
            set_mm = set(f"{m:02d}" for m in range(1, 13))
        por_anio[anio] = set_mm
        if comparables is None:
            comparables = set_mm
        else:
            comparables &= set_mm
    return {"comparables": comparables or set(), "por_anio": por_anio}


def _meses_parciales(fd: str, fh: str) -> set:
    """Meses (MM) incompletos del rango: corte a mitad de mes.

    El mes del corte final si no llega al fin de mes; el de inicio si
    empieza pasado el día 1. El año base no cuenta: la ventana se abre
    el día 1 (ver _fd_comparativo).
    """
    import calendar

    out = set()
    try:
        y, m, d = (int(x) for x in str(fh)[:10].split("-"))
        if d < calendar.monthrange(y, m)[1]:
            out.add(f"{m:02d}")
    except Exception:
        pass
    try:
        y1, m1, d1 = (int(x) for x in str(fd)[:10].split("-"))
        if d1 > 1:
            out.add(f"{m1:02d}")
    except Exception:
        pass
    return out


def _cmp_anios(df) -> list:
    """Años presentes en el df, descendentes, tope _CMP_ANIOS."""
    if df is None or df.empty:
        return []
    return sorted({str(m)[:4] for m in df["MES_REF"]}, reverse=True)[:_CMP_ANIOS]


def _cmp_ncols(n_anios: int, n_labels: int) -> int:
    """Columnas de un bloque.

    n_labels + valores por año + dif/% por par + Obs. + 2 indicadores
    de tendencia (facturación y precio promedio unitario).
    """
    if n_anios <= 0:
        return n_labels + 1
    return n_labels + 2 * n_anios + 2 * (n_anios - 1) + 3


def _precio_prom(soles, unid):
    """Precio promedio unitario (S/und), o None si la base no es válida."""
    if not isinstance(soles, (int, float)) or not isinstance(unid, (int, float)):
        return None
    if isinstance(soles, bool) or isinstance(unid, bool) or unid <= 0:
        return None
    return soles / unid


def _tendencia_texto(v_last, v_prev) -> str:
    """Indicador de tendencia: flecha + variación % entre dos valores.

    ``> +5%`` → "↑", ``< -5%`` → "↓", resto → "→". Devuelve "" si no hay
    comparación posible (falta un valor o el previo no es positivo), para
    no inventar variaciones sobre bases no interpretables.
    """
    if not isinstance(v_last, (int, float)) or not isinstance(v_prev, (int, float)):
        return ""
    if isinstance(v_last, bool) or isinstance(v_prev, bool) or v_prev <= 0:
        return ""
    pct = (v_last - v_prev) / v_prev
    arrow = "↑" if pct > 0.05 else ("↓" if pct < -0.05 else "→")
    return f"{arrow} {'+' if pct >= 0 else ''}{pct:.0%}"


def _cmp_pivot(df, anios, parciales):
    """Filas (mes × línea × sku) con los valores de cada año (sin dif/%).

    Granulidad Mes × Código línea × SKU. Detecta SKUs que aparecen bajo
    más de una línea en el mismo mes (``sku_mixto``) para marcarlos con
    flag en la columna Obs. Orden: mes ascendente, dentro de cada mes
    línea con mayor neto primero, dentro de cada línea SKU con mayor neto
    primero (mantiene spans contiguos por mes para los SUBTOTAL).
    Retorna (header, filas_de_datos, spans, sku_mixto_set) donde
    ``spans`` mapea ``MM`` a (idx_inicio, idx_fin) 0-based dentro de
    ``filas``; ``sku_mixto_set`` es el conjunto de (mm, cod_sku) que
    tienen >1 línea.
    """
    n = len(anios)
    datos: dict = {}
    meses: set = set()
    lineas_por_sku: dict = {}  # (mm, cod_sku) -> set of cod_linea
    sku_nom: dict = {}  # cod_sku -> nom (take max for consistency)
    linea_nom: dict = {}  # cod_linea -> nom (take max)
    for _, row in df.iterrows():
        ref = str(row["MES_REF"])
        mm = ref[5:7]
        meses.add(mm)
        cod_linea = str(row.get("COD_LINEA", "")).strip()
        cod_sku = str(row.get("COD_SKU", "")).strip()
        nom_sku = str(row.get("SKU", "")).strip()
        nom_linea = str(row.get("LINEA", "")).strip()
        if nom_sku:
            sku_nom[cod_sku] = nom_sku
        if nom_linea:
            linea_nom[cod_linea] = nom_linea
        lineas_por_sku.setdefault((mm, cod_sku), set()).add(cod_linea)
        datos[(ref[:4], mm, cod_linea, cod_sku)] = (float(row["CANTIDAD"]), float(row["SOLES"]))

    # Orden único por (mes, línea, SKU); cada combinación genera UNA fila
    # con valores de todos los años en columnas separadas.
    def _total_anio(key: tuple, año: str) -> float:
        """Neto total de una fila (cod_linea, cod_sku) en un año dado,
        sumando todos los meses presentes en el df para ese año."""
        s = 0.0
        kl, ks = key
        for _a, m, klinea, ksku in datos:
            if klinea == kl and ksku == ks and _a == año:
                v = datos[(_a, m, klinea, ksku)]
                if v:
                    s += v[1]
        return s

    # Claves únicas (mm, cod_linea, cod_sku)
    unique_keys = {(m, kl, ks) for (a, m, kl, ks) in datos}
    orden_keys = sorted(
        unique_keys,
        key=lambda km: (int(km[0]), -_total_anio((km[1], km[2]), anios[0])),
    )

    sku_mixto_set: set = {k for k, vs in lineas_por_sku.items() if len(vs) > 1}

    anio_ref = anios[0]

    def _mes_lab(mm: str) -> str:
        try:
            lab = _mes_display(mm, anio_ref)
        except Exception:
            return mm
        return f"{lab} (parcial)" if mm in parciales else lab

    header = ["Mes", "Código línea", "Línea", "Código SKU", "SKU"]
    for i, anio in enumerate(anios):
        header += [f"Unid {anio}", f"Soles {anio}"]
        if i < n - 1:
            par = anios[i + 1][2:]
            header += [f"dif {anio[2:]}-{par}", f"% {anio[2:]}-{par}"]
    header.append("Obs.")
    header.append("Tend. Soles")
    header.append("Tend. Precio")

    # Índice de columna por año/métrica para resolver los indicadores
    # de tendencia sin recalcular el mapeo en cada fila.
    col_unid: dict = {}
    col_soles: dict = {}
    for hi, h in enumerate(header):
        if isinstance(h, str) and h.startswith(("Unid ", "Soles ")):
            metrica, anio = h.split(" ", 1)
            (col_unid if metrica == "Unid" else col_soles)[anio] = hi
    anios_trend = sorted(col_soles, key=int, reverse=True)
    # Tendencia = variación entre los dos años más recientes con dato.
    y_trend, y_ref_trend = (anios_trend[0], anios_trend[1]) if len(anios_trend) >= 2 else ("", "")

    filas = []
    for mes, cod_linea, cod_sku in orden_keys:
        fila = [
            _mes_lab(mes),
            cod_linea,
            linea_nom.get(cod_linea, ""),
            cod_sku,
            sku_nom.get(cod_sku, ""),
        ]
        # Valores por año en columnas separadas.
        for i, anio in enumerate(anios):
            lookup_key = (str(anio), mes, cod_linea, cod_sku)
            v = datos.get(lookup_key)
            fila += [None if v is None else v[0], None if v is None else v[1]]
            if i < n - 1:
                fila += [None, None]  # dif/%: fórmulas aparte
        # Obs.: marca flag si corresponde
        obs = ""
        from src.core.ventas_db_config import is_allowed_line

        if cod_linea and not is_allowed_line(cod_linea):
            obs = "Fuera del allowlist"
        if cod_linea == "99":
            obs = (obs + "; Línea genérica") if obs else "Línea genérica"
        if (mes, cod_sku) in sku_mixto_set:
            obs = (obs + "; SKU en 2+ líneas") if obs else "SKU en 2+ líneas"
        fila.append(obs)
        # Dos indicadores sobre el mismo par de años. Soles = volumen ×
        # precio, así que la facturación y el precio promedio unitario sí son
        # ejes independientes: cuando el precio cae y las ventas suben, el
        # crecimiento es puro volumen comprado con descuento.
        fila.append(
            _tendencia_texto(
                fila[col_soles[y_trend]] if y_trend else None,
                fila[col_soles[y_ref_trend]] if y_ref_trend else None,
            )
        )
        fila.append(
            _tendencia_texto(
                _precio_prom(fila[col_soles[y_trend]], fila[col_unid[y_trend]])
                if y_trend
                else None,
                _precio_prom(fila[col_soles[y_ref_trend]], fila[col_unid[y_ref_trend]])
                if y_ref_trend
                else None,
            )
        )
        filas.append(fila)

    spans: dict = {}
    for j, fila in enumerate(filas):
        mm = fila[0].split("-")[0]
        if mm not in spans:
            spans[mm] = [j, j]
        else:
            spans[mm][1] = j
    return header, filas, spans, sku_mixto_set


def _cmp_formulas_filas(filas, r0, n):
    """dif/% por fila (vs año previo). dif = Soles act − Soles prev;
    % con guarda prev ≤ 0 → "—". El año más viejo no lleva dif."""
    from openpyxl.utils import get_column_letter

    n_labels = 5  # Mes, Código línea, Línea, Código SKU, SKU
    for j, fila in enumerate(filas):
        rr = r0 + j
        for i in range(n - 1):
            s_c = get_column_letter(n_labels + 2 + 4 * i)  # Soles del año actual
            prev = get_column_letter(n_labels + 4 + 4 * i + 2)  # Soles del año previo
            dif_idx = n_labels + 2 + 4 * i  # 0-based: dif column
            fila[dif_idx] = f'=IF({prev}{rr}="","—",{s_c}{rr}-{prev}{rr})'
            fila[dif_idx + 1] = f'=IF({prev}{rr}<=0,"—",({s_c}{rr}-{prev}{rr})/{prev}{rr})'


def _cmp_fmt_cf(ws, r0, rtot, n):
    """Formatos del bloque (Unid entero; Soles/dif 2 dec; % 1 dec) + CF."""
    from openpyxl.utils import get_column_letter

    n_labels = 5  # Mes, Código línea, Línea, Código SKU, SKU
    for i in range(n):
        for rr in range(r0, rtot + 1):
            ws.cell(row=rr, column=n_labels + 1 + 4 * i).number_format = "#,##0"
            ws.cell(row=rr, column=n_labels + 2 + 4 * i).number_format = "#,##0.00"
            if i < n - 1:
                ws.cell(row=rr, column=n_labels + 3 + 4 * i).number_format = "#,##0.00"
                ws.cell(row=rr, column=n_labels + 4 + 4 * i).number_format = "0.0%"
    for i in range(n - 1):
        col_d = get_column_letter(n_labels + 3 + 4 * i)
        _x_cf(ws, f"{col_d}{r0}:{col_d}{rtot}", "lessThan")
        col_p = get_column_letter(n_labels + 4 + 4 * i)
        _x_cf(ws, f"{col_p}{r0}:{col_p}{rtot}", "lessThan")
    # Obs. + Tend. Soles + Tend. Precio: texto libre (sin formato numérico).
    for extra in (1, 2, 3):
        col = n_labels + 2 * n + 2 * (n - 1) + extra
        for rr in range(r0, rtot + 1):
            ws.cell(row=rr, column=col).number_format = "@"


def _x_cmp_bloque(
    ws, df, st, r, subtitulo, tabla, anios, parciales=frozenset(), fd="", fh="", corte_hasta=""
):
    """Tabla fusionada (mes × línea × sku) × año; dif/% = Soles vs el año previo.

    Header: Mes | Código línea | Línea | Código SKU | SKU | valores × años |
    Obs. | Tend. Soles | Tend. Precio.
    Dos filas TOTAL: COMPARABLE (intersección de meses presentes en los 3 años)
    y DEL RANGO (meses del rango por año, sin dif). Retorna la primera fila
    libre después del bloque.
    """
    from openpyxl.utils import get_column_letter

    n = len(anios)
    if n == 0:
        return r
    header, filas, spans, sku_mixto_set = _cmp_pivot(df, anios, parciales)
    if not filas:
        return r
    nfilas = len(filas)
    r0, rlast = r + 2, r + 1 + nfilas
    _cmp_formulas_filas(filas, r0, n)
    n_labels = 5  # Mes, Código línea, Línea, Código SKU, SKU

    # Two total rows: COMPARABLE (same MM set across years) and
    # DEL RANGO (only months that fall inside the report range per year).
    tot_info = _cmp_totales_rango(corte_hasta, anios)
    comp_mms = sorted(tot_info["comparables"])
    tot_comp = ["TOTAL COMPARABLE"] + [""] * (n_labels - 1)
    tot_rango = ["TOTAL DEL RANGO"] + [""] * (n_labels - 1)
    for i in range(n):
        anio = anios[i]
        u_c = get_column_letter(n_labels + 1 + 4 * i)
        s_c = get_column_letter(n_labels + 2 + 4 * i)
        # COMPARABLE: rows whose month is in the intersection across all years
        if comp_mms:
            first_j, last_j = None, None
            for j, fila in enumerate(filas):
                lbl_mm = fila[0].split("-")[0]
                if lbl_mm in comp_mms:
                    if first_j is None or j < first_j:
                        first_j = j
                    if last_j is None or j > last_j:
                        last_j = j
            if first_j is not None:
                tot_comp += [
                    f"=SUBTOTAL(109,{u_c}{r0 + first_j}:{u_c}{r0 + last_j})",
                    f"=SUBTOTAL(109,{s_c}{r0 + first_j}:{s_c}{r0 + last_j})",
                ]
            else:
                tot_comp += [None, None]
        else:
            tot_comp += [None, None]
        if i < n - 1:
            prev = get_column_letter(n_labels + 5 + 4 * i + 1)
            tot_comp += [
                f"={s_c}{rlast + 1}-{prev}{rlast + 1}",
                f'=IF({prev}{rlast + 1}<=0,"—",'
                f"({s_c}{rlast + 1}-{prev}{rlast + 1})/"
                f"{prev}{rlast + 1})",
            ]
        # DEL RANGO: sum Soles per year only for months inside the report range.
        # Compute directly in Python to avoid formulas that exceed Excel's 8192-char limit.
        rango_mms = sorted(tot_info["por_anio"].get(anio, set()))
        if rango_mms:
            s_sum = 0.0
            u_sum = 0.0
            for j, fila in enumerate(filas):
                lbl_mm = fila[0].split("-")[0]
                if lbl_mm in rango_mms:
                    s_val = fila[n_labels + 1 + 4 * i]
                    u_val = fila[n_labels + 4 * i]
                    if isinstance(s_val, (int, float)):
                        s_sum += s_val
                    if isinstance(u_val, (int, float)):
                        u_sum += u_val
            tot_rango += [u_sum if u_sum else None, s_sum if s_sum else None]
        else:
            tot_rango += [None, None]
        if i < n - 1:
            tot_rango += ["—", "—"]
    tot_comp.append("")  # Obs. col empty
    tot_comp.append("")  # Tend. Soles col empty
    tot_comp.append("")  # Tend. Precio col empty
    tot_rango.append("")
    tot_rango.append("")
    tot_rango.append("")

    matriz = [header] + filas + [tot_comp, tot_rango]
    rtot1, rtot2 = rlast + 1, rlast + 2
    r, r_hdr = _x_bloque(ws, matriz, "#,##0.00", r, st, subtitulo=subtitulo)
    _cmp_fmt_cf(ws, r0, rtot2, n)
    _tabla_excel(ws, tabla, r_hdr, rlast, _cmp_ncols(n, n_labels))
    _x_fmt_total(ws, rtot1, _cmp_ncols(n, n_labels), st)
    _x_fmt_total(ws, rtot2, _cmp_ncols(n, n_labels), st)
    # Pintar celdas Obs. con flag
    col_obs = _cmp_ncols(n, n_labels) - 2
    obs_filas = [r0 + j for j, f in enumerate(filas) if f[col_obs - 1]]
    if obs_filas:
        ws.cell(row=rtot1, column=_cmp_ncols(n, n_labels)).number_format = "@"
        ws.cell(row=rtot2, column=_cmp_ncols(n, n_labels)).number_format = "@"
    return r


def _x_comparativo(
    wb, df_comp_sku, st, nombre, cid, meses_par=frozenset(), fd="", fh="", corte_hasta=""
):
    """Comparativo interanual fusionado: (mes × línea × sku) vs año previo.

    Una sola tabla con identidad discreta (Mes, Código línea, Línea,
    Código SKU, SKU, Obs.). dif/% comparan Soles contra el año previo;
    el año más viejo no tiene dif. La ventana siempre arranca el 1 de
    enero del año (corte_hasta - 2) para mostrar 3 años completos (el
    último parcialmente). Meses incompletos del rango marcados "(parcial)".
    SKUs que aparecen bajo 2+ líneas en un mes se partieron en 2 filas y
    llevan el flag "SKU en 2+ líneas". Líneas fuera del allowlist llevan
    "Fuera del allowlist" (no entran al sustento). Cierra con dos
    indicadores sobre el mismo par de años: facturación (Soles) y precio
    promedio unitario. Actualizar = re-exportar.
    """
    anios = _cmp_anios(df_comp_sku)
    if not anios:
        return
    ncols = _cmp_ncols(len(anios), 5)
    ws = wb.create_sheet("Comparativo")
    r = 1
    r = _x_cmp_bloque(
        ws,
        df_comp_sku,
        st,
        r,
        "POR SKU × LÍNEA (S/ vs AÑO PREVIO)",
        "ComparativoSKUs",
        anios,
        meses_par,
        fd,
        fh,
        corte_hasta=corte_hasta,
    )
    # Anchos por columna.
    from openpyxl.utils import get_column_letter

    ws.column_dimensions["A"].width = 18  # "09-2026 (parcial)"
    ws.column_dimensions["B"].width = 10  # Código línea
    ws.column_dimensions["C"].width = 24  # Línea
    ws.column_dimensions["D"].width = 12  # Código SKU
    ws.column_dimensions["E"].width = 30  # SKU
    _patron = (10, 14, 12, 9)  # Unid, Soles, dif, %
    for c in range(6, ncols + 1):
        ws.column_dimensions[get_column_letter(c)].width = _patron[(c - 6) % 4]
    ws.column_dimensions[get_column_letter(ncols)].width = 12  # Tend. Precio
    ws.column_dimensions[get_column_letter(ncols - 1)].width = 12  # Tend. Soles
    ws.column_dimensions[get_column_letter(ncols - 2)].width = 24  # Obs.
    _x_print_header(
        ws,
        "COMPRAS NETAS — COMPARATIVO INTERANUAL",
        nombre,
        cid,
        ncols=ncols,
        rango_desde=fd,
        rango_hasta=fh,
    )
    _x_nota(
        ws,
        r + 1,
        ncols,
        'dif/% = Soles contra el año previo ("—" si el previo ≤ 0). '
        "Ventana arranca el 1 de enero del año (corte_hasta - 2) para "
        "mostrar 3 años completos (el último parcialmente). Cada fila "
        "tiene identidad discreta: Mes | Código línea | Línea | "
        "Código SKU | SKU | Obs. Los meses incompletos del rango van "
        "marcados '(parcial)' y comparan parcial vs completo del año "
        "previo. SKUs bajo 2+ líneas se partieron en filas separadas. "
        "Dos totales: COMPARABLE (intersección de meses entre años, "
        "dif válida) y DEL RANGO (meses del periodo por año, sin dif). "
        "Col 'Tend. Soles' = variación % de facturación y 'Tend. Precio' = "
        "variación % del precio promedio unitario (Soles/Unidades), "
        "ambos entre los dos años más recientes con dato (flecha: "
        "↑ > +5%, ↓ < -5%, → estable; vacío si no hay base comparable). "
        "Como Soles = volumen × precio, leerlos juntos explica de dónde "
        "viene el movimiento: precio ↓ con ventas ↑ = crecimiento en "
        "volumen sostenido con descuento; precio ↑ con ventas ↑ = mejora "
        "de precio; ambos ↓ = caída real de precio. "
        "Actualizar = re-exportar. Soles sin IGV.",
    )


def _x_tabla_datos(wb, df, sheet_name, table_name, columns, widths):
    """Hoja operativa con encabezados en fila 1 y una Tabla de Excel real.

    ``columns`` son pares (clave DataFrame, encabezado visible). No añade
    títulos ni totales al rango: queda lista para filtros y consumo Power BI.
    """
    if df is None or df.empty:
        return
    from openpyxl.styles import Alignment, Font, PatternFill

    headers = [label for _key, label in columns]
    if len(headers) != len(set(headers)):
        raise ValueError(f"encabezados duplicados en {sheet_name}: {headers}")
    ws = wb.create_sheet(sheet_name)
    ws.sheet_view.showGridLines = False
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True, size=9, color="FFFFFF")
        cell.fill = PatternFill(start_color="0D2B4E", end_color="0D2B4E", fill_type="solid")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[1].height = 30
    for _, row in df.iterrows():
        values = []
        for key, _label in columns:
            value = row[key]
            try:
                if pd.isna(value):
                    value = None
            except (TypeError, ValueError):
                pass
            if hasattr(value, "item"):
                value = value.item()
            values.append(value)
        ws.append(values)
    for col, width in widths.items():
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A2"
    _tabla_excel(ws, table_name, 1, len(df) + 1, len(columns))
    enteros = {"RANK", "CANTIDAD", "N_DOCS", "N_SKUS"}
    monetarios = {"BRUTA", "DEV_S", "DESC_S", "NDB_S", "SOLES"}
    for col_idx, (key, _label) in enumerate(columns, start=1):
        fmt = (
            "0.0%"
            if key.startswith("PCT_")
            else "#,##0"
            if key in enteros
            else "#,##0.00"
            if key in monetarios
            else None
        )
        if not fmt:
            continue
        for rr in range(2, len(df) + 2):
            cell = ws.cell(row=rr, column=col_idx)
            cell.number_format = fmt
            cell.alignment = Alignment(horizontal="right")
    return ws


def _x_analisis_sucursales(wb, df_pareto, df_linea_mes, df_sku_mes):
    """Pareto del rango y detalle por línea/SKU mensual (tres tablas planas)."""
    _x_tabla_datos(
        wb,
        df_pareto,
        "Sucursales",
        "SucursalesPareto",
        [
            ("RANK", "Orden Pareto"),
            ("COD_SUCURSAL", "Código sucursal"),
            ("SUCURSAL", "Sucursal"),
            ("TIPO_SUCURSAL", "Tipo sucursal"),
            ("ID_UBIGEO", "Ubigeo"),
            ("DISTRITO", "Distrito"),
            ("BRUTA", "Bruta S/"),
            ("DEV_S", "Devoluciones S/"),
            ("DESC_S", "Descuentos S/"),
            ("NDB_S", "Débitos S/"),
            ("SOLES", "Neto S/"),
            ("CANTIDAD", "Unidades"),
            ("N_DOCS", "N° docs"),
            ("PCT_BRUTA", "% Bruta"),
            ("PCT_ACUMULADO", "% Acumulado"),
        ],
        {
            "A": 13,
            "B": 17,
            "C": 34,
            "D": 18,
            "E": 12,
            "F": 24,
            "G": 16,
            "H": 18,
            "I": 16,
            "J": 14,
            "K": 16,
            "L": 14,
            "M": 14,
            "N": 13,
            "O": 16,
        },
    )
    _x_tabla_datos(
        wb,
        df_linea_mes,
        "Sucursal_Linea_Mes",
        "SucursalLineaMes",
        [
            ("MES_REF", "Mes"),
            ("COD_SUCURSAL", "Código sucursal"),
            ("SUCURSAL", "Sucursal"),
            ("TIPO_SUCURSAL", "Tipo sucursal"),
            ("COD_LINEA", "Código línea"),
            ("LINEA", "Línea"),
            ("BRUTA", "Bruta S/"),
            ("DEV_S", "Devoluciones S/"),
            ("DESC_S", "Descuentos S/"),
            ("NDB_S", "Débitos S/"),
            ("SOLES", "Neto S/"),
            ("CANTIDAD", "Unidades"),
            ("N_DOCS", "N° docs"),
            ("N_SKUS", "SKU distintos"),
        ],
        {
            "A": 12,
            "B": 17,
            "C": 34,
            "D": 18,
            "E": 14,
            "F": 28,
            "G": 16,
            "H": 18,
            "I": 16,
            "J": 14,
            "K": 16,
            "L": 14,
            "M": 14,
            "N": 15,
        },
    )
    _x_tabla_datos(
        wb,
        df_sku_mes,
        "Sucursal_SKU_Mes",
        "SucursalSkuMes",
        [
            ("MES_REF", "Mes"),
            ("COD_SUCURSAL", "Código sucursal"),
            ("SUCURSAL", "Sucursal"),
            ("TIPO_SUCURSAL", "Tipo sucursal"),
            ("COD_SKU", "Código SKU"),
            ("SKU", "Artículo"),
            ("COD_LINEA", "Código línea"),
            ("LINEA", "Línea"),
            ("BRUTA", "Bruta S/"),
            ("DEV_S", "Devoluciones S/"),
            ("DESC_S", "Descuentos S/"),
            ("NDB_S", "Débitos S/"),
            ("SOLES", "Neto S/"),
            ("CANTIDAD", "Unidades"),
            ("N_DOCS", "N° docs"),
        ],
        {
            "A": 12,
            "B": 17,
            "C": 34,
            "D": 18,
            "E": 16,
            "F": 42,
            "G": 14,
            "H": 28,
            "I": 16,
            "J": 18,
            "K": 16,
            "L": 14,
            "M": 16,
            "N": 14,
            "O": 14,
        },
    )


def _x_bd(wb, df, st, nombre, cid):
    """4. BD_Registro: una fila por línea de documento (BD de salida).

    Orden canónico Documento, Fecha … Línea, SKU, Cant., P.U., Neto +
    columnas analíticas al final (Pedido, FAE, Dev., Bruto, Ajuste).
    Línea solo con el código canónico (01, 78): el nombre de la línea
    es redundante frente a Artículo. Fecha como date real para el filtro
    nativo; P.U. a 5 decimales; fila TOTAL fuera del rango de la tabla
    nativa. Es SALIDA (se regenera desde la captura: no pegar filas).
    """
    if df is None or df.empty:
        return
    cab = [
        "Documento",
        "Fecha",
        "Mes",
        "Tipo",
        "Ref.",
        "Línea",
        "Sucursal",
        "SKU",
        "Artículo",
        "Cant.",
        "P.U.",
        "Neto (S/)",
        "Operación",
        "Pedido",
        "FAE",
        "Dev. (und.)",
        "Bruto (S/)",
        "Ajuste (S/)",
    ]
    mat = [cab]
    for _, d in df.iterrows():
        mat.append(
            [
                str(d.get("DOC", "")),
                _fecha_obj(d.get("FECHA")),
                str(d.get("MES_REF", "")),
                str(d.get("TPO_DOC", "")),
                str(d.get("REF", "") or ""),
                str(d.get("COD_LINEA", "")),
                str(d.get("SUCURSAL", "") or ""),
                str(d.get("COD_SKU", "")),
                str(d.get("ARTICULO", "") or ""),
                float(d.get("CANTIDAD", 0) or 0),
                float(d.get("PU", 0) or 0),
                float(d.get("SOLES", 0) or 0),
                str(d.get("OPERACION", "") or ""),
                str(d.get("PEDIDO", "") or ""),
                float(d.get("FAE", 0) or 0),
                float(d.get("CANT_DEV", 0) or 0),
                float(d.get("BRUTO", 0) or 0),
                float(d.get("AJUSTE", 0) or 0),
            ]
        )
    n = len(mat) - 1  # filas de datos
    r0, rlast = 2, 1 + n
    mat.append(
        ["TOTAL"]
        + [""] * 8
        + [
            f"=SUBTOTAL(109,J{r0}:J{rlast})",
            "",
            f"=SUBTOTAL(109,L{r0}:L{rlast})",
            "",
            "",
            "",
            f"=SUBTOTAL(109,P{r0}:P{rlast})",
            f"=SUBTOTAL(109,Q{r0}:Q{rlast})",
            f"=SUBTOTAL(109,R{r0}:R{rlast})",
        ]
    )
    ws = wb.create_sheet("BD_Registro")
    r = _x_hoja(ws, mat, "COMPRAS NETAS — BD REGISTRO", st, nombre, cid, "#,##0.00", banner=False)
    for col, w in zip(
        "ABCDEFGHIJKLMNOPQR", (15, 11, 9, 7, 15, 8, 15, 10, 30, 11, 12, 12, 13, 12, 10, 11, 12, 12)
    ):
        ws.column_dimensions[col].width = w
    for rrow in ws.iter_rows(min_row=2, min_col=1, max_row=rlast, max_col=18):
        rrow[1].number_format = "dd-mm-yyyy"
        rrow[9].number_format = "#,##0"
        rrow[10].number_format = EXCEL_FMT_UNIT_PRICE
        rrow[11].number_format = "#,##0.00"
        rrow[14].number_format = "#,##0"
        rrow[15].number_format = "#,##0"
        rrow[16].number_format = "#,##0.00"
        rrow[17].number_format = "#,##0.00"
    ws.cell(row=rlast + 1, column=10).number_format = "#,##0"
    ws.cell(row=rlast + 1, column=12).number_format = "#,##0.00"
    ws.cell(row=rlast + 1, column=16).number_format = "#,##0"
    ws.cell(row=rlast + 1, column=17).number_format = "#,##0.00"
    ws.cell(row=rlast + 1, column=18).number_format = "#,##0.00"
    _tabla_excel(ws, "BDRegistro", 1, rlast, len(cab))
    _x_fmt_total(ws, rlast + 1, len(cab), st)
    _x_cf(ws, f"L{r0}:L{rlast + 1}", "lessThan")
    _x_print_header(ws, "COMPRAS NETAS — BD REGISTRO", nombre, cid, ncols=len(cab))
    _x_nota(
        ws,
        r + 1,
        len(cab),
        "BD de salida: se regenera desde la captura (no pegar filas). "
        "Dev. = unidades ya devueltas contra esa línea; "
        "Bruto + Ajuste = Neto por fila. Soles sin IGV.",
    )


def _tabla_excel(ws, nombre, hdr_row, last_row, ncols):
    """Rango como tabla de Excel (piloto: hoja Documentos).

    La tabla trae sus propios filtros y bandas: no se usa el autofilter
    de hoja (superponerlos deja el archivo ilegible en Excel) y la fila
    TOTAL queda fuera del rango para no viajar con los filtros.
    """
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.table import Table, TableStyleInfo

    tab = Table(displayName=nombre, ref=f"A{hdr_row}:{get_column_letter(ncols)}{last_row}")
    tab.tableStyleInfo = TableStyleInfo(
        name="TableStyleMedium2",
        showFirstColumn=False,
        showLastColumn=False,
        showRowStripes=True,
        showColumnStripes=False,
    )
    ws.add_table(tab)


def _x_nota(ws, r, ncols, texto):
    """Nota al pie (fuera del autofiltro): letra pequeña gris cursiva."""
    from openpyxl.styles import Font

    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=ncols)
    c = ws.cell(row=r, column=1, value=texto)
    c.font = Font(size=8, italic=True, color="666666")
    return r + 1


# Motivo de la nota desde su tipo (etiqueta cerrada, sin texto libre).
_MOTIVO = {"devolucion": "DEVOLUCIÓN", "ajuste_valor": "DESCUENTO", "nota_debito": "NDB"}


def _motivo(tipo: str) -> str:
    """Etiqueta de motivo para una nota (fallback: tipo crudo)."""
    return _MOTIVO.get(str(tipo or ""), str(tipo or ""))


# ── hoja Resumen Ejecutivo (KPIs + tabla mensual) ────────────────


def _fin_de_mes(anho: int, mes: int) -> str:
    """Último día del mes en YYYY-MM-DD."""
    import calendar

    return f"{anho}-{mes:02d}-{calendar.monthrange(anho, mes)[1]:02d}"


def _kpis_resumen() -> list:
    """(etiqueta, columna, formato, ancla) de la tabla de indicadores.

    Columnas SIN Año ('Mes' ya es YYYY-MM); las referencias apuntan a
    la fila TOTAL (rtot) salvo 'last' (último mes, rlast).
    """
    return [
        ("Venta bruta (S/)", "B", "#,##0.00", None),
        ("Devoluciones (S/)", "C", "#,##0.00", None),
        ("Descuentos (S/)", "D", "#,##0.00", None),
        ("NDB (S/)", "E", "#,##0.00", None),
        ("Venta neta (S/)", "F", "#,##0.00", None),
        ("Unidades fact.", "G", "#,##0", None),
        ("Unidades dev.", "H", "#,##0", None),
        ("Unidades netas", "I", "#,##0", None),
        ("% devoluciones", "J", "0.0%", None),
        ("Facturas", "K", "#,##0", None),
        ("Notas de crédito", "L", "#,##0", None),
        ("Notas de débito", "M", "#,##0", None),
        ("Ticket promedio (S/)", "O", "#,##0.00", None),
        ("Último mes", "A", None, "last"),
        ("Neta del último mes (S/)", "F", "#,##0.00", "last"),
    ]


def _matriz_mensual(df_hist, corte, r0, rlast, rtot):
    """Header + filas de la tabla mensual con fórmulas y TOTAL.

    neta = SUM(componentes), U. netas = fact − dev (dev con signo),
    %dev y ticket con guardas; TOTAL con SUBTOTAL (filtra con la tabla)
    y ratios en J/O en vez de sumas. Agrega columna "Tendencia" (Q) con
    barra ASCII proporcional al neto del mes.
    """
    from openpyxl.utils import get_column_letter

    header = [
        "Mes",
        "Venta bruta",
        "Dev. S/",
        "Desc. S/",
        "NDB S/",
        "Venta neta",
        "U. fact.",
        "U. dev.",
        "U. netas",
        "% dev.",
        "Facturas",
        "NC",
        "NDB docs",
        "SKUs",
        "Ticket",
        "Estado",
        "Tendencia",
    ]
    mat = [header]
    for _, rh in df_hist.iterrows():
        mes = str(rh["MES"])
        anho, mm = int(mes[:4]), int(mes[5:7])
        # Mes completo si ya terminó al corte (aunque su última transacción
        # sea del día 5); solo el mes del corte puede salir PARCIAL.
        estado = "COMPLETO" if _fin_de_mes(anho, mm) <= corte[:10] else "PARCIAL"
        mat.append(
            [
                mes,
                float(rh["BRUTA"]),
                float(rh["DEV_S"]),
                float(rh["DESC_S"]),
                float(rh["NDB_S"]),
                None,
                float(rh["UFACT"]),
                float(rh["UDEV"]),
                None,
                None,
                int(rh["FACTURAS"]),
                int(rh["NC"]),
                int(rh["NDB_DOCS"]),
                int(rh["SKUS"]),
                None,
                estado,
                None,
            ]
        )
    m = len(mat) - 1
    for i in range(m):
        rr = r0 + i
        mat[1 + i][5] = f"=SUM(B{rr}:E{rr})"
        mat[1 + i][8] = f"=G{rr}+H{rr}"
        mat[1 + i][9] = f'=IF(G{rr}=0,"—",-H{rr}/G{rr})'
        mat[1 + i][14] = f'=IF(K{rr}=0,"—",B{rr}/K{rr})'
    mat.append(
        ["TOTAL"]
        + [
            f"=SUBTOTAL(109,{get_column_letter(c)}{r0}:{get_column_letter(c)}{rlast})"
            for c in range(2, 16)
        ]
        + ["", ""]
    )
    # J (10) y O (15) del TOTAL deben ser ratios, no sumas:
    mat[-1][9] = f'=IF(G{rtot}=0,"—",-H{rtot}/G{rtot})'
    mat[-1][14] = f'=IF(K{rtot}=0,"—",B{rtot}/K{rtot})'
    return header, mat


def _netos_mensuales(df_hist):
    """Neto por mes a partir de los componentes brutos."""
    out = []
    for _, rh in df_hist.iterrows():
        neta = float(rh["BRUTA"]) + float(rh["DEV_S"]) + float(rh["DESC_S"]) + float(rh["NDB_S"])
        out.append(neta)
    return out


def _pintar_tendencia(ws, r0, rtot, netos=None):
    """Rellena la columna Q con barras ASCII proporcionales al neto.

    ``netos`` es una lista de valores numéricos (uno por fila de dato);
    si no se pasa se intenta leer de las celdas de F (solo tras abrir
    el libro en Excel). El TOTAL (última fila) siempre queda vacío.
    """
    max_neto = 0.0
    if netos is not None:
        max_neto = max((v for v in netos if v > 0), default=0.0)
    else:
        for rr in range(r0, rtot):
            v = ws.cell(row=rr, column=6).value
            if isinstance(v, (int, float)) and v > max_neto:
                max_neto = float(v)
    if max_neto <= 0:
        return
    blocks = "█▓▓▓▒▒▒▒░░░░"  # 12 chars to cover 0-10 range with padding
    # range(r0, rtot) = data rows only (TOTAL está en rtot, no se incluye).
    for idx, rr in enumerate(range(r0, rtot)):
        v = netos[idx] if netos is not None else ws.cell(row=rr, column=6).value
        if not isinstance(v, (int, float)):
            ws.cell(row=rr, column=17).value = ""
            continue
        if v <= 0:
            ws.cell(row=rr, column=17).value = "—"
            continue
        fill = int(v / max_neto * 10)
        bar = blocks[:fill] + " " * (10 - fill)
        ws.cell(row=rr, column=17).value = bar


def _fmt_mensual(ws, r0, rtot):
    """Formatos de la tabla mensual (datos y TOTAL: mismas columnas)."""
    for rrow in ws.iter_rows(min_row=r0, min_col=1, max_row=rtot, max_col=16):
        for ci in (6, 7):
            rrow[ci].number_format = "#,##0"
        rrow[9].number_format = "0.0%"
        for ci in (10, 11, 12, 13):
            rrow[ci].number_format = "#,##0"
        rrow[14].number_format = "#,##0.00"


def _x_resumen_indice(wb, ws, st, r):
    """Índice de hojas con hipervínculos internos (sin fórmulas)."""
    from openpyxl.styles import Alignment, Font
    from openpyxl.worksheet.hyperlink import Hyperlink

    ri = r + 2
    ws.merge_cells(start_row=ri, start_column=1, end_row=ri, end_column=3)
    c = ws.cell(row=ri, column=1, value="ÍNDICE DE HOJAS (clic para abrir)")
    c.font = Font(bold=True, size=10, color="0D2B4E")
    c.fill = st["total"]
    ri += 1
    h = ws.cell(row=ri, column=1, value="Hoja")
    h.font = Font(bold=True, size=9, color="FFFFFF")
    h.fill = st["header"]
    h.alignment = Alignment(horizontal="center")
    ri += 1
    for hoja in wb.sheetnames:
        if hoja in ("Resumen Ejecutivo", "Sheet"):
            continue
        c = ws.cell(row=ri, column=1, value=hoja)
        c.hyperlink = Hyperlink(ref=c.coordinate, location=f"'{hoja}'!A1")
        c.font = Font(size=9, color="0563C1", underline="single")
        ri += 1
    return ri


def _x_resumen(
    wb,
    df_hist,
    corte_hasta,
    incluir_nc,
    st,
    nombre,
    cid,
    rango_desde="",
    rango_hasta="",
    ruc="",
    vendedor="",
    hojas_incluidas=None,
    total_hojas=7,
):
    """1. RESUMEN EJECUTIVO: KPIs enlazados + tabla mensual + índice.

    Los KPIs son fórmulas vivas a la fila TOTAL de la tabla mensual
    (misma hoja): si se edita un mes, el resumen se recalcula solo.
    Piezas: _kpis_resumen (indicadores), _matriz_mensual (tabla con
    fórmulas), _fmt_mensual (formatos) e _x_resumen_indice (links).
    Se crea la última (todas las hojas ya existen) y va al frente.
    Fila 2 = cliente + RUC; fila 3 = vendedor + NC/ND + chip de alcance.
    """
    if df_hist is None or df_hist.empty:
        return None
    from openpyxl.styles import Alignment, Font

    corte = str(corte_hasta or "").strip()[:10]
    if not corte and "MAX_FECHA" in df_hist.columns:
        corte = str(df_hist["MAX_FECHA"].max())[:10]
    if not corte:
        return None
    m = len(df_hist)

    # Geometría (determinista: los KPIs referencian la fila TOTAL).
    # La fila 3 de metadata desplaza el KPI block, pero el monthly hdr
    # cae en la misma fila que antes (23) porque el KPI block gana 1 fila.
    kpis = _kpis_resumen()
    nk = len(kpis)
    r_men_hdr = 8 + nk
    r0, rlast, rtot = r_men_hdr + 1, r_men_hdr + m, r_men_hdr + m + 1

    ws = wb.create_sheet("Resumen Ejecutivo")
    _x_encabezado(ws, "COMPRAS NETAS — RESUMEN EJECUTIVO", st, nombre, cid, ncols=16)
    _x_meta_resumen(
        ws,
        st,
        nombre,
        cid,
        ruc=ruc,
        vendedor=vendedor,
        ncols=16,
        modo=("SÍ" if incluir_nc else "NO"),
        n_incluidas=len(hojas_incluidas) if hojas_incluidas else 0,
        n_total=total_hojas,
    )

    mat = [["Indicador", "Valor", "Origen"]]
    for etiqueta, col, _fmt, which in kpis:
        ref = f"{col}{rlast}" if which == "last" else f"{col}{rtot}"
        mat.append([etiqueta, f"={ref}", ref])
    r, r_kpi_hdr = _x_bloque(ws, mat, "#,##0.00", 4, st, subtitulo="INDICADORES DEL RANGO")
    for i, (_et, _c, fmt, _w) in enumerate(kpis):
        c = ws.cell(row=r_kpi_hdr + 1 + i, column=2)
        if fmt:
            c.number_format = fmt
        c.alignment = Alignment(horizontal="right")
        ws.cell(row=r_kpi_hdr + 1 + i, column=1).font = Font(size=9, bold=True)
        ws.cell(row=r_kpi_hdr + 1 + i, column=3).font = Font(size=8, color="666666")
    _x_cf(ws, f"B{r_kpi_hdr + 1}:B{r_kpi_hdr + nk}", "lessThan")

    header, mat = _matriz_mensual(df_hist, corte, r0, rlast, rtot)
    r, r_hdr = _x_bloque(ws, mat, "#,##0.00", r + 1, st, subtitulo="MENSUAL (S/ SIN IGV)")
    assert r_hdr == r_men_hdr, (r_hdr, r_men_hdr)
    from openpyxl.utils import get_column_letter

    # Ancho col A (Mes) algo mayor para etiquetas más largas.
    col_widths = (26, 22, 12, 12, 12, 13, 11, 11, 11, 9, 10, 8, 10, 8, 12, 11, 14)
    for col, w in zip(range(1, len(col_widths) + 1), col_widths):
        ws.column_dimensions[get_column_letter(col)].width = w
    _fmt_mensual(ws, r0, rtot)
    # Completar barras de tendencia (valores crudos, sin esperar fórmulas).
    netos = _netos_mensuales(df_hist)
    _pintar_tendencia(ws, r0, rtot, netos=netos)
    # Tabla incluye la col Q; TOTAL queda fuera.
    _tabla_excel(ws, "ResumenMensual", r_hdr, rlast, len(header))
    _x_fmt_total(ws, rtot, len(header), st)
    _x_cf(ws, f"F{r0}:F{rtot}", "lessThan")
    # Fondo ámbar para meses PARCIALES (columna P).
    _x_cf_fill(ws, f"P{r0}:P{rtot}", "equal", '"PARCIAL"')
    modo = (
        "SÍ — neto con NC/NDB del periodo."
        if incluir_nc
        else "NO — solo facturas/boletas (componentes en cero)."
    )
    r = _x_nota(ws, r + 1, len(header), f"Incluir NC/ND: {modo}")
    if rango_desde and rango_hasta:
        r = _x_nota(
            ws,
            r + 1,
            len(header),
            f"Rango: {_fecha_corta(rango_desde)} → {_fecha_corta(rango_hasta)}.",
        )
    alertas = reconciliar_mensual(df_hist)
    if alertas:
        nota = "⚠ Conciliación: " + " | ".join(alertas[:3])
    else:
        nota = (
            "✓ Concilia: bruta+DEV+DESC+NDB = neta por mes. "
            "Ticket = Venta bruta / Nº facturas. % dev sobre bruta. "
            "Soles sin IGV. Leyenda: '—' = sin dato / no aplica; "
            "Obs. vacío = sin novedad (los Obs. solo llevan flags "
            "cerrados)."
        )
    r = _x_nota(ws, r + 1, len(header), nota)
    _x_resumen_indice(wb, ws, st, r)
    return ws


# ── hoja Facturas ────────────────────────────────────────────────

_ALERTA = "FFC7CE"


def _pintar_obs(ws, filas_obs, col_obs):
    """Rellena en rosa las celdas Obs. con flag (alerta visual)."""
    from openpyxl.styles import PatternFill

    alerta = PatternFill(start_color=_ALERTA, end_color=_ALERTA, fill_type="solid")
    for rr in filas_obs:
        ws.cell(row=rr, column=col_obs).fill = alerta


# ── formato condicional (lectura rápida) ───────────────────────────
# Solo reglas CellIs sobre el valor calculado (era-2007: las evalúan
# Excel y LibreOffice). Rojo = salidas/negativos; verde = NDB positivo
# (solo S/ NC de Ajustes). Sin barras ni escalas (ruido visual).
_CF_ROJO = "9C0006"
_CF_VERDE = "006100"
_CF_AMBER = "FFF2CC"  # fondo ámbar suave para meses PARCIALES


def _x_cf(ws, rango, operador, formula="0", color=_CF_ROJO):
    """Regla de color de fuente sobre un rango (p. ej. M12:M269 < 0)."""
    from openpyxl.formatting.rule import CellIsRule
    from openpyxl.styles import Font

    ws.conditional_formatting.add(
        rango, CellIsRule(operator=operador, formula=[formula], font=Font(color=color))
    )


def _x_cf_fill(ws, rango, operador, formula, color=_CF_AMBER):
    """Regla de fondo sobre un rango (ej. P24:P38 = \"PARCIAL\" → ámbar)."""
    from openpyxl.formatting.rule import CellIsRule
    from openpyxl.styles import PatternFill

    ws.conditional_formatting.add(
        rango,
        CellIsRule(
            operator=operador,
            formula=[formula],
            fill=PatternFill(fill_type="solid", start_color=color, end_color=color),
        ),
    )


def _x_print_header(
    ws, titulo, nombre, cid, ruc="", vendedor="", ncols=10, rango_desde="", rango_hasta=""
):
    """Configura encabezado/pie de impresión para hojas operativas limpias.

    El banner (título + cliente) se mueve del cuerpo visible al encabezado,
    así la tabla arranca en fila 1 y las herramientas de BI no tienen que
    saltarse filas innecesarias. El resultado es invisible en pantalla y
    aparece impreso con cliente + RUC + rango + número de página.
    """
    cid_v = _c_visible(cid)
    parts_center = [nombre]
    if ruc:
        parts_center.append(f"RUC {ruc}")
    parts_center.append(f"({cid_v})")
    ws.oddHeader.center.text = " · ".join(parts_center)
    ws.oddHeader.left.text = titulo
    ws.oddHeader.right.text = "Página &P de &N"

    footer_parts = []
    if rango_desde and rango_hasta:
        footer_parts.append(f"{_fecha_corta(rango_desde)} → {_fecha_corta(rango_hasta)}")
    footer_parts.append("importes sin IGV")
    ws.oddFooter.left.text = " · ".join(footer_parts)

    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.page_setup.fitToPage = True
    ws.sheet_properties.pageSetUpPr = getattr(ws.sheet_properties, "pageSetUpPr", None)
    if ws.sheet_properties.pageSetUpPr is None:
        ws.sheet_properties.pageSetUpPr = type(ws.sheet_properties)("pageSetUpPr", fitToPage=True)
    else:
        ws.sheet_properties.pageSetUpPr.fitToPage = True


def _x_meta_resumen(
    ws, st, nombre, cid, ruc="", vendedor="", ncols=16, modo="", n_incluidas=0, n_total=0
):
    """Filas 2-3 de la portada: identificación + contexto del export.

    Fila 2: CLIENTE · RUC (id) — alineada a la derecha, muted.
    Fila 3: vendedor · NC/ND · alcance del export — alineada derecha,
    tamaño más chico (8pt). Solo se llama desde _x_resumen; las otras
    hojas usan _x_print_header.
    """
    from openpyxl.styles import Alignment, Font

    # Fila 2: identificación (append RUC al texto existente si no lo tiene).
    existing_a2 = str(ws.cell(row=2, column=1).value or "")
    if ruc and "RUC" not in existing_a2:
        ws.cell(row=2, column=1).value = f"{existing_a2} · RUC {ruc}"
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=ncols)
    c = ws.cell(row=2, column=1)
    c.font = st["muted"]
    c.alignment = Alignment(horizontal="right")

    # Fila 3: contexto del export
    parts3 = []
    if vendedor:
        parts3.append(f"vendedor {vendedor}")
    if modo:
        parts3.append(f"NC/ND: {modo}")
    if n_total > 0 and n_incluidas != n_total:
        parts3.append(f"export ({n_incluidas} de {n_total} hojas)")
    ws.merge_cells(start_row=3, start_column=1, end_row=3, end_column=ncols)
    c = ws.cell(row=3, column=1, value=" · ".join(parts3) if parts3 else "")
    c.font = Font(size=8, color="666666")
    c.alignment = Alignment(horizontal="right")


def _x_fmt_total(ws, rtot, ncols, st):
    """Fila TOTAL identificable: negrita + fondo (no viaja con filtros)."""
    from openpyxl.styles import Font

    for cc in range(1, ncols + 1):
        c = ws.cell(row=rtot, column=cc)
        c.font = Font(size=9, bold=True)
        c.fill = st["total"]


def _x_facturas(wb, df_det, df_dev, rango_ajustes, mapa_nc, st, nombre, cid):
    """5. Facturas: tabla única por (factura, SKU) con NC asociadas.

    Factura, Fecha, Pedido, OC, Sucursal, Línea (código canónico), SKU,
    Descripción, Cant. física, P.U. Bruto, Total Bruto, Ajuste/NC
    (SUMIFS a Ajustes o valor), Total Neto, Costo Unit. Neto y NC
    asociadas (enumeradas, con hipervínculo a su fila en Ajustes).
    Sucursal/Pedido/OC/Línea son únicos por grupo (verificado: 0
    dispersión en datos). Sin hoja Ajustes, Ajuste/NC e hipervínculos
    degradan a valores/texto (nunca #REF!).
    """
    if df_det is None or df_det.empty:
        return
    AJ = "'Ajustes_NC_NDB'"
    aj = {}
    if df_dev is not None and not df_dev.empty:
        aj = df_dev.groupby(["FACTURA", "SKU"])["SOLES_NC"].sum().round(2).to_dict()
    hay_aj = "Ajustes_NC_NDB" in wb.sheetnames
    ws = wb.create_sheet("Facturas")
    mat = [
        [
            "Factura",
            "Fecha",
            "Pedido",
            "OC",
            "Sucursal",
            "Línea",
            "SKU",
            "Descripción",
            "Cant.",
            "P.U. Bruto",
            "Total Bruto",
            "Ajuste/NC",
            "Total Neto",
            "Costo Unit. Neto",
            "NC asociadas",
        ]
    ]
    for _, d in df_det.iterrows():
        ncs = list(d.get("NC", []) or [])
        if not ncs:
            nc_cell = "—"
        elif hay_aj and mapa_nc:
            dest = mapa_nc.get(ncs[0], 1)
            loc = "#'Ajustes_NC_NDB'!A" + str(dest)
            nc_cell = f'=HYPERLINK("{loc}","{", ".join(ncs)}")'
        else:
            nc_cell = ", ".join(ncs)
        mat.append(
            [
                str(d.get("DOC", "")),
                _fecha_obj(str(d.get("FECHA", ""))[:10]),
                str(d.get("PEDIDO", "") or ""),
                str(d.get("OC", "") or ""),
                str(d.get("SUCURSAL", "") or ""),
                str(d.get("COD_LINEA", "")),
                str(d.get("SKU", "")),
                str(d.get("ARTICULO", "") or ""),
                float(d.get("CANT", 0) or 0),
                None,  # PU bruto: fórmula más abajo
                round(float(d.get("BRUTO", 0) or 0), 2),
                None,  # Ajuste: SUMIFS o valor más abajo
                None,  # Neto: fórmula más abajo
                None,  # Costo neto: fórmula más abajo
                nc_cell,
            ]
        )
    n = len(mat) - 1
    r0, rlast, rtot = 3, 2 + n, 3 + n
    for i in range(n):
        rr = r0 + i
        fila = mat[1 + i]
        fila[9] = f'=IF(I{rr}=0,"—",K{rr}/I{rr})'
        if rango_ajustes is not None:
            a0, a1 = rango_ajustes
            fila[11] = (
                f"=SUMIFS({AJ}!$Q${a0}:$Q${a1},"
                f"{AJ}!$A${a0}:$A${a1},A{rr},"
                f"{AJ}!$C${a0}:$C${a1},G{rr})"
            )
        else:
            fila[11] = round(float(aj.get((fila[0], fila[6]), 0.0)), 2)
        fila[12] = f"=K{rr}+L{rr}"
        fila[13] = f'=IF(I{rr}=0,"—",M{rr}/I{rr})'
    mat.append(
        [
            "TOTAL",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            f"=SUBTOTAL(109,I{r0}:I{rlast})",
            "—",
            f"=SUBTOTAL(109,K{r0}:K{rlast})",
            f"=SUBTOTAL(109,L{r0}:L{rlast})",
            f"=SUBTOTAL(109,M{r0}:M{rlast})",
            "—",
            "",
        ]
    )
    r, r_hdr = _x_bloque(ws, mat, "#,##0.00", 1, st, subtitulo="DETALLADO POR (FACTURA, SKU)")
    for rr in range(r0, rtot + 1):
        ws.cell(row=rr, column=2).number_format = "dd-mm-yyyy"
        ws.cell(row=rr, column=9).number_format = "#,##0"
        for cc in (10, 11, 12, 13, 14):
            ws.cell(row=rr, column=cc).number_format = "#,##0.00"
    for col, w in zip(
        "ABCDEFGHIJKLMNO", (14, 11, 12, 14, 15, 8, 10, 30, 11, 12, 12, 12, 12, 14, 24)
    ):
        ws.column_dimensions[col].width = w
    _tabla_excel(ws, "FacturasDetalle", r_hdr, rlast, len(mat[0]))
    _x_fmt_total(ws, rtot, len(mat[0]), st)
    _x_cf(ws, f"M{r0}:M{rtot}", "lessThan")
    _x_print_header(ws, "COMPRAS NETAS — FACTURAS DETALLADAS", nombre, cid, ncols=15)
    _x_nota(
        ws,
        r + 1,
        len(mat[0]),
        "Una fila por (factura, SKU) con NC asociadas e hipervínculo "
        "a su fila en Ajustes. S/ y costos con signo (DEV/DESC "
        "negativos, NDB positivo). Cant. física; costos con guarda "
        '("—" si Cant. = 0). Actualizar = re-exportar el reporte. '
        "Soles sin IGV.",
    )


def _ajustes_fila(drow) -> tuple:
    """Fila del detalle de Ajustes: valores + Obs (lógica pura).

    Devuelve (celdas[19], obs_texto, tiene_obs). Reglas: Cant. = 0 en
    descuento/NDB (no mueven unidades); P.U. NC desde la base FAE;
    Cobertura/Afecta/P.U. neto solo para DESCUENTO (SÍ = FAE exacta
    a lo facturado → precio actualizado verificado; NO = puntual).
    """
    tipo = str(drow.get("TIPO", ""))
    motivo = _motivo(tipo)
    cant_fact = float(drow.get("CANT_FACT", 0) or 0)
    sfact = drow.get("SOLES_FACT", None)
    sfact = None if sfact is None else float(sfact)
    fae = abs(float(drow.get("FAE", 0) or 0))
    soles_nc = round(float(drow.get("SOLES_NC", 0) or 0), 2)
    cant_nc = float(drow.get("CANT_NC", 0) or 0)
    if tipo == "devolucion":
        cant_c = cant_nc
        pu_c = round(float(drow.get("PU_NC", 0) or 0), 5)
        if not pu_c and cant_c:
            pu_c = round(abs(soles_nc) / cant_c, 5)
    else:
        # Regla física estricta: descuento/NDB no mueven unidades
        # (la base FAE solo alimenta el P.U.).
        cant_c = 0
        pu_c = round(abs(soles_nc) / fae, 5) if fae else "—"
    pu_fact = round(float(drow.get("PU_FACT", 0) or 0), 5)
    obs = []
    if not cant_fact:
        obs.append("SKU no facturado")
        pu_fact = "—"
    if tipo != "devolucion" and not cant_nc and not fae:
        obs.append("Revisar: sin base")
    if tipo == "ajuste_valor" and fae and cant_fact and abs(fae - cant_fact) > 0.000001:
        obs.append("FAE ≠ cant. fact.")
    saldo = drow.get("SALDO", None)
    saldo_f = None if saldo is None or saldo != saldo else float(saldo)
    if saldo_f is not None and saldo_f < 0:
        obs.append("Saldo negativo")
    # Cobertura exacta (FAE == facturado): descuento total con
    # precio actualizado verificado; si no, puntual (NO).
    if tipo != "ajuste_valor" or not cant_fact:
        cobertura, afecta, pu_neto = "—", "—", "—"
    elif not fae:
        cobertura, afecta, pu_neto = 0.0, "NO", "—"
    elif abs(fae - cant_fact) <= 0.000001 and sfact is not None:
        cobertura = 1.0
        afecta = "SÍ"
        pu_neto = round((sfact - abs(soles_nc)) / cant_fact, 5)
    else:
        cobertura = round(fae / cant_fact, 4)
        afecta, pu_neto = "NO", "—"
    celdas = [
        str(drow.get("FACTURA", "")),
        _fecha_corta(drow.get("F_FACT", "")) or "—",
        str(drow.get("SKU", "")),
        str(drow.get("ARTICULO", ""))[:40],
        cant_fact,
        "—" if sfact is None else round(sfact, 2),
        str(drow.get("NC", "") or ""),
        _fecha_corta(drow.get("FECHA_DOC", "")),
        motivo,
        cant_c,
        fae,
        pu_fact,
        pu_c,
        cobertura,
        afecta,
        pu_neto,
        soles_nc,
        "" if saldo_f is None else saldo_f,
        "; ".join(obs),
    ]
    return celdas, "; ".join(obs), bool(obs)


def _x_ajustes_huerfanas(ws, df_huerf, st, r):
    """Bloque final 'NC SIN FACTURA (REVISAR)'. Retorna la fila libre."""
    mat = [
        [
            "NC",
            "F. doc",
            "Motivo",
            "Factura ref.",
            "SKU",
            "Artículo",
            "Cant.",
            "Base FAE",
            "S/ NC",
            "Obs.",
        ]
    ]
    for _, drow in df_huerf.iterrows():
        ref = str(drow.get("FACTURA", "") or "")
        obs = "Sin factura ref." if not ref else "Factura no encontrada"
        mat.append(
            [
                str(drow.get("NC", "") or ""),
                _fecha_corta(drow.get("FECHA_DOC", "")),
                _motivo(drow.get("TIPO", "")),
                ref or "—",
                str(drow.get("SKU", "")),
                str(drow.get("ARTICULO", "") or "")[:40],
                float(drow.get("CANT_NC", 0) or 0),
                abs(float(drow.get("FAE", 0) or 0)),
                round(float(drow.get("SOLES_NC", 0) or 0), 2),
                obs,
            ]
        )
    n = len(mat) - 1
    r, r_hdr = _x_bloque(ws, mat, "#,##0.00", r, st, subtitulo="NC SIN FACTURA (REVISAR)")
    for rr in range(r_hdr + 1, r_hdr + 1 + n):
        ws.cell(row=rr, column=7).number_format = "#,##0"
        ws.cell(row=rr, column=8).number_format = "#,##0"
        ws.cell(row=rr, column=9).number_format = "#,##0.00"
    _pintar_obs(ws, list(range(r_hdr + 1, r_hdr + 1 + n)), 10)
    for col, w in zip("ABCDEFGHIJ", (14, 12, 12, 14, 12, 30, 11, 11, 12, 22)):
        ws.column_dimensions[col].width = w
    _tabla_excel(ws, "AjustesHuerfanas", r_hdr, r - 1, 10)
    _x_cf(ws, f"I{r_hdr + 1}:I{r - 1}", "lessThan")
    _x_cf(ws, f"I{r_hdr + 1}:I{r - 1}", "greaterThan", color=_CF_VERDE)
    return r


def _x_ajustes_detalle(ws, df_dev, st, r):
    """Cabecera SUMIFS + detalle por (factura, SKU, documento).

    Cabecera: totales por motivo con SUMIFS sobre el detalle (S/ NC con
    signo) + neto de ajustes; el detalle se escribe abajo porque la
    cabecera lo referencia. Retorna (fila libre, (r0, rlast),
    mapa {nc_doc: fila}).
    """
    # Detalle primero en el cálculo (la cabecera lo referencia con
    # SUMIFS hacia abajo): sub en r+6, header en r+7.
    r_det_sub, r_det_hdr = r + 6, r + 7
    r0, rlast = r_det_hdr + 1, r_det_hdr + len(df_dev)
    cab = [
        "Factura",
        "F. fact.",
        "SKU",
        "Artículo",
        "Cant. fact.",
        "S/ fact.",
        "NC",
        "F. doc",
        "Motivo",
        "Cant.",
        "Base FAE",
        "P.U. fact.",
        "P.U. NC",
        "Cobertura",
        "Afecta precio",
        "P.U. neto",
        "S/ NC",
        "Saldo",
        "Obs.",
    ]
    mat = [cab]
    obs_det = []
    grupos = df_dev.drop_duplicates(subset=["FACTURA", "SKU"])
    tot_cant_fact = round(float(grupos["CANT_FACT"].sum()), 2)
    mapa_nc: dict = {}
    for j, (_, drow) in enumerate(df_dev.iterrows(), start=1):
        celdas, _obs, tiene = _ajustes_fila(drow)
        mat.append(celdas)
        if tiene:
            obs_det.append(len(mat) - 1)
        nc = str(drow.get("NC", "") or "")
        if nc:
            mapa_nc.setdefault(nc, r_det_hdr + j)
    rtot = rlast + 1
    mat.append(
        [
            "TOTAL",
            "",
            "",
            "",
            tot_cant_fact,
            "",
            "",
            "",
            "",
            f"=SUBTOTAL(109,J{r0}:J{rlast})",
            "—",
            "—",
            "—",
            "—",
            "—",
            "—",
            f"=SUBTOTAL(109,Q{r0}:Q{rlast})",
            f"=SUBTOTAL(109,R{r0}:R{rlast})",
            "",
        ]
    )
    # Cabecera por motivo (SUMIFS sobre el detalle, filas r+2..r+5).
    cab_m = [["Motivo", "S/"]]
    for i, mot in enumerate(("DEVOLUCIÓN", "DESCUENTO", "NDB")):
        rr = r + 2 + i
        cab_m.append([mot, f'=SUMIFS(Q{r0}:Q{rlast},I{r0}:I{rlast},"{mot}")'])
    cab_m.append(["NETO AJUSTES", f"=B{r + 2}+B{r + 3}+B{r + 4}"])
    _r1, _h1 = _x_bloque(ws, cab_m, "#,##0.00", r, st, subtitulo="TOTALES POR MOTIVO (S/)")
    _r2, r_hdr = _x_bloque(
        ws, mat, "#,##0.00", r_det_sub, st, subtitulo="DETALLE POR (FACTURA, SKU, DOCUMENTO)"
    )
    assert r_hdr == r_det_hdr, (r_hdr, r_det_hdr)
    for rr in range(r0, rtot + 1):
        ws.cell(row=rr, column=5).number_format = "#,##0"
        ws.cell(row=rr, column=10).number_format = "#,##0"
        ws.cell(row=rr, column=11).number_format = "#,##0"
        ws.cell(row=rr, column=12).number_format = "#,##0.00000"
        ws.cell(row=rr, column=13).number_format = "#,##0.00000"
        ws.cell(row=rr, column=14).number_format = "0.0%"
        ws.cell(row=rr, column=16).number_format = "#,##0.00000"
        ws.cell(row=rr, column=17).number_format = "#,##0.00"
        ws.cell(row=rr, column=18).number_format = "#,##0"
    for col, w in zip(
        "ABCDEFGHIJKLMNOPQRS",
        (14, 12, 12, 30, 11, 12, 14, 11, 12, 11, 11, 12, 12, 10, 12, 12, 12, 11, 22),
    ):
        ws.column_dimensions[col].width = w
    _tabla_excel(ws, "AjustesDetalle", r_hdr, rlast, len(cab))
    _x_fmt_total(ws, rtot, len(cab), st)
    _x_cf(ws, f"Q{r0}:Q{rtot}", "lessThan")
    _x_cf(ws, f"Q{r0}:Q{rtot}", "greaterThan", color=_CF_VERDE)
    _x_cf(ws, f"R{r0}:R{rtot}", "lessThan")
    _pintar_obs(ws, [r_hdr + i for i in obs_det], 19)
    return _r2 + 1, (r0, rlast), mapa_nc


def _x_ajustes(wb, df_dev, df_huerf, st, nombre, cid):
    """3. Ajustes NC/NDB: cabecera por motivo (SUMIFS) + detalle + huérfanas.

    La cabecera y el detalle viven en ``_x_ajustes_detalle`` y el bloque
    final "NC SIN FACTURA (REVISAR)" en ``_x_ajustes_huerfanas``. Nota
    al pie: signos, regla física de Cant., cobertura 100% = precio
    actualizado. Retorna ((r0, rlast) del detalle, mapa {nc_doc: fila})
    para el SUMIFS de Facturas, o (None, {}).
    """
    if (df_dev is None or df_dev.empty) and (df_huerf is None or df_huerf.empty):
        return None, {}
    ws = wb.create_sheet("Ajustes_NC_NDB")
    r = 1
    rango_det, mapa_nc = None, {}
    if df_dev is not None and not df_dev.empty:
        r, rango_det, mapa_nc = _x_ajustes_detalle(ws, df_dev, st, r)
    if df_huerf is not None and not df_huerf.empty:
        r = _x_ajustes_huerfanas(ws, df_huerf, st, r)
    _x_print_header(ws, "COMPRAS NETAS — AJUSTES NC/NDB", nombre, cid, ncols=19)
    _x_nota(
        ws,
        r + 1,
        19,
        "S/ NC con signo (DEV/DESC negativos, NDB positivo). Cant. = "
        "unidades físicas (0 en descuento/NDB: no mueven stock); "
        "Base FAE = base monetaria de la nota. Cobertura = FAE/Cant. "
        "fact.: 100% = descuento total con precio actualizado "
        "verificado (Afecta precio SÍ); si no, puntual (NO). "
        "Soles sin IGV.",
    )
    return rango_det, mapa_nc


def _escribir_xlsx(
    path,
    cid,
    nombre,
    *,
    df_hist=None,
    df_lineas=None,
    df_skus=None,
    df_comp_sku=None,
    df_dev=None,
    df_huerf=None,
    df_hechos=None,
    df_det=None,
    df_suc_pareto=None,
    df_suc_linea_mes=None,
    df_suc_sku_mes=None,
    corte_hasta="",
    incluir_nc=True,
    hojas=None,
    rango_desde="",
    rango_hasta="",
    ruc="",
    vendedor="",
    hojas_incluidas=None,
    total_hojas=6,
):
    """Escribe el paquete de hojas (hojas=None → todas).

    ``hojas`` es un set de claves ('resumen', 'consolidado',
    'comparativo', 'ajustes', 'bd', 'facturas', 'sucursales'). La opción
    sucursales escribe Pareto + tablas mensuales por sucursal×línea y
    sucursal×SKU. Las dos últimas tienen encabezados en fila 1, sin títulos
    ni totales dentro de la tabla. ``incluir_nc`` gobierna las métricas.
    fullCalcOnLoad: Excel recalcula al abrir (las fórmulas nunca se ven
    en blanco).
    Resumen se fuerza siempre que haya datos (portada con RUC + vendedor
    + chip de alcance). Las hojas operativas carecen de banner visible:
    el cliente va al print-header.
    """
    from openpyxl import Workbook

    wb = Workbook()
    wb.calculation.fullCalcOnLoad = True
    wb.properties.creator = "ccusi"
    wb.properties.lastModifiedBy = "ccusi"
    wb.properties.title = "Reporte de compras"
    wb.properties.description = "Generado por G360"
    st = _estilos()

    def _sel(*keys):
        return hojas is None or any(k in hojas for k in keys)

    if _sel("consolidado"):
        _x_consolidado(wb, df_lineas, df_skus, st, nombre, cid, fd=rango_desde, fh=rango_hasta)
    if _sel("comparativo"):
        _x_comparativo(
            wb,
            df_comp_sku,
            st,
            nombre,
            cid,
            _meses_parciales(rango_desde, rango_hasta),
            rango_desde,
            rango_hasta,
            corte_hasta=corte_hasta,
        )
    if _sel("sucursales"):
        _x_analisis_sucursales(wb, df_suc_pareto, df_suc_linea_mes, df_suc_sku_mes)
    rango_ajustes, mapa_nc = None, {}
    if _sel("ajustes"):
        res_aj = _x_ajustes(wb, df_dev, df_huerf, st, nombre, cid)
        if res_aj:
            rango_ajustes, mapa_nc = res_aj
    if _sel("bd"):
        _x_bd(wb, df_hechos, st, nombre, cid)
    if _sel("facturas"):
        _x_facturas(wb, df_det, df_dev, rango_ajustes, mapa_nc, st, nombre, cid)
    # Resumen como portada: se intenta siempre que haya datos.
    # Si no se puede construir (sin histórico o sin corte), se omite sin error.
    _puede_resumen = df_hist is not None and not df_hist.empty and corte_hasta
    if _sel("resumen") or _puede_resumen:
        ws_res = _x_resumen(
            wb,
            df_hist,
            corte_hasta,
            incluir_nc,
            st,
            nombre,
            cid,
            rango_desde,
            rango_hasta,
            ruc=ruc,
            vendedor=vendedor,
            hojas_incluidas=hojas,
            total_hojas=total_hojas,
        )
        if ws_res is not None:
            wb.move_sheet(ws_res, offset=-wb.index(ws_res))

    # Si no se pidió la primera hoja, la activa por defecto quedó vacía.
    if "Sheet" in wb.sheetnames:
        if len(wb.sheetnames) == 1:
            raise ValueError("sin datos para exportar")
        del wb["Sheet"]

    wb.save(str(path))
