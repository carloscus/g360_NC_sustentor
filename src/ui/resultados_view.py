"""Result rendering for RecogniciónView._mostrar_resultado.

Centraliza la lógica de renderizado de resultados (tabla, alertas, verificación)
en funciones puras que reciben datos y devuelven componentes Flet.
"""

import re
import flet as ft
import pandas as pd
from src.core.g360_theme import G360Theme

# Precios unitarios: 5 decimales en toda la app (acuerdo comercial).
# Montos/subtotales siguen a 2 decimales.
_COLS_PRECIO_UNIT = frozenset(
    {
        "PRECIO_HIST",
        "PRECIO_BASE",
        "PRECIO_NETO",
        "PRECIO_UNITARIO",
        "DIFERENCIA",
        "P.U. HIST.",
        "P.U. RESULT.",
        "DESC. UNIT.",
        "PRECIO LISTA",
        "PRECIO UNID.",
        "DIF. UNITARIA",
        "P. UNITARIO",
    }
)


def render_resultado(
    resultado, tipo_actual, df_historial, accent_color, success_color, marcar_nd=False
):
    """Renderiza el resultado completo y retorna los componentes UI.

    marcar_nd: cuando la opción "Calcular Nota de Débito" está activa, las filas
    con diferencia negativa se muestran como ND (no como "SIN NC"), para que la
    vista coincida con el expediente que se generará.

    Returns:
        dict con keys:
            - 'total_nc': str formateado
            - 'skus_label': str formateado
            - 'alertas_label': str formateado
            - 'table_columns': list[ft.DataColumn]
            - 'table_rows': list[ft.DataRow]
            - 'aplicar_toggles': dict (solo para rebate_volumen)
            - 'alertas_content': ft.Column | None
            - 'alertas_visible': bool
            - 'content': ft.Column (panel completo)
    """
    res = resultado.resultado
    df = res.dataframe
    total_nc = res.resumen.get("total_nc", 0)
    skus = res.resumen.get("skus_afectados", 0)
    alertas = res.alertas

    total_nc_str = f"S/ {total_nc:,.2f}"
    skus_str = f"{skus} LÍNEA" if tipo_actual == "rebate_volumen" else f"{skus} SKU"
    alertas_str = f"{len(alertas)} alertas"

    if df.empty:
        return {
            "total_nc": total_nc_str,
            "skus_label": skus_str,
            "alertas_label": alertas_str,
            "table_columns": [],
            "table_rows": [],
            "aplicar_toggles": {},
            "alertas_content": None,
            "alertas_visible": False,
            "content": None,
        }

    if tipo_actual == "rebate_volumen":
        columns, rows, toggles = _build_rebate_table(df)
        status_counts = {}
    else:
        columns, rows, status_counts = _build_standard_table(
            df, df_historial, tipo_actual, marcar_nd=marcar_nd
        )
        toggles = {}

    alertas_content, alertas_visible = _build_alertas_panel(alertas)
    nc_alertas = (resultado.resultado.metricas or {}).get("nc_alertas", [])
    nc_audit_panel = _build_nc_audit_panel(nc_alertas, accent_color)
    verification_panel = _build_verification_panel(
        resultado,
        tipo_actual,
        alertas,
        accent_color,
        success_color,
    )
    summary_panel = _build_summary_panel(total_nc_str, skus_str, alertas_str, accent_color)

    # Build status legend banner
    n_genera = status_counts.get("n_genera", 0)
    n_alarma = status_counts.get("n_alarma", 0)
    n_nd = status_counts.get("n_nd", 0)
    n_coincide = status_counts.get("n_coincide", 0)
    total_rows = n_genera + n_alarma + n_nd + n_coincide

    legend_items = []
    if n_genera > 0:
        legend_items.append(
            ft.Container(
                content=ft.Row(
                    [
                        ft.Container(
                            width=12, height=12, border_radius=3, bgcolor=G360Theme.ok_color()
                        ),
                        ft.Text(
                            f"Genera NC ({n_genera})",
                            size=10,
                            color=G360Theme.ok_color(),
                            weight=ft.FontWeight.W_600,
                        ),
                    ],
                    spacing=6,
                ),
            )
        )
    if n_nd > 0:
        legend_items.append(
            ft.Container(
                content=ft.Row(
                    [
                        ft.Container(
                            width=12, height=12, border_radius=3, bgcolor=G360Theme.warning_color()
                        ),
                        ft.Text(
                            f"Genera ND ({n_nd})",
                            size=10,
                            color=G360Theme.warning_color(),
                            weight=ft.FontWeight.W_600,
                        ),
                    ],
                    spacing=6,
                ),
            )
        )
    if n_alarma > 0:
        legend_items.append(
            ft.Container(
                content=ft.Row(
                    [
                        ft.Container(
                            width=12, height=12, border_radius=3, bgcolor=G360Theme.error_color()
                        ),
                        ft.Text(
                            f"Sin NC / Alarma ({n_alarma})",
                            size=10,
                            color=G360Theme.error_color(),
                            weight=ft.FontWeight.W_600,
                        ),
                    ],
                    spacing=6,
                ),
            )
        )
    if n_coincide > 0:
        legend_items.append(
            ft.Container(
                content=ft.Row(
                    [
                        ft.Container(
                            width=12,
                            height=12,
                            border_radius=3,
                            bgcolor=ft.Colors.ON_SURFACE_VARIANT,
                        ),
                        ft.Text(
                            f"Coincide ({n_coincide})",
                            size=10,
                            color=ft.Colors.ON_SURFACE_VARIANT,
                            weight=ft.FontWeight.W_600,
                        ),
                    ],
                    spacing=6,
                ),
            )
        )

    legend_banner = None
    if legend_items:
        legend_banner = ft.Container(
            content=ft.Row(
                legend_items, spacing=16, vertical_alignment=ft.CrossAxisAlignment.CENTER
            ),
            padding=ft.padding.symmetric(horizontal=16, vertical=8),
            bgcolor=G360Theme.surface_variant_color(),
            border=ft.border.all(1, G360Theme.border_subtle_color()),
            border_radius=10,
        )

    # Warning: no rows generate NC
    expediente_warning = None
    if n_genera == 0 and total_rows > 0:
        if n_nd > 0 and n_alarma == 0 and n_coincide == 0:
            warn_msg = (
                "Las diferencias negativas generarán una Nota de Débito (ND), no NC. "
                "El expediente incluirá el cálculo ND."
            )
        elif n_alarma > 0 and n_coincide == 0:
            warn_msg = (
                "Todas las diferencias son negativas — no se generará monto NC. "
                "El expediente se creará como constancia de análisis."
            )
        elif n_coincide > 0:
            warn_msg = (
                "No hay diferencias significativas — los precios coincinden con la lista. "
                "El expediente se creará como constancia de análisis."
            )
        else:
            warn_msg = (
                "No hay filas que generen NC. El expediente se creará como constancia de análisis."
            )
        expediente_warning = ft.Container(
            content=ft.Row(
                [
                    ft.Icon(ft.Icons.WARNING_OUTLINED, size=16, color=G360Theme.warning_color()),
                    ft.Text(warn_msg, size=11, color=G360Theme.warning_color()),
                ],
                spacing=8,
                wrap=True,
            ),
            padding=ft.padding.symmetric(horizontal=14, vertical=10),
            bgcolor=G360Theme.with_opacity(0.12, G360Theme.warning_color()),
            border=ft.border.all(1, G360Theme.with_opacity(0.4, G360Theme.warning_color())),
            border_radius=10,
        )

    content = ft.Column(
        [
            ft.Row(
                [
                    ft.Icon(ft.Icons.ANALYTICS_OUTLINED, size=18, color=accent_color),
                    ft.Text("RESULTADO", size=13, weight=ft.FontWeight.W_700),
                ],
                spacing=8,
            ),
            summary_panel,
            nc_audit_panel,
            ft.Divider(height=12, color=G360Theme.border_subtle_color()),
            legend_banner,
            ft.Divider(height=8, color=G360Theme.border_subtle_color()),
            ft.Container(
                content=ft.Row([], scroll=ft.ScrollMode.ALWAYS),
                padding=15,
                border_radius=14,
                bgcolor=G360Theme.surface_variant_color(),
                border=ft.border.all(1, G360Theme.border_subtle_color()),
            ),
            ft.Divider(height=12, color="transparent"),
            verification_panel,
        ],
        spacing=10,
    )

    return {
        "total_nc": total_nc_str,
        "skus_label": skus_str,
        "alertas_label": alertas_str,
        "table_columns": columns,
        "table_rows": rows,
        "aplicar_toggles": toggles,
        "alertas_content": alertas_content,
        "alertas_visible": alertas_visible,
        "content": content,
        "status_counts": status_counts,
        "expediente_warning": expediente_warning,
        "lazy_info": {
            "total_rows": status_counts.get("total_rows", 0),
            "shown_rows": status_counts.get("shown_rows", len(rows)),
            "has_more": status_counts.get("has_more", False),
            "n_genera": n_genera,
            "n_alarma": n_alarma,
            "n_coincide": n_coincide,
        },
    }


