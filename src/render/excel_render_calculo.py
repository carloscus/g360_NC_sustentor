# -*- coding: utf-8 -*-
"""Mixin de escritura de las hojas 'Calculo' y 'Detalle Descuentos' de Excel.

Extraído de src/render/excel_renderer.py: ``_escribir_calculo`` (individual),
``_escribir_calculo_dual`` (dos tablas) y ``_crear_detalle_descuentos``.
Solo dependen de tres globales del módulo padre (COLUMNAS_POR_TIPO,
TOTAL_NC_KEYS, HEADER_NAVY), que se importan de forma perezosa dentro de
cada método para evitar el ciclo de import (excel_renderer importa este
módulo a nivel de módulo).
"""

from __future__ import annotations

import re

from openpyxl.styles import Alignment, Border, Side, Font, PatternFill
from openpyxl.utils import get_column_letter

from src.render.g360_styles import G360Styles
from src.render.g360_styles import (
    EXP_NAVY,
    EXP_BORDER,
    EXP_BORDER_STRONG,
    EXP_LABEL_BG,
    EXP_WHITE,
    EXP_INK_SOFT,
    EXP_ERR_TX,
    EXP_EDIT_BG,
    EXP_HIGHLIGHT_BG,
    EXP_NOTE_BG,
    EXP_NOTE_BD,
    EXP_NOTE_TX,
    exp_fill,
)


