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

import logging
import os
import threading
import time
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
    _filtrar_por_sku,
    _paquete_export,
    _parse_skus,
    _sugerir_variante_ceros,
    nombre_archivo_compras,
)

logger = logging.getLogger(__name__)


class ReportePanel:
    """Card colapsable de reportes con filtros propios (vendedor/cliente/rango)."""

    # Hojas seleccionables del paquete de export (clave interna → etiqueta).
    # "resumen" no aparece: la portada se fuerza siempre que haya datos.
    # Label corto: un chip de 33 chars ("Sucursales · Pareto + mes×línea/SKU")
    # rompia la fila; el detalle del contenido va en el tooltip.
    HOJAS_EXPORT = (
        ("consolidado", "Consolidado"),
        ("comparativo", "Comparativo"),
        ("sucursales", "Sucursales (Pareto)"),
        ("ajustes", "Ajustes NC/NDB"),
        ("bd", "BD Registro"),
        ("facturas", "Facturas"),
    )
    HOJAS_TOOLTIP = {
        "sucursales": "Pareto del rango + desglose mes×línea y mes×SKU",
    }
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
            "skus": [],  # filtro SKU de la vista previa ("" = sin filtro)
            "sku_sugerencias": {},  # {cid: [cods]} variantes en ceros si el filtro dio vacío
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

    @staticmethod
    def _sugerencias_sku(cli, cid, fd, fh, skus):
        """Variantes en ceros existentes cuando el filtro SKU dio vacío.

        Nunca relaja el filtro: solo sugiere ("¿quisiste decir 011019?")
        para corregir el tipeo. Sin DB o sin variantes, lista vacía.
        """
        try:
            ref = cli.fetch_skus_resumen_cliente(
                cid, fecha_desde=fd, fecha_hasta=fh, incluir_nc=True
            )
            existentes = ref["COD_SKU"].tolist() if ref is not None and not ref.empty else []
            return _sugerir_variante_ceros(skus, existentes)
        except Exception:
            return []

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

        self._resumen = ft.Text("", size=G360Theme.TYPE_BODY, color=muted, expand=True)

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
        self.sku_field = control_factory.text_field(
            "SKU (opcional)",
            width=control_factory.WIDTH_FIELD,
            hint="Filtra vista previa: 78339, ...",
        )
        # Checkbox (no Switch): el mismo semántico "incluir/calcular NC" es
        # checkbox en DOCUMENTOS DEL HISTORIAL. Un concepto, un control.
        self.nc_sw = control_factory.checkbox("Incluir NC/ND (neto)", value=True)
        # Jerarquia de los dos pasos: "Ver datos" consulta y pinta la vista
        # previa (por cliente: meses, lineas, neto S/); "Generar Excel" escribe
        # los archivos. Antes los dos eran accent relleno con rotulos
        # ("Generar" / "Exportar Excel") que no decian cual era el final: el
        # peso visual ahora lo dice, sin perder la verificacion previa.
        self.btn_generar = ft.OutlinedButton(
            "Ver datos",
            icon=ft.Icons.VISIBILITY_OUTLINED,
            height=control_factory.HEIGHT,
            style=ft.ButtonStyle(
                color=accent,
                side=ft.BorderSide(1, accent),
                shape=ft.RoundedRectangleBorder(radius=G360Theme.BTN_RADIUS),
            ),
            on_click=self._generar,
        )
        self.btn_exportar = ft.ElevatedButton(
            "Generar Excel",
            icon=ft.Icons.GRID_ON,
            height=control_factory.HEIGHT,
            style=ft.ButtonStyle(bgcolor=accent, color=G360Theme.section_button_fg()),
            on_click=self._exportar,
        )
        self.status = ft.Text("", size=G360Theme.TYPE_BODY, color=muted)
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
        header = ft.Column(
            [
                ft.Row(
                    [
                        ft.GestureDetector(content=titulo, on_tap=lambda _: self._toggle()),
                        self._resumen,
                        btn_limpiar,
                        self._chevron,
                    ],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=6,
                ),
                # Bajada en el header como el resto de cards: antes vivia como
                # primer hijo del body colapsable y la card cerrada no explicaba nada.
                G360Theme.subtitle(
                    "Consulta directa a la base local: elige cliente, rango y hojas a exportar"
                ),
            ],
            spacing=2,
        )

        # Chips de hojas a exportar (multiselección; todas marcadas por defecto)
        self._chips = {}
        self._chips_row = ft.Row([], wrap=True, spacing=6)
        for key, label in self.HOJAS_EXPORT:
            chip = ft.Chip(
                label=ft.Text(label, size=G360Theme.TYPE_SUBTITLE),
                selected=key in self.state["hojas"],
                show_checkmark=True,
                data=key,
                on_select=self._on_chip,
                tooltip=self.HOJAS_TOOLTIP.get(key, ""),
                padding=ft.padding.symmetric(horizontal=8, vertical=2),
            )
            self._chips[key] = chip
            self._chips_row.controls.append(chip)

        # Bloques con jerarquia (filters_block): uso cotidiano, luego los
        # criterios especificos y por ultimo como se empaqueta. Antes todo
        # compite en la misma caja: el SKU (uso ocasional) estaba en la misma
        # fila que el vendedor (uso diario).
        bloque_filtros = control_factory.filters_block(
            principales=[
                # Sin wrap y sin filas anidadas: un hijo con expand dentro de
                # un Row con wrap rompe el layout y pinta un bloque gris.
                ft.Row(
                    [self.vend_dd, self.btn_buscar_cli],
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                self.cli_chips,
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
                    ],
                    spacing=8,
                    wrap=True,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ],
            complementarios=[
                ft.Row(
                    [self.sku_field],
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ],
            salida=[
                self.nc_sw,
                # Botón Default: sin reset rápido, desmarcar 5 chips a mano
                # era el path común.
                ft.Row(
                    [
                        G360Theme.section_header(ft.Icons.LAYERS_OUTLINED, "Hojas a exportar"),
                        ft.TextButton(
                            "Default",
                            on_click=self._restaurar_hojas_default,
                            tooltip="Restaura las hojas por defecto (sucursales desactivada)",
                        ),
                    ],
                    spacing=8,
                ),
                self._chips_row,
            ],
        )

        self._body = ft.Container(
            content=ft.Column(
                [
                    bloque_filtros,
                    ft.Divider(height=4, color=G360Theme.border_subtle_color()),
                    # Acciones: el paso 2 (Generar Excel) es el primario.
                    ft.Row(
                        [self.btn_generar, self.btn_exportar],
                        spacing=8,
                        alignment=ft.MainAxisAlignment.END,
                    ),
                    # Salida
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

    def _restaurar_hojas_default(self):
        """Restaura DEFAULT_HOJAS y repinta los chips."""
        self.state["hojas"] = set(self.DEFAULT_HOJAS)
        for key, _ in self.HOJAS_EXPORT:
            self._chips[key].selected = key in self.state["hojas"]
        self._update()

    # ── expandir / colapsar / limpiar ────────────────────────────────

    def _toggle(self):
        if self._abierto:
            self.colapsar()
        else:
            self._mostrar()

    def abrir(self):
        """Abre la card sin generar (acceso directo del header).

        La zona de reporte vive al final del flujo y colapsada no dice
        nada: el boton del header abre y el scroll lo deja a la vista.
        """
        self.construir_card()
        if not self._abierto:
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

    def expandir(self, cliente=None, vendedor=None, rango=None, clientes=None):
        """Abre la card con prefill y genera.

        `cliente` es un (cid, nombre) individual; `clientes` es la lista completa
        que usa el botón "Reporte de compras" de la card Búsqueda, que ya viene
        con multi-selección. `expandir` los combina: la card nunca reemplaza una
        selección existente, la suma.
        """
        # Asegura la card construida: con zonas en vistas separadas, expandir
        # puede llegar antes del primer montaje (el puente de Busqueda).
        self.construir_card()
        seleccionados = []
        if cliente:
            seleccionados.append(cliente)
        seleccionados += [c for c in (clientes or []) if c]
        agregados = 0
        for cid, nom in seleccionados:
            if not any(c[0] == cid for c in self.state["clientes"]):
                self.state["clientes"].append((cid, nom or cid))
                agregados += 1
        if agregados:
            self._pintar_cli_chips()
            logger.info(
                "reporte.prefill: %d cliente(s) desde otra seccion (%d ya estaban)",
                len(seleccionados),
                agregados,
            )
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
            control_factory.chip_cliente(
                cid, nom, on_delete=lambda _, c=cid: self._quitar_cliente(c)
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
            skus=[],
            sku_sugerencias={},
        )
        self._pintar_cli_chips()
        self.vend_dd.value = None
        self.sku_field.value = ""
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
        self.state["skus"] = _parse_skus(getattr(self.sku_field, "value", "") or "")
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
            fallos: list = []
            t0 = time.perf_counter()
            skus = list(self.state["skus"])
            sug: dict = {}
            for cid, _nom in clientes:
                try:
                    if skus:
                        # Vista por SKU: grano mes×SKU (el agregado por línea
                        # no trae SKU y no se puede filtrar). El export sale
                        # completo: el filtro rige solo la vista previa.
                        df = cli.fetch_compras_sku_mes_cliente(
                            cid,
                            fecha_desde=fd,
                            fecha_hasta=fh,
                            incluir_nc=self.state["incluir_nc"],
                        )
                        datos[cid] = _filtrar_por_sku(df, skus)
                    else:
                        datos[cid] = cli.fetch_compras_cliente(
                            cid,
                            fecha_desde=fd,
                            fecha_hasta=fh,
                            incluir_nc=self.state["incluir_nc"],
                            solo_lineas_activas=False,
                        )
                    if skus and (datos[cid] is None or getattr(datos[cid], "empty", True)):
                        sug[cid] = self._sugerencias_sku(cli, cid, fd, fh, skus)
                except Exception as ex:
                    # Un cliente que falla no puede arrastrar a los otros: con
                    # multi-selección se perdían N-1 previews que sí funcionaban.
                    logger.exception("reporte.preview fallo cid=%s", cid)
                    datos[cid] = None
                    fallos.append(f"{_c_visible(cid)}: {ex}")
            self.state["datos"] = datos
            self.state["sku_sugerencias"] = sug
            self.state["fallos"] = fallos
            # Antes de liberar los botones: si el log va despues, un lector que
            # espera al fin de la tarea puede ver la consulta "sin rastro".
            logger.info(
                "reporte.preview %d cliente(s) fd=%s fh=%s nc=%s sku=%s fallos=%d (%.1fs)",
                len(clientes),
                fd,
                fh,
                self.state["incluir_nc"],
                ",".join(skus) if skus else "-",
                len(fallos),
                time.perf_counter() - t0,
            )
            self._set_busy(False)
            self._render_resultados(fd, fh, fallos)

        threading.Thread(target=task, daemon=True).start()

    def _render_resultados(self, fd, fh, fallos=None):
        self.resultados.controls.clear()
        datos = self.state.get("datos") or {}
        clientes = self.state["clientes"]
        fallos = list(fallos or self.state.get("fallos") or [])
        periodo = (
            f"{fecha_ui(self.state['desde']) if self.state['desde'] else 'inicio'}"
            f" → {fecha_ui(self.state['hasta']) if self.state['hasta'] else 'hoy'}"
        )
        tot_soles = 0.0
        tot_cant = 0.0
        n_ok = 0
        caidos = {linea.split(":", 1)[0] for linea in fallos}
        skus = list(self.state.get("skus") or [])
        por_sku = bool(skus)
        sku_txt = ",".join(skus)
        for cid, nombre in clientes:
            visible = _c_visible(cid)
            if visible in caidos:
                continue  # se reporta al final, como error (no como "sin compras")
            df = datos.get(cid)
            if df is None or df.empty:
                motivo = f" para SKU {sku_txt}" if por_sku else ""
                if por_sku:
                    cand = (self.state.get("sku_sugerencias") or {}).get(cid) or []
                    if cand:
                        motivo += f" — ¿quisiste decir {', '.join(cand)}?"
                self.resultados.controls.append(
                    ft.Text(
                        f"{nombre or cid} ({visible}) — sin compras en el rango{motivo}",
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
            if por_sku:
                detalle = f" · {df['COD_SKU'].nunique()} SKU(s)"
                tabla = _construir_tabla(df, col_cod="COD_SKU", col_nom="SKU", codigo_completo=True)
            else:
                detalle = f" · {df['COD_LINEA'].nunique()} línea(s)"
                tabla = _construir_tabla(df)
            self.resultados.controls.append(
                ft.Text(
                    f"{nombre or cid} ({_c_visible(cid)}){detalle}"
                    f" · {df['MES_REF'].nunique()} mes(es) · neto S/ {s:,.2f} · {q:,.0f} u",
                    size=12,
                    weight=ft.FontWeight.W_700,
                    color=ft.Colors.ON_SURFACE,
                )
            )
            self.resultados.controls.append(
                ft.Row([tabla], scroll=ft.ScrollMode.AUTO, expand=False)
            )

        # Los que no se pudieron consultar van al final, marcados como error y
        # no como "sin compras": son cosas distintas.
        for linea in fallos:
            self.resultados.controls.append(
                ft.Text(
                    f"{linea} — no se pudo consultar",
                    size=12,
                    color=self.app.G360_ERROR,
                )
            )

        n = len(clientes)
        if n_ok:
            self.status.value = (
                f"{n} cliente(s) · {n_ok} con compras · "
                f"neto S/ {tot_soles:,.2f} · {tot_cant:,.0f} u"
            )
            if por_sku:
                self.status.value += f" · SKU {sku_txt} (solo vista previa)"
            if fallos:
                self.status.value += f" · {len(fallos)} con error"
                self.status.color = self.app.G360_WARNING
            else:
                self.status.color = self.app.G360_SUCCESS
            self._resumen.value = f"{n} cliente(s) · {periodo} · S/ {tot_soles:,.2f}"
        elif fallos:
            self.status.value = f"No se pudo consultar {len(fallos)} cliente(s)"
            self.status.color = self.app.G360_ERROR
            self._resumen.value = ""
        else:
            self.status.value = "Sin compras en el rango"
            self.status.color = G360Theme.text_muted_color()
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
            fallidos: list = []
            ultimo = None
            t_ini = time.perf_counter()
            logger.info(
                "reporte.export inicio: %d cliente(s) hojas=%s nc=%s rango=%s..%s -> %s",
                len(clientes),
                "+".join(sorted(hojas)),
                self.state["incluir_nc"],
                fd,
                fh,
                out_dir,
            )
            try:
                for cid_raw, nombre in clientes:
                    t0 = time.perf_counter()
                    try:
                        bundle = _paquete_export(
                            cli,
                            cid_raw,
                            fd,
                            fh,
                            self.state["incluir_nc"],
                            hojas | {"resumen"},
                            # INTENCIONAL: el reporte de compras NO filtra por
                            # allowlist (incluye todas las lineas). El sustento
                            # (Busqueda) usa el default True. Mismo cliente y
                            # rango dan conjuntos distintos a proposito: cada
                            # seccion tiene sus reglas. No cambiar sin avisar.
                            solo_lineas_activas=False,
                            corte_hasta=fh,
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
                        out_path = resolve_output_path(
                            out_dir / nombre_archivo_compras(nombre, cid)
                        )
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
                    except Exception as ex:
                        # El fallo se acota al cliente: con multi-selección, un
                        # Excel trabado (archivo abierto, disco lleno) acababa el
                        # lote entero y los demas se quedaban sin nada.
                        logger.exception("reporte.export fallo cid=%s", _c_visible(cid_raw))
                        fallidos.append(f"{_c_visible(cid_raw)}: {ex}")
                        continue
                    generados += 1
                    ultimo = out_path
                    logger.info(
                        "reporte.export OK cid=%s -> %s (%.1fs)",
                        cid,
                        out_path.name,
                        time.perf_counter() - t0,
                    )
                logger.info(
                    "reporte.export fin: %d Excel en %.1fs (%d con error)",
                    generados,
                    time.perf_counter() - t_ini,
                    len(fallidos),
                )
                self.state["fallos_export"] = fallidos
                if fallidos:
                    app.show_snackbar(
                        f"⚠️ {generados} Excel generados, {len(fallidos)} con error: {fallidos[0]}",
                        app.G360_WARNING,
                    )
                elif generados == 1:
                    app.show_snackbar(f"✅ Excel: {ultimo.name}", app.G360_SUCCESS)
                else:
                    app.show_snackbar(f"✅ {generados} Excel generados", app.G360_SUCCESS)
                if os.name == "nt":
                    os.startfile(str(out_dir))
            except Exception as ex:
                # Solo llega algo que no sea el fallo de un cliente (p.ej. la
                # carpeta de salida): el try interno ya absorbe esos.
                logger.exception(
                    "reporte.export fallo tras %d Excel (%.1fs)",
                    generados,
                    time.perf_counter() - t_ini,
                )
                from src.ui.mensajes import mensaje

                app.show_snackbar(mensaje(ex, "generar el reporte"), app.G360_ERROR)
            finally:
                app.hide_loading()
                self._set_busy(False)
                self._update()

        threading.Thread(target=task, daemon=True).start()
