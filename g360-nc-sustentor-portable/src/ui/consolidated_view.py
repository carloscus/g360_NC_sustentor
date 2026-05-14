import flet as ft
from typing import List, Dict
from src.reports.consolidated import ReporteConsolidado
import logging

logger = logging.getLogger(__name__)

class ConsolidatedReportsModule:
    """
    Módulo de UI para Reportes Consolidados mejorado con diseño G360 Premium.
    """
    def __init__(self, app):
        self.app = app
        self.selected_vendors = []
        self.selected_clients = []
        self.selected_lines = []
        self.all_vendors = []
        self.all_clients = []
        self.lineas_resumen = []
        self.selection_counter = None
        self._lines_search_text = ""
        self._init_components()

    def _init_components(self):
        # Dashboard: lista única de líneas con filtro
        self.lines_list = ft.Column(spacing=6, scroll=ft.ScrollMode.ADAPTIVE, expand=True)
        self.lines_search = ft.TextField(
            label="Filtrar líneas...",
            hint_text="Escriba para buscar...",
            prefix_icon=ft.icons.SEARCH,
            on_change=self._filter_lines,
            height=38,
            width=280,
            bgcolor=ft.colors.with_opacity(0.05, ft.colors.WHITE),
            border_radius=10,
        )

        # Autocompletes
        self.vendor_autocomplete = ft.TextField(
            label="Buscar vendedor...",
            hint_text="Escriba para buscar...",
            on_change=self._filter_vendor_options,
            width=280,
            height=45,
        )
        self.client_autocomplete = ft.TextField(
            label="Buscar cliente...",
            hint_text="Escriba para buscar...",
            on_change=self._filter_client_options,
            width=280,
            height=45,
        )

        self.vendor_chips = ft.Container(content=ft.Row([], wrap=True, spacing=3), height=36)
        self.client_chips = ft.Container(content=ft.Row([], wrap=True, spacing=3), height=36)
        
        self.chk_all_vendors = ft.Checkbox(label="Todos", value=False, on_change=self._toggle_all_vendors)
        self.chk_all_clients = ft.Checkbox(label="Todos", value=False, on_change=self._toggle_all_clients)

        # Radio Group para Agrupación
        self.rg_agrupacion = ft.RadioGroup(
            content=ft.Row([
                ft.Radio(value="ID_ARTICULO", label="SKU"),
                ft.Radio(value="NOM_LINEA", label="Línea"),
                ft.Radio(value="ID_CLIENTE", label="Cliente"),
                ft.Radio(value="PERIODO_MES", label="Mes"),
                ft.Radio(value="FACTURA", label="Fact."),
                ft.Radio(value="PARETO_CLIENTE", label="Pareto"),
            ], spacing=10),
            value="PERIODO_MES",
            on_change=self._on_grouping_change,
        )
        
        self.chk_comparacion = ft.Checkbox(label="Comparar por Mes", value=False)

        self.btn_generate = ft.ElevatedButton(
            "GENERAR REPORTE EXCEL",
            icon=ft.icons.ROCKET_LAUNCH_ROUNDED,
            height=60,
            width=350,
            style=ft.ButtonStyle(
                bgcolor={"": self.app.G360_ACCENT, "disabled": "white10"},
                color="white",
                shape=ft.RoundedRectangleBorder(radius=15),
            ),
            on_click=self._generate_report,
        )

        self.status = ft.Text("", size=12, weight="bold")
        self.progress_ring = ft.ProgressRing(visible=False, width=20, height=20)
        self.txt_filtros_activos = ft.Text("Sin filtros activos", size=11, color=self.app.G360_TEXT_MUTED)
        self.selection_counter = ft.Text("Seleccionadas: 0 líneas", size=12)

    def build(self) -> ft.Container:
        total_soles = sum(l.get("SOLES", 0) for l in self.lineas_resumen)
        total_skus = sum(l.get("SKU_COUNT", 0) for l in self.lineas_resumen)
        
        self.metrics_summary = ft.Container(
            padding=20, bgcolor=ft.colors.with_opacity(0.03, ft.colors.WHITE), border_radius=20,
            border=ft.border.all(1, self.app.G360_BORDER),
            content=ft.Row([
                self._build_metric("MONTO TOTAL", f"S/ {total_soles:,.0f}", ft.icons.MONETIZATION_ON_OUTLINED, self.app.G360_ACCENT),
                ft.VerticalDivider(width=1, color=self.app.G360_BORDER),
                self._build_metric("TOTAL SKUs", f"{total_skus:,}", ft.icons.INVENTORY_2_OUTLINED, ft.colors.BLUE_400),
                ft.VerticalDivider(width=1, color=self.app.G360_BORDER),
                self._build_metric("LÍNEAS", f"{len(self.lineas_resumen)}", ft.icons.CATEGORY_OUTLINED, ft.colors.PURPLE_400),
            ], alignment=ft.MainAxisAlignment.SPACE_EVENLY),
        )

        return ft.Container(
            expand=True,
            padding=ft.padding.only(top=30, bottom=40, left=50, right=50),
            content=ft.Column([
                ft.Row([
                    ft.IconButton(ft.icons.ARROW_BACK_IOS_NEW_ROUNDED, on_click=self._go_back),
                    ft.Column([
                        ft.Text("REPORTES CONSOLIDADOS", size=32, weight=ft.FontWeight.W_900),
                        ft.Text("Análisis y consolidación de datos", size=14, color=self.app.G360_TEXT_MUTED),
                    ], spacing=2),
                ], spacing=15),
                ft.Divider(height=20, color="transparent"),
                self.metrics_summary,
                ft.Divider(height=10, color="transparent"),
                ft.Row([
                    ft.Icon(ft.icons.ANALYTICS_OUTLINED, color=self.app.G360_ACCENT, size=20),
                    ft.Text("DASHBOARD DE LÍNEAS", size=14, weight=ft.FontWeight.BOLD),
                    ft.Container(expand=True),
                    self.lines_search,
                    self.selection_counter,
                ], spacing=12),
                self._build_dashboard_card(),
                ft.Row([
                    ft.TextButton("Seleccionar todas", icon=ft.icons.SELECT_ALL, on_click=self._toggle_all_lines),
                    ft.TextButton("Deseleccionar todas", icon=ft.icons.DESELECT, on_click=self._deselect_all_lines),
                ]),
                ft.Divider(height=20, color=self.app.G360_BORDER),
                ft.Text("FILTROS", size=14, weight="bold"),
                ft.Row([
                    ft.Container(
                        expand=True, padding=15, bgcolor=ft.colors.with_opacity(0.03, ft.colors.WHITE),
                        border_radius=18, border=ft.border.all(1, self.app.G360_BORDER),
                        content=ft.Column([
                            ft.Row([
                                ft.Text("VENDEDORES", size=11, weight="bold", color=self.app.G360_TEXT_MUTED, expand=True),
                                ft.TextButton("Todos", icon=ft.icons.SELECT_ALL, on_click=lambda _: self._toggle_all_vendors_btn(True)),
                                ft.TextButton("Ninguno", icon=ft.icons.CLOSE, on_click=lambda _: self._toggle_all_vendors_btn(False)),
                            ], spacing=5),
                            ft.Row([self.vendor_autocomplete, ft.IconButton(ft.icons.LIST, on_click=self._open_vendor_modal)]),
                            self.vendor_chips,
                        ])),
                    ft.Container(
                        expand=True, padding=15, bgcolor=ft.colors.with_opacity(0.03, ft.colors.WHITE),
                        border_radius=18, border=ft.border.all(1, self.app.G360_BORDER),
                        content=ft.Column([
                            ft.Row([
                                ft.Text("CLIENTES", size=11, weight="bold", color=self.app.G360_TEXT_MUTED, expand=True),
                                ft.TextButton("Todos", icon=ft.icons.SELECT_ALL, on_click=lambda _: self._toggle_all_clients_btn(True)),
                                ft.TextButton("Ninguno", icon=ft.icons.CLOSE, on_click=lambda _: self._toggle_all_clients_btn(False)),
                            ], spacing=5),
                            ft.Row([self.client_autocomplete, ft.IconButton(ft.icons.LIST, on_click=self._open_client_modal)]),
                            self.client_chips,
                        ])),
                ], spacing=20),
                ft.Container(padding=12, bgcolor=ft.colors.with_opacity(0.05, self.app.G360_ACCENT), border_radius=12,
                             content=ft.Row([ft.Icon(ft.icons.FILTER_ALT_OUTLINED, size=16), self.txt_filtros_activos])),
                ft.Divider(height=20, color="transparent"),
                ft.Container(
                    padding=20, bgcolor=self.app.G360_SURFACE, border_radius=18, border=ft.border.all(1, self.app.G360_BORDER),
                    content=ft.Row([
                        ft.Column([ft.Text("AGRUPACIÓN", size=10, weight="bold"), self.rg_agrupacion], expand=True),
                        self.chk_comparacion,
                    ])),
                ft.Container(
                    content=ft.Column([
                        ft.Row([self.status], alignment="center"),
                        ft.Row([self.progress_ring, self.btn_generate], alignment="center", spacing=15),
                    ], spacing=15),
                    padding=ft.padding.only(top=10),
                ),
            ], scroll=ft.ScrollMode.AUTO, spacing=15),
        )

    def _build_metric(self, label, value, icon, color):
        return ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Icon(icon, size=14, color=color),
                    ft.Text(label, size=10, weight=ft.FontWeight.BOLD, color=self.app.G360_TEXT_MUTED),
                ], spacing=8, alignment=ft.MainAxisAlignment.CENTER),
                ft.Text(value, size=20, weight=ft.FontWeight.W_900, color="white"),
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=4),
            expand=True,
        )

    def _update_header_metrics(self):
        """Actualiza las métricas del header basado en la selección."""
        if not hasattr(self, 'metrics_summary') or self.metrics_summary is None:
            return
            
        df_target = self.lineas_resumen
        if self.selected_lines:
            # Filtrar buscando coincidencia en nombre real o nombre formateado
            df_target = [l for l in self.lineas_resumen if l.get("NOM_LINEA") in self.selected_lines or l.get("NOM_LINEA_FMT") in self.selected_lines]
            
        total_soles = sum(l.get("SOLES", 0) for l in df_target)
        total_skus = sum(l.get("SKU_COUNT", 0) for l in df_target)
        
        # Actualizar valores
        metrics_row = self.metrics_summary.content
        metrics_row.controls[0].content.controls[1].value = f"S/ {total_soles:,.0f}"
        metrics_row.controls[2].content.controls[1].value = f"{total_skus:,}"
        metrics_row.controls[4].content.controls[1].value = f"{len(df_target)}"
        
        self.metrics_summary.update()

    def _build_dashboard_card(self) -> ft.Container:
        return ft.Container(height=300, padding=15, bgcolor=self.app.G360_SURFACE, border_radius=20,
                            border=ft.border.all(1, self.app.G360_BORDER), content=self.lines_list)

    def update_data(self, proc):
        self.lineas_resumen = ReporteConsolidado.obtener_resumen_lineas(proc.historial)
        try:
            vendedores = ReporteConsolidado.obtener_vendedores(proc.historial)
            self.all_vendors = [{'id': v['ID_VENDEDOR'], 'nombre': v.get('NOM_VENDEDOR', '')} for v in vendedores]
            self.all_clients = proc.historial['NOM_CLIENTE'].dropna().unique().tolist()
        except: pass

    def _update_dashboard(self): self._render_lines()

    def _render_lines(self):
        self.lines_list.controls.clear()
        palette = [
            ft.colors.CYAN_400, ft.colors.AMBER_400, ft.colors.PURPLE_400,
            ft.colors.TEAL_400, ft.colors.ORANGE_400, ft.colors.PINK_400,
            ft.colors.INDIGO_400,
        ]
        
        search = self._lines_search_text.lower()
        # Encontrar el máximo para la escala de las barras
        max_soles = max([l.get("SOLES", 1) for l in self.lineas_resumen]) if self.lineas_resumen else 1
        
        for i, line in enumerate(self.lineas_resumen):
            name = line.get("NOM_LINEA", "")
            display_name = line.get("NOM_LINEA_FMT", name)
            
            if search and search not in display_name.lower():
                continue
                
            is_selected = name in self.selected_lines
            color = palette[i % len(palette)]
            soles = line.get("SOLES", 0)
            sku_count = line.get("SKU_COUNT", 0)
            escala = soles / max_soles if max_soles > 0 else 0
            
            self.lines_list.controls.append(
                ft.Container(
                    content=ft.Row([
                        ft.Checkbox(
                            value=is_selected, 
                            on_change=lambda e, n=name: self._toggle_line_chk(e, n),
                            fill_color=color,
                        ),
                        ft.Container(
                            content=ft.Column([
                                ft.Row([
                                    ft.Text(display_name, size=12, weight=ft.FontWeight.W_600, color=color, expand=True),
                                    ft.Text(f"S/ {soles:,.0f}", size=12, weight=ft.FontWeight.BOLD, color="white"),
                                    ft.Text(f"{sku_count} SKUs", size=10, color=self.app.G360_TEXT_MUTED),
                                ], spacing=10),
                                ft.ProgressBar(
                                    value=escala,
                                    color=color,
                                    bgcolor=ft.colors.with_opacity(0.1, color),
                                    height=4,
                                    border_radius=2,
                                ),
                            ], spacing=4),
                            expand=True,
                            on_click=lambda _, n=name: self._toggle_line_click(n),
                        ),
                    ], spacing=10),
                    padding=ft.padding.symmetric(horizontal=10, vertical=8),
                    border_radius=12,
                    bgcolor=ft.colors.with_opacity(0.08, color) if is_selected else ft.colors.with_opacity(0.02, ft.colors.WHITE),
                    border=ft.border.all(1, ft.colors.with_opacity(0.2, color)) if is_selected else None,
                )
            )
        if self.lines_list.page:
            self.lines_list.update()

    def _toggle_line_click(self, name):
        """Helper para click en la tarjeta de línea."""
        if name in self.selected_lines:
            self.selected_lines.remove(name)
        else:
            self.selected_lines.append(name)
        self._on_selection_changed()

    def _toggle_line_chk(self, e, name):
        if e.control.value:
            if name not in self.selected_lines:
                self.selected_lines.append(name)
        else:
            if name in self.selected_lines:
                self.selected_lines.remove(name)
        self._update_selection_counter()
        self._update_filtros_activos()
        self._render_lines()
        try:
            self._update_header_metrics()
        except:
            pass
        if self.app.page:
            self.app.page.update()

    def _on_selection_changed(self):
        """Actualiza todo lo necesario tras un cambio de selección."""
        self._update_selection_counter()
        self._update_filtros_activos()
        self._render_lines()
        try:
            self._update_header_metrics()
        except:
            pass
        if self.app.page:
            self.app.page.update()

    def _filter_lines(self, e):
        self._lines_search_text = e.control.value or ""
        self._render_lines()

    def _update_selection_counter(self):
        self.selection_counter.value = f"Seleccionadas: {len(self.selected_lines)} líneas"
        if self.selection_counter.page: self.selection_counter.update()

    def _update_filtros_activos(self):
        partes = []
        if self.selected_vendors: partes.append(f"Vendedores: {len(self.selected_vendors)}")
        if self.selected_clients: partes.append(f"Clientes: {len(self.selected_clients)}")
        if self.selected_lines: partes.append(f"Líneas: {len(self.selected_lines)}")
        self.txt_filtros_activos.value = " | ".join(partes) if partes else "Sin filtros activos"
        if self.txt_filtros_activos.page: self.txt_filtros_activos.update()

    def _go_back(self, e):
        self.app.body.content = self.app.main_view
        self.app.sidebar_nc_indicator.visible = True
        self.app.sidebar_consolidated_indicator.visible = False
        self.app.page.update()

    def _on_grouping_change(self, e):
        agrupacion = e.control.value
        if not agrupacion:
            return
        disable_comp = agrupacion in ["PERIODO_MES", "FACTURA", "PARETO_CLIENTE"]
        self.chk_comparacion.disabled = disable_comp
        if disable_comp:
            self.chk_comparacion.value = False
        if self.chk_comparacion.page: self.chk_comparacion.update()

    def _toggle_all_vendors_btn(self, select_all: bool):
        if select_all:
            self.selected_vendors = [v['id'] for v in self.all_vendors]
        else:
            self.selected_vendors = []
        self._update_vendor_chips()
        self._update_filtros_activos()
        self.app.page.update()

    def _toggle_all_vendors(self, e):
        self._toggle_all_vendors_btn(e.control.value)

    def _toggle_all_clients_btn(self, select_all: bool):
        if select_all:
            self.selected_clients = self.all_clients.copy() if self.all_clients else []
        else:
            self.selected_clients = []
        self._update_client_chips()
        self._update_filtros_activos()
        self._update_client_summary()

    def _toggle_all_clients(self, e):
        self._toggle_all_clients_btn(e.control.value)

    def _update_vendor_chips(self):
        self.vendor_chips.content.controls.clear()
        for v_id in self.selected_vendors[:10]:
            v_name = next((v['nombre'] for v in self.all_vendors if v['id'] == v_id), v_id)
            self.vendor_chips.content.controls.append(
                ft.Chip(
                    label=ft.Text(v_name[:15], size=10),
                    on_delete=lambda _, vid=v_id: self._remove_vendor(vid),
                )
            )
        if len(self.selected_vendors) > 10:
            self.vendor_chips.content.controls.append(ft.Text(f"+{len(self.selected_vendors)-10}", size=10))
        self.vendor_chips.update()

    def _update_client_chips(self):
        self.client_chips.content.controls.clear()
        for c_name in self.selected_clients[:10]:
            self.client_chips.content.controls.append(
                ft.Chip(
                    label=ft.Text(c_name[:15], size=10),
                    on_delete=lambda _, cname=c_name: self._remove_client(cname),
                )
            )
        if len(self.selected_clients) > 10:
            self.client_chips.content.controls.append(ft.Text(f"+{len(self.selected_clients)-10}", size=10))
        self.client_chips.update()

    def _remove_vendor(self, v_id):
        if v_id in self.selected_vendors:
            self.selected_vendors.remove(v_id)
        self._update_vendor_chips()
        self._update_filtros_activos()

    def _remove_client(self, c_name):
        if c_name in self.selected_clients:
            self.selected_clients.remove(c_name)
        self._update_client_chips()
        self._update_filtros_activos()

    def _update_client_summary(self):
        if not hasattr(self, 'modal_selected_count') or self.modal_selected_count is None:
            return
        try:
            if self.modal_selected_count.page is None:
                return
        except:
            return
        self.modal_selected_count.value = f"Seleccionados: {len(self.selected_clients)}"
        self.modal_selected_count.update()

    def _open_vendor_modal(self, e):
        if not self.all_vendors:
            self.status.value = "No hay vendedores cargados"
            self.status.color = "orange"
            self.app.page.update()
            return

        self.vendor_search = ft.TextField(
            label="Buscar vendedor...",
            prefix_icon=ft.icons.SEARCH,
            on_change=self._filter_vendors_modal,
            height=40,
        )

        self.vendor_modal_checkboxes = []
        for v in sorted(self.all_vendors, key=lambda x: x['nombre'].lower()):
            label = f"{v['id']} - {v['nombre']}" if v['nombre'] else v['id']
            cb = ft.Checkbox(
                label=label,
                value=v['id'] in self.selected_vendors,
                on_change=lambda ev, vend=v['id']: self._toggle_vendor_modal(ev, vend),
            )
            cb.vendor_id = v['id']
            cb.vendor_nombre = v['nombre']
            self.vendor_modal_checkboxes.append(cb)

        self.vendor_listview = ft.ListView(
            controls=self.vendor_modal_checkboxes,
            height=350,
            spacing=2,
        )

        self.app.page.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(f"Seleccionar Vendedores ({len(self.all_vendors)} disponibles)"),
            content=ft.Container(
                width=500,
                height=450,
                content=ft.Column([
                    ft.Row([
                        ft.TextButton("Seleccionar todos", icon=ft.icons.SELECT_ALL, on_click=lambda _: self._toggle_all_vendors_modal(True)),
                        ft.TextButton("Deseleccionar", icon=ft.icons.DESELECT, on_click=lambda _: self._toggle_all_vendors_modal(False)),
                    ]),
                    self.vendor_search,
                    self.vendor_listview,
                ]),
            ),
            actions=[
                ft.TextButton("Aceptar", on_click=self._close_vendor_modal),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.app.page.dialog.open = True
        self.app.page.update()

    def _toggle_vendor_modal(self, e, vendedor):
        if e.control.value:
            if vendedor not in self.selected_vendors:
                self.selected_vendors.append(vendedor)
        else:
            if vendedor in self.selected_vendors:
                self.selected_vendors.remove(vendedor)
        self._update_vendor_chips()

    def _toggle_all_vendors_modal(self, select_all: bool):
        for cb in self.vendor_modal_checkboxes:
            if cb.visible:
                cb.value = select_all
                vendedor = cb.vendor_id
                if select_all:
                    if vendedor not in self.selected_vendors:
                        self.selected_vendors.append(vendedor)
                else:
                    if vendedor in self.selected_vendors:
                        self.selected_vendors.remove(vendedor)
        self.vendor_listview.update()
        self._update_vendor_chips()

    def _close_vendor_modal(self, e):
        self.app.page.dialog.open = False
        self.app.page.update()

    def _open_client_modal(self, e):
        if not self.all_clients:
            self.status.value = "No hay clientes cargados"
            self.status.color = "orange"
            self.app.page.update()
            return

        self.client_search = ft.TextField(
            label="Buscar cliente...",
            prefix_icon=ft.icons.SEARCH,
            on_change=self._filter_clients_modal,
            height=40,
        )

        self.modal_checkboxes = []
        for cliente in sorted(self.all_clients)[:500]:
            cb = ft.Checkbox(
                label=cliente,
                value=cliente in self.selected_clients,
                on_change=lambda ev, c=cliente: self._toggle_client_modal(ev, c),
            )
            self.modal_checkboxes.append(cb)

        self.client_listview = ft.ListView(
            controls=self.modal_checkboxes,
            height=350,
            spacing=2,
        )

        self.modal_selected_count = ft.Text(f"Seleccionados: {len(self.selected_clients)}", size=11, color=self.app.G360_ACCENT)
        self.app.page.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(f"Seleccionar Clientes ({len(self.all_clients)} disponibles)"),
            content=ft.Container(
                width=500,
                height=450,
                content=ft.Column([
                    ft.Row([
                        ft.TextButton("Seleccionar todos", icon=ft.icons.SELECT_ALL, on_click=lambda _: self._toggle_all_clients_modal(True)),
                        ft.TextButton("Deseleccionar", icon=ft.icons.DESELECT, on_click=lambda _: self._toggle_all_clients_modal(False)),
                        self.modal_selected_count,
                    ]),
                    self.client_search,
                    self.client_listview,
                ]),
            ),
            actions=[
                ft.TextButton("Aceptar", on_click=self._close_client_modal),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.app.page.dialog.open = True
        self.app.page.update()

    def _toggle_client_modal(self, e, cliente):
        if e.control.value:
            if cliente not in self.selected_clients:
                self.selected_clients.append(cliente)
        else:
            if cliente in self.selected_clients:
                self.selected_clients.remove(cliente)
        self._update_client_chips()

    def _toggle_all_clients_modal(self, select_all: bool):
        for cb in self.modal_checkboxes:
            if cb.visible:
                cb.value = select_all
                cliente = cb.label
                if select_all:
                    if cliente not in self.selected_clients:
                        self.selected_clients.append(cliente)
                else:
                    if cliente in self.selected_clients:
                        self.selected_clients.remove(cliente)
        self.client_listview.update()
        self._update_client_chips()
        self._update_client_summary()

    def _close_client_modal(self, e):
        self.app.page.dialog.open = False
        self.app.page.update()

    def _filter_vendors_modal(self, e):
        search = e.control.value.lower() if e.control.value else ""
        for cb in self.vendor_modal_checkboxes:
            nombre = getattr(cb, 'vendor_nombre', '').lower()
            vendor_id = getattr(cb, 'vendor_id', '').lower()
            cb.visible = search in nombre or search in vendor_id
        self.vendor_listview.update()

    def _filter_clients_modal(self, e):
        search = e.control.value.lower() if e.control.value else ""
        for cb in self.modal_checkboxes:
            cb.visible = search in cb.label.lower()
        self.client_listview.update()

    def _filter_vendor_options(self, e):
        query = e.control.value.upper() if e.control.value else ""
        filtro = [v for v in self.all_vendors if query in v['nombre'].upper() or query in v['id']]
        e.control.options = [
            ft.AutocompleteOption(key=v['id'], text=f"{v['id']} - {v['nombre']}") 
            for v in filtro[:15]
        ]
        e.control.update()

    def _filter_client_options(self, e):
        query = e.control.value.upper() if e.control.value else ""
        filtro = [c for c in self.all_clients if query in c.upper()]
        e.control.options = [
            ft.AutocompleteOption(key=c, text=c[:50]) 
            for c in filtro[:15]
        ]
        e.control.update()

    def _toggle_all_lines(self, e):
        self.selected_lines = [l.get("NOM_LINEA") for l in self.lineas_resumen]
        self._render_lines()
        self._update_filtros_activos()
        self._update_selection_counter()
        try:
            self._update_header_metrics()
        except:
            pass
    def _deselect_all_lines(self, e):
        self.selected_lines = []
        self._render_lines()
        self._update_filtros_activos()
        self._update_selection_counter()
        try:
            self._update_header_metrics()
        except:
            pass
    def _generate_report(self, e):
        self.progress_ring.visible = True
        self.btn_generate.disabled = True
        self.status.value = "Generando reporte..."
        self.status.color = self.app.G360_BLUE
        self.app.page.update()

        try:
            proc = self.app.current_processor
            clientes_filtro = self.selected_clients if self.selected_clients else None
            vendedores_filtro = self.selected_vendors if self.selected_vendors else None
            lineas_filtro = self.selected_lines if self.selected_lines else None
            agrupacion = self.rg_agrupacion.value or "ID_ARTICULO"
            
            if agrupacion == "PARETO_CLIENTE":
                datos = ReporteConsolidado.generar_pareto_completo(
                    historial=proc.historial,
                    clientes_filtro=clientes_filtro,
                    vendedores_filtro=vendedores_filtro,
                    lineas_filtro=lineas_filtro
                )
            elif self.chk_comparacion.value:
                datos_raw = ReporteConsolidado.generar_comparacion_mes_a_mes(
                    historial=proc.historial, agrupacion=agrupacion,
                    clientes_filtro=clientes_filtro, vendedores_filtro=vendedores_filtro,
                    lineas_filtro=lineas_filtro, tipo_comparacion="2MESES")
                datos = {}
                for vid, vdata in datos_raw.items():
                    datos[vid] = vdata.get('DATA', [])
                if not agrupacion.startswith("COMPARATIVO_"):
                    agrupacion = "COMPARATIVO_" + agrupacion
            else:
                datos = ReporteConsolidado.generar_consolidado(
                    historial=proc.historial, agrupacion=agrupacion,
                    clientes_filtro=clientes_filtro, vendedores_filtro=vendedores_filtro,
                    lineas_filtro=lineas_filtro
                )

            if agrupacion == "PARETO_CLIENTE":
                if not datos.get('CLIENTES'):
                    self.status.value = "No hay datos para Pareto"
                    self.status.color = "orange"
                else:
                    from datetime import datetime
                    from src.excel.generator import ExcelGenerator
                    desktop = self.app._get_desktop_path()
                    # Limpiar nombre de archivo: eliminar espacios y caracteres especiales
                    agrupacion_clean = agrupacion.lower().strip().replace(' ', '_')
                    out_path = desktop / f"reporte_{agrupacion_clean}_{datetime.now().strftime('%d%m%y_%H%M')}.xlsx"
                    
                    filtros_desc = []
                    if clientes_filtro: filtros_desc.append(f"Clientes: {len(clientes_filtro)}")
                    if vendedores_filtro: filtros_desc.append(f"Vendedores: {len(vendedores_filtro)}")
                    if lineas_filtro: filtros_desc.append(f"Líneas: {len(lineas_filtro)}")
                    desc_str = " | ".join(filtros_desc) if filtros_desc else "Todos"

                    ExcelGenerator().generar_reporte_consolidado_excel(
                        str(out_path), "Reporte Pareto", datos, agrupacion,
                        proc.obtener_rango_fechas(), proc.historial, filtros_aplicados=desc_str
                    )
                    
                    self.status.value = f"Reporte generado: {out_path.name}"
                    self.status.color = self.app.G360_SUCCESS
                    import os
                    if os.name == 'nt': os.startfile(str(out_path))
            else:
                if not datos:
                    self.status.value = "No hay datos para los filtros seleccionados"
                    self.status.color = "orange"
                else:
                    from datetime import datetime
                    from src.excel.generator import ExcelGenerator
                    desktop = self.app._get_desktop_path()
                    # Limpiar nombre de archivo: eliminar espacios y caracteres especiales
                    agrupacion_clean = agrupacion.lower().strip().replace(' ', '_')
                    out_path = desktop / f"reporte_{agrupacion_clean}_{datetime.now().strftime('%d%m%y_%H%M')}.xlsx"
                    
                    filtros_desc = []
                    if clientes_filtro: filtros_desc.append(f"Clientes: {len(clientes_filtro)}")
                    if vendedores_filtro: filtros_desc.append(f"Vendedores: {len(vendedores_filtro)}")
                    if lineas_filtro: filtros_desc.append(f"Líneas: {len(lineas_filtro)}")
                    desc_str = " | ".join(filtros_desc) if filtros_desc else "Todos"

                    ExcelGenerator().generar_reporte_consolidado_excel(
                        str(out_path), "Reporte Consolidado", datos, agrupacion,
                        proc.obtener_rango_fechas(), proc.historial, filtros_aplicados=desc_str
                    )
                    
                    self.status.value = f"Reporte generado: {out_path.name}"
                    self.status.color = self.app.G360_SUCCESS
                    import os
                    if os.name == 'nt': os.startfile(str(out_path))

        except Exception as ex:
            self.status.value = f"Error: {str(ex)}"
            self.status.color = "red"
            logger.error(f"Error generando reporte: {ex}")

        self.progress_ring.visible = False
        self.btn_generate.disabled = False
        self.app.page.update()