class _CalculoWriter:
    """Métodos de las hojas de cálculo numérico (mixin de ExcelRenderer)."""

    def _fuente_base(self, cell):
        """Normaliza la celda de datos a Calibri 10 (conserva énfasis y color)."""
        f = cell.font
        cell.font = Font(
            name=f.name or "Calibri",
            size=10,
            bold=bool(f.bold),
            italic=bool(f.italic),
            color=f.color,
        )

    @staticmethod
    def _resolver_expr_formula(expr, col_defs, r, data_start, data_end):
        """Resuelve una formula_expr con referencias por nombre de encabezado.

        Sintaxis: {HEADER} → letra+fila (ej. {CANTIDAD} → F23);
        {SUM:HEADER} → SUM(letra$ini:letra$fin) sobre el rango de datos.
        Retorna None si algún encabezado no existe (el llamador cae a valor).
        """
        letters = {}
        for idx, cd in enumerate(col_defs):
            h = cd.get("header")
            if h and h not in letters:
                letters[h] = get_column_letter(idx + 1)

        def _rep_sum(m):
            h = m.group(1)
            if h not in letters:
                raise KeyError(h)
            col = letters[h]
            return f"SUM({col}{data_start}:{col}{data_end})"

        def _rep_cell(m):
            h = m.group(1)
            if h not in letters:
                raise KeyError(h)
            return f"{letters[h]}{r}"

        try:
            out = re.sub(r"\{SUM:([^{}]+)\}", _rep_sum, expr)
            out = re.sub(r"\{([^{}]+)\}", _rep_cell, out)
        except KeyError:
            return None
        return out

    def _escribir_calculo(
        self, df, tipo, alertas, start_row, desc_origen=None, modalidad="individual"
    ):
        from src.render.excel_renderer import (
            COLUMNAS_POR_TIPO,
            TOTAL_NC_KEYS,
            HEADER_NAVY,
            columnas_consolidadas_dc,
            TIPOS_CONSOLIDADOS_POR_SKU,
        )  # noqa: E402

        ws = self.ws
        col_defs = COLUMNAS_POR_TIPO.get(tipo, COLUMNAS_POR_TIPO["diferencia_precio"])
        if tipo in TIPOS_CONSOLIDADOS_POR_SKU and modalidad == "consolidado":
            col_defs = columnas_consolidadas_dc(col_defs)
        if isinstance(col_defs, dict):
            return self._escribir_calculo_dual(
                df, col_defs, alertas, start_row, desc_origen=desc_origen
            )

        gs = G360Styles()
        header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
        header_border = Border(
            left=Side(style="thin", color=EXP_BORDER_STRONG),
            right=Side(style="thin", color=EXP_BORDER_STRONG),
            top=Side(style="thin", color=EXP_BORDER_STRONG),
            bottom=Side(style="thin", color=EXP_BORDER_STRONG),
        )
        thin_border = Border(
            left=Side(style="thin", color=EXP_BORDER),
            right=Side(style="thin", color=EXP_BORDER),
            top=Side(style="thin", color=EXP_BORDER),
            bottom=Side(style="thin", color=EXP_BORDER),
        )
        cell_bg_white = PatternFill(start_color=EXP_WHITE, end_color=EXP_WHITE, fill_type="solid")

        # Detectar columnas DESC del dataframe para construir cadena de descuentos
        # Usar nombres reales de columnas del dataframe (DESC1, DESC2, o DESCUENTO_COMPUESTO, etc.)
        desc_std = sorted(
            [
                c
                for c in df.columns
                if re.match(r"^DESC\d+$", c) and not (df[c].fillna(0) == 0).all()
            ],
            key=lambda c: int(re.search(r"\d+", c).group()),
        )
        # Mapear nombres alternativos del strategy a formato estandar para formulas
        desc_alias_map = {}
        for alias_col, std_name in {
            "DESCUENTO_COMPUESTO": "DESC1",
            "%_DESCUENTO": "DESC1",
            "DESC_REQ": "DESC1",
        }.items():
            if alias_col in df.columns and not (df[alias_col].fillna(0) == 0).all():
                if std_name not in desc_std:
                    desc_std.append(std_name)
                desc_alias_map[std_name] = alias_col
        desc_cols = desc_std

        r = start_row

        # --- FILA DE ENCABEZADO DE SECCIONES ---
        section_groups = []
        current_sec = None
        group_start = 1
        for i, cd in enumerate(col_defs, 1):
            sec = cd.get("section", "")
            if sec != current_sec:
                if current_sec:
                    section_groups.append((current_sec, group_start, i - 1))
                current_sec = sec
                group_start = i
        if current_sec:
            section_groups.append((current_sec, group_start, len(col_defs)))

        has_sections = bool(section_groups) and bool(section_groups[0][0])
        if has_sections:
            section_fills = {
                "GENERAL": PatternFill(
                    start_color=HEADER_NAVY, end_color=HEADER_NAVY, fill_type="solid"
                ),
                "SE ATENDIÓ": PatternFill(
                    start_color=HEADER_NAVY, end_color=HEADER_NAVY, fill_type="solid"
                ),
                "SE DEBIÓ ATENDER": PatternFill(
                    start_color=HEADER_NAVY, end_color=HEADER_NAVY, fill_type="solid"
                ),
                "RESOLUCIÓN": PatternFill(
                    start_color=HEADER_NAVY, end_color=HEADER_NAVY, fill_type="solid"
                ),
            }
            for sec_name, sc, ec in section_groups:
                cell = ws.cell(row=r, column=sc, value=sec_name)
                cell.font = Font(bold=True, size=10, color=EXP_WHITE)
                cell.fill = section_fills.get(sec_name, gs.header_fill)
                cell.alignment = header_align
                cell.border = header_border
                if ec > sc:
                    ws.merge_cells(start_row=r, start_column=sc, end_row=r, end_column=ec)
            r += 1

        # --- FILA DE ENCABEZADO DE COLUMNAS ---
        col_header_fills = {
            "GENERAL": PatternFill(
                start_color=HEADER_NAVY, end_color=HEADER_NAVY, fill_type="solid"
            ),
            "SE ATENDIÓ": PatternFill(
                start_color=HEADER_NAVY, end_color=HEADER_NAVY, fill_type="solid"
            ),
            "SE DEBIÓ ATENDER": PatternFill(
                start_color=HEADER_NAVY, end_color=HEADER_NAVY, fill_type="solid"
            ),
            "RESOLUCIÓN": PatternFill(
                start_color=HEADER_NAVY, end_color=HEADER_NAVY, fill_type="solid"
            ),
        }
        for i, col_def in enumerate(col_defs, 1):
            c = ws.cell(row=r, column=i, value=col_def["header"])
            sec = col_def.get("section", "")
            c.font = gs.header_font
            c.fill = col_header_fills.get(sec, gs.header_fill)
            c.alignment = header_align
            c.border = header_border
            if "width" in col_def:
                ws.column_dimensions[get_column_letter(i)].width = col_def["width"]

        ws.freeze_panes = f"A{r + 1}"
        data_start = r + 1
        r = data_start

        section_cell_fills = {
            "GENERAL": PatternFill(
                start_color=EXP_LABEL_BG, end_color=EXP_LABEL_BG, fill_type="solid"
            ),
            "SE ATENDIÓ": PatternFill(
                start_color=EXP_LABEL_BG, end_color=EXP_LABEL_BG, fill_type="solid"
            ),
            "SE DEBIÓ ATENDER": PatternFill(
                start_color=EXP_LABEL_BG, end_color=EXP_LABEL_BG, fill_type="solid"
            ),
            "RESOLUCIÓN": PatternFill(
                start_color=EXP_LABEL_BG, end_color=EXP_LABEL_BG, fill_type="solid"
            ),
        }
        for row_idx, (_, row) in enumerate(df.iterrows()):
            for col_idx, col_def in enumerate(col_defs):
                cell = ws.cell(row=r, column=col_idx + 1)
                cell.border = thin_border
                self._fuente_base(cell)
                sec = col_def.get("section", "")
                cell.fill = section_cell_fills.get(sec, cell_bg_white)

                if col_def.get("index"):
                    cell.value = row_idx + 1
                    cell.alignment = Alignment(horizontal="center")
                elif col_def["header"] == "DESC. APLICADOS":
                    parts = []
                    for dc in desc_cols:
                        val = row.get(dc)
                        pct = float(val) if val and str(val) not in ("", "nan") else 0
                        if pct > 0:
                            parts.append(f"{pct * 100:.0f}%")
                    cell.value = " + ".join(parts) if parts else "—"
                    cell.alignment = Alignment(horizontal="left")
                elif col_def.get("formula"):
                    expr = col_def.get("formula_expr")
                    monto_estatico = None
                    if expr and col_def["header"] == "MONTO":
                        # En consolidado el motor deja MONTO_EXACTO (suma exacta
                        # de las líneas del SKU) y se escribe estático: la
                        # fórmula DIF × CANTIDAD usaría la moda y descuadraría
                        # el total contra el informe (mismo criterio que DC).
                        _monto_exacto = row.get("MONTO_EXACTO")
                        if _monto_exacto is not None and str(_monto_exacto) not in (
                            "",
                            "nan",
                            "None",
                        ):
                            try:
                                monto_estatico = round(float(_monto_exacto), 2)
                            except (TypeError, ValueError):
                                monto_estatico = 0
                    if monto_estatico is not None:
                        cell.value = monto_estatico
                    elif expr:
                        # Hoja viva: formula_expr con {HEADER} resuelto a celda.
                        # Si un encabezado no existe, cae al valor del motor.
                        data_end = data_start + max(len(df) - 1, 0)
                        resolved = self._resolver_expr_formula(
                            expr, col_defs, r, data_start, data_end
                        )
                        if resolved is not None:
                            cell.value = f"={resolved}"
                        else:
                            cell.value = self._obtener_valor(
                                row, *col_def.get("keys", []), default=0
                            )
                    elif col_def["header"] == "TOTAL FACTURA":
                        cant_col = None
                        pcol = None
                        for ci, cd in enumerate(col_defs):
                            if cd.get("header") == "CANTIDAD":
                                cant_col = get_column_letter(ci + 1)
                            if cd.get("header") == "PRECIO UNID.":
                                pcol = get_column_letter(ci + 1)
                        if cant_col and pcol:
                            cell.value = f"=ROUND({cant_col}{r}*{pcol}{r},2)"
                        else:
                            cell.value = 0
                    elif col_def["header"] == "DIF. UNITARIA":
                        hist_col = None
                        lista_col = None
                        for ci, cd in enumerate(col_defs):
                            if cd.get("header") == "PRECIO UNID.":
                                hist_col = get_column_letter(ci + 1)
                            if cd.get("header") == "PRECIO NETO":
                                lista_col = get_column_letter(ci + 1)
                        if hist_col and lista_col:
                            cell.value = f"=MAX(0,ROUND({hist_col}{r}-{lista_col}{r},5))"
                        else:
                            cell.value = 0
                    elif col_def["header"] == "DIF. TOTAL":
                        cant_col = None
                        dif_col = None
                        for ci, cd in enumerate(col_defs):
                            if cd.get("header") == "CANTIDAD":
                                cant_col = get_column_letter(ci + 1)
                            if cd.get("header") == "DIF. UNITARIA":
                                dif_col = get_column_letter(ci + 1)
                        if cant_col and dif_col:
                            cell.value = f"=ROUND({dif_col}{r}*{cant_col}{r},2)"
                        else:
                            cell.value = self._obtener_valor(
                                row, *TOTAL_NC_KEYS.values(), default=0
                            )
                    elif col_def["header"] == "PRECIO NETO":
                        lista_col = None
                        desc_col = None
                        for ci, cd in enumerate(col_defs):
                            if cd.get("header") == "PRECIO LISTA":
                                lista_col = get_column_letter(ci + 1)
                            if cd.get("header") == "% DESC. COMP.":
                                desc_col = get_column_letter(ci + 1)
                        if lista_col and desc_col:
                            cell.value = f"=ROUND({lista_col}{r}*(1-{desc_col}{r}),5)"
                        else:
                            cell.value = self._obtener_valor(
                                row, *col_def.get("keys", []), default=0
                            )
                    else:
                        cell.value = 0
                elif col_def.get("link"):
                    target = row_idx + 2
                    cell.hyperlink = f"#'Detalle Descuentos'!A{target}"
                    cell.value = "↳"
                    cell.font = Font(color="0000FF", underline="single", size=10)
                    cell.alignment = Alignment(horizontal="center")
                elif col_def.get("editable"):
                    cell.value = None
                else:
                    keys = col_def.get("keys", [])
                    if col_def["header"] == "ARTICULO" and keys:
                        val = str(row.get(keys[0], ""))[:40]
                    elif col_def["header"] == "FACTURA" and keys:
                        val = str(row.get(keys[0], ""))
                    elif col_def["header"] == "ALERTA" and keys:
                        val = self._alerta_con_refs(row, row.get(keys[0], "OK"), vacio="OK")
                    elif col_def["header"] == "GLOSA" and keys:
                        val = self._alerta_con_refs(row, row.get(keys[0], ""), vacio="")
                    elif col_def["header"] == "REGLA":
                        ciclos = self._obtener_valor(row, "CICLOS", default=0)
                        val = f"{int(ciclos)} ciclos" if ciclos else ""
                    elif col_def["header"] == "BONIFICACION":
                        val = int(self._obtener_valor(row, "BONIFICACION", default=0))
                    elif col_def["header"] in ("CANTIDAD", "CANT. FACTURADA", "CANT. DISPONIBLE"):
                        # Columnas de cobertura (VRS/FPE): numéricas, no texto
                        # (si se escribieran como texto el SUM del pie y las
                        # comparaciones de la hoja no las ringan).
                        val = self._obtener_valor(row, *keys, default=0)
                    elif col_def["header"] in (
                        "COMPRA TOTAL",
                        "META",
                        "MONTO REBATE",
                        "MONTO FACTURA",
                    ):
                        val = self._obtener_valor(row, *keys, default=0)
                    elif col_def["header"] in (
                        "PRECIO HIST.",
                        "PRECIO LISTA",
                        "PRECIO UNIT.",
                        "PRECIO FACT.",
                        "PRECIO RECONOC.",
                        "PRECIO APLICABLE",
                        "PRECIO UNID.",
                        "PRECIO NETO",
                        "PRECIO ATENDIDO",
                        "DIF. UNITARIA",
                        "MONTO",
                    ):
                        val = self._obtener_valor(row, *keys, default=0)
                    elif col_def["header"] in (
                        "% DESCUENTO",
                        "% REBATE",
                        "DESC1",
                        "DESC2",
                        "% DESC.",
                        "% DESC. COMP.",
                        "% STOCK REST.",
                    ):
                        val = self._obtener_valor(row, *keys, default=0)
                    else:
                        val = str(row.get(keys[0], "")) if keys else ""
                    cell.value = val

                if col_def.get("format"):
                    cell.number_format = col_def["format"]
                if col_def.get("center"):
                    cell.alignment = Alignment(horizontal="center")
                elif col_def["header"] in (
                    "ARTICULO",
                    "CLIENTE",
                    "LINEA",
                    "FACTURA",
                    "ALERTA",
                    "GLOSA",
                ):
                    cell.alignment = Alignment(horizontal="left", wrap_text=True, vertical="top")

                if col_def.get("highlight"):
                    cell.fill = exp_fill(EXP_HIGHLIGHT_BG)
                    cell.font = gs.alert_font

                if col_def["header"] == "AUDITORÍA":
                    cell.alignment = Alignment(horizontal="left", wrap_text=True, vertical="top")

                if col_def["header"] == "ALERTA":
                    keys = col_def.get("keys", [])
                    self._color_estado(
                        cell, self._estado(str(row.get(keys[0], "")) if keys else "")
                    )

            r += 1

        last_data_row = r - 1
        if last_data_row >= data_start:
            monto_nc_col_idx = None
            for ci, cd in enumerate(col_defs):
                if cd.get("highlight"):
                    monto_nc_col_idx = ci + 1
                    break

            if monto_nc_col_idx:
                col_letter = get_column_letter(monto_nc_col_idx)
                # Totales del header como formulas SUM vivas (se recalculan si
                # el usuario elimina filas de la tabla).
                self._fijar_totales_formulas(col_letter, data_start, last_data_row)
                label_col = monto_nc_col_idx - 1 if monto_nc_col_idx > 1 else 1
                medium_border = Border(
                    left=Side(style="medium", color=EXP_BORDER_STRONG),
                    right=Side(style="medium", color=EXP_BORDER_STRONG),
                    top=Side(style="medium", color=EXP_BORDER_STRONG),
                    bottom=Side(style="medium", color=EXP_BORDER_STRONG),
                )

                r_sub = r + 1
                ws.cell(row=r_sub, column=label_col, value="Subtotal (Sin IGV):").font = Font(
                    bold=True, size=10
                )
                ws.cell(row=r_sub, column=label_col).alignment = Alignment(horizontal="right")
                # Referencia directa al header — si el usuario elimina filas, el header se mantiene
                sub_cell = ws.cell(
                    row=r_sub,
                    column=monto_nc_col_idx,
                    value=f"=${get_column_letter(self._hdr_sub_col)}${self._hdr_sub_row}",
                )
                sub_cell.number_format = self.fmt_currency
                sub_cell.font = Font(bold=True, size=10)
                sub_cell.border = medium_border

                r_igv = r_sub + 1
                ws.cell(row=r_igv, column=label_col, value="IGV (18%):").font = Font(
                    bold=True, size=10
                )
                ws.cell(row=r_igv, column=label_col).alignment = Alignment(horizontal="right")
                igv_cell = ws.cell(
                    row=r_igv,
                    column=monto_nc_col_idx,
                    value=f"=ROUND(${get_column_letter(self._hdr_sub_col)}${self._hdr_sub_row}*0.18,2)",
                )
                igv_cell.number_format = self.fmt_currency
                igv_cell.font = Font(bold=True, size=10)
                igv_cell.border = medium_border

                r_tot = r_igv + 1
                ws.cell(row=r_tot, column=label_col, value="TOTAL (con IGV):").font = Font(
                    bold=True, size=11, color=EXP_NAVY
                )
                ws.cell(row=r_tot, column=label_col).alignment = Alignment(horizontal="right")
                total_cell = ws.cell(
                    row=r_tot,
                    column=monto_nc_col_idx,
                    value=f"=ROUND(${get_column_letter(self._hdr_sub_col)}${self._hdr_sub_row}+${get_column_letter(self._hdr_igv_col)}${self._hdr_igv_row},2)",
                )
                total_cell.number_format = self.fmt_currency
                total_cell.font = Font(bold=True, color=EXP_ERR_TX, size=12)
                total_cell.border = medium_border

        return r

    def _alerta_con_refs(self, row, base, vacio="OK"):
        """Texto de ALERTA/GLOSA con documentos relacionados plegados.

        La columna NC_EXISTENTE (notas ya emitidas contra la factura de la
        fila) se anexa al final para no crear una columna dedicada.
        """
        txt = str(base if base is not None else "").strip()
        nc = str(row.get("NC_EXISTENTE", "") or "").strip()
        if nc and nc.lower() not in ("nan", "none"):
            if not txt or txt == "OK":
                return f"Docs relacionados: {nc}"
            return f"{txt} | Docs relacionados: {nc}"
        return txt if txt and txt.lower() not in ("nan", "none") else vacio

    def _escribir_calculo_dual(self, df, col_defs, alertas, start_row, desc_origen=None):
        from src.render.excel_renderer import HEADER_NAVY  # noqa: E402

        ws = self.ws
        gs = G360Styles()
        header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
        header_border = Border(
            left=Side(style="thin", color=EXP_BORDER_STRONG),
            right=Side(style="thin", color=EXP_BORDER_STRONG),
            top=Side(style="thin", color=EXP_BORDER_STRONG),
            bottom=Side(style="thin", color=EXP_BORDER_STRONG),
        )
        thin_border = Border(
            left=Side(style="thin", color=EXP_BORDER),
            right=Side(style="thin", color=EXP_BORDER),
            top=Side(style="thin", color=EXP_BORDER),
            bottom=Side(style="thin", color=EXP_BORDER),
        )

        desc_std = sorted(
            [
                c
                for c in df.columns
                if re.match(r"^DESC\d+$", c) and not (df[c].fillna(0) == 0).all()
            ],
            key=lambda c: int(re.search(r"\d+", c).group()),
        )
        desc_alias_map = {}
        for alias_col, std_name in {
            "DESCUENTO_COMPUESTO": "DESC1",
            "%_DESCUENTO": "DESC1",
            "DESC_REQ": "DESC1",
        }.items():
            if alias_col in df.columns and not (df[alias_col].fillna(0) == 0).all():
                if std_name not in desc_std:
                    desc_std.append(std_name)
                desc_alias_map[std_name] = alias_col
        desc_cols = desc_std if not df.empty else []

        r = start_row

        # --- TABLE 1: COMO SE ATENDIÓ ---
        t1_cols = col_defs["table1"]["columns"]
        t1_title = f"1.  {col_defs['table1']['title']}"

        # Title row
        ws.cell(row=r, column=1, value=t1_title).font = Font(bold=True, size=11, color=EXP_WHITE)
        ws.cell(row=r, column=1).fill = PatternFill(
            start_color=HEADER_NAVY, end_color=HEADER_NAVY, fill_type="solid"
        )
        ws.cell(row=r, column=1).alignment = header_align
        ws.cell(row=r, column=1).border = header_border
        if len(t1_cols) > 1:
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=len(t1_cols))
        r += 1

        # Header row
        for i, cd in enumerate(t1_cols, 1):
            c = ws.cell(row=r, column=i, value=cd["header"])
            c.font = gs.header_font
            c.fill = PatternFill(start_color=HEADER_NAVY, end_color=HEADER_NAVY, fill_type="solid")
            c.alignment = header_align
            c.border = header_border
            if "width" in cd:
                ws.column_dimensions[get_column_letter(i)].width = cd["width"]

        t1_data_start = r + 1
        r = t1_data_start

        # Data rows
        for row_idx, (_, row) in enumerate(df.iterrows()):
            for col_idx, cd in enumerate(t1_cols):
                cell = ws.cell(row=r, column=col_idx + 1)
                cell.border = thin_border
                self._fuente_base(cell)

                if cd.get("index"):
                    cell.value = row_idx + 1
                    cell.alignment = Alignment(horizontal="center")
                elif cd.get("formula"):
                    if cd["header"] == "TOTAL FACTURA":
                        _total_exacto = row.get("TOTAL_FACTURA_EXACTO")
                        if _total_exacto is not None and str(_total_exacto) not in (
                            "",
                            "nan",
                            "None",
                        ):
                            # Consolidado: total de soles por suma exacta de líneas.
                            try:
                                cell.value = round(float(_total_exacto), 2)
                            except (TypeError, ValueError):
                                cell.value = 0
                        else:
                            cant_col_letter = None
                            pcol_letter = None
                            for ci, cd2 in enumerate(t1_cols):
                                if cd2.get("header") == "CANTIDAD":
                                    cant_col_letter = get_column_letter(ci + 1)
                                if cd2.get("header") == "PRECIO UNID.":
                                    pcol_letter = get_column_letter(ci + 1)
                            if cant_col_letter and pcol_letter:
                                cell.value = f"=ROUND({cant_col_letter}{r}*{pcol_letter}{r},2)"
                            else:
                                cell.value = 0
                    else:
                        cell.value = 0
                else:
                    keys = cd.get("keys", [])
                    if cd["header"] == "ARTICULO" and keys:
                        val = str(row.get(keys[0], ""))[:40]
                    elif cd["header"] in ("SKU", "FACTURA", "FACTURAS") and keys:
                        val = str(row.get(keys[0], ""))
                    elif cd["header"] == "CANTIDAD":
                        val = int(self._obtener_valor(row, *keys, default=0))
                    else:
                        val = (
                            self._obtener_valor(row, *keys, default=0)
                            if keys
                            else (str(row.get(keys[0], "")) if keys else "")
                        )
                    cell.value = val

                if cd.get("format"):
                    cell.number_format = cd["format"]
                if cd.get("center"):
                    cell.alignment = Alignment(horizontal="center")
                elif cd["header"] in ("ARTICULO", "FACTURAS"):
                    cell.alignment = Alignment(horizontal="left", wrap_text=True, vertical="top")
                if cd.get("erp_input"):
                    cell.fill = PatternFill(
                        start_color=EXP_EDIT_BG, end_color=EXP_EDIT_BG, fill_type="solid"
                    )

            r += 1

        # --- Gap between tables: calculo explicativo ---
        r += 1
        gs = G360Styles()
        calc_fill = PatternFill(start_color=EXP_NOTE_BG, end_color=EXP_NOTE_BG, fill_type="solid")
        calc_border = Border(
            left=Side(style="thin", color=EXP_NOTE_BD),
            right=Side(style="thin", color=EXP_NOTE_BD),
            top=Side(style="thin", color=EXP_NOTE_BD),
            bottom=Side(style="thin", color=EXP_NOTE_BD),
        )
        # Pre-calc max cols for merge (t2_cols not defined yet, use t1_cols + buffer)
        max_cols_for_merge = len(t1_cols) + 6
        ws.cell(
            row=r,
            column=1,
            value=(
                "CÁLCULO:  DIF. UNITARIA = PRECIO HISTÓRICO EFECTIVO − PRECIO NETO  |  "
                "MONTO = DIF. UNITARIA × CANTIDAD ELEGIBLE"
            ),
        ).font = Font(size=9, italic=True, color=EXP_NOTE_TX)
        ws.cell(row=r, column=1).fill = calc_fill
        ws.cell(row=r, column=1).border = calc_border
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=max_cols_for_merge)
        r += 2

        # --- TABLE 2: LISTA DE PRECIOS ---
        t2_base = col_defs["table2"]["columns"]
        t2_title = f"2.  {col_defs['table2']['title']}"

        # Build full column list with DESC injection after PRECIO LISTA
        t2_cols = []
        for cd in t2_base:
            t2_cols.append(cd)
            if cd["header"] == "PRECIO LISTA":
                for dc in desc_cols:
                    t2_cols.append(
                        {
                            "header": dc,
                            "keys": [dc],
                            "format": "0.00%",
                            "width": 10,
                            "center": True,
                        }
                    )

        t2_ncols = len(t2_cols)

        # Resolve column letters for formulas
        col_letters = {}
        for idx, cd in enumerate(t2_cols):
            col_letters[cd["header"]] = get_column_letter(idx + 1)

        # Title row
        ws.cell(row=r, column=1, value=t2_title).font = Font(bold=True, size=11, color=EXP_WHITE)
        ws.cell(row=r, column=1).fill = PatternFill(
            start_color=HEADER_NAVY, end_color=HEADER_NAVY, fill_type="solid"
        )
        ws.cell(row=r, column=1).alignment = header_align
        ws.cell(row=r, column=1).border = header_border
        if t2_ncols > 1:
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=t2_ncols)
        r += 1

        # Header row
        for i, cd in enumerate(t2_cols, 1):
            c = ws.cell(row=r, column=i, value=cd["header"])
            c.font = gs.header_font
            c.fill = PatternFill(start_color=HEADER_NAVY, end_color=HEADER_NAVY, fill_type="solid")
            c.alignment = header_align
            c.border = header_border
            if "width" in cd:
                existing = ws.column_dimensions[get_column_letter(i)].width
                if not existing or existing < cd["width"]:
                    ws.column_dimensions[get_column_letter(i)].width = cd["width"]

        t2_data_start = r + 1
        r = t2_data_start

        # Resolve Table 1 column letters for cross-table references
        t1_col_letters = {}
        for idx, cd in enumerate(t1_cols):
            t1_col_letters[cd["header"]] = get_column_letter(idx + 1)

        # Data rows
        for row_idx, (_, row) in enumerate(df.iterrows()):
            for col_idx, cd in enumerate(t2_cols):
                cell = ws.cell(row=r, column=col_idx + 1)
                cell.border = thin_border
                self._fuente_base(cell)

                hdr = cd["header"]

                if cd.get("index"):
                    cell.value = row_idx + 1
                    cell.alignment = Alignment(horizontal="center")
                elif cd.get("formula"):
                    if hdr == "PRECIO NETO":
                        # Hoja viva vs exactitud: si algún DESC visible viene del
                        # compuesto redondeado (DESCUENTO_COMPUESTO, 4 dec. — la
                        # procedencia la informa generar() vía desc_origen, pues
                        # el rename ya borró la columna original), se congela el
                        # valor del motor: recalcular desde el redondeado
                        # reintroduce doble redondeo y descuadra los totales del
                        # xlsx contra el docx. Con DESC exactos (cadena cruda del
                        # archivo o % directo) va fórmula viva y la simulación
                        # recalcula sola (mismas entradas y mismo ROUND(5) →
                        # mismo resultado que el motor).
                        _origen = desc_origen or {}
                        redondeado = any(
                            _origen.get(dc) == "DESCUENTO_COMPUESTO" for dc in desc_cols
                        )
                        neto_raw = row.get("PRECIO_NETO")
                        neto_val = None
                        if neto_raw is not None and str(neto_raw) not in ("", "nan", "None"):
                            try:
                                neto_val = float(neto_raw)
                            except (TypeError, ValueError):
                                neto_val = None
                        lista_l = col_letters.get("PRECIO LISTA")
                        desc_letters_in = [
                            col_letters.get(dc) for dc in desc_cols if col_letters.get(dc)
                        ]
                        if redondeado and neto_val is not None:
                            cell.value = round(neto_val, 5)
                            cell.number_format = "#,##0.00000"
                        elif lista_l:
                            if desc_letters_in:
                                expr = f"ROUND({lista_l}{r}"
                                for dl in desc_letters_in:
                                    expr += f"*(1-{dl}{r})"
                                expr += ",5)"
                                cell.value = f"={expr}"
                            else:
                                # Sin descuentos: referencia viva al precio de lista
                                cell.value = f"={lista_l}{r}"
                        elif neto_val is not None:
                            cell.value = round(neto_val, 5)
                            cell.number_format = "#,##0.00000"
                        else:
                            cell.value = 0
                    elif hdr == "DIF. UNITARIA":
                        # Precio histórico efectivo puede incorporar una FAE
                        # exacta; no usar el PU bruto de tabla1 para el cálculo.
                        pcol_hist = col_letters.get("PRECIO HIST. EFECTIVO")
                        neto_l = col_letters.get("PRECIO NETO")
                        if pcol_hist and neto_l:
                            cell.value = f"=MAX(0,ROUND({pcol_hist}{r}-{neto_l}{r},5))"
                        else:
                            cell.value = 0
                    elif hdr == "DIF. TOTAL":
                        # DIF. TOTAL = DIF. UNITARIA × CANTIDAD (misma fila, ambas en tabla2)
                        dif_l = col_letters.get("DIF. UNITARIA")
                        cant_l = col_letters.get("CANTIDAD")
                        if cant_l and dif_l:
                            cell.value = f"=ROUND({dif_l}{r}*{cant_l}{r},2)"
                        elif dif_l:
                            cell.value = f"=ROUND({dif_l}{r}*1,2)"
                        else:
                            cell.value = 0
                    elif hdr in ("MONTO", "MONTO (Sin IGV)"):
                        # MONTO = DIF. UNITARIA × CANTIDAD (misma fila, ambas en tabla2).
                        # En consolidado el motor deja MONTO_EXACTO (suma exacta de
                        # líneas) y se escribe estático para no descuadrar.
                        _monto_exacto = row.get("MONTO_EXACTO")
                        if _monto_exacto is not None and str(_monto_exacto) not in (
                            "",
                            "nan",
                            "None",
                        ):
                            try:
                                cell.value = round(float(_monto_exacto), 2)
                            except (TypeError, ValueError):
                                cell.value = 0
                            cell.number_format = "#,##0.00"
                        else:
                            dif_l = col_letters.get("DIF. UNITARIA")
                            cant_l = col_letters.get("CANTIDAD")
                            if dif_l and cant_l:
                                cell.value = f"=ROUND({dif_l}{r}*{cant_l}{r},2)"
                            elif dif_l:
                                cell.value = f"=ROUND({dif_l}{r}*1,2)"
                            else:
                                cell.value = 0
                    else:
                        cell.value = 0
                elif cd.get("link"):
                    target = row_idx + 2
                    cell.hyperlink = f"#'Detalle Descuentos'!A{target}"
                    cell.value = "↳"
                    cell.font = Font(color="0000FF", underline="single", size=10)
                    cell.alignment = Alignment(horizontal="center")
                elif cd.get("editable"):
                    cell.value = None
                else:
                    keys = cd.get("keys", [])
                    if hdr == "ARTICULO" and keys:
                        val = str(row.get(keys[0], ""))[:40]
                    elif hdr == "ALERTA" and keys:
                        val = self._alerta_con_refs(row, row.get(keys[0], "OK"), vacio="OK")
                    elif hdr == "CANTIDAD":
                        val = int(self._obtener_valor(row, *keys, default=0))
                    elif hdr.startswith("DESC") and keys:
                        val = self._obtener_valor(row, *keys, default=0)
                    elif hdr in (
                        "PRECIO HIST. EFECTIVO",
                        "PRECIO LISTA",
                        "PRECIO NETO",
                        "MONTO",
                        "MONTO (Sin IGV)",
                        "PRECIO UNID.",
                    ):
                        val = self._obtener_valor(row, *keys, default=0)
                    else:
                        val = str(row.get(keys[0], "")) if keys else ""
                    cell.value = val

                if cd.get("format"):
                    cell.number_format = cd["format"]
                if cd.get("center"):
                    cell.alignment = Alignment(horizontal="center")
                elif hdr in ("ARTICULO", "ALERTA", "FACTURAS"):
                    cell.alignment = Alignment(horizontal="left", wrap_text=True, vertical="top")
                elif hdr == "AUDITORÍA":
                    cell.alignment = Alignment(horizontal="left", wrap_text=True, vertical="top")

                if cd.get("highlight"):
                    cell.fill = exp_fill(EXP_HIGHLIGHT_BG)
                    cell.font = gs.alert_font
                elif cd.get("erp_input"):
                    cell.fill = PatternFill(
                        start_color=EXP_EDIT_BG, end_color=EXP_EDIT_BG, fill_type="solid"
                    )

                if hdr == "ALERTA":
                    keys = cd.get("keys", [])
                    self._color_estado(
                        cell, self._estado(str(row.get(keys[0], "")) if keys else "")
                    )

            r += 1

        t2_last_data = r - 1

        # --- Subtotal / IGV / Total rows ---
        highlight_idx = None
        for idx, cd in enumerate(t2_cols):
            if cd.get("highlight"):
                highlight_idx = idx + 1
                break

        if highlight_idx and t2_last_data >= t2_data_start:
            hl_letter = get_column_letter(highlight_idx)
            # Totales del header como formulas SUM vivas sobre MONTO (tabla 2)
            self._fijar_totales_formulas(hl_letter, t2_data_start, t2_last_data)
            label_col = highlight_idx - 1 if highlight_idx > 1 else 1
            medium_border = Border(
                left=Side(style="thin", color=EXP_BORDER_STRONG),
                right=Side(style="thin", color=EXP_BORDER_STRONG),
                top=Side(style="thin", color=EXP_BORDER_STRONG),
                bottom=Side(style="thin", color=EXP_BORDER_STRONG),
            )

            r_sub = r + 1
            ws.cell(row=r_sub, column=label_col, value="Subtotal (Sin IGV):").font = Font(
                bold=True, size=10, color=EXP_INK_SOFT
            )
            ws.cell(row=r_sub, column=label_col).alignment = Alignment(horizontal="right")
            sub_cell = ws.cell(
                row=r_sub,
                column=highlight_idx,
                value=f"=${get_column_letter(self._hdr_sub_col)}${self._hdr_sub_row}",
            )
            sub_cell.number_format = "#,##0.00"
            sub_cell.font = Font(bold=True, size=10)
            sub_cell.border = medium_border

            r_igv = r_sub + 1
            ws.cell(row=r_igv, column=label_col, value="IGV (18%):").font = Font(
                bold=True, size=10, color=EXP_INK_SOFT
            )
            ws.cell(row=r_igv, column=label_col).alignment = Alignment(horizontal="right")
            igv_cell = ws.cell(
                row=r_igv,
                column=highlight_idx,
                value=f"=ROUND(${get_column_letter(self._hdr_sub_col)}${self._hdr_sub_row}*0.18,2)",
            )
            igv_cell.number_format = "#,##0.00"
            igv_cell.font = Font(bold=True, size=10)
            igv_cell.border = medium_border

            r_tot = r_igv + 1
            ws.cell(row=r_tot, column=label_col, value="TOTAL (con IGV):").font = Font(
                bold=True, size=11, color=EXP_NAVY
            )
            ws.cell(row=r_tot, column=label_col).alignment = Alignment(horizontal="right")
            total_cell = ws.cell(
                row=r_tot,
                column=highlight_idx,
                value=f"=ROUND(${get_column_letter(self._hdr_sub_col)}${self._hdr_sub_row}+${get_column_letter(self._hdr_igv_col)}${self._hdr_igv_row},2)",
            )
            total_cell.number_format = "#,##0.00"
            total_cell.font = Font(bold=True, color=EXP_ERR_TX, size=12)
            total_cell.border = medium_border

        return r

    def _crear_detalle_descuentos(self, df, deduplicar_sku=False):
        ws = self.wb.create_sheet(title="Detalle Descuentos")

        desc_cols = sorted(
            [
                c
                for c in df.columns
                if re.match(r"^DESC\d+$", c) and not (df[c].fillna(0) == 0).all()
            ],
            key=lambda c: int(re.search(r"\d+", c).group()),
        )
        cols = [
            {"header": "N°", "width": 5},
            {"header": "SKU", "width": 12},
            {"header": "ARTICULO", "width": 35},
            {"header": "PRECIO BASE", "width": 13},
        ]
        for dc in desc_cols:
            cols.append({"header": dc, "width": 10})
        cols.append({"header": "PRECIO NETO", "width": 14})
        cols.append({"header": "% DESC. COMP.", "width": 13})

        gs = G360Styles()
        header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
        header_border = Border(
            left=Side(style="thin", color=EXP_BORDER_STRONG),
            right=Side(style="thin", color=EXP_BORDER_STRONG),
            top=Side(style="thin", color=EXP_BORDER_STRONG),
            bottom=Side(style="thin", color=EXP_BORDER_STRONG),
        )
        thin_border = Border(
            left=Side(style="thin", color=EXP_BORDER),
            right=Side(style="thin", color=EXP_BORDER),
            top=Side(style="thin", color=EXP_BORDER),
            bottom=Side(style="thin", color=EXP_BORDER),
        )

        r = 1
        max_col = len(cols)
        last_letter = get_column_letter(max_col)
        ws.cell(row=r, column=1, value="DETALLE DE DESCUENTOS POR SKU")
        ws.cell(row=r, column=1).font = Font(bold=True, size=13, color=EXP_NAVY)
        ws.merge_cells(f"A{r}:{last_letter}{r}")

        r = 3
        for i, cd in enumerate(cols, 1):
            c = ws.cell(row=r, column=i, value=cd["header"])
            c.font = gs.header_font
            c.fill = gs.header_fill
            c.alignment = header_align
            c.border = header_border
            if cd.get("width"):
                ws.column_dimensions[get_column_letter(i)].width = cd["width"]

        ws.freeze_panes = f"A{r + 1}"
        r += 1

        base_col = get_column_letter(4)
        desc_letters = [get_column_letter(5 + i) for i in range(len(desc_cols))]
        neto_col = get_column_letter(4 + len(desc_cols) + 1)

        # Hoja viva: PRECIO NETO siempre como fórmula sobre PRECIO BASE y
        # descuentos (ver tabla 2 de cálculo). Si el usuario simula, recalcula.
        iter_df = df.drop_duplicates(subset=["SKU"]) if deduplicar_sku else df
        for _, row in iter_df.iterrows():
            ws.cell(row=r, column=1, value=r - 3).alignment = Alignment(horizontal="center")
            ws.cell(row=r, column=1).border = thin_border
            ws.cell(row=r, column=2, value=str(row.get("SKU", ""))).border = thin_border
            ws.cell(row=r, column=3, value=str(row.get("ARTICULO", ""))[:40]).border = thin_border
            c = ws.cell(row=r, column=4, value=float(row.get("PRECIO_BASE", 0)))
            c.number_format = "#,##0.00000"
            c.border = thin_border

            ci = 5
            for dc in desc_cols:
                val = float(row.get(dc, 0))
                c = ws.cell(row=r, column=ci, value=val)
                c.number_format = "0.00%"
                c.border = thin_border
                ci += 1

            expr = f"ROUND({base_col}{r}"
            for dl in desc_letters:
                expr += f"*(1-{dl}{r})"
            expr += ",5)"
            c = ws.cell(row=r, column=ci, value=f"={expr}")
            c.number_format = "#,##0.00000"
            c.border = thin_border
            ci += 1

            c = ws.cell(row=r, column=ci, value=f"=ROUND(1-{neto_col}{r}/{base_col}{r},4)")
            c.number_format = "0.00%"
            c.border = thin_border
            r += 1