def _build_rebate_table(df):
    """Construye columnas y filas para rebate_volumen."""
    cols_vr = [
        "LÍNEA",
        "MONTO VENDIDO",
        "% A RECONOCER",
        "MONTO A RECONOCER",
        "MONTO NC/ND",
        "APLICAR",
    ]
    columns = [ft.DataColumn(ft.Text(col, size=10, weight="bold")) for col in cols_vr]
    rows = []
    toggles = {}

    for _, row in df.iterrows():
        linea = str(row.get("LINEA", ""))
        monto_vendido = row.get("SOLES", 0)
        pct_recon = row.get("%_DEL_TOTAL", 0)
        monto_recon = row.get("MONTO_NC", 0)
        monto_ncnd = row.get("MONTO_NC_ND", 0)
        toggle = ft.Checkbox(value=True, label="")
        toggles[linea] = (toggle, monto_recon)
        cells = [
            ft.DataCell(ft.Text(linea, size=9)),
            ft.DataCell(ft.Text(f"S/ {monto_vendido:,.2f}", size=9)),
            ft.DataCell(ft.Text(f"{pct_recon:.2f}%", size=9)),
            ft.DataCell(ft.Text(f"S/ {monto_recon:,.2f}", size=9)),
            ft.DataCell(ft.Text(f"S/ {monto_ncnd:,.2f}" if monto_ncnd else "S/ 0.00", size=9)),
            ft.DataCell(toggle),
        ]
        rows.append(ft.DataRow(cells=cells))

    return columns, rows, toggles


