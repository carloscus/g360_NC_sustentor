"""Card "Reportes de compras" — análisis directo sobre la DB local.

Estado propio e independiente del caso de NC:

- colapsada por defecto; el botón "Compras" (card Búsqueda) la expande con
  prefill de cliente/vendedor/rango y genera al instante;
- cambiar de caso NO la toca (la vista reutiliza esta misma instancia en
  cada rebuild del layout);
- su botón Limpiar la deja abierta y lista para otra consulta;
- el reset general de la app la limpia y además la colapsa;
- Generar/Exportar corren en hilo background y bloquean los botones
  (incluido Limpiar) mientras hay una tarea en curso.
"""

from __future__ import annotations

import os
import threading
from datetime import datetime

import flet as ft

from src.core.g360_theme import G360Theme
from src.core.fechas import fecha_ui
from src.core.utils import resolve_output_path
from src.ui.widgets import control_factory
from src.ui.reporte_compras import (
    _c_visible,
    _construir_tabla,
    _escribir_xlsx,
    _paquete_export,
    nombre_archivo_compras,
)


class ReportePanel:
    """Card colapsable de reportes con filtros propios (vendedor/cliente/rango)."""

    # Hojas seleccionables del paquete de export (clave interna → etiqueta).
    # "resumen" no aparece: la portada se fuerza siempre que haya datos.
    HOJAS_EXPORT = (
        ("consolidado", "Consolidado"),
        ("comparativo", "Comparativo"),
        ("sucursales", "Sucursales · Pareto + mes×línea/SKU"),
        ("ajustes", "Ajustes NC/NDB"),
        ("bd", "BD Registro"),
        ("facturas", "Facturas"),
    )
    TODAS = frozenset(k for k, _ in HOJAS_EXPORT)
    # El desglose sucursal×línea/SKU es opcional por volumen y por ser
    # un análisis especializado (supermercados/TAI LOY).
    DEFAULT_HOJAS = TODAS - {"sucursales"}

    def __init__(self, app, view):
        self.app = app
        self.view = view
        self._card = None  # contenedor cacheado (se construye UNA vez)
        self._body = None
        self._chevron = None
        self._abierto = False
        self._busy = False
        self._cargado = False  # dropdowns poblados (primera apertura)
        self._chips = {}
        hoy = datetime.now()
        self.state = {
            "clientes": [],  # [(cid, nombre)] — multi-selección
            "vendedor": None,
            "desde": datetime(hoy.year, 1, 1),
            "hasta": hoy,
            "incluir_nc": True,
            "hojas": set(self.DEFAULT_HOJAS),
            "datos": {},  # {cid: DataFrame} de la última generación
        }

    # ── página / actualizaciones (tolerante a tests sin page) ────────

    def _page(self):
        return self.app.page

    def _update(self):
        try:
            page = self._page()
            if page is not None:
                page.update()
        except Exception:
            pass

    def _snack(self, msg, color_attr="G360_SUCCESS"):
        try:
            getattr(self.app, "show_snackbar")(msg, getattr(self.app, color_attr, None))
        except Exception:
            pass

    # ── construcción (una sola vez; el layout reutiliza la instancia) ─

    def construir_card(self) -> ft.Container:
        if self._card is None:
            self._card = self._build()
        return self._card

    def _build(self) -> ft.Container:
        # Paleta propia de la sección: el reporte es una zona independiente y
        # con el azul global se leía como otra pieza del flujo principal.
        accent = G360Theme.section_accent_color()
        muted = G360Theme.text_muted_color()

        self._resumen = ft.Text("", size=10, color=muted, expand=True)

        def _fmt(d):
            return fecha_ui(d) if d else "todas"

        self.desde_label = control_factory.date_label(f"Desde: {_fmt(self.state['desde'])}")
        self.hasta_label = control_factory.date_label(f"Hasta: {_fmt(self.state['hasta'])}")
        self.vend_dd = control_factory.dropdown(
            "Vendedor (opcional)",
            icon=ft.Icons.PERSON_OUTLINED,
            # Ancho fijo, no expand: con expand el dropdown se estira a todo el
            # espacio libre de la card y queda mas ancho que el de Búsqueda, que
            # usa WIDTH_FILTER. Eran el mismo control con dos medidas distintas.
            width=control_factory.WIDTH_FILTER,
            search=True,
            hint="Todos los vendedores…",
            on_change=self._on_vendedor,
        )
        self.cli_chips = ft.Row(
            [], wrap=True, spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER
        )
        self.btn_buscar_cli = control_factory.search_button(
            "Buscar cliente",
            ft.Icons.SEARCH,
            on_click=lambda _: self._abrir_picker_clientes(),
        )
        self.nc_sw = ft.Switch(
            label="Incluir NC/ND (neto)", value=True, label_style=ft.TextStyle(size=12)
        )
        self.btn_generar = ft.ElevatedButton(
            "Generar",
            icon=ft.Icons.PLAY_ARROW,
            height=control_factory.HEIGHT,
            style=ft.ButtonStyle(bgcolor=accent, color="white"),
            on_click=self._generar,
        )
        self.btn_exportar = ft.ElevatedButton(
            "Exportar Excel",
            icon=ft.Icons.GRID_ON,
            height=control_factory.HEIGHT,
            style=ft.ButtonStyle(bgcolor=ft.Colors.GREEN_800, color="white"),
            on_click=self._exportar,
        )
        self.status = ft.Text("", size=10, color=ft.Colors.ON_SURFACE_VARIANT)
        self.resultados = ft.Column([], spacing=6, scroll=ft.ScrollMode.AUTO, expand=True)

        # Píldora icono+texto (un chevron solo no se identifica como botón).
        self._chevron = ft.ElevatedButton(
            text="Expandir",
            icon=ft.Icons.EXPAND_MORE,
            height=28,
            tooltip="Expandir / colapsar la card",
            style=ft.ButtonStyle(
                bgcolor=ft.Colors.with_opacity(0.10, accent),
                color=accent,
                icon_color=accent,
                side=ft.BorderSide(1, accent),
                padding=ft.padding.symmetric(horizontal=10, vertical=4),
            ),
            on_click=lambda _: self._toggle(),
        )
        btn_limpiar = ft.IconButton(
            icon=ft.Icons.DELETE_OUTLINED,
            icon_size=18,
            tooltip="Limpiar filtros y resultados (la card queda abierta)",
            on_click=lambda _: self.limpiar(colapsar=False),
        )
        self._btn_limpiar = btn_limpiar

        titulo = ft.Row(
            [
                ft.Icon(ft.Icons.INSIGHTS_OUTLINED, size=18, color=accent),
                G360Theme.card_title("Reporte de compras"),
            ],
            spacing=8,
        )
        header = ft.Row(
            [
                ft.GestureDetector(content=titulo, on_tap=lambda _: self._toggle()),
                self._resumen,
                btn_limpiar,
                self._chevron,
            ],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=6,
        )

        # Chips de hojas a exportar (multiselección; todas marcadas por defecto)
        self._chips = {}
        self._chips_row = ft.Row([], wrap=True, spacing=6)
        for key, label in self.HOJAS_EXPORT:
            chip = ft.Chip(
                label=ft.Text(label, size=10),
                selected=key in self.state["hojas"],
                show_checkmark=True,
                data=key,
                on_select=self._on_chip,
                padding=ft.padding.symmetric(horizontal=8, vertical=2),
            )
            self._chips[key] = chip
            self._chips_row.controls.append(chip)

        self._body = ft.Container(
            content=ft.Column(
                [
                    ft.Text(
                        "Consulta directa a la base local: elige cliente, rango y hojas a exportar",
                        size=10,
                        color=G360Theme.text_muted_color(),
                    ),
                    # 1. Filtros: vendedor + búsqueda de clientes en una línea
                    # (sin wrap y sin filas anidadas: un hijo con expand dentro
                    # de un Row con wrap rompe el layout y pinta un bloque gris),
                    # y debajo los clientes elegidos — igual que Búsqueda.
                    ft.Row(
                        [self.vend_dd, self.btn_buscar_cli],
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    self.cli_chips,
                    # 2. Rango + neto
                    ft.Row(
                        [
                            ft.Icon(
                                ft.Icons.CALENDAR_TODAY_OUTLINED,
                                size=14,
                                color=G360Theme.accent_color(),
                            ),
                            self.desde_label,
                            self._picker_btn("desde", self.desde_label, "Desde"),
                            self.hasta_label,
                            self._picker_btn("hasta", self.hasta_label, "Hasta"),
                            self.nc_sw,
                        ],
                        spacing=8,
                        wrap=True,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    # 3. Hojas a exportar
                    ft.Text("Hojas a exportar:", size=12, color=G360Theme.text_muted_color()),
                    self._chips_row,
                    ft.Divider(height=4, color=G360Theme.border_subtle_color()),
                    # 4. Acciones
                    ft.Row(
                        [self.btn_generar, self.btn_exportar],
                        spacing=8,
                        alignment=ft.MainAxisAlignment.END,
                    ),
                    # 5. Salida
                    self.status,
                    self.resultados,
                ],
                spacing=8,
            ),
            visible=False,
            padding=ft.padding.only(left=8, right=8, bottom=4),
        )

        card = G360Theme.card(
            content=ft.Column([header, self._body], spacing=6),
            padding=16,
            border_radius=14,
            key="reportes_card",
            # La separación de zona es de color, no de líneas: fondo velado y
            # borde de 1px teñido (nada de border-left grueso).
            bgcolor=G360Theme.section_surface_color(),
            border_color=G360Theme.section_border_color(),
        )
        return card

    def _picker_btn(self, storage_key, label_ctl, side):
        def open_(_):
            def on_change(ev):
                self.state[storage_key] = ev.control.value
                label_ctl.value = (
                    f"{side}: {fecha_ui(ev.control.value)}"
                    if ev.control.value
                    else f"{side}: todas"
                )
                label_ctl.color = (
                    G360Theme.accent_color() if ev.control.value else G360Theme.text_muted_color()
                )
                self._update()

            page = self._page()
            if page is None:
                return
            page.open(
                ft.DatePicker(
                    first_date=datetime(2010, 1, 1),
                    last_date=datetime.now(),
                    on_change=on_change,
                )
            )

        return control_factory.date_button(open_, side)

    def _on_chip(self, ev):
        """Multiselección de hojas a exportar (chips)."""
        key = getattr(ev.control, "data", None)
        if key not in self.TODAS:
            return
        if ev.control.selected:
            self.state["hojas"].add(key)
        else:
            self.state["hojas"].discard(key)

    # ── expandir / colapsar / limpiar ────────────────────────────────

    def _toggle(self):
        if self._abierto:
            self.colapsar()
        else:
            self._mostrar()

    def _set_chevron(self, abierto: bool):
        """Sincroniza icono y texto de la píldora de expandir/colapsar."""
        self._chevron.icon = ft.Icons.EXPAND_LESS if abierto else ft.Icons.EXPAND_MORE
        self._chevron.text = "Colapsar" if abierto else "Expandir"

    def _mostrar(self):
        self._body.visible = True
        self._abierto = True
        self._set_chevron(True)
        self._update()  # monta la card visible antes de pedir datos
        # Primera apertura: poblar el selector de vendedores.
        if not self._cargado:
            self._cargado = True
            self._cargar_vendedores()
        self._scroll()

    def colapsar(self):
        self._body.visible = False
        self._abierto = False
        self._set_chevron(False)
        self._update()

    def _scroll(self):
        try:
            page = self._page()
            if page is not None:
                page.scroll_to(key="reportes_card", duration=250)
        except Exception:
            pass

    def expandir(self, cliente=None, vendedor=None, rango=None):
        """Abre la card con prefill (cliente (cid, nom), vendedor, (desde, hasta))."""
        if cliente:
            cid, nom = cliente
            if not any(c[0] == cid for c in self.state["clientes"]):
                self.state["clientes"].append((cid, nom or cid))
            self._pintar_cli_chips()
        if vendedor:
            self.state["vendedor"] = vendedor
            self.vend_dd.value = vendedor
        if rango:
            desde, hasta = rango
            if desde:
                self.state["desde"] = desde
                self.desde_label.value = f"Desde: {fecha_ui(desde)}"
                self.desde_label.color = G360Theme.accent_text_color()
            if hasta:
                self.state["hasta"] = hasta
                self.hasta_label.value = f"Hasta: {fecha_ui(hasta)}"
                self.hasta_label.color = G360Theme.accent_text_color()
        if not self._abierto:
            self._mostrar()  # primera apertura: carga vendedores
        else:
            self._update()
        if self.state["clientes"]:
            self._generar()

    def _abrir_picker_clientes(self):
        """Modal de clientes compartido (multi-selección), igual que Búsqueda."""
        from src.ui.widgets.cliente_picker import abrir_picker_clientes

        abrir_picker_clientes(
            self.app,
            on_confirm=self._on_clientes_confirmados,
            multiple=True,
            initial={cid for cid, _ in self.state["clientes"]},
            vendedor_id=self.state["vendedor"],
            fecha_desde=(self.state["desde"].strftime("%Y-%m-%d") if self.state["desde"] else None),
            fecha_hasta=(self.state["hasta"].strftime("%Y-%m-%d") if self.state["hasta"] else None),
        )

    def _on_clientes_confirmados(self, elegidos: list):
        for c in elegidos:
            if not any(cid == c["id"] for cid, _ in self.state["clientes"]):
                self.state["clientes"].append((c["id"], c["nombre"]))
        self._pintar_cli_chips()
        self._generar()

    def _quitar_cliente(self, cid: str):
        self.state["clientes"] = [c for c in self.state["clientes"] if c[0] != cid]
        self.state["datos"].pop(cid, None)
        self._pintar_cli_chips()
        self._update()

    def _pintar_cli_chips(self):
        self.cli_chips.controls = [
            ft.Chip(
                label=ft.Text(f"{nom[:28]} ({_c_visible(cid)})", size=10),
                on_delete=lambda _, c=cid: self._quitar_cliente(c),
                delete_icon_color=G360Theme.error_color(),
                bgcolor=ft.Colors.with_opacity(0.12, G360Theme.section_accent_color()),
                padding=ft.padding.symmetric(horizontal=8, vertical=2),
            )
            for cid, nom in self.state["clientes"]
        ]

    def limpiar(self, colapsar: bool = False):
        """Limpia filtros y resultados; con colapsar=True cierra la card.

        Es la limpieza de la card (no colapsa por defecto, queda lista para
        otra consulta); el reset general de la app llama con colapsar=True.
        Nunca interrumpe una tarea en curso.
        """
        if self._busy:
            return
        hoy = datetime.now()
        self.state.update(
            clientes=[],
            vendedor=None,
            desde=datetime(hoy.year, 1, 1),
            hasta=hoy,
            incluir_nc=True,
            hojas=set(self.DEFAULT_HOJAS),
            datos={},
        )
        self._pintar_cli_chips()
        self.vend_dd.value = None
        if self._cargado:
            # Deja el dropdown como recién abierto: sin selección y con
            # las opciones recargadas desde la DB (el modal de clientes
            # ya abre limpio por construcción: initial = state vacío).
            self._cargar_vendedores()
        for key, chip in self._chips.items():
            chip.selected = key in self.state["hojas"]
        self.desde_label.value = f"Desde: {fecha_ui(self.state['desde'])}"
        self.desde_label.color = G360Theme.accent_text_color()
        self.hasta_label.value = f"Hasta: {fecha_ui(hoy)}"
        self.hasta_label.color = G360Theme.accent_text_color()
        self.nc_sw.value = True
        self.status.value = ""
        self._resumen.value = ""
        self.resultados.controls.clear()
        if colapsar:
            self.colapsar()
        else:
            self._update()

    # ── eventos de filtros ───────────────────────────────────────────

    def _on_vendedor(self, ev):
        """Cambia la cartera: se limpian los clientes elegidos."""
        self.state["vendedor"] = ev.control.value or None
        self.state["clientes"] = []
        self.state["datos"] = {}
        self._pintar_cli_chips()
        self.resultados.controls.clear()
        self.status.value = ""
        self._resumen.value = ""
        self._update()

    def _cargar_vendedores(self):
        """Opciones de vendedor (fondo). Solo con UI (tests no tocan la DB)."""
        if self._page() is None:
            return

        def task():
            from src.core.ventas_db_client import VentasDbClient

            try:
                vends = VentasDbClient().fetch_vendedores(min_docs=100)
            except Exception:
                return
            opts = [
                ft.dropdown.Option(key=v["id"], text=f"{v['nombre']} ({v.get('codigo', v['id'])})")
                for v in vends
            ]
            vid = self.state["vendedor"]
            if vid and all(o.key != vid for o in opts):
                opts.insert(0, ft.dropdown.Option(key=vid, text=str(vid)))
            self.vend_dd.options = opts
            if vid:
                self.vend_dd.value = vid
            try:
                self.vend_dd.update()
            except Exception:
                pass

        threading.Thread(target=task, daemon=True).start()

    # ── generar (vista previa en pantalla) ───────────────────────────

    def _set_busy(self, v: bool):
        self._busy = v
        for btn in (self.btn_generar, self.btn_exportar, self.btn_buscar_cli, self._btn_limpiar):
            btn.disabled = v

    def _generar(self, e=None):
        """Consulta la DB para cada cliente seleccionado (multi-selección)."""
        if self._page() is None:
            return  # sin UI no hay dónde pintar (y evita tocar la DB en tests)
        clientes = list(self.state["clientes"])
        if not clientes:
            self._snack("Selecciona al menos un cliente", "G360_WARNING")
            return
        if self._busy:
            return
        self.state["incluir_nc"] = bool(self.nc_sw.value)
        fd = self.state["desde"].strftime("%Y-%m-%d") if self.state["desde"] else None
        fh = self.state["hasta"].strftime("%Y-%m-%d") if self.state["hasta"] else None
        self.status.value = "Consultando…"
        self.status.color = G360Theme.warning_color()
        self._set_busy(True)
        self._update()

        def task():
            from src.core.ventas_db_client import VentasDbClient

            cli = VentasDbClient()
            datos: dict = {}
            error = None
            for cid, _nom in clientes:
                try:
                    datos[cid] = cli.fetch_compras_cliente(
                        cid,
                        fecha_desde=fd,
                        fecha_hasta=fh,
                        incluir_nc=self.state["incluir_nc"],
                        solo_lineas_activas=False,
                    )
                except Exception as ex:
                    datos[cid] = None
                    error = str(ex)
            self.state["datos"] = datos
            self._set_busy(False)
            if error:
                self.status.value = f"Error: {error}"
                self.status.color = self.app.G360_ERROR
                self._update()
                return
            self._render_resultados(fd, fh)

        threading.Thread(target=task, daemon=True).start()

    def _render_resultados(self, fd, fh):
        self.resultados.controls.clear()
        datos = self.state.get("datos") or {}
        clientes = self.state["clientes"]
        periodo = (
            f"{fecha_ui(self.state['desde']) if self.state['desde'] else 'inicio'}"
            f" → {fecha_ui(self.state['hasta']) if self.state['hasta'] else 'hoy'}"
        )
        tot_soles = 0.0
        tot_cant = 0.0
        n_ok = 0
        for cid, nombre in clientes:
            df = datos.get(cid)
            if df is None or df.empty:
                self.resultados.controls.append(
                    ft.Text(
                        f"{nombre or cid} ({_c_visible(cid)}) — sin compras en el rango",
                        size=12,
                        color=G360Theme.text_muted_color(),
                    )
                )
                continue
            n_ok += 1
            s = float(df["SOLES"].sum())
            q = float(df["CANTIDAD"].sum())
            tot_soles += s
            tot_cant += q
            self.resultados.controls.append(
                ft.Text(
                    f"{nombre or cid} ({_c_visible(cid)}) · {df['COD_LINEA'].nunique()} línea(s)"
                    f" · {df['MES_REF'].nunique()} mes(es) · neto S/ {s:,.2f} · {q:,.0f} u",
                    size=12,
                    weight=ft.FontWeight.W_700,
                    color=ft.Colors.ON_SURFACE,
                )
            )
            self.resultados.controls.append(
                ft.Row([_construir_tabla(df)], scroll=ft.ScrollMode.AUTO, expand=False)
            )

        n = len(clientes)
        if n_ok:
            self.status.value = (
                f"{n} cliente(s) · {n_ok} con compras · "
                f"neto S/ {tot_soles:,.2f} · {tot_cant:,.0f} u"
            )
            self.status.color = self.app.G360_SUCCESS
            self._resumen.value = f"{n} cliente(s) · {periodo} · S/ {tot_soles:,.2f}"
        else:
            self.status.value = "Sin compras en el rango"
            self.status.color = ft.Colors.ON_SURFACE_VARIANT
            self._resumen.value = ""
        self._update()

    # ── exportar (un Excel por cliente) ──────────────────────────────

    def _exportar(self, e=None):
        datos = self.state.get("datos") or {}
        clientes = [
            (cid, nom)
            for cid, nom in self.state["clientes"]
            if datos.get(cid) is not None and not datos[cid].empty
        ]
        if not clientes:
            self._snack("Genera el reporte primero", "G360_WARNING")
            return
        hojas = set(self.state.get("hojas") or ())
        if not hojas:
            self._snack("Selecciona al menos una hoja a exportar", "G360_WARNING")
            return
        if self._busy:
            return
        app = self.app
        if self._page() is None:
            return
        app.show_loading("Generando Excel…")
        self._set_busy(True)

        def task():
            from src.core.ventas_db_client import VentasDbClient

            cli = VentasDbClient()
            fd = self.state["desde"].strftime("%Y-%m-%d") if self.state["desde"] else None
            fh = self.state["hasta"].strftime("%Y-%m-%d") if self.state["hasta"] else None
            out_dir = app._get_desktop_path()
            out_dir.mkdir(exist_ok=True)
            generados = 0
            ultimo = None
            try:
                for cid_raw, nombre in clientes:
                    bundle = _paquete_export(
                        cli,
                        cid_raw,
                        fd,
                        fh,
                        self.state["incluir_nc"],
                        hojas | {"resumen"},
                        solo_lineas_activas=False,
                    )
                    ruc_info = cli.fetch_client_by_id(cid_raw) or {}
                    ruc = ruc_info.get("doc") or ""
                    # Si el nombre es solo el CID (sin ceros), reemplazar por el real.
                    if nombre.isdigit() or nombre == _c_visible(cid_raw):
                        nombre = ruc_info.get("nombre") or nombre
                    # Resolver nombre del vendedor si solo hay ID.
                    vend = self.state["vendedor"] or ""
                    if vend:
                        vinfo = cli.fetch_vendedor_by_id(vend)
                        if vinfo:
                            vend = vinfo.get("nombre") or vend
                    cid = _c_visible(cid_raw)
                    out_path = resolve_output_path(out_dir / nombre_archivo_compras(nombre, cid))
                    _escribir_xlsx(
                        out_path,
                        cid,
                        nombre,
                        hojas=hojas,
                        corte_hasta=fh or "",
                        incluir_nc=self.state["incluir_nc"],
                        rango_desde=fd or "",
                        rango_hasta=fh or "",
                        ruc=ruc,
                        vendedor=vend,
                        hojas_incluidas=hojas,
                        total_hojas=len(self.TODAS),
                        **bundle,
                    )
                    generados += 1
                    ultimo = out_path
                if generados == 1:
                    app.show_snackbar(f"✅ Excel: {ultimo.name}", app.G360_SUCCESS)
                else:
                    app.show_snackbar(f"✅ {generados} Excel generados", app.G360_SUCCESS)
                if os.name == "nt":
                    os.startfile(str(out_dir))
            except Exception as ex:
                from src.ui.mensajes import mensaje

                app.show_snackbar(mensaje(ex, "generar el reporte"), app.G360_ERROR)
            finally:
                app.hide_loading()
                self._set_busy(False)
                self._update()

        threading.Thread(target=task, daemon=True).start()
