# -*- coding: utf-8 -*-
"""Renderizador de reportes de auditoría y reporte histórico para SUNAT.

Genera reportes Excel con la marca CIPSA: encabezados formales, estadísticas
resumidas y el segmento histórico completo usado en el análisis. Sirve como
evidencia de soporte para auditorías de SUNAT.

El logo se almacena como PNG pre-convertido en assets/images/ (versionado en
git). Se eliminó la conversión SVG→PNG en runtime para evitar la dependencia
de cairosvg.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
from openpyxl import Workbook
from openpyxl.drawing.image import Image as XlImage
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from src.render.g360_styles import (
    EXP_NAVY,
    EXP_BORDER,
    EXP_GRAY,
    EXP_HIST_NOTA_BG,
    EXP_ACCENT,
    exp_fill,
)

# ── Branding ────────────────────────────────────────────────────────────────

CIPSA_LOGO_PATH = Path(__file__).parent.parent.parent / "assets" / "images" / "Logo_cipsa_solid.png"
HEADER_NAVY = EXP_NAVY
ACCENT_GREEN = EXP_ACCENT
BORDER_COLOR = EXP_BORDER
LIGHT_BG = "F8FAFC"
WHITE = "FFFFFF"

_HEADER_FONT = Font(name="Calibri", bold=True, size=11, color="FFFFFF")
_BODY_FONT = Font(name="Calibri", size=9, color="334155")
_SMALL_FONT = Font(name="Calibri", size=8, color="94A3B8")

_HEADER_FILL = exp_fill(EXP_NAVY)
_ACCENT_FILL = PatternFill(start_color=ACCENT_GREEN, end_color=ACCENT_GREEN, fill_type="solid")
_LIGHT_FILL = PatternFill(start_color=LIGHT_BG, end_color=LIGHT_BG, fill_type="solid")
_WHITE_FILL = PatternFill(start_color=WHITE, end_color=WHITE, fill_type="solid")

THIN_BORDER = Border(
    left=Side(style="thin", color=BORDER_COLOR),
    right=Side(style="thin", color=BORDER_COLOR),
    top=Side(style="thin", color=BORDER_COLOR),
    bottom=Side(style="thin", color=BORDER_COLOR),
)


def _normalizar_exp_id(expediente_id: str) -> str:
    """ID sin prefijo duplicado: 'EXP-DC-…' y 'DC-…' → 'DC-…'."""
    eid = str(expediente_id or "").strip()
    if eid[:4].upper() == "EXP-":
        return eid[4:]
    return eid


_OPERACION_LEGACY = {
    "diferencia_precio": "Diferencia de Precio (DC)",
    "descuento_precio": "Descuento Comercial (DO)",
    "anular_factura": "Anulación de Factura (ANF)",
}


def _etiqueta_operacion(tipo_operacion: str) -> str:
    """Etiqueta legible: clave legacy, código CATALOGO ('DC') o valor crudo."""
    if tipo_operacion in _OPERACION_LEGACY:
        return _OPERACION_LEGACY[tipo_operacion]
    try:
        from src.ui.catalog import CATALOGO  # noqa: E402 (lazy: evita ciclo de import)

        caso = CATALOGO.get(tipo_operacion)
        if caso is not None:
            return f"{caso.label} ({caso.codigo})"
    except Exception:
        pass
    return tipo_operacion


def _write_header_row(ws, row, cols_config):
    """Escribe la fila de encabezados de columna con formato estándar."""
    for col_idx, cfg in enumerate(cols_config, 1):
        c = ws.cell(row=row, column=col_idx, value=cfg["header"])
        c.font = _HEADER_FONT
        c.fill = _HEADER_FILL
        c.alignment = Alignment(
            horizontal=cfg.get("align", "left"), vertical="center", wrap_text=True
        )
        c.border = THIN_BORDER
        if cfg.get("width"):
            ws.column_dimensions[get_column_letter(col_idx)].width = cfg["width"]


def _write_data_rows(ws, start_row, df, cols_config):
    """Escribe las filas de datos alternando el color de fondo por fila."""
    for i, (_, row) in enumerate(df.iterrows()):
        r = start_row + i
        fill = _LIGHT_FILL if i % 2 == 0 else _WHITE_FILL
        for col_idx, cfg in enumerate(cols_config, 1):
            key = cfg.get("key", cfg["header"])
            val = row.get(key, "")
            if val is None:
                val = ""
            if isinstance(val, float):
                fmt = cfg.get("fmt")
                if fmt:
                    try:
                        text = fmt.format(val)
                    except (ValueError, TypeError):
                        text = f"{val}"
                else:
                    text = f"{val:,.2f}"
            elif isinstance(val, (int, float)) and cfg.get("pct"):
                text = f"{val:.2%}"
            elif isinstance(val, (int, float)) and cfg.get("currency"):
                text = f"S/ {val:,.2f}"
            else:
                text = str(val)[:60]

            c = ws.cell(row=r, column=col_idx, value=text)
            c.font = _BODY_FONT
            c.fill = fill
            c.border = THIN_BORDER
            c.alignment = Alignment(
                horizontal=cfg.get("align", "left"),
                vertical="center",
            )


def generar_historico_clasico(
    df_historial: pd.DataFrame,
    cliente_nombre: str = "",
    cliente_ruc: str = "",
    tipo_operacion: str = "",
    fecha_desde: Optional[str] = None,
    fecha_hasta: Optional[str] = None,
    vendedor: str = "",
    sort_mode: str = "fecha_desc",
    expediente_id: str = "",
    ruta_salida: Optional[Path] = None,
    reclamados: Optional[set] = None,
) -> Path:
    """Genera el reporte histórico en estilo clásico tipo ERP.

    Reproduce el aspecto sobrio de los reportes impresos del ERP legado
    creado con Gupta: cuadrícula blanco y negro, cabecera de consulta técnica
    (sin logos ni colores de marca), tabla simple y fila de totales. Sirve
    para el ``Historico.xlsx`` incluido dentro de cada expediente.

    Args:
        reclamados: conjunto de tuplas (factura_id, sku) incluidas en el
            Cálculo; esas filas se resaltan con relleno y leyenda.
    """
    from src.core.fechas import fecha_ui

    wb = Workbook()

    # ── Classic fonts / styles (legacy ERP look) ──
    CLASSIC = "Arial"
    TITLE_FONT = Font(name=CLASSIC, bold=True, size=12)
    SUB_FONT = Font(name=CLASSIC, bold=True, size=10)
    LABEL_FONT = Font(name=CLASSIC, bold=True, size=10)
    VALUE_FONT = Font(name=CLASSIC, size=10)
    HEADER_FONT = Font(name=CLASSIC, bold=True, size=10)
    BODY_FONT = Font(name=CLASSIC, size=10)
    TOT_FONT = Font(name=CLASSIC, bold=True, size=9)

    BLACK = "000000"
    thin = Border(
        left=Side(style="thin", color=BLACK),
        right=Side(style="thin", color=BLACK),
        top=Side(style="thin", color=BLACK),
        bottom=Side(style="thin", color=BLACK),
    )
    bottom_med = Border(bottom=Side(style="medium", color=BLACK))
    top_med = Border(top=Side(style="medium", color=BLACK))
    NOTA_FILL = exp_fill(EXP_HIST_NOTA_BG)
    # Relleno de líneas incluidas en el Cálculo (amarillo pálido, distinto
    # del gris de notas para no confundir con crédito/débito).
    RECLAMO_FILL = exp_fill("FFF2CC")

    today_str = fecha_ui(datetime.now())
    operacion_label = _etiqueta_operacion(tipo_operacion)
    reclamos = {(str(f).strip(), str(s).strip()) for f, s in (reclamados or set())}

    # ── Columnas de la cuadrícula (compartida por todas las hojas) ──
    hist_cols = [
        {"header": "FECHA EMISION", "key": "FECHA", "width": 13, "align": "center", "kind": "date"},
        {"header": "TIPO", "key": "TIPO_DOC", "width": 9, "align": "center"},
        {"header": "SERIE", "key": "SERIE", "width": 8, "align": "center"},
        {"header": "N. DOCUMENTO", "key": "NUMERO", "width": 12},
        {"header": "SKU", "key": "CODIGO", "width": 12, "align": "center"},
        {"header": "ARTICULO", "key": "ARTICULO", "width": 42},
        {"header": "CANTIDAD", "key": "CANTIDAD", "width": 11, "align": "right", "kind": "int"},
        {
            "header": "P. UNITARIO",
            "key": "PRECIO_UNITARIO",
            "width": 13,
            "align": "right",
            "kind": "price",
        },
        {"header": "SOLES", "key": "SOLES", "width": 13, "align": "right", "kind": "money"},
        {"header": "RUC CLIENTE", "key": "DOC_CLIENTE", "width": 14},
    ]
    data_start = 12

    def _escribir(ws, df, anio_hoja, periodo_str):
        """Escribe una hoja completa del histórico clásico.

        Todo lo que se muestra (resumen, totales, leyenda, print_area) se
        calcula desde `df`, que es el subconjunto de ESA hoja.
        """
        # ── Logo CIPSA: columna A, ocupando las filas 1-2 (1.80 × 2.00 cm) ──
        logo_path = CIPSA_LOGO_PATH if CIPSA_LOGO_PATH.exists() else None
        if logo_path:
            try:
                img = XlImage(str(logo_path))
                img.width, img.height = 68, 76  # 1.80 cm × 2.00 cm @96 dpi
                ws.add_image(img, "A1")
            except Exception:
                pass
        ws.row_dimensions[1].height = 28
        ws.row_dimensions[2].height = 28

        # ── Report title / subtitle: B1/B2 (columna A reservada para el logo) ──
        ws.merge_cells("B1:J1")
        c = ws.cell(row=1, column=2, value="REPORTE DE PRECIOS — SEGMENTO HISTÓRICO DEL ERP")
        c.font = TITLE_FONT
        c.alignment = Alignment(horizontal="left", vertical="center")

        ws.merge_cells("B2:J2")
        subtitulo = f"LISTADO DE DOCUMENTOS DEL CLIENTE  ·  {operacion_label}"
        if anio_hoja is not None:
            subtitulo = f"{subtitulo}  ·  AÑO {anio_hoja}"
        c = ws.cell(row=2, column=2, value=subtitulo)
        c.font = SUB_FONT
        c.alignment = Alignment(horizontal="left", vertical="center")

        # ── Query header (the classic filter block of the ERP report) ──
        metadata = [
            ("CLIENTE", cliente_nombre or "—"),
            ("RUC", cliente_ruc or "—"),
            ("PERIODO", periodo_str),
            ("OPERACION", operacion_label),
            ("VENDEDOR", vendedor or "—"),
            ("EXPEDIENTE", f"EXP-{_normalizar_exp_id(expediente_id)}" if expediente_id else "—"),
            ("CRITERIO DE ORDEN", sort_mode.replace("_", " ").title() if sort_mode else "—"),
            ("FECHA DE EXTRACCION", today_str),
        ]
        for i, (lbl, val) in enumerate(metadata):
            r = 3 + i
            c_lbl = ws.cell(row=r, column=1, value=f"{lbl}:")
            c_lbl.font = LABEL_FONT
            c_val = ws.cell(row=r, column=2, value=val)
            c_val.font = VALUE_FONT
            c_val.border = bottom_med

        # ── Resumen superior (fila 11, libre): conteo y totales ─────────
        from src.core.detector import _es_documento_nota  # noqa: E402 (lazy: evita ciclo de import)
        from src.core.fechas import a_fecha, excel_fmt_ui, fecha_ui

        n_hist = len(df)
        if "TIPO_CLASE" in df.columns:
            es_nota = df["TIPO_CLASE"].astype(str).str.strip().str.lower() != "factura"
        elif "TIPO_DOC" in df.columns:
            es_nota = df["TIPO_DOC"].apply(_es_documento_nota)
        else:
            es_nota = pd.Series(False, index=df.index)
        # OJO: lista posicional, alineada con las filas de ESTA hoja.
        nota_flags = list(es_nota)
        n_not = sum(1 for f in nota_flags if f)
        fac_rows = df[[not f for f in nota_flags]]
        if "DOC_ID" in fac_rows.columns:
            n_fac = int(fac_rows["DOC_ID"].astype(str).str.strip().nunique())
        elif {"TIPO_DOC", "SERIE", "NUMERO"} <= set(fac_rows.columns):
            n_fac = int(
                fac_rows[["TIPO_DOC", "SERIE", "NUMERO"]]
                .astype(str)
                .agg(lambda r: "-".join(v.strip() for v in r), axis=1)
                .nunique()
            )
        else:
            n_fac = len(fac_rows)
        tot_resumen = 0.0
        if "SOLES" in df.columns:
            tot_resumen = float(pd.to_numeric(df["SOLES"], errors="coerce").fillna(0).sum())
        ws.merge_cells("A11:J11")
        c = ws.cell(
            row=11,
            column=1,
            value=(
                f"RESUMEN: {n_hist} registro(s) · {n_fac} factura(s) · {n_not} nota(s) "
                f"· Total S/ {tot_resumen:,.2f}"
            ),
        )
        c.font = Font(name=CLASSIC, bold=True, size=9)
        c.alignment = Alignment(horizontal="left", vertical="center")
        nota_leyenda = " Filas sombreadas en gris: notas de crédito/débito." if n_not else ""
        n_reclamo = 0

        # ── Grid (la tabla inicia en la columna A) ──
        hdr_row = data_start
        for col_idx, cfg in enumerate(hist_cols, 1):
            c = ws.cell(row=hdr_row, column=col_idx, value=cfg["header"])
            c.font = HEADER_FONT
            c.alignment = Alignment(horizontal=cfg.get("align", "left"), vertical="center")
            c.border = thin
            ws.column_dimensions[get_column_letter(col_idx)].width = cfg["width"]

        # ¿Toda la columna FECHA es interpretable? Si sí se escriben fechas
        # reales (Excel las ordena y filtra); si hay alguna ilegible, la
        # columna cae a texto para no mezclar tipos.
        # Sin columna FECHA no hay nada que escribir como fecha: se queda en
        # texto (y no en None, que seria perder la celda).
        fechas_reales = False
        if "FECHA" in df.columns and len(df):
            from src.core.fechas import a_fecha as _a_fecha

            fechas_reales = all(
                _a_fecha(v) is not None for v in df["FECHA"].tolist()
            )

        r = hdr_row + 1
        tot_cant = 0.0
        tot_soles = 0.0
        n = len(df)
        for i, (_, row) in enumerate(df.iterrows()):
            es_nota_row = nota_flags[i] if i < len(nota_flags) else False
            es_reclamo = False
            if reclamos and not es_nota_row:
                fac_id = (
                    f"{str(row.get('TIPO_DOC', '')).strip()[:1]}"
                    f"{str(row.get('SERIE', '')).strip()}-"
                    f"{str(row.get('NUMERO', '')).strip()}"
                ).strip("-")
                es_reclamo = (fac_id, str(row.get("CODIGO", "")).strip()) in reclamos
                n_reclamo += 1 if es_reclamo else 0
            for col_idx, cfg in enumerate(hist_cols, 1):
                key = cfg.get("key", cfg["header"])
                val = row.get(key, "")
                c = ws.cell(row=r, column=col_idx)
                c.font = BODY_FONT
                c.border = thin
                c.alignment = Alignment(
                    horizontal=cfg.get("align", "left"),
                    vertical="center",
                    wrap_text=(key == "ARTICULO"),
                )
                if es_nota_row:
                    c.fill = NOTA_FILL
                elif es_reclamo:
                    c.fill = RECLAMO_FILL
                kind = cfg.get("kind")
                if kind == "date":
                    original = "" if val is None else str(val).strip()
                    if fechas_reales:
                        # Fecha real: el formato lo pone Excel, y así la
                        # columna ordena y filtra como fecha (era texto y no
                        # ordenaba).
                        c.value = a_fecha(val)
                        c.number_format = excel_fmt_ui()
                    else:
                        # Con alguna fecha ilegible se escribe texto: se ve
                        # igual y no se pierde el dato.
                        c.value = fecha_ui(val) or original
                elif kind == "int":
                    try:
                        fv = float(val)
                        tot_cant += fv
                        c.value = int(fv)
                        c.number_format = "#,##0"
                    except (TypeError, ValueError):
                        c.value = "" if val is None else str(val)
                elif kind == "price":
                    try:
                        c.value = float(val)
                        c.number_format = "#,##0.00000"
                    except (TypeError, ValueError):
                        c.value = "" if val is None else str(val)
                elif kind == "money":
                    try:
                        fv = float(val)
                        tot_soles += fv
                        c.value = fv
                        c.number_format = "#,##0.00"
                    except (TypeError, ValueError):
                        c.value = "" if val is None else str(val)
                else:
                    c.value = "" if val is None else str(val)[:60]
            r += 1

        # ── Totals row (classic double line) ──
        t_row = r
        c = ws.cell(row=t_row, column=6, value=f"TOTAL DE {n} REGISTRO(S)")
        c.font = TOT_FONT
        c.alignment = Alignment(horizontal="right")
        c.border = top_med
        for col_idx in range(1, 11):
            if col_idx != 6:
                ws.cell(row=t_row, column=col_idx).border = top_med
        c_qty = ws.cell(row=t_row, column=7, value=int(tot_cant))
        c_qty.font = TOT_FONT
        c_qty.number_format = "#,##0"
        c_qty.alignment = Alignment(horizontal="right")
        c_mon = ws.cell(row=t_row, column=9, value=round(tot_soles, 2))
        c_mon.font = TOT_FONT
        c_mon.number_format = "#,##0.00"
        c_mon.alignment = Alignment(horizontal="right")

        note_row = t_row + 2
        ws.merge_cells(f"A{note_row}:J{note_row}")
        leyenda = nota_leyenda
        if n_reclamo:
            leyenda += (
                f" Filas resaltadas en amarillo: líneas incluidas en el Cálculo "
                f"({n_reclamo} de {n})."
            )
        ws.cell(
            row=note_row,
            column=1,
            value=(
                "Datos extractados directamente del ERP, sin calculos realizados en esta hoja. "
                "Cruce el detalle con Calculo.xlsx e Informe.docx del expediente. "
                f"Generado por G360 NC Sustentor el {today_str}.{leyenda}"
            ),
        ).font = Font(name=CLASSIC, size=8, italic=True, color=EXP_GRAY)

        # ── Print setup ──
        # Columna A a 15.00 (~110 px). Márgenes laterales simétricos 0.8" (~2.0 cm).
        ws.column_dimensions["A"].width = 15
        ws.page_margins.left = 0.8
        ws.page_margins.right = 0.8
        ws.print_area = f"A1:J{t_row}"
        ws.page_setup.orientation = "landscape"
        ws.page_setup.fitToPage = True
        ws.page_setup.fitToWidth = 1
        ws.sheet_view.showGridLines = False
        # Cabecera fija: al partir por año las hojas son largas y sin esto se
        # pierde el encabezado al hacer scroll.
        ws.freeze_panes = f"A{hdr_row + 1}"

    # ── Hojas: una por año calendar ──
    # Con un solo año se mantiene la hoja única "Historico" de siempre.
    periodo_global = f"{fecha_desde or 'Sin definir'} al {fecha_hasta or 'Sin definir'}"
    grupos = _agrupar_por_anio(df_historial)
    # Con un solo año se deja la hoja como "Historico", igual que siempre: el
    # nombre por año solo tiene sentido cuando hay varias hojas.
    una_sola = len(grupos) == 1
    primera = True
    for etiqueta, sub, anio_hoja in grupos:
        ws = wb.active if primera else wb.create_sheet()
        primera = False
        ws.title = "Historico" if una_sola else etiqueta
        if anio_hoja is None:
            periodo = "Sin fecha interpretable"
        elif len(grupos) == 1:
            periodo = periodo_global
        else:
            # En la hoja de un año el periodo es el de ESE año, no el global:
            # si no, cada hoja repetiría el rango completo y no se sabría qué
            # contiene.
            fechas = [d for d in (_a_fecha(v) for v in sub["FECHA"]) if d is not None]
            periodo = (
                f"{fecha_ui(min(fechas))} al {fecha_ui(max(fechas))}" if fechas else str(anio_hoja)
            )
        # En la hoja única no se marca el año: el nombre de la hoja ya lo dice
        # y el caso de siempre tiene que quedar igual que antes.
        _escribir(ws, sub, None if una_sola else anio_hoja, periodo)

    if ruta_salida is not None:
        out_path = Path(ruta_salida)
        out_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        out_dir = Path(__file__).parent.parent.parent / "_e2e_output"
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = out_dir / f"Historico_{expediente_id or ts}.xlsx"
    wb.save(str(out_path))
    return out_path


# ── Partición por año ─────────────────────────────────────────────────────────
# Excel limita el nombre de hoja a 31 caracteres y prohíbe []:*?/\. "2026" y
# "Sin fecha" entran de sobra, pero el sanitizado se hace igual por si algún
# día la etiqueta cambia.
_INVALIDOS_SHEET = set(r"[]:*?/\'\"")
_SIN_ANIO = "Sin fecha"


def _nombre_seguro(texto: str, usados: set) -> str:
    limpio = "".join(" " if ch in _INVALIDOS_SHEET else ch for ch in str(texto)).strip()
    limpio = limpio[:31] or "Hoja"
    cand = limpio
    n = 2
    while cand in usados:
        sufijo = f" ({n})"
        cand = limpio[: 31 - len(sufijo)] + sufijo
        n += 1
    usados.add(cand)
    return cand


def _agrupar_por_anio(df: pd.DataFrame):
    """[(nombre_hoja, subconjunto, anio_o_None)] ordenado por año.

    Las filas sin fecha interpretable van a su propia hoja en vez de
    repartirse: descartarlas perdería histórico, y mezclarlas en un año
    cualquiera haría el RESUMEN mentiroso.
    """
    if df is None or len(df) == 0:
        return [("Historico", df if df is not None else pd.DataFrame(), None)]

    if "FECHA" not in df.columns:
        return [("Historico", df, None)]

    from src.core.fechas import anio_de

    anios = df["FECHA"].map(anio_de)
    sin_anio = anios.isna()
    partes = []
    usados: set = set()
    for anio in sorted(a for a in anios.dropna().unique()):
        sub = df[anios == anio]
        partes.append((_nombre_seguro(str(int(anio)), usados), sub, int(anio)))
    if bool(sin_anio.any()):
        partes.append((_nombre_seguro(_SIN_ANIO, usados), df[sin_anio], None))
    return partes


def _a_fecha(valor):
    from src.core.fechas import a_fecha

    return a_fecha(valor)