def _cell(texto, color=None, bgcolor=None):
    """Crea un DataCell ligero — evita el overhead de Container cuando no requiere estilo."""
    txt = ft.Text(texto, size=9, color=color or ft.Colors.ON_SURFACE)
    if bgcolor:
        return ft.DataCell(ft.Container(content=txt, padding=4, bgcolor=bgcolor))
    return ft.DataCell(txt)


def _build_standard_table(
    df, df_historial, tipo_actual, max_rows=50, sort_col=None, sort_asc=True, marcar_nd=False
):
    """Construye columnas y filas para tipos estándar con codificación de colores por estado.

    marcar_nd: las filas con diferencia negativa (MONTO_NC 0) se muestran como
    "GENERA ND" (ámbar) en vez de "SIN NC", reflejando la opción de cálculo.
    """

    def _clas(dif, monto):
        if monto > 0.001:
            return "genera"
        if dif < -0.001:
            return "nd" if marcar_nd else "alarma"
        return "coincide"

    cols_fallback = ["SKU", "CANTIDAD", "MONTO_NC"]
    cols_prioridad = [
        "SKU",
        "ARTICULO",
        "CANTIDAD",
        "PRECIO_HIST",
        "PRECIO_BASE",
        "PRECIO_NETO",
        "DIFERENCIA",
        "MONTO_NC",
        "ALERTA",
        "NC_EXISTENTE",
    ]
    cols_disponibles = [c for c in cols_prioridad if c in df.columns]
    if not cols_disponibles:
        cols_disponibles = [c for c in cols_fallback if c in df.columns]
    if not cols_disponibles:
        cols_disponibles = list(df.columns)[:5]

    cols_extras = [c for c in ["FACTURA", "FACTURAS", "AUDITORIA_NC"] if c in df.columns]
    todas_cols = cols_disponibles + ["ESTADO"] + cols_extras

    notas = None
    if df_historial is not None and not df_historial.empty:
        if "NC_ASOCIADAS" in df_historial.columns and "DOC_ID" in df_historial.columns:
            notas = (
                df_historial[df_historial["TIPO_CLASE"] != "factura"]
                if "TIPO_CLASE" in df_historial.columns
                else pd.DataFrame()
            )
        else:
            from src.core.detector import detectar_notas_en_historial

            notas = detectar_notas_en_historial(df_historial)

    col_ncnd = "NC/ND"
    todas_cols.append(col_ncnd)

    columns = []
    for col in todas_cols:
        indicator = ""
        if sort_col == col:
            indicator = " ▲" if sort_asc else " ▼"
        txt = ft.Text(f"{col.upper()}{indicator}", size=10, weight="bold")
        # data = nombre real de columna: la vista lo usa en on_sort para reordenar.
        # (DataColumn no acepta on_click en esta version de Flet; el sort se
        # cablea en la vista con col.on_sort.)
        dc = ft.DataColumn(txt, data=col)
        columns.append(dc)

    # Sort dataframe if requested
    _sort_df = df
    if sort_col and sort_col in df.columns:
        _sort_df = df.sort_values(sort_col, ascending=sort_asc).reset_index(drop=True)
        # Rebuild row_statuses after sorting
        row_statuses = []
        for _, row in _sort_df.iterrows():
            dif = float(row.get("DIFERENCIA", 0) or 0)
            monto = float(row.get("MONTO_NC", 0) or 0)
            row_statuses.append(_clas(dif, monto))
        n_genera = row_statuses.count("genera")
        n_alarma = row_statuses.count("alarma")
        n_nd = row_statuses.count("nd")
        n_coincide = row_statuses.count("coincide")
    else:
        row_statuses = []
        for _, row in df.iterrows():
            dif = float(row.get("DIFERENCIA", 0) or 0)
            monto = float(row.get("MONTO_NC", 0) or 0)
            row_statuses.append(_clas(dif, monto))
        n_genera = row_statuses.count("genera")
        n_alarma = row_statuses.count("alarma")
        n_nd = row_statuses.count("nd")
        n_coincide = row_statuses.count("coincide")

    rows = []
    for idx, (_, row) in enumerate(_sort_df.iterrows()):
        if idx >= max_rows:
            break
        status = row_statuses[idx] if idx < len(row_statuses) else "coincide"
        dif = float(row.get("DIFERENCIA", 0) or 0)
        monto = float(row.get("MONTO_NC", 0) or 0)

        # Row background based on status
        if status == "genera":
            row_bg = G360Theme.with_opacity(0.10, G360Theme.ok_color())
        elif status in ("alarma", "nd"):
            row_bg = G360Theme.with_opacity(
                0.10, G360Theme.error_color() if status == "alarma" else G360Theme.warning_color()
            )
        else:
            row_bg = None

        celdas = []
        for col in cols_disponibles + cols_extras:
            val = row.get(col, "")
            if isinstance(val, float):
                if col in _COLS_PRECIO_UNIT:
                    texto = f"{val:.5f}" if abs(val) < 10000 else f"{val:,.5f}"
                else:
                    texto = f"{val:.2f}" if abs(val) < 10000 else f"{val:,.2f}"
            else:
                texto = str(val)[:25]

            color = None
            bg_cell = None
            if col == "DIFERENCIA":
                if dif > 0.001:
                    color = G360Theme.ok_color()
                    bg_cell = G360Theme.with_opacity(0.15, G360Theme.ok_color())
                elif dif < -0.001:
                    color = G360Theme.error_color()
                    bg_cell = G360Theme.with_opacity(0.15, G360Theme.error_color())
                    texto = "—"
                elif abs(dif) <= 0.001:
                    texto = "0.00000"
            elif col == "MONTO_NC":
                if monto > 0.001:
                    color = G360Theme.ok_color()
                    bg_cell = G360Theme.with_opacity(0.20, G360Theme.ok_color())
                elif monto <= 0.001:
                    color = G360Theme.error_color()
                    bg_cell = G360Theme.with_opacity(0.10, G360Theme.error_color())
            elif col == "ALERTA":
                txt = str(val).upper()
                if "ERROR" in txt:
                    color = G360Theme.error_color()
                elif "OK" in txt:
                    color = G360Theme.ok_color()
                elif "no genera" in str(val).lower() or "sin nc" in str(val).lower():
                    color = G360Theme.error_color()
                    bg_cell = G360Theme.with_opacity(0.10, G360Theme.error_color())

            celdas.append(_cell(texto, color=color, bgcolor=bg_cell or row_bg))

        # ESTADO column
        if status == "genera":
            estado_icon, estado_txt, estado_color = "\u2713", "GENERA NC", G360Theme.ok_color()
        elif status == "nd":
            estado_icon, estado_txt, estado_color = "\u21a7", "GENERA ND", G360Theme.warning_color()
        elif status == "alarma":
            estado_icon, estado_txt, estado_color = "\u26a0", "SIN NC", G360Theme.error_color()
        else:
            estado_icon, estado_txt, estado_color = (
                "\u2014",
                "COINCIDE",
                ft.Colors.ON_SURFACE_VARIANT,
            )

        estado_content = ft.Row(
            [
                ft.Text(estado_icon, size=10, color=estado_color),
                ft.Text(estado_txt, size=9, color=estado_color, weight=ft.FontWeight.W_600),
            ],
            spacing=4,
        )
        estado_cell = ft.DataCell(ft.Container(content=estado_content, padding=4, bgcolor=row_bg))
        celdas.insert(len(cols_disponibles), estado_cell)

        # NC/ND column
        facturas_ids = _extraer_facturas(row)
        docs_nota = []
        if notas is not None and not notas.empty:
            if "DOC_NOTA" in notas.columns:
                for fid in facturas_ids:
                    mask = notas["FACTURA_REF"] == fid
                    docs_nota.extend(notas.loc[mask, "DOC_NOTA"].tolist())
            elif "DOC_ID" in notas.columns:
                for fid in facturas_ids:
                    mask = notas["FACTURA_REF"] == fid
                    docs_nota.extend(notas.loc[mask, "DOC_ID"].tolist())
        if docs_nota:
            txt_ncnd = "\u26a0\ufe0f " + ", ".join(sorted(set(docs_nota)))
            color_ncnd = ft.Colors.AMBER_400
        else:
            txt_ncnd = "\u2014"
            color_ncnd = ft.Colors.ON_SURFACE
        celdas.append(_cell(txt_ncnd, color=color_ncnd, bgcolor=row_bg))

        rows.append(ft.DataRow(cells=celdas))

    has_more = len(df) > len(rows)

    return (
        columns,
        rows,
        {
            "n_genera": n_genera,
            "n_alarma": n_alarma,
            "n_nd": n_nd,
            "n_coincide": n_coincide,
            "has_more": has_more,
            "total_rows": len(df),
            "shown_rows": len(rows),
        },
    )


