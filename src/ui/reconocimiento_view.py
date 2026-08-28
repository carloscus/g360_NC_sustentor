import flet as ft
import pandas as pd
import os
import threading
from pathlib import Path
from datetime import datetime
import re
from typing import Optional, Callable, List
from src.domain import ExpedienteComercial, PipelineContext, RecognitionResult
from src.pipeline import Pipeline
from src.render.excel_renderer import ExcelRenderer
from src.render.docx_renderer import DocxInformeRenderer
from src.render.templates import PlantillaGenerator
from src.core.g360_theme import G360Theme, safe_handler
from src.core.utils import (
    format_id_name, read_erp_file, build_dropdown_options, resolve_output_path,
    sanitize_label, build_expediente_dir, build_excel_filename,
)
from src.core.supabase_client import SupabaseVentasClient
from src.ui.reconocimiento_config import TIPO_CONFIG, ESTRATEGIA_POR_TIPO
from src.ui.widgets.cliente_factura_selector import ClienteFacturaSelector


class ReconocimientoView:
    def __init__(self, app):
        self.app = app
        self.tipo_actual = "diferencia_precio"
        self.historial_path = None
        self.lista_path = None
        self.requerimientos_paths: List[Path] = []
        self.container = None
        self.df_historial = None
        self.resultado = None

    def build(self):
        self._init_controls()
        self.container = ft.Container(
            expand=True,
            padding=ft.padding.only(top=30, bottom=40, left=50, right=50),
            content=ft.Column([], scroll=ft.ScrollMode.AUTO, spacing=15),
        )
        self._renderizar_ui()
        return self.container

    def _init_controls(self):
        self.tipo_radio = ft.RadioGroup(
            content=ft.Column([], spacing=6),
            on_change=self._on_tipo_change,
        )
        self._construir_tipo_selector()

        self.lbl_historial = ft.Text("Ninguno", size=11, color=ft.Colors.ON_SURFACE_VARIANT)
        self.lbl_lista = ft.Text("Ninguno", size=11, color=ft.Colors.ON_SURFACE_VARIANT)
        self.lbl_requerimientos_list = ft.Column([], spacing=4)
        self.lbl_requerimientos_count = ft.Text("Ninguno", size=11, color=ft.Colors.ON_SURFACE_VARIANT)

        self.config_container = ft.Container(padding=10)

        self.modalidad_dropdown = ft.Dropdown(
            label="Modalidad",
            options=[
                ft.dropdown.Option("por_factura", "Por Factura Específica"),
                ft.dropdown.Option("por_sku_periodo", "Por SKU + Periodo"),
            ],
            value="por_factura",
            width=300,
            on_change=self._on_modalidad_change,
            border_radius=12,
            dense=True,
            text_size=13,
        )
        self.factura_dropdown = ft.Dropdown(
            label="Seleccionar Factura",
            width=400,
            on_change=self._on_factura_selected,
            border_radius=12,
            dense=True,
            text_size=13,
        )
        self.docx_mode_radio = ft.RadioGroup(
            content=ft.Row([
                ft.Radio(value="unico", label="Único"),
                ft.Radio(value="por_factura", label="Por factura"),
            ]),
            value="unico",
        )
        self.docx_mode_container = ft.Container(
            content=ft.Column([
                ft.Divider(height=5, color="transparent"),
                ft.Row([
                    ft.Icon(ft.Icons.DOCUMENT_SCANNER_OUTLINED, size=16, color=self.app.G360_ACCENT),
                    ft.Text("DOCUMENTOS", size=12, weight=ft.FontWeight.BOLD, color=ft.Colors.ON_SURFACE_VARIANT),
                ], spacing=8),
                ft.Text("Generar informe DOCX:", size=11, color=ft.Colors.ON_SURFACE_VARIANT),
                self.docx_mode_radio,
            ], spacing=4),
            padding=5,
        )

        self.selector_ci = ClienteFacturaSelector(
            suffix="ci",
            on_cliente_change=self._on_cliente_change_ci,
            on_factura_change=self._on_factura_change_ci,
            show_factura=True,
        )
        self.cliente_dropdown_ci = self.selector_ci.cliente_dropdown
        self.factura_dropdown_ci = self.selector_ci.factura_dropdown

        self.fecha_desde = ft.TextField(label="Desde (dd/mm/aaaa)", width=180, border_radius=12, text_size=13, dense=True)
        self.fecha_hasta = ft.TextField(label="Hasta (dd/mm/aaaa)", width=180, border_radius=12, text_size=13, dense=True)

        self.mecanica_dropdown = ft.Dropdown(
            label="Mecánica",
            options=[
                ft.dropdown.Option("12+1"),
                ft.dropdown.Option("24+2"),
                ft.dropdown.Option("48+1"),
                ft.dropdown.Option("personalizado", "Personalizado"),
            ],
            value="12+1",
            width=250,
            border_radius=12,
            dense=True,
            text_size=13,
            on_change=self._on_mecanica_change,
        )
        self.mecanica_personalizada = ft.TextField(
            label="Mecánica (ej: 10+2)", width=200, visible=False, border_radius=12, text_size=13, dense=True
        )

        self.vendedor_dropdown = ft.Dropdown(
            label="Vendedor",
            width=400,
            border_radius=15,
            on_change=self._on_vendedor_change,
        )

        self.selector_pd = ClienteFacturaSelector(
            suffix="pd",
            on_cliente_change=self._on_cliente_change_pd,
            show_factura=False,
        )
        self.cliente_dropdown_pd = self.selector_pd.cliente_dropdown
        self.fecha_desde_pd = ft.TextField(label="Desde (dd/mm/aaaa)", width=180, border_radius=15, text_size=13)
        self.fecha_hasta_pd = ft.TextField(label="Hasta (dd/mm/aaaa)", width=180, border_radius=15, text_size=13)

        self.evidencias_opts = {
            "Facturas originales": False,
            "Guías de remisión": False,
            "Contrato / Acuerdo comercial": False,
            "Lista de precios del cliente": False,
            "Correo de soporte": False,
            "Otros": False,
        }
        self.evidencias_checkboxes = [
            ft.Checkbox(label=k, value=False, on_change=self._on_evidencia_change, label_style=ft.TextStyle(size=13))
            for k in self.evidencias_opts
        ]

        self.observaciones = ft.TextField(
            label="Observaciones (opcional)",
            width=400,
            border_radius=12,
            text_size=13,
            multiline=True,
            min_lines=2,
            max_lines=4,
            dense=True,
        )

        self.antecedentes = ft.TextField(
            label="Antecedentes / Análisis Comercial *",
            width=400,
            border_radius=12,
            text_size=13,
            multiline=True,
            min_lines=3,
            max_lines=6,
            hint_text="Ej: Acuerdo del 10/09/2025, precio aprobado S/ 2.90...",
            dense=True,
        )

        self.meta_monto = ft.TextField(label="Meta (S/)", width=180, border_radius=12, text_size=13, dense=True)
        self.rebate_pct = ft.TextField(label="% Rebate", width=150, border_radius=12, text_size=13, dense=True)

        self.sort_mode_radio = ft.RadioGroup(
            content=ft.Column([
                ft.Radio(value="fecha_asc", label="FIFO (fecha ascendente)"),
                ft.Radio(value="fecha_desc", label="LIFO (fecha descendente)"),
                ft.Radio(value="cantidad_asc", label="Menor cantidad primero"),
                ft.Radio(value="cantidad_desc", label="Mayor cantidad primero"),
            ], spacing=4),
            value="fecha_asc",
        )
        self.chk_forzar_cant = ft.Checkbox(label="Forzar cantidad solicitada", value=True)

        import datetime as _dt
        default_min = _dt.datetime(2020, 1, 1)
        default_max = _dt.datetime(2030, 12, 31)

        self.selector_fp = ClienteFacturaSelector(
            suffix="fp",
            on_cliente_change=self._on_cliente_change_fp,
            show_factura=False,
            use_build_options=False,
        )
        self.cliente_dropdown_fp = self.selector_fp.cliente_dropdown
        self.fp_desde = ft.DatePicker(
            on_change=self._on_fp_desde_change,
            first_date=default_min,
            last_date=default_max,
        )
        self.fp_hasta = ft.DatePicker(
            on_change=self._on_fp_hasta_change,
            first_date=default_min,
            last_date=default_max,
        )
        self.fecha_desde_fp = ft.OutlinedButton(
            text="Desde: Sin filtro",
            icon=ft.Icons.CALENDAR_MONTH,
            on_click=self._abrir_fp_desde,
            style=ft.ButtonStyle(padding=ft.padding.symmetric(horizontal=14)),
        )
        self.fecha_hasta_fp = ft.OutlinedButton(
            text="Hasta: Sin filtro",
            icon=ft.Icons.CALENDAR_MONTH,
            on_click=self._abrir_fp_hasta,
            style=ft.ButtonStyle(padding=ft.padding.symmetric(horizontal=14)),
        )

        self.selector_sf = ClienteFacturaSelector(
            suffix="sf",
            on_cliente_change=self._on_cliente_change_sf,
            on_factura_change=self._on_factura_change_sf,
            show_factura=True,
        )
        self.cliente_dropdown_sf = self.selector_sf.cliente_dropdown
        self.factura_dropdown_sf = self.selector_sf.factura_dropdown
        self.alertas_nc_container_sf = ft.Container(visible=False, padding=12, border_radius=12,
                                                     bgcolor=G360Theme.surface_variant_color(),
                                                     border=ft.border.all(1, G360Theme.border_subtle_color()))

        self.selector_df = ClienteFacturaSelector(
            suffix="df",
            on_cliente_change=self._on_cliente_change_df,
            on_factura_change=self._on_factura_change_df,
            show_factura=True,
        )
        self.cliente_dropdown_df = self.selector_df.cliente_dropdown
        self.factura_dropdown_df = self.selector_df.factura_dropdown
        self.descuento_pct = ft.TextField(
            label="% Descuento", width=200, border_radius=12, text_size=13, dense=True,
            keyboard_type=ft.KeyboardType.NUMBER,
            on_change=self._on_descuento_pct_change,
        )
        self.selector_pb = ClienteFacturaSelector(
            suffix="pb",
            on_cliente_change=self._on_cliente_change_pb,
            show_factura=False,
        )
        self.cliente_dropdown_pb = self.selector_pb.cliente_dropdown

        self.linea_checkboxes: dict[str, ft.Checkbox] = {}
        self.linea_checkbox_container = ft.Container(
            content=ft.Column([], spacing=4, scroll=ft.ScrollMode.AUTO),
            height=180,
            border=G360Theme.hr().color,
            border_radius=12,
            padding=10,
        )
        self.linea_select_all_btn = ft.ElevatedButton(
            "Seleccionar todas", on_click=self._linea_select_all, height=28,
            style=ft.ButtonStyle(padding=ft.padding.symmetric(horizontal=10)),
        )
        self.linea_clear_btn = ft.ElevatedButton(
            "Limpiar", on_click=self._linea_clear, height=28,
            style=ft.ButtonStyle(padding=ft.padding.symmetric(horizontal=10)),
        )
        self.chk_incluir_nc = ft.Checkbox(
            label="Incluir NC/ND en el cálculo", value=True,
            label_style=ft.TextStyle(size=12),
        )
        self.categoria_nc_checkboxes: dict[str, ft.Checkbox] = {}
        for cat_key, cat_label in [("devolucion", "Devoluciones (NCR)"), ("descuento", "Descuentos (NC)"), ("cargo", "Cargos (NDB/ND)")]:
            self.categoria_nc_checkboxes[cat_key] = ft.Checkbox(
                label=cat_label, value=(cat_key == "devolucion"),
                label_style=ft.TextStyle(size=12),
            )

        self.sku_filter_path = None
        self.sku_filter_clear_btn = ft.IconButton(
            icon=ft.Icons.CLOSE, icon_size=14, height=24, width=24,
            on_click=self._quitar_sku_filter, visible=False,
            tooltip="Quitar filtro SKU",
        )
        self.lbl_sku_filter = ft.Text("Ninguno", size=12, color=ft.Colors.ON_SURFACE_VARIANT, expand=True)

        # Stock file controls (for diferencia_stock)
        self.lbl_stock_cliente = ft.Text("Ninguno", size=12, color=ft.Colors.ON_SURFACE_VARIANT, expand=True)
        self.stock_cliente_clear_btn = ft.IconButton(
            icon=ft.Icons.CLOSE, icon_size=14, height=24, width=24,
            on_click=self._quitar_stock_cliente, visible=False,
            tooltip="Quitar archivo de stock",
        )
        self.stock_cliente_path = None
        self.desc_file_path = None
        self.lbl_desc_file = ft.Text("Ninguno", size=12, color=ft.Colors.ON_SURFACE_VARIANT)

        # Sort mode for diferencia_stock (expanded with 4 options)
        self.sort_mode_sp_radio = ft.RadioGroup(
            content=ft.Column([
                ft.Radio(value="fecha_asc", label="FIFO (fecha ascendente)"),
                ft.Radio(value="fecha_desc", label="LIFO (fecha descendente)"),
                ft.Radio(value="cantidad_desc", label="Mayor cantidad primero"),
                ft.Radio(value="cantidad_asc", label="Menor cantidad primero"),
            ]),
            value="fecha_asc",
        )
        self.chk_forzar_cant_sp = ft.Checkbox(
            label="Forzar cantidad (usar stock aunque no haya sustento completo)",
            value=True,
            label_style=ft.TextStyle(size=12),
        )

        # Fecha de corte del reporte stock (informative date)
        from datetime import datetime as _dt
        _today = _dt.now()
        _min_date = _dt(2020, 1, 1)
        _max_date = _dt(_today.year + 1, 12, 31)
        self.fecha_corte_stock_picker = ft.DatePicker(
            on_change=self._on_fecha_corte_stock_change,
            first_date=_min_date,
            last_date=_max_date,
        )
        self.fecha_corte_stock_btn = ft.OutlinedButton(
            "Fecha de corte: Sin especificar",
            icon=ft.Icons.CALENDAR_MONTH,
            on_click=lambda e: self._abrir_fecha_corte_stock(),
            height=32,
            style=ft.ButtonStyle(padding=ft.padding.symmetric(horizontal=10)),
        )
        self.fecha_corte_stock_value = None

        # Date range for diferencia_stock
        from datetime import datetime as _dt2
        _today2 = _dt2.now()
        _min_date2 = _dt2(2020, 1, 1)
        _max_date2 = _dt2(_today2.year + 1, 12, 31)
        self.fecha_desde_sp_picker = ft.DatePicker(
            on_change=self._on_fecha_desde_sp_change,
            first_date=_min_date2,
            last_date=_max_date2,
        )
        self.fecha_hasta_sp_picker = ft.DatePicker(
            on_change=self._on_fecha_hasta_sp_change,
            first_date=_min_date2,
            last_date=_max_date2,
        )
        self.fecha_desde_sp = ft.OutlinedButton(
            "Desde: Sin filtro",
            icon=ft.Icons.CALENDAR_MONTH,
            on_click=lambda e: self._abrir_fecha_desde_sp(),
            height=32,
            style=ft.ButtonStyle(padding=ft.padding.symmetric(horizontal=10)),
        )
        self.fecha_hasta_sp = ft.OutlinedButton(
            "Hasta: Sin filtro",
            icon=ft.Icons.CALENDAR_MONTH,
            on_click=lambda e: self._abrir_fecha_hasta_sp(),
            height=32,
            style=ft.ButtonStyle(padding=ft.padding.symmetric(horizontal=10)),
        )
        self.sp_desde = None
        self.sp_hasta = None
        self.skus_table_sf = ft.DataTable(
            columns=[
                ft.DataColumn(ft.Text("SKU", size=10, weight="bold")),
                ft.DataColumn(ft.Text("ARTÍCULO", size=10, weight="bold")),
                ft.DataColumn(ft.Text("CANT.", size=10, weight="bold")),
                ft.DataColumn(ft.Text("P.U. FACT.", size=10, weight="bold")),
                ft.DataColumn(ft.Text("TOTAL FACT.", size=10, weight="bold")),
                ft.DataColumn(ft.Text("INCLUIR", size=10, weight="bold")),
            ],
            column_spacing=12,
            heading_row_height=32,
            heading_row_color=ft.Colors.with_opacity(0.2, self.app.G360_ACCENT),
            border_radius=12,
            horizontal_lines=ft.border.BorderSide(0.5, G360Theme.border_subtle_color()),
        )
        self.skus_table_container = ft.Container(
            content=ft.Column([
                ft.Row([
                    G360Theme.section_header(ft.Icons.INVENTORY_OUTLINED, "SKU DE LA FACTURA"),
                    ft.ElevatedButton("Marcar todas", on_click=self._marcar_todas_sf, height=26,
                                      style=ft.ButtonStyle(padding=ft.padding.symmetric(horizontal=8),
                                                            shape=ft.RoundedRectangleBorder(radius=10))),
                    ft.ElevatedButton("Desmarcar todas", on_click=self._desmarcar_todas_sf, height=26,
                                      style=ft.ButtonStyle(padding=ft.padding.symmetric(horizontal=8),
                                                            shape=ft.RoundedRectangleBorder(radius=10))),
                ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Container(
                    content=ft.Row([self.skus_table_sf], scroll=ft.ScrollMode.ALWAYS),
                    border_radius=10,
                ),
            ], spacing=6),
            visible=False, padding=12, border_radius=12,
            bgcolor=G360Theme.surface_color(),
            border=ft.border.all(1, G360Theme.border_subtle_color()),
        )

        self.btn_ejecutar = G360Theme.accent_button(
            "Ejecutar reconocimiento",
            ft.Icons.ROCKET_LAUNCH_ROUNDED,
            on_click=self._ejecutar,
            disabled=True,
        )

        self.lbl_total_nc = ft.Text("S/ 0.00", size=24, weight=ft.FontWeight.W_900, color=self.app.G360_ACCENT)
        self.lbl_skus = ft.Text("0 SKU", size=14, color=ft.Colors.ON_SURFACE_VARIANT)
        self.lbl_alertas_count = ft.Text("0 alertas", size=14, color=ft.Colors.ON_SURFACE_VARIANT)
        self.resultados_table = ft.DataTable(
            columns=[],
            rows=[],
            column_spacing=15,
            heading_row_height=35,
            heading_row_color=ft.Colors.with_opacity(0.2, self.app.G360_ACCENT),
            border_radius=15,
            horizontal_lines=ft.border.BorderSide(0.5, G360Theme.border_subtle_color()),
        )
        self.resultados_container = ft.Container(
            visible=False, padding=20, border_radius=G360Theme.CARD_RADIUS,
            bgcolor=G360Theme.surface_color(),
            border=ft.border.all(1, G360Theme.border_subtle_color()),
            shadow=ft.BoxShadow(spread_radius=0, blur_radius=16,
                                 color=G360Theme.SHADOW_COLOR, blur_style=ft.ShadowBlurStyle.OUTER),
        )

        self.btn_expediente = G360Theme.accent_button("Generar expediente", ft.Icons.FOLDER_SHARED_OUTLINED,
                                                       on_click=self._generar_expediente, disabled=True)

        self.alertas_container = ft.Container(visible=False, padding=15, border_radius=18)
        self.aplicar_toggles: dict[str, ft.Checkbox] = {}

    def _construir_tipo_selector(self):
        grupos = {}
        for key, cfg in TIPO_CONFIG.items():
            grupo = cfg.get("grupo", "Sin grupo")
            grupos.setdefault(grupo, []).append((key, cfg))

        radios = []
        for grupo, items in grupos.items():
            radios.append(ft.Container(
                content=ft.Row([
                    ft.Icon(ft.Icons.GROUP_OUTLINED, size=13, color=self.app.G360_ACCENT),
                    ft.Text(f"{grupo}", size=11, weight=ft.FontWeight.W_700, color=self.app.G360_ACCENT),
                ], spacing=6),
                padding=ft.padding.only(left=12, top=10, bottom=4),
            ))
            for key, cfg in items:
                radios.append(ft.Container(
                    content=ft.Row([
                        ft.Radio(value=key),
                        ft.Column([
                            ft.Text(cfg["label"], size=13, weight=ft.FontWeight.W_500, color=ft.Colors.ON_SURFACE),
                            ft.Text(cfg.get("descripcion", ""), size=10, color=ft.Colors.ON_SURFACE_VARIANT),
                        ], spacing=2, expand=True),
                    ], spacing=10),
                    padding=ft.padding.symmetric(vertical=8, horizontal=12),
                    border_radius=10,
                ))
        self.tipo_radio.content.controls = radios

    def _renderizar_ui(self):
        tipo_cfg = TIPO_CONFIG.get(self.tipo_actual, {})
        self._renderizar_config(tipo_cfg)
        layout = self._construir_layout(tipo_cfg)
        self.container.content = layout

    def _renderizar_config(self, tipo_cfg):
        config_cols = []
        config_cols.append(ft.Divider(height=5, color="transparent"))
        config_cols.append(G360Theme.section_header(ft.Icons.PERSON_SEARCH_OUTLINED, "FILTRAR POR VENDEDOR (opcional)"))
        config_cols.append(self.vendedor_dropdown)
        config_cols.append(ft.Divider(height=5, color="transparent"))
        if tipo_cfg.get("tiene_modalidad", False):
            config_cols.append(self.modalidad_dropdown)
            config_cols.append(ft.Container(content=self.factura_dropdown, visible=True, padding=5))
        if tipo_cfg.get("tiene_periodo", False):
            config_cols.append(G360Theme.section_header(ft.Icons.DATE_RANGE_OUTLINED, "PERIODO"))
            config_cols.append(ft.Row([
                self.fecha_desde, ft.Icon(ft.Icons.ARROW_FORWARD, size=14, color=ft.Colors.ON_SURFACE_VARIANT), self.fecha_hasta,
            ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER))
        if tipo_cfg.get("tiene_mecanica", False):
            config_cols.append(G360Theme.section_header(ft.Icons.TUNE_OUTLINED, "MECANICA"))
            config_cols.append(ft.Row([self.mecanica_dropdown, self.mecanica_personalizada], spacing=10))
        if tipo_cfg.get("tiene_meta", False):
            config_cols.append(G360Theme.section_header(ft.Icons.TRENDING_UP_OUTLINED, "META Y REBATE"))
            config_cols.append(ft.Row([
                self.meta_monto, self.rebate_pct,
            ], spacing=10))

        if self.tipo_actual == "feria_preventa":
            config_cols.append(ft.Divider(height=10, color="transparent"))
            config_cols.append(G360Theme.section_header(ft.Icons.BUSINESS_OUTLINED, "FILTRO POR CLIENTE (opcional)"))
            config_cols.append(self.cliente_dropdown_fp)
            config_cols.append(ft.Divider(height=10, color="transparent"))
            config_cols.append(G360Theme.section_header(ft.Icons.DATE_RANGE_OUTLINED, "FILTRO DE FECHAS (opcional)"))
            config_cols.append(ft.Row([
                self.fecha_desde_fp, ft.Icon(ft.Icons.ARROW_FORWARD, size=14, color=ft.Colors.ON_SURFACE_VARIANT),
                self.fecha_hasta_fp,
            ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER))
            config_cols.append(self.fp_desde)
            config_cols.append(self.fp_hasta)
            config_cols.append(ft.Divider(height=10, color="transparent"))
            config_cols.append(G360Theme.section_header(ft.Icons.SORT_OUTLINED, "ORDEN DE ASIGNACIÓN"))
            config_cols.append(self.sort_mode_radio)
            config_cols.append(self.chk_forzar_cant)

        if self.tipo_actual == "diferencia_stock":
            config_cols.append(ft.Divider(height=10, color="transparent"))
            config_cols.append(G360Theme.section_header(ft.Icons.BUSINESS_OUTLINED, "FILTRO POR CLIENTE (opcional)"))
            config_cols.append(self.cliente_dropdown_fp)
            config_cols.append(ft.Divider(height=10, color="transparent"))
            config_cols.append(G360Theme.section_header(ft.Icons.DATE_RANGE_OUTLINED, "FILTRO DE FECHAS (opcional)"))
            config_cols.append(ft.Row([
                self.fecha_desde_sp, ft.Icon(ft.Icons.ARROW_FORWARD, size=14, color=ft.Colors.ON_SURFACE_VARIANT),
                self.fecha_hasta_sp,
            ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER))
            config_cols.append(ft.Divider(height=10, color="transparent"))
            config_cols.append(G360Theme.section_header(ft.Icons.SORT_OUTLINED, "ORDEN DE ASIGNACIÓN"))
            config_cols.append(self.sort_mode_sp_radio)
            config_cols.append(self.chk_forzar_cant_sp)

        if self.tipo_actual == "bonificacion_promocion":
            config_cols.append(ft.Divider(height=10, color="transparent"))
            config_cols.append(G360Theme.section_header(ft.Icons.BUSINESS_OUTLINED, "FILTRO POR CLIENTE (opcional)"))
            config_cols.append(self.cliente_dropdown_pb)

        if self.tipo_actual == "rebate_volumen":
            config_cols.append(ft.Divider(height=10, color="transparent"))
            config_cols.append(G360Theme.section_header(ft.Icons.BUSINESS_OUTLINED, "FILTRO POR CLIENTE (opcional)"))
            config_cols.append(self.cliente_dropdown_pb)
            config_cols.append(ft.Divider(height=10, color="transparent"))
            self._actualizar_lineas()
            config_cols.append(G360Theme.section_header(ft.Icons.CATEGORY_OUTLINED, "LÍNEAS DE PRODUCTO (seleccionar una o más)"))
            config_cols.append(ft.Row([
                self.linea_select_all_btn, self.linea_clear_btn,
            ], spacing=8))
            config_cols.append(self.linea_checkbox_container)
            config_cols.append(ft.Divider(height=10, color="transparent"))
            config_cols.append(G360Theme.section_header(ft.Icons.REQUEST_QUOTE_OUTLINED, "NC/ND A INCLUIR EN CÁLCULO"))
            config_cols.append(ft.Row(
                list(self.categoria_nc_checkboxes.values()),
                spacing=10, wrap=True,
            ))

        if self.tipo_actual == "sustento_factura":
            config_cols.append(ft.Divider(height=10, color="transparent"))
            config_cols.append(G360Theme.section_header(ft.Icons.BUSINESS_OUTLINED, "FACTURA INDIVIDUAL"))
            config_cols.append(self.cliente_dropdown_sf)
            config_cols.append(self.factura_dropdown_sf)
            config_cols.append(self.alertas_nc_container_sf)
            config_cols.append(self.skus_table_container)

        if self.tipo_actual == "descuento_factura":
            config_cols.append(ft.Divider(height=10, color="transparent"))
            config_cols.append(G360Theme.section_header(ft.Icons.BUSINESS_OUTLINED, "FACTURA"))
            config_cols.append(self.cliente_dropdown_df)
            config_cols.append(self.factura_dropdown_df)
            config_cols.append(ft.Divider(height=10, color="transparent"))
            config_cols.append(G360Theme.section_header(ft.Icons.PERCENT_OUTLINED, "DESCUENTO GLOBAL (alternativo al archivo por SKU)"))
            config_cols.append(self.descuento_pct)

        if self.tipo_actual == "diferencia_precio":
            config_cols.append(ft.Divider(height=10, color="transparent"))
            config_cols.append(G360Theme.section_header(ft.Icons.BUSINESS_OUTLINED, "FILTROS (opcional)"))
            config_cols.append(self.cliente_dropdown_pd)
            config_cols.append(ft.Row([
                self.fecha_desde_pd, ft.Icon(ft.Icons.ARROW_FORWARD, size=14, color=ft.Colors.ON_SURFACE_VARIANT), self.fecha_hasta_pd,
            ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER))
            config_cols.append(self.chk_incluir_nc)

        if self.tipo_actual == "descuento_precio":
            config_cols.append(ft.Divider(height=10, color="transparent"))
            config_cols.append(G360Theme.section_header(ft.Icons.BUSINESS_OUTLINED, "FILTROS (opcional)"))
            config_cols.append(self.cliente_dropdown_pd)
            config_cols.append(ft.Row([
                self.fecha_desde_pd, ft.Icon(ft.Icons.ARROW_FORWARD, size=14, color=ft.Colors.ON_SURFACE_VARIANT), self.fecha_hasta_pd,
            ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER))
            self.docx_mode_container.visible = True
            config_cols.append(self.docx_mode_container)
            config_cols.append(self.chk_incluir_nc)

        if self.tipo_actual == "anular_factura":
            config_cols.append(ft.Divider(height=10, color="transparent"))
            config_cols.append(G360Theme.section_header(ft.Icons.CANCEL_OUTLINED, "ANULAR FACTURA"))
            config_cols.append(self.cliente_dropdown_ci)
            config_cols.append(self.factura_dropdown_ci)

        config_cols.append(ft.Divider(height=10, color="transparent"))
        config_cols.append(G360Theme.section_header(ft.Icons.PERSON_OUTLINE, "JUSTIFICACIÓN DEL RECONOCIMIENTO"))
        config_cols.append(ft.Text("Evidencias adjuntas:", size=11, color=ft.Colors.ON_SURFACE_VARIANT))
        config_cols.append(ft.Column(self.evidencias_checkboxes, spacing=4))
        config_cols.append(ft.Divider(height=8, color="transparent"))
        config_cols.append(self.antecedentes)
        config_cols.append(self.observaciones)
        self.config_container.content = ft.Column(config_cols, spacing=8) if config_cols else ft.Text("")

    def _construir_layout(self, tipo_cfg):
        archivos_col = []

        archivos_col.append(G360Theme.section_header(ft.Icons.UPLOAD_FILE_OUTLINED, "HISTORIAL"))
        archivos_col.append(ft.Row([
            ft.Text(self.lbl_historial.value, size=11, color=self.lbl_historial.color, expand=True),
            ft.ElevatedButton("Cargar historial", on_click=self._cargar_historial, height=32,
                              style=ft.ButtonStyle(padding=ft.padding.symmetric(horizontal=12),
                                                    shape=ft.RoundedRectangleBorder(radius=12))),
            ft.ElevatedButton("🔄 Supabase", on_click=self._cargar_desde_supabase, height=32,
                              style=ft.ButtonStyle(
                                  padding=ft.padding.symmetric(horizontal=10),
                                  shape=ft.RoundedRectangleBorder(radius=12),
                                  bgcolor=ft.colors.with_opacity(0.15, ft.colors.BLUE),
                              )),
        ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER))

        if tipo_cfg.get("necesita_lista", False):
            archivos_col.append(ft.Divider(height=10, color="transparent"))
            archivos_col.append(G360Theme.section_header(ft.Icons.LIST_ALT_OUTLINED, "LISTA DE PRECIOS / CONDICIÓN"))
            archivos_col.append(ft.Row([
                ft.Text(self.lbl_lista.value, size=11, color=self.lbl_lista.color, expand=True),
                ft.ElevatedButton("Cargar lista", on_click=self._cargar_lista, height=32,
                                  style=ft.ButtonStyle(padding=ft.padding.symmetric(horizontal=12),
                                                        shape=ft.RoundedRectangleBorder(radius=12))),
            ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER))

        if tipo_cfg.get("necesita_requerimientos", False):
            archivos_col.append(ft.Divider(height=10, color="transparent"))
            archivos_col.append(G360Theme.section_header(ft.Icons.ASSIGNMENT_OUTLINED, "REQUERIMIENTOS"))
            archivos_col.append(ft.Row([
                ft.Text(self.lbl_requerimientos_count.value, size=11, color=self.lbl_requerimientos_count.color, expand=True),
                ft.ElevatedButton("Agregar archivos", on_click=self._cargar_requerimientos, height=32,
                                  style=ft.ButtonStyle(padding=ft.padding.symmetric(horizontal=12),
                                                        shape=ft.RoundedRectangleBorder(radius=12))),
            ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER))
            archivos_col.append(self.lbl_requerimientos_list)

        if self.tipo_actual == "diferencia_stock":
            archivos_col.append(ft.Divider(height=10, color="transparent"))
            archivos_col.append(G360Theme.section_header(ft.Icons.INVENTORY_2_OUTLINED, "STOCK DEL CLIENTE"))
            archivos_col.append(ft.Row([
                ft.Container(expand=True),
                ft.Text(self.lbl_stock_cliente.value, size=11, color=self.lbl_stock_cliente.color),
                self.stock_cliente_clear_btn,
            ], vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=4))
            archivos_col.append(ft.Row([
                ft.Icon(ft.Icons.CALENDAR_TODAY_OUTLINED, size=14, color=self.app.G360_ACCENT),
                self.fecha_corte_stock_btn,
            ], spacing=6))

        if self.tipo_actual in ("descuento_precio", "descuento_factura"):
            archivos_col.append(ft.Divider(height=10, color="transparent"))
            archivos_col.append(G360Theme.section_header(ft.Icons.PERCENT_OUTLINED, "DESCUENTOS POR SKU (CODIGO + %%)"))
            archivos_col.append(ft.Row([
                ft.Text(self.lbl_desc_file.value, size=11, color=self.lbl_desc_file.color, expand=True),
                ft.ElevatedButton("Cargar descuentos", on_click=self._cargar_desc_file, height=32,
                                  style=ft.ButtonStyle(padding=ft.padding.symmetric(horizontal=12),
                                                        shape=ft.RoundedRectangleBorder(radius=12))),
            ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER))

        if tipo_cfg.get("tiene_sku", False):
            archivos_col.append(ft.Divider(height=10, color="transparent"))
            archivos_col.append(G360Theme.section_header(ft.Icons.FILTER_LIST_OUTLINED, "FILTRO SKU (opcional)"))
            archivos_col.append(ft.Row([
                ft.Text(self.lbl_sku_filter.value, size=11, color=self.lbl_sku_filter.color, expand=True),
                self.sku_filter_clear_btn,
                ft.ElevatedButton("Cargar filtro", on_click=self._cargar_sku_filter, height=32,
                                  style=ft.ButtonStyle(padding=ft.padding.symmetric(horizontal=12),
                                                        shape=ft.RoundedRectangleBorder(radius=12))),
            ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER))

        return ft.Column([
            ft.Container(
                content=ft.Column([
                    G360Theme.section_header(ft.Icons.CATEGORY_OUTLINED, "TIPO DE OPERACIÓN"),
                    self.tipo_radio,
                ], spacing=10),
                padding=20, border_radius=G360Theme.CARD_RADIUS,
                bgcolor=G360Theme.surface_color(),
                border=ft.border.all(1, G360Theme.border_subtle_color()),
                shadow=ft.BoxShadow(spread_radius=0, blur_radius=16,
                                     color=G360Theme.SHADOW_COLOR, blur_style=ft.ShadowBlurStyle.OUTER),
            ),
            ft.Divider(height=16, color="transparent"),
            ft.Container(
                content=ft.Column([
                    G360Theme.section_header(ft.Icons.FOLDER_OUTLINED, "ARCHIVOS"),
                    ft.Divider(height=8, color="transparent"),
                    *archivos_col,
                ], spacing=4),
                padding=20, border_radius=G360Theme.CARD_RADIUS,
                bgcolor=G360Theme.surface_color(),
                border=ft.border.all(1, G360Theme.border_subtle_color()),
                shadow=ft.BoxShadow(spread_radius=0, blur_radius=16,
                                     color=G360Theme.SHADOW_COLOR, blur_style=ft.ShadowBlurStyle.OUTER),
            ),
            ft.Divider(height=16, color="transparent"),
            ft.Container(
                content=ft.Column([
                    G360Theme.section_header(ft.Icons.TUNE_OUTLINED, "CONFIGURACIÓN"),
                    ft.Divider(height=8, color="transparent"),
                    self.config_container,
                ], spacing=4),
                padding=20, border_radius=G360Theme.CARD_RADIUS,
                bgcolor=G360Theme.surface_color(),
                border=ft.border.all(1, G360Theme.border_subtle_color()),
                shadow=ft.BoxShadow(spread_radius=0, blur_radius=16,
                                     color=G360Theme.SHADOW_COLOR, blur_style=ft.ShadowBlurStyle.OUTER),
            ),
            ft.Divider(height=16, color="transparent"),
            ft.Row([self.btn_ejecutar], alignment=ft.MainAxisAlignment.CENTER),
            self.resultados_container,
        ], scroll=ft.ScrollMode.AUTO, spacing=4)

    def _on_tipo_change(self, e):
        self.tipo_actual = e.control.value
        self.resultado = None
        self.resultados_container.visible = False
        self.alertas_container.visible = False
        cfg = TIPO_CONFIG.get(self.tipo_actual, {})
        if not cfg.get("necesita_requerimientos", False):
            self.requerimientos_paths.clear()
            self._actualizar_lista_requerimientos()
        try:
            self._renderizar_ui()
        except Exception as ex:
            import traceback
            traceback.print_exc()
            self.app.show_snackbar(f"Error al renderizar UI: {ex}", color=self.app.G360_ERROR)
        self._verificar_puede_ejecutar()
        if self.container:
            self.container.update()
        if self.app.page:
            self.app.page.update()

    def _actualizar_lista_requerimientos(self):
        self.lbl_requerimientos_list.controls.clear()
        if not self.requerimientos_paths:
            self.lbl_requerimientos_count.value = "Ninguno"
            self.lbl_requerimientos_count.color = ft.Colors.ON_SURFACE_VARIANT
        else:
            n = len(self.requerimientos_paths)
            self.lbl_requerimientos_count.value = f"✓ {n} archivo(s) cargado(s)"
            self.lbl_requerimientos_count.color = self.app.G360_SUCCESS
            for p in self.requerimientos_paths:
                name = Path(p).name
                row = ft.Row([
                    ft.Text(f"  • {name}", size=11, color=ft.Colors.ON_SURFACE_VARIANT, expand=True),
                    ft.IconButton(icon=ft.Icons.CLOSE, icon_size=14, height=24, width=24,
                                  on_click=lambda e, ps=str(p): self._quitar_requerimiento(ps)),
                ], spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER)
                self.lbl_requerimientos_list.controls.append(row)

    def _quitar_requerimiento(self, path_str: str):
        rp = Path(path_str)
        self.requerimientos_paths = [p for p in self.requerimientos_paths if Path(p) != rp]
        self._actualizar_lista_requerimientos()
        self._verificar_puede_ejecutar()
        if self.app.page:
            self.app.page.update()

    def _on_cliente_change_sf(self, e):
        cliente = e.control.value
        if cliente:
            self._cargar_facturas_cliente_sf(cliente)
        else:
            self.factura_dropdown_sf.options = []
            self.factura_dropdown_sf.value = None
            self.skus_table_container.visible = False
            self.alertas_nc_container_sf.visible = False
        if self.app.page:
            self.app.page.update()

    def _on_factura_change_sf(self, e):
        factura_id = e.control.value
        if factura_id:
            self._render_sku_table_sf(factura_id)
            self._check_existing_notes_sf(factura_id)
        else:
            self.skus_table_container.visible = False
            self.alertas_nc_container_sf.visible = False
        self._verificar_puede_ejecutar()
        if self.app.page:
            self.app.page.update()

    def _cargar_clientes_dropdown_sf(self):
        self.selector_sf.cargar_clientes(self.df_historial, self.vendedor_dropdown.value)

    def _cargar_facturas_cliente_sf(self, cliente):
        self.selector_sf.cargar_facturas(self.df_historial)

    def _render_sku_table_sf(self, factura_id):
        if self.df_historial is None or not factura_id:
            self.skus_table_container.visible = False
            return
        tipo = factura_id[0]
        resto = factura_id[1:]
        serie, nro = resto.split("-", 1)
        mask = (
            (self.df_historial["TIPO_DOC"].astype(str).str.strip().str.upper().str.startswith(tipo)) &
            (self.df_historial["SERIE"].astype(str).str.strip() == serie) &
            (self.df_historial["NUMERO"].astype(str).str.strip() == nro)
        )
        df_inv = self.df_historial[mask].copy()
        if df_inv.empty:
            self.skus_table_container.visible = False
            return

        self.skus_table_sf.rows.clear()
        cols_req = ["CODIGO", "CANTIDAD", "PRECIO_UNITARIO"]
        if not all(c in df_inv.columns for c in cols_req):
            self.skus_table_container.visible = False
            return

        for _, row in df_inv.head(100).iterrows():
            sku = str(row.get("CODIGO", ""))
            articulo = str(row.get("ARTICULO", ""))[:30]
            cant = int(row.get("CANTIDAD", 0))
            pu = float(row.get("PRECIO_UNITARIO", 0))
            total = cant * pu
            incluir = ft.Checkbox(value=True)

            self.skus_table_sf.rows.append(ft.DataRow(cells=[
                ft.DataCell(ft.Text(sku, size=9)),
                ft.DataCell(ft.Text(articulo, size=9)),
                ft.DataCell(ft.Text(str(cant), size=9)),
                ft.DataCell(ft.Text(f"S/ {pu:.2f}", size=9)),
                ft.DataCell(ft.Text(f"S/ {total:.2f}", size=9)),
                ft.DataCell(incluir),
            ]))

        self.skus_table_container.visible = True

    def _check_existing_notes_sf(self, factura_id):
        if self.df_historial is None or not factura_id:
            self.alertas_nc_container_sf.visible = False
            return
        try:
            from src.core.detector import detectar_notas_en_historial, obtener_notas_de_factura
            notas = detectar_notas_en_historial(self.df_historial)
            ref_notas = obtener_notas_de_factura(notas, factura_id)
            if ref_notas.empty:
                self.alertas_nc_container_sf.visible = False
                return
            total_soles = abs(ref_notas["SOLES"].sum()) if "SOLES" in ref_notas.columns else 0
            skus_afectados = ref_notas["CODIGO"].nunique() if "CODIGO" in ref_notas.columns else 0
            items = [
                ft.Row([
                    ft.Icon(ft.Icons.WARNING_AMBER_OUTLINED, size=16, color=ft.Colors.AMBER_400),
                    ft.Text(f"⚠ NC/NDB existentes: {len(ref_notas)} nota(s), S/ {total_soles:.2f}, {skus_afectados} SKU(s)",
                            size=11, color=ft.Colors.AMBER_400),
                ], spacing=6),
            ]
            for _, nr in ref_notas.head(5).iterrows():
                items.append(ft.Text(
                    f"  • {nr.get('DOC_NOTA','')} | {nr.get('CODIGO','')} | Cant: {nr.get('CANTIDAD',0)} | S/ {abs(nr.get('SOLES',0)):.2f}",
                    size=10, color=ft.Colors.ON_SURFACE_VARIANT,
                ))
            self.alertas_nc_container_sf.content = ft.Column(items, spacing=4)
            self.alertas_nc_container_sf.bgcolor = ft.Colors.with_opacity(0.08, ft.Colors.AMBER_400)
            self.alertas_nc_container_sf.border = ft.border.all(1, ft.Colors.with_opacity(0.2, ft.Colors.AMBER_400))
            self.alertas_nc_container_sf.visible = True
        except ImportError:
            self.alertas_nc_container_sf.visible = False

    def _marcar_todas_sf(self, e):
        for row in self.skus_table_sf.rows:
            for cell in row.cells:
                if isinstance(cell.content, ft.Checkbox):
                    cell.content.value = True
        self._verificar_puede_ejecutar()
        if self.app.page:
            self.app.page.update()

    def _desmarcar_todas_sf(self, e):
        for row in self.skus_table_sf.rows:
            for cell in row.cells:
                if isinstance(cell.content, ft.Checkbox):
                    cell.content.value = False
        self._verificar_puede_ejecutar()
        if self.app.page:
            self.app.page.update()

    def _cargar_clientes_dropdown_df(self):
        self.selector_df.cargar_clientes(self.df_historial, self.vendedor_dropdown.value)

    def _on_cliente_change_df(self, e):
        cliente = e.control.value
        if cliente:
            self._cargar_facturas_cliente_df(cliente)
        else:
            self.factura_dropdown_df.options = []
            self.factura_dropdown_df.value = None
        self._verificar_puede_ejecutar()
        if self.app.page:
            self.app.page.update()

    def _cargar_facturas_cliente_df(self, cliente):
        self.selector_df.cargar_facturas(self.df_historial)

    def _on_factura_change_ci(self, e):
        self._verificar_puede_ejecutar()
        if self.app.page:
            self.app.page.update()

    def _on_factura_change_df(self, e):
        self._verificar_puede_ejecutar()
        if self.app.page:
            self.app.page.update()

    def _on_descuento_pct_change(self, e):
        self._verificar_puede_ejecutar()

    def _on_mecanica_change(self, e):
        es_personalizado = e.control.value == "personalizado"
        self.mecanica_personalizada.visible = es_personalizado
        if self.app.page:
            self.app.page.update()

    def _cargar_clientes_dropdown_pb(self):
        self.selector_pb.cargar_clientes(self.df_historial, self.vendedor_dropdown.value)

    def _on_cliente_change_pb(self, e):
        self._actualizar_lineas()
        self._verificar_puede_ejecutar()
        if self.app.page:
            self.app.page.update()

    def _cargar_sku_filter(self, e):
        self.app.show_loading("Seleccionando filtro SKU...")
        def pick():
            try:
                ruta = self.app._pick_file("Seleccionar archivo SKU")
                if ruta:
                    self.sku_filter_path = ruta
                    self.lbl_sku_filter.value = f"✓ {Path(ruta).name}"
                    self.lbl_sku_filter.color = self.app.G360_SUCCESS
                    if self.tipo_actual == "descuento_factura":
                        self.descuento_pct.disabled = True
                    self.sku_filter_clear_btn.visible = True
                    self._verificar_puede_ejecutar()
            except Exception as ex:
                self.app.show_snackbar(f"Error: {ex}", self.app.G360_ERROR)
            finally:
                self.app.hide_loading()
                if self.app.page:
                    self.app.page.update()
        threading.Thread(target=pick, daemon=True).start()

    def _quitar_sku_filter(self, e):
        self.sku_filter_path = None
        self.lbl_sku_filter.value = "Ninguno"
        self.lbl_sku_filter.color = ft.Colors.ON_SURFACE_VARIANT
        if self.tipo_actual == "descuento_factura":
            self.descuento_pct.disabled = False
        self.sku_filter_clear_btn.visible = False
        self._verificar_puede_ejecutar()
        if self.app.page:
            self.app.page.update()

    def _on_modalidad_change(self, e):
        es_por_factura = e.control.value == "por_factura"
        self.factura_dropdown.visible = es_por_factura
        if es_por_factura and self.df_historial is not None:
            self._cargar_facturas_dropdown()
        if self.app.page:
            self.app.page.update()

    def _on_cliente_change_pd(self, e):
        self._verificar_puede_ejecutar()
        if self.app.page:
            self.app.page.update()

    def _cargar_clientes_dropdown_pd(self):
        self.selector_pd.cargar_clientes(self.df_historial, self.vendedor_dropdown.value)

    def _on_factura_selected(self, e):
        if self.app.page:
            self._verificar_puede_ejecutar()

    def _on_cliente_change_ci(self, e):
        cliente = e.control.value
        if cliente:
            self._cargar_facturas_cliente_ci(cliente)
        else:
            self.factura_dropdown_ci.options = []
            self.factura_dropdown_ci.value = None
        self._verificar_puede_ejecutar()
        if self.app.page:
            self.app.page.update()

    def _cargar_clientes_dropdown_ci(self):
        self.selector_ci.cargar_clientes(self.df_historial, self.vendedor_dropdown.value)

    def _cargar_facturas_cliente_ci(self, cliente):
        self.selector_ci.cargar_facturas(self.df_historial)

    def _abrir_fp_desde(self, e):
        if self.app.page:
            self.app.page.show_dialog(self.fp_desde)

    def _abrir_fp_hasta(self, e):
        if self.app.page:
            self.app.page.show_dialog(self.fp_hasta)

    def _on_cliente_change_fp(self, e):
        self._verificar_puede_ejecutar()
        if self.app.page:
            self.app.page.update()

    def _on_fp_desde_change(self, e):
        val = self.fp_desde.value
        label = val.strftime("%d/%m/%Y") if val else "Sin filtro"
        self.fecha_desde_fp.text = f"Desde: {label}"
        self._verificar_puede_ejecutar()
        if self.app.page:
            self.app.page.update()

    def _on_fp_hasta_change(self, e):
        val = self.fp_hasta.value
        label = val.strftime("%d/%m/%Y") if val else "Sin filtro"
        self.fecha_hasta_fp.text = f"Hasta: {label}"
        self._verificar_puede_ejecutar()
        if self.app.page:
            self.app.page.update()

    def _on_vendedor_change(self, e):
        for dd in [self.cliente_dropdown_sf, self.cliente_dropdown_ci, self.cliente_dropdown_df, self.cliente_dropdown_fp, self.cliente_dropdown_pb, self.cliente_dropdown_pd]:
            dd.value = None
        for dd in [self.factura_dropdown_sf, self.factura_dropdown_ci, self.factura_dropdown_df]:
            dd.options = []
            dd.value = None
        self.skus_table_container.visible = False
        self.alertas_nc_container_sf.visible = False
        self._cargar_clientes_dropdown_sf()
        self._cargar_clientes_dropdown_ci()
        self._cargar_clientes_dropdown_df()
        self._cargar_clientes_dropdown_fp()
        self._cargar_clientes_dropdown_pb()
        self._cargar_clientes_dropdown_pd()
        if self.tipo_actual == "rebate_volumen":
            self._actualizar_lineas()
        self._verificar_puede_ejecutar()
        if self.app.page:
            self.app.page.update()

    def _on_evidencia_change(self, e):
        self.evidencias_opts[e.control.label] = e.control.value

    def _cargar_facturas_dropdown(self):
        if self.df_historial is None:
            return
        df = self.df_historial
        req_cols = ["TIPO_DOC", "SERIE", "NUMERO"]
        if not all(c in df.columns for c in req_cols):
            self.factura_dropdown.options = []
            self.factura_dropdown.value = None
            return
        facturas = df[df["TIPO_DOC"].astype(str).str.upper().str.startswith("F")].copy()
        if not facturas.empty:
            facturas["DOC_ID"] = facturas.apply(
                lambda r: f"{str(r['TIPO_DOC']).strip()[0]}{str(r['SERIE']).strip()}-{str(r['NUMERO']).strip().replace('.0', '')}",
                axis=1,
            )
            facturas["DISPLAY"] = facturas.apply(
                lambda r: f"{r['DOC_ID']} | {str(r.get('FECHA', ''))[:10]} | S/ {r.get('SOLES', 0):,.2f}",
                axis=1,
            )
            unicas = facturas.groupby("DOC_ID").first().reset_index()
            opts = [ft.dropdown.Option(key=r.DOC_ID, text=r.DISPLAY) for r in unicas.itertuples()][:200]
            self.factura_dropdown.options = opts

    def _cargar_vendedores_dropdown(self):
        if self.df_historial is None:
            return
        df = self.df_historial
        tiene_id = "COD_VENDEDOR" in df.columns
        tiene_nom = "VENDEDOR" in df.columns
        if not tiene_nom:
            self.vendedor_dropdown.options = []
            self.vendedor_dropdown.value = None
            return

        if tiene_id:
            mask_valida = df["VENDEDOR"].astype(str).str.strip().ne("") & df["VENDEDOR"].notna()
            vendedores = df.loc[mask_valida, ["COD_VENDEDOR", "VENDEDOR"]].drop_duplicates()
            opts = []
            for _, r in vendedores.iterrows():
                vid = str(r["COD_VENDEDOR"]).replace(".0", "").strip()
                vnom = str(r["VENDEDOR"]).strip()
                display = f"{vid} - {vnom}" if vid else vnom
                opts.append(ft.dropdown.Option(key=vid or vnom, text=display))
        else:
            vendedores = df["VENDEDOR"].dropna().unique()
            opts = [ft.dropdown.Option(key=v, text=v) for v in sorted(vendedores)]
        self.vendedor_dropdown.options = opts
        if len(opts) == 1:
            self.vendedor_dropdown.value = opts[0].key
        else:
            self.vendedor_dropdown.value = None

    def _cargar_clientes_dropdown_fp(self):
        self.selector_fp.cargar_clientes(self.df_historial, self.vendedor_dropdown.value)

    def _actualizar_rango_fechas_fp(self):
        if self.df_historial is None:
            return
        df = self.df_historial
        if "FECHA" not in df.columns or df["FECHA"].dropna().empty:
            return
        min_f = df["FECHA"].min()
        max_f = df["FECHA"].max()
        self.fp_desde.first_date = min_f
        self.fp_desde.last_date = max_f
        self.fp_hasta.first_date = min_f
        self.fp_hasta.last_date = max_f

    @safe_handler
    def _actualizar_lineas(self):
        if self.df_historial is None or "LINEA" not in self.df_historial.columns:
            self.linea_checkbox_container.visible = False
            return
        df = self.df_historial
        cliente = self.cliente_dropdown_pb.value
        if cliente and "CLIENTE" in df.columns:
            df = df[df["CLIENTE"].astype(str).str.strip() == cliente.strip()]
        vendedor_id = self.vendedor_dropdown.value
        if vendedor_id and "COD_VENDEDOR" in df.columns:
            df = df[df["COD_VENDEDOR"].astype(str).str.strip() == vendedor_id.strip()]
        lineas = sorted(df["LINEA"].dropna().unique())
        prev_values = {name: cb.value for name, cb in self.linea_checkboxes.items()}
        checks = []
        self.linea_checkboxes.clear()
        for linea in lineas:
            cb = ft.Checkbox(
                label=linea,
                value=prev_values.get(linea, False),
                label_style=ft.TextStyle(size=12),
                on_change=self._on_linea_toggle,
            )
            self.linea_checkboxes[linea] = cb
            checks.append(cb)
        if checks:
            self.linea_checkbox_container.content = ft.Column(checks, spacing=4, scroll=ft.ScrollMode.AUTO)
            self.linea_checkbox_container.visible = True
        else:
            self.linea_checkbox_container.visible = False

    def _on_linea_toggle(self, e):
        self._verificar_puede_ejecutar()

    def _linea_select_all(self, e):
        for cb in self.linea_checkboxes.values():
            cb.value = True
        self._verificar_puede_ejecutar()
        if self.app.page:
            self.app.page.update()

    def _linea_clear(self, e):
        for cb in self.linea_checkboxes.values():
            cb.value = False
        self._verificar_puede_ejecutar()
        if self.app.page:
            self.app.page.update()

    def _cargar_historial(self, e):
        self.app.show_loading("Seleccionando historial...")
        def pick():
            try:
                ruta = self.app._pick_file("Seleccionar Historial")
                if ruta:
                    self.historial_path = ruta
                    df = read_erp_file(ruta)
                    from src.validation.normalization import NormalizationEngine
                    norm = NormalizationEngine()
                    self.df_historial = norm.normalizar_historial(df)
                    from src.core.document_classifier import DocumentClassifier
                    clf = DocumentClassifier()
                    self.df_historial = clf.classify(self.df_historial)
                    self.lbl_historial.value = f"✓ {Path(ruta).name} ({len(df)} filas)"
                    self.lbl_historial.color = self.app.G360_SUCCESS
                    self._cargar_facturas_dropdown()
                    self._cargar_vendedores_dropdown()
                    self._cargar_clientes_dropdown_sf()
                    self._cargar_clientes_dropdown_fp()
                    self._cargar_clientes_dropdown_ci()
                    self._cargar_clientes_dropdown_df()
                    self._cargar_clientes_dropdown_pb()
                    self._cargar_clientes_dropdown_pd()
                    self._actualizar_lineas()
                    self._actualizar_rango_fechas_fp()
                    self._verificar_puede_ejecutar()
            except Exception as ex:
                self.app.show_snackbar(f"Error: {ex}", self.app.G360_ERROR)
            finally:
                self.app.hide_loading()
                if self.app.page:
                    self.app.page.update()
        threading.Thread(target=pick, daemon=True).start()

    def _cargar_desde_supabase(self, e):
        """Dialog para cargar historial directamente desde Supabase (g360-ventas-db)."""
        import flet as ft
        from src.validation.normalization import NormalizationEngine
        from src.core.document_classifier import DocumentClassifier

        self.supabase_cliente_input = ft.TextField(
            label="ID Cliente (ej: 00068414)",
            hint_text="Ingresa el ID del cliente",
            expand=True,
            border_radius=12,
        )
        self.supabase_sku_input = ft.TextField(
            label="SKU (opcional)",
            hint_text="Deja vacío para todo el historial del cliente",
            expand=True,
            border_radius=12,
        )

        def cerrar_dialog(_):
            dlg.open = False
            page.update()

        def confirmar(_):
            id_cliente = self.supabase_cliente_input.value
            sku = (self.supabase_sku_input.value or "").strip()
            if not id_cliente:
                self.app.show_snackbar("Ingresa el ID del cliente", self.app.G360_ERROR)
                return
            cerrar_dialog(_)
            self._fetch_supabase(id_cliente, sku)

        dlg = ft.AlertDialog(
            title=ft.Row([
                ft.Icon(ft.Icons.CLOUD_OUTLINE, color=self.app.G360_ACCENT),
                ft.Text("Historial desde Supabase", size=14, weight=ft.FontWeight.BOLD),
            ], spacing=8),
            content=ft.Column([
                ft.Text("Conectará con g360-ventas-db. Solo lectura.", size=12, color=ft.Colors.ON_SURFACE_VARIANT),
                ft.Divider(height=6, color="transparent"),
                self.supabase_cliente_input,
                self.supabase_sku_input,
            ], spacing=8, tight=True),
            actions=[
                ft.TextButton("Cancelar", on_click=cerrar_dialog),
                ft.ElevatedButton("Conectar", on_click=confirmar,
                                  style=ft.ButtonStyle(bgcolor=self.app.G360_ACCENT)),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )

        page = self.app.page
        page.overlay.append(dlg)
        dlg.open = True
        page.update()

    def _fetch_supabase(self, id_cliente: str, sku: str = ""):
        """Busca datos en Supabase y pobla el historial."""
        self.app.show_loading("Consultando Supabase...")

        def worker():
            try:
                cli = SupabaseVentasClient()
                df = cli.fetch_historial(id_cliente=id_cliente, id_articulo=sku or None)
                if df.empty:
                    self.app.show_snackbar("Sin datos para este cliente" + (f" SKU={sku}" if sku else ""), self.app.G360_ERROR)
                    return

                norm = NormalizationEngine()
                df_norm = norm.normalizar_historial(df)
                clf = DocumentClassifier()
                df_norm = clf.classify(df_norm)

                self.df_historial = df_norm
                fuente = f"Supabase ({len(df)} filas)"
                if sku:
                    fuente += f" SKU={sku}"
                self.lbl_historial.value = f"✓ {fuente}"
                self.lbl_historial.color = self.app.G360_SUCCESS
                self._cargar_facturas_dropdown()
                self._cargar_vendedores_dropdown()
                self._cargar_clientes_dropdown_sf()
                self._cargar_clientes_dropdown_fp()
                self._cargar_clientes_dropdown_ci()
                self._cargar_clientes_dropdown_df()
                self._cargar_clientes_dropdown_pb()
                self._cargar_clientes_dropdown_pd()
                self._actualizar_lineas()
                self._actualizar_rango_fechas_fp()
                self._verificar_puede_ejecutar()
            except Exception as ex:
                self.app.show_snackbar(f"Error Supabase: {ex}", self.app.G360_ERROR)
            finally:
                self.app.hide_loading()
                if self.app.page:
                    self.app.page.update()

        threading.Thread(target=worker, daemon=True).start()

    def _cargar_lista(self, e):
        self.app.show_loading("Seleccionando lista de precios...")
        def pick():
            try:
                ruta = self.app._pick_file("Seleccionar Lista de Precios")
                if ruta:
                    self.lista_path = ruta
                    self.lbl_lista.value = f"✓ {Path(ruta).name}"
                    self.lbl_lista.color = self.app.G360_SUCCESS
                    self._verificar_puede_ejecutar()
            except Exception as ex:
                self.app.show_snackbar(f"Error: {ex}", self.app.G360_ERROR)
            finally:
                self.app.hide_loading()
                if self.app.page:
                    self.app.page.update()
        threading.Thread(target=pick, daemon=True).start()

    def _cargar_desc_file(self, e):
        self.app.show_loading("Seleccionando archivo de descuentos...")
        def pick():
            try:
                ruta = self.app._pick_file("Seleccionar Archivo de Descuentos (SKU + %%)")
                if ruta:
                    self.desc_file_path = ruta
                    self.lbl_desc_file.value = f"✓ {Path(ruta).name}"
                    self.lbl_desc_file.color = self.app.G360_SUCCESS
                    self._verificar_puede_ejecutar()
            except Exception as ex:
                self.app.show_snackbar(f"Error: {ex}", self.app.G360_ERROR)
            finally:
                self.app.hide_loading()
                if self.app.page:
                    self.app.page.update()
        threading.Thread(target=pick, daemon=True).start()

    def _quitar_stock_cliente(self, e):
        self.stock_cliente_path = None
        self.lbl_stock_cliente.value = "Ninguno"
        self.lbl_stock_cliente.color = ft.Colors.ON_SURFACE_VARIANT
        self.stock_cliente_clear_btn.visible = False
        self._verificar_puede_ejecutar()
        if self.app.page:
            self.app.page.update()

    def _cargar_stock_cliente(self, e):
        self.app.show_loading("Seleccionando archivo de stock...")
        def pick():
            try:
                ruta = self.app._pick_file("Seleccionar Stock del Cliente")
                if ruta:
                    self.stock_cliente_path = ruta
                    self.lbl_stock_cliente.value = f"✓ {Path(ruta).name}"
                    self.lbl_stock_cliente.color = self.app.G360_SUCCESS
                    self.stock_cliente_clear_btn.visible = True
                    self._verificar_puede_ejecutar()
            except Exception as ex:
                self.app.show_snackbar(f"Error: {ex}", self.app.G360_ERROR)
            finally:
                self.app.hide_loading()
                if self.app.page:
                    self.app.page.update()
        threading.Thread(target=pick, daemon=True).start()

    def _abrir_fecha_corte_stock(self):
        if self.app.page:
            self.app.page.show_dialog(self.fecha_corte_stock_picker)

    def _on_fecha_corte_stock_change(self, e):
        val = self.fecha_corte_stock_picker.value
        label = val.strftime("%d/%m/%Y") if val else "Sin especificar"
        self.fecha_corte_stock_btn.text = f"Fecha de corte: {label}"
        self.fecha_corte_stock_value = val
        if self.app.page:
            self.app.page.update()

    def _abrir_fecha_desde_sp(self):
        if self.app.page:
            self.app.page.show_dialog(self.fecha_desde_sp_picker)

    def _abrir_fecha_hasta_sp(self):
        if self.app.page:
            self.app.page.show_dialog(self.fecha_hasta_sp_picker)

    def _on_fecha_desde_sp_change(self, e):
        val = self.fecha_desde_sp_picker.value
        label = val.strftime("%d/%m/%Y") if val else "Sin filtro"
        self.fecha_desde_sp.text = f"Desde: {label}"
        self.sp_desde = val
        if self.app.page:
            self.app.page.update()

    def _on_fecha_hasta_sp_change(self, e):
        val = self.fecha_hasta_sp_picker.value
        label = val.strftime("%d/%m/%Y") if val else "Sin filtro"
        self.fecha_hasta_sp.text = f"Hasta: {label}"
        self.sp_hasta = val
        if self.app.page:
            self.app.page.update()

    @safe_handler
    def _cargar_requerimientos(self, e):
        self.app.show_loading("Seleccionando requerimientos...")
        def pick():
            try:
                rutas = self.app._pick_files("Seleccionar Requerimientos")
                if rutas:
                    for r in rutas:
                        rp = Path(r)
                        if rp not in [Path(p) for p in self.requerimientos_paths]:
                            self.requerimientos_paths.append(r)
                    self._actualizar_lista_requerimientos()
                    self._verificar_puede_ejecutar()
            except Exception as ex:
                self.app.show_snackbar(f"Error: {ex}", self.app.G360_ERROR)
            finally:
                self.app.hide_loading()
                if self.app.page:
                    self.app.page.update()
        threading.Thread(target=pick, daemon=True).start()

    def _verificar_puede_ejecutar(self):
        if not self.app.page:
            return
        cfg = TIPO_CONFIG.get(self.tipo_actual, {})
        puede = self.df_historial is not None
        if cfg.get("necesita_lista", False):
            puede = puede and self.lista_path is not None
        if cfg.get("necesita_requerimientos", False):
            puede = puede and len(self.requerimientos_paths) > 0
        if self.tipo_actual == "anular_factura":
            puede = puede and self.factura_dropdown_ci.value is not None
        if self.tipo_actual == "sustento_factura":
            tiene_skus = False
            for row in self.skus_table_sf.rows:
                for cell in row.cells:
                    if isinstance(cell.content, ft.Checkbox) and cell.content.value:
                        tiene_skus = True
                        break
            puede = puede and self.factura_dropdown_sf.value is not None and tiene_skus
        if self.tipo_actual == "diferencia_stock":
            puede = puede and self.stock_cliente_path is not None
        if self.tipo_actual == "descuento_factura":
            puede = puede and self.factura_dropdown_df.value is not None
            if puede:
                try:
                    tiene_pct = float(self.descuento_pct.value or "0") > 0
                except ValueError:
                    tiene_pct = False
                puede = puede and (self.desc_file_path is not None or tiene_pct)
        if self.tipo_actual == "descuento_precio":
            puede = puede and self.desc_file_path is not None
        if self.tipo_actual == "rebate_volumen":
            try:
                meta_val = float(self.meta_monto.value or "0")
                pct_val = float(self.rebate_pct.value or "0")
                puede = puede and meta_val > 0 and pct_val > 0
            except ValueError:
                puede = False
        self.btn_ejecutar.disabled = not puede
        try:
            self.btn_ejecutar.update()
        except AssertionError:
            pass
        if self.app and self.app.page:
            self.app.page.update()

    def _collect_ui_values(self) -> dict:
        """Recolecta valores de los controles UI en un dict para config_builder."""
        skus_incluidos = []
        for row in self.skus_table_sf.rows:
            incluir = None
            sku = None
            for cell in row.cells:
                if isinstance(cell.content, ft.Checkbox):
                    incluir = cell.content.value
                elif isinstance(cell.content, ft.Text):
                    if sku is None:
                        sku = cell.content.value
            if incluir and sku:
                skus_incluidos.append(sku)

        return {
            "modalidad": self.modalidad_dropdown.value,
            "factura_id": self.factura_dropdown.value,
            "fecha_desde": self.fecha_desde.value,
            "fecha_hasta": self.fecha_hasta.value,
            "fecha_desde_pd": self.fecha_desde_pd.value,
            "fecha_hasta_pd": self.fecha_hasta_pd.value,
            "mecanica": self.mecanica_dropdown.value,
            "mecanica_personalizada": self.mecanica_personalizada.value,
            "meta_monto": self.meta_monto.value,
            "rebate_pct": self.rebate_pct.value,
            "requerimientos_paths": self.requerimientos_paths,
            "sort_mode": self.sort_mode_radio.value,
            "forzar_cantidad": self.chk_forzar_cant.value,
            "fp_desde": self.fp_desde.value,
            "fp_hasta": self.fp_hasta.value,
            "cliente_fp": self.cliente_dropdown_fp.value,
            "stock_cliente_path": self.stock_cliente_path,
            "fecha_corte_stock": self.fecha_corte_stock_value,
            "sort_mode_sp": self.sort_mode_sp_radio.value,
            "forzar_cantidad_sp": self.chk_forzar_cant_sp.value,
            "sp_desde": self.sp_desde,
            "sp_hasta": self.sp_hasta,
            "cliente_sp": self.cliente_dropdown_fp.value,
            "factura_ci": self.factura_dropdown_ci.value,
            "factura_sf": self.factura_dropdown_sf.value,
            "factura_df": self.factura_dropdown_df.value,
            "descuento_pct": self.descuento_pct.value,
            "desc_file_path": self.desc_file_path,
            "sku_filter_path": self.sku_filter_path,
            "cliente_pd": self.cliente_dropdown_pd.value,
            "chk_incluir_nc": self.chk_incluir_nc.value,
            "cliente_pb": self.cliente_dropdown_pb.value,
            "lineas_selected": [name for name, cb in self.linea_checkboxes.items() if cb.value],
            "categorias_nc": [cat for cat, cb in self.categoria_nc_checkboxes.items() if cb.value],
            "skus_incluidos": skus_incluidos,
            "vendedor_id": self.vendedor_dropdown.value,
            "df_historial_full": self.df_historial,
        }

    @safe_handler
    def _ejecutar(self, e):
        self.app.show_loading("Ejecutando reconocimiento...")

        def task():
            try:
                from src.ui.config_builder import build_config, build_datos_exp
                estrategia, variante = ESTRATEGIA_POR_TIPO.get(self.tipo_actual, ("", ""))

                ui = self._collect_ui_values()
                config = build_config(self.tipo_actual, ui)
                datos_exp = build_datos_exp(self.tipo_actual, self.df_historial, config, ui)

                exp = ExpedienteComercial(
                    nombre=TIPO_CONFIG.get(self.tipo_actual, {}).get("label", ""),
                    familia=TIPO_CONFIG.get(self.tipo_actual, {}).get("label", ""),
                    estrategia=estrategia,
                    variante=variante,
                    datos=datos_exp,
                    contexto=PipelineContext(
                        config=config,
                        antecedentes=self.antecedentes.value or "",
                        observaciones=self.observaciones.value or "",
                    ),
                )

                condiciones = []
                cfg = TIPO_CONFIG.get(self.tipo_actual, {})
                if cfg.get("necesita_lista", False) and self.lista_path:
                    cond_df = read_erp_file(self.lista_path)
                    condiciones.append(cond_df)
                if self.tipo_actual in ("descuento_precio", "descuento_factura") and self.desc_file_path:
                    cond_df = read_erp_file(self.desc_file_path)
                    condiciones.append(cond_df)
                exp.condiciones = condiciones

                pipeline = Pipeline()
                exp = pipeline.ejecutar(exp)
                self.resultado = exp

                if exp.resultado and not exp.resultado.dataframe.empty:
                    df_res = exp.resultado.dataframe
                    from src.core.document_classifier import resumen_global
                    solo_ncnd = self.df_historial[
                        self.df_historial["TIPO_CLASE"].astype(str).str.lower() != "factura"
                    ] if "TIPO_CLASE" in self.df_historial.columns else self.df_historial
                    if not solo_ncnd.empty and "CODIGO" in solo_ncnd.columns and "SKU" in df_res.columns:
                        skus_procesados = set(df_res["SKU"].astype(str).str.strip())
                        solo_ncnd = solo_ncnd[solo_ncnd["CODIGO"].astype(str).str.strip().isin(skus_procesados)]
                    full_resumen = resumen_global(solo_ncnd)
                    if full_resumen:
                        exp.resultado.metricas["nc_detalle"] = full_resumen
                    if "NC_ASOCIADAS" in self.df_historial.columns:
                        doc_to_nc = dict(zip(
                            self.df_historial["DOC_ID"],
                            self.df_historial["NC_ASOCIADAS"].apply(lambda x: ", ".join(x) if x else "")
                        ))
                        def _map_nc_existente(factura_val):
                            val = str(factura_val).strip()
                            if not val or val == "nan":
                                return ""
                            if ";" in val:
                                parts = [p.strip().split(" (")[0].strip() for p in val.split(";") if p.strip()]
                                nc_parts = [doc_to_nc.get(p, "") for p in parts if doc_to_nc.get(p)]
                                return ", ".join(sorted(set(nc_parts))) if nc_parts else ""
                            return doc_to_nc.get(val, "")
                        factura_col = "FACTURA" if "FACTURA" in df_res.columns else "FACTURAS" if "FACTURAS" in df_res.columns else None
                        if factura_col:
                            df_res["NC_EXISTENTE"] = df_res[factura_col].apply(_map_nc_existente)
                    from src.core.nc_auditor import CreditNoteAuditor
                    nc_alertas = CreditNoteAuditor().auditar(self.df_historial)
                    exp.resultado.metricas["nc_alertas"] = nc_alertas
                    df_res["AUDITORIA_NC"] = CreditNoteAuditor.build_audit_column(df_res, nc_alertas)
                    if "TIPO_CLASE" in self.df_historial.columns:
                        notas_df = self.df_historial[self.df_historial["TIPO_CLASE"] != "factura"]
                        if not notas_df.empty:
                            facturas_con_nc = notas_df["FACTURA_REF"].dropna().unique()
                            if len(facturas_con_nc) > 0:
                                from src.domain import BusinessAlert
                                exp.resultado.alertas.insert(0, BusinessAlert(
                                    tipo="warning", severidad="baja",
                                    mensaje=f"Facturas con NC/ND existentes en el rango: "
                                            f"{', '.join(sorted(facturas_con_nc)[:5])}"
                                            f"{'...' if len(facturas_con_nc) > 5 else ''}. "
                                            f"Evaluar ajustes manuales.",
                                    motor=exp.estrategia if exp.estrategia else "N/A",
                                ))

                if exp.resultado and not exp.resultado.dataframe.empty:
                    self._mostrar_resultado()
                else:
                    alertas_ordenadas = sorted(exp.alertas or [], key=lambda a: 0 if a.tipo == "error" else 1 if a.tipo == "warning" else 2)
                    alert_msgs = [a.mensaje for a in alertas_ordenadas][:3]
                    detail = " | ".join(alert_msgs) if alert_msgs else "Sin alertas"
                    self.app.show_snackbar(f"Sin resultados: {detail}", self.app.G360_WARNING)

            except Exception as ex:
                import traceback
                traceback.print_exc()
                self.app.show_snackbar(f"❌ Error: {str(ex)}", self.app.G360_ERROR)
            finally:
                self.app.hide_loading()
                if self.app.page:
                    self.app.page.update()

        threading.Thread(target=task, daemon=True).start()

    def _mostrar_resultado(self):
        from src.ui.resultados_view import render_resultado
        if not self.resultado or not self.resultado.resultado:
            return

        result = render_resultado(
            self.resultado, self.tipo_actual, self.df_historial,
            self.app.G360_ACCENT, self.app.G360_SUCCESS,
        )

        self.lbl_total_nc.value = result["total_nc"]
        self.lbl_skus.value = result["skus_label"]
        self.lbl_alertas_count.value = result["alertas_label"]

        self.resultados_table.columns.clear()
        self.resultados_table.rows.clear()

        if result["content"] is None:
            self.resultados_container.visible = False
            return

        if result["aplicar_toggles"]:
            self.aplicar_toggles = result["aplicar_toggles"]
            for linea, (cb, _) in self.aplicar_toggles.items():
                def _make_on_toggle(ln):
                    def _on_toggle(e):
                        checked = sum(
                            float(val)
                            for l, (c, val) in self.aplicar_toggles.items() if c.value
                        )
                        self.lbl_total_nc.value = f"S/ {checked:,.2f}"
                        if self.app.page:
                            self.app.page.update()
                    return _on_toggle
                cb.on_change = _make_on_toggle(linea)

        for col in result["table_columns"]:
            self.resultados_table.columns.append(col)
        for row in result["table_rows"]:
            self.resultados_table.rows.append(row)

        if result["alertas_visible"] and result["alertas_content"]:
            self.alertas_container = result["alertas_content"]
        else:
            self.alertas_container.visible = False

        summary_panel = result["content"]
        summary_panel.controls.append(
            ft.Container(
                content=ft.Row([self.resultados_table], scroll=ft.ScrollMode.ALWAYS),
                padding=15, border_radius=14,
                bgcolor=G360Theme.surface_variant_color(),
                border=ft.border.all(1, G360Theme.border_subtle_color()),
            )
        )
        summary_panel.controls.append(ft.Row([self.btn_expediente], alignment=ft.MainAxisAlignment.CENTER, spacing=20))

        self.resultados_container.content = summary_panel
        self.resultados_container.visible = True
        self.btn_expediente.disabled = False

    def _generar_expediente(self, e):
        if not self.resultado or not self.resultado.resultado:
            return
        self.app.show_loading("Generando Expediente...")

        def task():
            try:
                from src.ui.expediente_service import generar_expediente

                cliente_dd_map = {
                    "sustento_factura": self.cliente_dropdown_sf,
                    "anular_factura": self.cliente_dropdown_ci,
                    "descuento_factura": self.cliente_dropdown_df,
                    "feria_preventa": self.cliente_dropdown_fp,
                    "bonificacion_promocion": self.cliente_dropdown_pb,
                    "rebate_volumen": self.cliente_dropdown_pb,
                    "diferencia_precio": self.cliente_dropdown_pd,
                    "descuento_precio": self.cliente_dropdown_pd,
                }
                dd = cliente_dd_map.get(self.tipo_actual)
                cliente_value = dd.value if dd and dd.value else ""

                vendedor_key = self.vendedor_dropdown.value or ""
                vendedor_display = vendedor_key
                if vendedor_key:
                    for opt in self.vendedor_dropdown.options:
                        if opt.key == vendedor_key:
                            vendedor_display = opt.text or vendedor_key
                            break

                evidencias = {k: v for k, v in self.evidencias_opts.items() if v}

                exp_dir = generar_expediente(
                    resultado=self.resultado,
                    tipo_actual=self.tipo_actual,
                    df_historial=self.df_historial,
                    cliente_value=cliente_value,
                    vendedor_value=vendedor_key,
                    vendedor_display=vendedor_display,
                    evidencias=evidencias,
                    antecedentes=self.antecedentes.value or "",
                    observaciones=self.observaciones.value or "",
                    desktop_path=self.app._get_desktop_path(),
                )

                self.app.show_snackbar(
                    f"\u2705 Expediente generado: {exp_dir.name}",
                    self.app.G360_SUCCESS,
                )
                if os.name == "nt":
                    os.startfile(str(exp_dir))

            except Exception as ex:
                self.app.show_snackbar(f"\u274c Error: {ex}", self.app.G360_ERROR)
            finally:
                self.app.hide_loading()

        threading.Thread(target=task, daemon=True).start()

    def reset(self):
        """Limpia todos los inputs y cache del view."""
        self.historial_path = None
        self.lista_path = None
        self.requerimientos_paths = []
        self.df_historial = None
        self.resultado = None
        self.sku_filter_path = None

        self._reset_ui()
        self.app.show_snackbar("✅ App reseteado", self.app.G360_SUCCESS)

    def _reset_ui(self):
        if hasattr(self, 'cliente_dropdown_pd'):
            self.cliente_dropdown_pd.value = None
        if hasattr(self, 'fecha_desde_pd'):
            self.fecha_desde_pd.value = ""
        if hasattr(self, 'fecha_hasta_pd'):
            self.fecha_hasta_pd.value = ""
        if hasattr(self, 'cliente_dropdown_ci'):
            self.cliente_dropdown_ci.value = None
        if hasattr(self, 'factura_dropdown_ci'):
            self.factura_dropdown_ci.value = None
        if hasattr(self, 'factura_dropdown'):
            self.factura_dropdown.options = []
            self.factura_dropdown.value = None
        if hasattr(self, 'vendedor_dropdown'):
            self.vendedor_dropdown.value = None
        if hasattr(self, 'modalidad_dropdown'):
            self.modalidad_dropdown.value = "por_factura"
        if hasattr(self, 'docx_mode_radio'):
            self.docx_mode_radio.value = "unico"
        if hasattr(self, 'mecanica_dropdown'):
            self.mecanica_dropdown.value = "12+1"
        if hasattr(self, 'mecanica_personalizada'):
            self.mecanica_personalizada.value = ""
            self.mecanica_personalizada.visible = False
        if hasattr(self, 'fecha_desde'):
            self.fecha_desde.value = ""
        if hasattr(self, 'fecha_hasta'):
            self.fecha_hasta.value = ""
        if hasattr(self, 'observaciones'):
            self.observaciones.value = ""
        if hasattr(self, 'evidencias_checkboxes'):
            for cb in self.evidencias_checkboxes:
                cb.value = False
        if hasattr(self, 'evidencias_opts'):
            for k in self.evidencias_opts:
                self.evidencias_opts[k] = False
        if hasattr(self, 'lbl_historial'):
            self.lbl_historial.value = "Ninguno"
        if hasattr(self, 'lbl_lista'):
            self.lbl_lista.value = "Ninguno"
        if hasattr(self, 'lbl_requerimientos_count'):
            self.lbl_requerimientos_count.value = "Ninguno"
        if hasattr(self, 'lbl_requerimientos_list'):
            self.lbl_requerimientos_list.controls.clear()
        if hasattr(self, 'cliente_dropdown_df'):
            self.cliente_dropdown_df.value = None
        if hasattr(self, 'factura_dropdown_df'):
            self.factura_dropdown_df.options = []
            self.factura_dropdown_df.value = None
        if hasattr(self, 'cliente_dropdown_pb'):
            self.cliente_dropdown_pb.value = None
        if hasattr(self, 'descuento_pct'):
            self.descuento_pct.value = ""
            self.descuento_pct.disabled = False
        if hasattr(self, 'lbl_sku_filter'):
            self.lbl_sku_filter.value = "Ninguno"
        if hasattr(self, 'sku_filter_clear_btn'):
            self.sku_filter_clear_btn.visible = False
        self.sku_filter_path = None
        if hasattr(self, 'lbl_stock_cliente'):
            self.lbl_stock_cliente.value = "Ninguno"
        if hasattr(self, 'stock_cliente_clear_btn'):
            self.stock_cliente_clear_btn.visible = False
        self.stock_cliente_path = None
        if hasattr(self, 'sort_mode_sp_radio'):
            self.sort_mode_sp_radio.value = "fecha_asc"
        if hasattr(self, 'chk_forzar_cant_sp'):
            self.chk_forzar_cant_sp.value = True
        if hasattr(self, 'fecha_desde_sp'):
            self.fecha_desde_sp.text = "Desde: Sin filtro"
        if hasattr(self, 'fecha_hasta_sp'):
            self.fecha_hasta_sp.text = "Hasta: Sin filtro"
        self.sp_desde = None
        self.sp_hasta = None
        if hasattr(self, 'fecha_corte_stock_btn'):
            self.fecha_corte_stock_btn.text = "Fecha de corte: Sin especificar"
        if hasattr(self, 'fecha_corte_stock_value'):
            self.fecha_corte_stock_value = None
        if hasattr(self, 'tipo_radio'):
            self.tipo_radio.value = "diferencia_precio"
            self._on_tipo_change(None)