def _extraer_facturas(row) -> set:
    """Extrae IDs de factura de una fila del resultado."""
    ids = set()
    for c in ["FACTURA", "FACTURAS", "FACTURA_REF"]:
        v = row.get(c, "")
        if v and str(v).strip() not in ("", "nan", "None"):
            ids.add(str(v).strip())
    for c in ["Facturas (qty)", "DOCUMENTOS"]:
        v = str(row.get(c, ""))
        if v and v not in ("", "nan", "None"):
            ids.update(re.findall(r"([A-Za-z]?\d+\-\d+)", v))
    return ids


def _build_alertas_panel(alertas):
    """Construye el panel de alertas."""
    if not alertas:
        return None, False

    items = []
    for a in alertas[:10]:
        icono = (
            ft.Icons.ERROR_OUTLINED
            if a.tipo == "error"
            else ft.Icons.WARNING_AMBER_OUTLINED
            if a.tipo == "warning"
            else ft.Icons.INFO_OUTLINED
        )
        color = (
            ft.Colors.RED_400
            if a.tipo == "error"
            else ft.Colors.AMBER_400
            if a.tipo == "warning"
            else ft.Colors.BLUE_300
        )
        items.append(
            ft.Row(
                [
                    ft.Icon(icono, size=14, color=color),
                    ft.Text(f"{a.mensaje}", size=11, color=ft.Colors.ON_SURFACE_VARIANT),
                ],
                spacing=6,
            )
        )

    content = ft.Container(
        content=ft.Column(items, spacing=4),
        bgcolor=G360Theme.surface_variant_color(),
        border=ft.border.all(1, G360Theme.border_subtle_color()),
        border_radius=12,
        padding=12,
        visible=True,
    )
    return content, True


def _build_verification_panel(resultado, tipo_actual, alertas, accent_color, success_color):
    """Construye el panel de verificación pre-exportación."""
    res = resultado.resultado
    df_raw = (
        resultado.datos if hasattr(resultado, "datos") and resultado.datos is not None else None
    )
    df_res = res.dataframe

    n_clientes = (
        df_raw["CLIENTE"].nunique() if df_raw is not None and "CLIENTE" in df_raw.columns else 0
    )
    n_facturas = (
        df_res["FACTURA"].nunique()
        if "FACTURA" in df_res.columns
        else df_res["FACTURAS"].nunique()
        if "FACTURAS" in df_res.columns
        else 0
    )
    col_sku_label = "LÍNEA" if tipo_actual == "rebate_volumen" else "SKU"
    n_skus = (
        df_res[col_sku_label].nunique()
        if col_sku_label in df_res.columns
        else df_res["SKU"].nunique()
        if "SKU" in df_res.columns
        else 0
    )
    n_alertas_error = sum(1 for a in alertas if a.tipo == "error")
    n_alertas_warning = sum(1 for a in alertas if a.tipo == "warning")
    n_alertas_info = sum(1 for a in alertas if a.tipo == "info")

    items = [
        ("Clientes", str(n_clientes), ft.Icons.GROUP_OUTLINED),
        ("Facturas", str(n_facturas), ft.Icons.RECEIPT_OUTLINED),
        (
            "LÍNEA" if tipo_actual == "rebate_volumen" else "SKU",
            str(n_skus),
            ft.Icons.INVENTORY_2_OUTLINED,
        ),
    ]
    if n_alertas_error > 0:
        items.append(("Errores", str(n_alertas_error), ft.Icons.ERROR_OUTLINED))
    if n_alertas_warning > 0:
        items.append(("Advertencias", str(n_alertas_warning), ft.Icons.WARNING_AMBER_OUTLINED))
    if n_alertas_info > 0:
        items.append(("Informes", str(n_alertas_info), ft.Icons.INFO_OUTLINED))

    if tipo_actual == "rebate_volumen" and res.metricas:
        m = res.metricas
        total_venta = m.get("total_venta", 0)
        meta = m.get("meta", 0)
        pct = m.get("porcentaje_rebate", 0)
        cumplida = total_venta >= meta if meta > 0 else False
        items.insert(2, ("Total Venta", f"S/ {total_venta:,.2f}", ft.Icons.TRENDING_UP_OUTLINED))
        items.insert(3, ("Meta", f"S/ {meta:,.2f}", ft.Icons.TRACK_CHANGES_OUTLINED))
        items.insert(
            4,
            (
                "Estado",
                "\u2713 Cumple" if cumplida else "\u26a0 Revisar",
                ft.Icons.CHECK_CIRCLE_OUTLINED if cumplida else ft.Icons.ERROR_OUTLINED,
            ),
        )
        items.append(("% Rebate", f"{pct:.1f}%", ft.Icons.PERCENT_OUTLINED))

    return ft.Container(
        content=ft.Column(
            [
                ft.Row(
                    [
                        ft.Icon(ft.Icons.VERIFIED_OUTLINED, size=16, color=success_color),
                        ft.Text(
                            "VERIFICACIÓN PRE-EXPORTACIÓN",
                            size=11,
                            weight=ft.FontWeight.W_700,
                            color=ft.Colors.ON_SURFACE_VARIANT,
                        ),
                    ],
                    spacing=8,
                ),
                ft.Row(
                    [
                        ft.Container(
                            content=ft.Column(
                                [
                                    ft.Row(
                                        [
                                            ft.Icon(icon, size=15, color=accent_color),
                                            ft.Text(
                                                lbl, size=10, color=ft.Colors.ON_SURFACE_VARIANT
                                            ),
                                        ],
                                        spacing=4,
                                    ),
                                    ft.Text(val, size=17, weight=ft.FontWeight.W_700),
                                ],
                                spacing=0,
                                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                            ),
                            padding=12,
                            expand=True,
                            border_radius=10,
                            bgcolor=G360Theme.surface_variant_color(),
                        )
                        for lbl, val, icon in items
                    ],
                    spacing=8,
                ),
            ],
            spacing=10,
        ),
        padding=18,
        border_radius=14,
        bgcolor=G360Theme.surface_color(),
        border=ft.border.all(1, G360Theme.border_subtle_color()),
        shadow=ft.BoxShadow(
            spread_radius=0,
            blur_radius=12,
            color=G360Theme.SHADOW_COLOR,
            blur_style=ft.ShadowBlurStyle.OUTER,
        ),
    )


def _build_nc_audit_panel(nc_alertas, accent_color):
    """Construye el panel de auditoría NC/ND."""
    if not nc_alertas:
        return ft.Container(visible=False)

    grupos = {}
    for a in nc_alertas:
        grupos.setdefault(a.tipo, []).append(a)

    tipo_config = {
        "match_directo": (
            ft.Icons.CHECK_CIRCLE_OUTLINE,
            ft.Colors.GREEN_400,
            "Match directo (afecta precio)",
        ),
        "nc_informativa": (
            ft.Icons.INFO_OUTLINED,
            ft.Colors.BLUE_300,
            "Informativa (no afecta precio)",
        ),
        "nc_consolidada": (
            ft.Icons.FOLDER_OUTLINED,
            ft.Colors.AMBER_400,
            "Consolidada (feria/acuerdo)",
        ),
        "nc_general": (ft.Icons.WARNING_AMBER_OUTLINED, ft.Colors.AMBER_400, "NC sin SKU"),
        "sku_no_en_factura": (
            ft.Icons.ERROR_OUTLINE,
            ft.Colors.RED_400,
            "SKU no est\u00e1 en factura",
        ),
        "sin_referencia": (ft.Icons.HELP_OUTLINE, ft.Colors.BLUE_300, "Sin referencia"),
    }

    items = []
    for tipo, alertas_grupo in grupos.items():
        icon, color, label = tipo_config.get(
            tipo, (ft.Icons.INFO_OUTLINED, ft.Colors.ON_SURFACE, tipo)
        )
        items.append(
            ft.Row(
                [
                    ft.Icon(icon, size=14, color=color),
                    ft.Text(
                        f"{label} ({len(alertas_grupo)})",
                        size=11,
                        weight=ft.FontWeight.W_600,
                        color=color,
                    ),
                ],
                spacing=6,
            )
        )
        for a in alertas_grupo:
            items.append(
                ft.Container(
                    content=ft.Text(
                        f"  \u2022 {a.mensaje}", size=10, color=ft.Colors.ON_SURFACE_VARIANT
                    ),
                    padding=ft.padding.only(left=20),
                )
            )

    return ft.Container(
        content=ft.ExpansionTile(
            title=ft.Row(
                [
                    ft.Icon(ft.Icons.FIND_IN_PAGE_OUTLINED, size=16, color=accent_color),
                    ft.Text(
                        f"Auditor\u00eda NC/ND ({len(nc_alertas)} alertas)",
                        size=12,
                        weight=ft.FontWeight.W_600,
                        color=accent_color,
                    ),
                ],
                spacing=8,
            ),
            controls=[
                ft.Container(
                    content=ft.Column(items, spacing=3),
                    padding=ft.padding.only(top=8, bottom=4),
                )
            ],
            initially_expanded=len(nc_alertas) <= 6,
        ),
        border_radius=12,
        bgcolor=G360Theme.surface_variant_color(),
        border=ft.border.all(1, G360Theme.border_subtle_color()),
    )


def _build_summary_panel(total_nc_str, skus_str, alertas_str, accent_color):
    """Construye el panel de resumen (Total NC, SKU, Alertas)."""
    return ft.Row(
        [
            ft.Container(
                content=ft.Column(
                    [
                        ft.Text(
                            "Total NC",
                            size=11,
                            color=ft.Colors.ON_SURFACE_VARIANT,
                            weight=ft.FontWeight.W_500,
                        ),
                        ft.Text(
                            total_nc_str, size=22, weight=ft.FontWeight.W_800, color=accent_color
                        ),
                    ],
                    spacing=4,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                padding=ft.padding.symmetric(horizontal=24, vertical=16),
                expand=True,
                border_radius=14,
                bgcolor=G360Theme.surface_variant_color(),
                border=ft.border.all(1, G360Theme.border_subtle_color()),
            ),
            ft.Container(
                content=ft.Column(
                    [
                        ft.Text(
                            "SKU / Líneas",
                            size=11,
                            color=ft.Colors.ON_SURFACE_VARIANT,
                            weight=ft.FontWeight.W_500,
                        ),
                        ft.Text(
                            skus_str,
                            size=22,
                            weight=ft.FontWeight.W_800,
                            color=ft.Colors.ON_SURFACE,
                        ),
                    ],
                    spacing=4,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                padding=ft.padding.symmetric(horizontal=24, vertical=16),
                expand=True,
                border_radius=14,
                bgcolor=G360Theme.surface_variant_color(),
                border=ft.border.all(1, G360Theme.border_subtle_color()),
            ),
            ft.Container(
                content=ft.Column(
                    [
                        ft.Text(
                            "Alertas",
                            size=11,
                            color=ft.Colors.ON_SURFACE_VARIANT,
                            weight=ft.FontWeight.W_500,
                        ),
                        ft.Text(
                            alertas_str,
                            size=22,
                            weight=ft.FontWeight.W_800,
                            color=ft.Colors.AMBER_400
                            if int(alertas_str.split()[0]) > 0
                            else ft.Colors.ON_SURFACE,
                        ),
                    ],
                    spacing=4,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                padding=ft.padding.symmetric(horizontal=24, vertical=16),
                expand=True,
                border_radius=14,
                bgcolor=G360Theme.surface_variant_color(),
                border=ft.border.all(1, G360Theme.border_subtle_color()),
            ),
        ],
        spacing=10,
    )
