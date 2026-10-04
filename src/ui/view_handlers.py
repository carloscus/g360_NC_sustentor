# -*- coding: utf-8 -*-
import flet as ft
import logging
import os
import threading
from pathlib import Path
from types import SimpleNamespace

_logger = logging.getLogger("src.ui.reconocimiento_view")
from src.domain import ExpedienteComercial, PipelineContext
from src.pipeline import Pipeline
from src.core.g360_theme import G360Theme, safe_handler
from src.core.fechas import fecha_ui
from src.core.utils import (
    read_erp_file,
)
from src.ui.reconocimiento_config import TIPO_CONFIG, ESTRATEGIA_POR_TIPO
from src.ui.catalog import (
    CATALOGO,
    MODALIDAD_CONSOLIDADO,
    MODALIDAD_INDIVIDUAL,
    HistorialConfig,
    build_expediente_id,
)


"""Manejadores de eventos, cargas de datos y ejecución de
ReconocimientoView (mixins). Heredado por ReconocimientoView
(src/ui/reconocimiento_view.py)."""


def mapear_nc_existente(factura_val, doc_to_nc: dict) -> str:
    """Notas (NC/NDB) ya emitidas contra la factura de referencia.

    Acepta documento único o listas ("F1, F2" / "F1; F2", con o sin sufijos
    " (...)"): resuelve por SKU si la factura referencia está en la lista.
    Retorna los documentos relacionados sin repetir.
    """
    val = str(factura_val).strip()
    if not val or val.lower() == "nan":
        return ""
    parts = [
        p.strip().split(" (")[0].strip() for p in val.replace(";", ",").split(",") if p.strip()
    ]
    if len(parts) > 1:
        nc_docs = []
        for p in parts:
            for d in str(doc_to_nc.get(p, "")).split(","):
                d = d.strip()
                if d:
                    nc_docs.append(d)
        return ", ".join(sorted(set(nc_docs))) if nc_docs else ""
    key = parts[0] if parts else val
    return doc_to_nc.get(key, "")


def expandir_facturas(valor, numero_a_docs: dict = None) -> list:
    """DOC_IDs desde una celda FACTURA/FACTURAS.

    Acepta documento único, listas con ","/" ;" y sufijos " (...)". Los
    NUMEROs sueltos (sin serie, ej. bonificación) se expanden vía
    numero_a_docs {numero: [doc_ids]}. Sin repetir, en orden.
    """
    docs = []
    for p in str(valor or "").replace(";", ",").split(","):
        p = p.strip().split(" (")[0].strip()
        if not p or p.lower() == "nan":
            continue
        if "-" in p or not numero_a_docs:
            docs.append(p)
        else:
            for d in numero_a_docs.get(p, numero_a_docs.get(p.lstrip("0"), [p])):
                if d not in docs:
                    docs.append(d)
    return docs


def _historial_para_auditoria(historial, documentos_historial: dict):
    """Facts are the audit baseline; notes are included only if shown or used."""
    if historial is None or historial.empty or not documentos_historial:
        return historial
    if "TIPO_CLASE" not in historial.columns:
        return historial
    clases = historial["TIPO_CLASE"].astype(str).str.lower()
    facturas = clases == "factura"
    if "TIPO_DOC" in historial.columns:
        tpos = historial["TIPO_DOC"].astype(str).str.upper()
        es_ndb = tpos.str.startswith("ND")
    else:
        es_ndb = clases == "cargo"

    def _visible_o_usar(doc):
        value = documentos_historial.get(doc, {})
        if isinstance(value, dict):
            return bool(value.get("mostrar", True) or value.get("usar", False))
        if isinstance(value, (tuple, list)) and len(value) >= 2:
            return bool(value[0] or value[1])
        return True

    keep_nc = _visible_o_usar("nc")
    keep_ndb = _visible_o_usar("ndb")
    notas = ~facturas
    keep = facturas | (notas & es_ndb & keep_ndb) | (notas & ~es_ndb & keep_nc)
    return historial[keep].copy()


class _ViewHandlers:
    def _on_tipo_change(self, e):
        valor = e.control.value if e is not None and hasattr(e, "control") else None
        if valor:
            self.tipo_actual = valor
        elif hasattr(self, "tipo_dropdown"):
            self.tipo_actual = self.tipo_dropdown.value or self.tipo_actual
        if hasattr(self, "tipo_dropdown") and self.tipo_dropdown.value != self.tipo_actual:
            self.tipo_dropdown.value = self.tipo_actual
        # Default modalidad to first available for this caso
        caso_data = self._caso_de_tipo()
        mods = caso_data.get("modalidades", (MODALIDAD_INDIVIDUAL, MODALIDAD_CONSOLIDADO))
        if self.modalidad_actual not in mods:
            self.modalidad_actual = mods[0]
            self.modalidad_radio.value = mods[0]
        # Reset historic config to caso default
        self._historico_config = HistorialConfig.from_caso(CATALOGO[self.tipo_actual])
        self.resultado = None
        self.resultados_container.visible = False
        self.alertas_container.visible = False
        try:
            self._renderizar_ui()
        except Exception as ex:
            from src.ui.mensajes import mensaje

            self.app.show_snackbar(
                mensaje(ex, "pintar la pantalla"), color=self.app.G360_ERROR
            )
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
                row = ft.Row(
                    [
                        ft.Text(
                            f"  • {name}", size=12, color=ft.Colors.ON_SURFACE_VARIANT, expand=True
                        ),
                        ft.IconButton(
                            icon=ft.Icons.CLOSE,
                            icon_size=14,
                            height=24,
                            width=24,
                            on_click=lambda e, ps=str(p): self._quitar_requerimiento(ps),
                        ),
                    ],
                    spacing=4,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                )
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
            (self.df_historial["TIPO_DOC"].astype(str).str.strip().str.upper().str.startswith(tipo))
            & (self.df_historial["SERIE"].astype(str).str.strip() == serie)
            & (self.df_historial["NUMERO"].astype(str).str.strip() == nro)
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

            self.skus_table_sf.rows.append(
                ft.DataRow(
                    cells=[
                        ft.DataCell(ft.Text(sku, size=10)),
                        ft.DataCell(ft.Text(articulo, size=10)),
                        ft.DataCell(ft.Text(str(cant), size=10)),
                        ft.DataCell(ft.Text(f"S/ {pu:.5f}", size=10)),
                        ft.DataCell(ft.Text(f"S/ {total:.2f}", size=10)),
                        ft.DataCell(incluir),
                    ]
                )
            )

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
                ft.Row(
                    [
                        ft.Icon(
                            ft.Icons.WARNING_AMBER_OUTLINED, size=16, color=ft.Colors.AMBER_400
                        ),
                        ft.Text(
                            f"⚠ NC/NDB existentes: {len(ref_notas)} nota(s), S/ {total_soles:.2f}, {skus_afectados} SKU(s)",
                            size=12,
                            color=ft.Colors.AMBER_400,
                        ),
                    ],
                    spacing=6,
                ),
            ]
            for _, nr in ref_notas.head(5).iterrows():
                items.append(
                    ft.Text(
                        f"  • {nr.get('DOC_NOTA', '')} | {nr.get('CODIGO', '')} | Cant: {nr.get('CANTIDAD', 0)} | S/ {abs(nr.get('SOLES', 0)):.2f}",
                        size=10,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    )
                )
            self.alertas_nc_container_sf.content = ft.Column(items, spacing=4)
            self.alertas_nc_container_sf.bgcolor = ft.Colors.with_opacity(0.08, ft.Colors.AMBER_400)
            self.alertas_nc_container_sf.border = ft.border.all(
                1, ft.Colors.with_opacity(0.2, ft.Colors.AMBER_400)
            )
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

    def _on_modalidad_change(self, e):
        self._leer_historico_config()
        modalidad = self.modalidad_radio.value or MODALIDAD_INDIVIDUAL
        if modalidad != self.modalidad_actual and self.resultado:
            self._invalidar_resultado_por_config(
                "Cambió la modalidad; vuelve a ejecutar el cálculo."
            )
        self.modalidad_actual = modalidad
        self._renderizar_config(self._caso_de_tipo())
        if getattr(self, "container", None):
            self.container.update()
        if self.app.page:
            self.app.page.update()

    def _invalidar_resultado_por_config(self, mensaje):
        """No permitir exportar un resultado calculado con checks anteriores."""
        self.resultado = None
        if hasattr(self, "resultados_container"):
            self.resultados_container.visible = False
        if hasattr(self, "alertas_container"):
            self.alertas_container.visible = False
        if hasattr(self, "lbl_ejecutar_hint"):
            self.lbl_ejecutar_hint.value = mensaje
            self.lbl_ejecutar_hint.color = G360Theme.warning_color()
        if self.app and self.app.page:
            self.app.page.update()

    def _on_cliente_change_pd(self, e):
        self._verificar_puede_ejecutar()
        if self.app.page:
            self.app.page.update()

    def _quitar_sku_filter(self, e):
        """Boton clear del insumo Filtro SKU: libera path y re-habilita descuento global."""
        self.sku_filter_path = None
        self.lbl_sku_filter.value = "Ninguno"
        self.lbl_sku_filter.color = ft.Colors.ON_SURFACE_VARIANT
        if self._tipo_incluye("descuento_precio"):
            self.descuento_pct.disabled = False
        self.sku_filter_clear_btn.visible = False
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
            self.app.page.open(self.fp_desde)

    def _abrir_fp_hasta(self, e):
        if self.app.page:
            self.app.page.open(self.fp_hasta)

    def _on_cliente_change_fp(self, e):
        self._verificar_puede_ejecutar()
        if self.app.page:
            self.app.page.update()

    def _on_fp_desde_change(self, e):
        val = self.fp_desde.value
        label = fecha_ui(val) if val else "Sin filtro"
        self.fecha_desde_fp.text = f"Desde: {label}"
        self._verificar_puede_ejecutar()
        if self.app.page:
            self.app.page.update()

    def _on_fp_hasta_change(self, e):
        val = self.fp_hasta.value
        label = fecha_ui(val) if val else "Sin filtro"
        self.fecha_hasta_fp.text = f"Hasta: {label}"
        self._verificar_puede_ejecutar()
        if self.app.page:
            self.app.page.update()

    def _on_vendedor_change(self, e):
        for dd in [
            self.cliente_dropdown_sf,
            self.cliente_dropdown_ci,
            self.cliente_dropdown_df,
            self.cliente_dropdown_fp,
            self.cliente_dropdown_pb,
            self.cliente_dropdown_pd,
        ]:
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
        if self._tipo_incluye("rebate_volumen"):
            self._actualizar_lineas()
        self._verificar_puede_ejecutar()
        if self.app.page:
            self.app.page.update()

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
                lambda r: (
                    f"{str(r['TIPO_DOC']).strip()[0]}{str(r['SERIE']).strip()}-{str(r['NUMERO']).strip().replace('.0', '')}"
                ),
                axis=1,
            )
            facturas["DISPLAY"] = facturas.apply(
                lambda r: (
                    f"{r['DOC_ID']} | {str(r.get('FECHA', ''))[:10]} | S/ {r.get('SOLES', 0):,.2f}"
                ),
                axis=1,
            )
            # Descendente por fecha: la factura mas reciente primero
            unicas = (
                facturas.sort_values("FECHA", ascending=False)
                .groupby("DOC_ID")
                .first()
                .reset_index()
            )
            opts = [ft.dropdown.Option(key=r.DOC_ID, text=r.DISPLAY) for r in unicas.itertuples()][
                :200
            ]
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

    def _agregar_insumo(self, ruta: str):
        """Agrega un archivo XLSX y lo asigna al insumo correspondiente del caso.

        Clasifica por nombre de archivo; si no reconoce el nombre, lo asigna
        al primer insumo requerido pendiente del caso actual.
        """
        from pathlib import Path
        from src.ui.catalog import CATALOGO

        name = Path(ruta).name
        lower = name.lower()
        caso = CATALOGO.get(self.tipo_actual)
        needs = [i for i in (caso.insumos if caso else ()) if i != "historico"]
        asignado = {
            "lista_precios": lambda: self.lista_path,
            "sku": lambda: self.sku_filter_path,
            "cantidad": lambda: self.stock_cliente_path,
            "porcentaje": lambda: self.desc_file_path,
            "mecanica": lambda: self.requerimientos_paths,
        }

        # FPE: todo archivo externo es un requerimiento del evento (SKU +
        # cantidad + descuento) y entra al slot de requerimientos; el gate
        # de ejecución exige ese slot, así que el routing no depende del
        # nombre del archivo.
        if self._tipo_incluye("feria_preventa"):
            tipo = "mecanica"
        # VRS: el archivo combinado (SKU + CANTIDAD + PRECIO_BASE) es el insumo
        # principal y el 2º archivo es el override de cantidades. El nombre
        # no decide el slot (la plantilla oficial dice "cantidad"), así que
        # se reparte por orden de carga.
        elif self._tipo_incluye("diferencia_stock"):
            tipo = "lista_precios" if not self.lista_path else "cantidad"
        elif "lista" in lower or "precio" in lower or "condicion" in lower:
            tipo = "lista_precios"
        elif "sku" in lower or "filtro" in lower:
            tipo = "sku"
        elif "stock" in lower or "cantidad" in lower:
            tipo = "cantidad"
        elif "descuento" in lower or "%" in lower or "rebate" in lower:
            tipo = "porcentaje"
        else:
            tipo = None
        if tipo is None:
            for cand in ("lista_precios", "sku", "cantidad", "porcentaje", "mecanica"):
                if cand in needs and not (asignado[cand]() if cand in asignado else None):
                    tipo = cand
                    break
        if tipo is None:
            tipo = "extra"

        labels = {
            "lista_precios": "Lista precios",
            "sku": "Filtro SKU",
            "cantidad": "Stock",
            "porcentaje": "Descuentos",
            "mecanica": "Mecánica",
            "extra": "Adjunto",
        }
        if self._tipo_incluye("diferencia_stock"):
            labels = {
                "lista_precios": "Cantidad y precio por SKU",
                "cantidad": "Override de cantidades",
            }
        if self._tipo_incluye("feria_preventa"):
            labels["mecanica"] = "Requerimientos"
        if tipo == "lista_precios":
            self.lista_path = ruta
            self.lbl_lista.value = f"✓ {name}"
            self.lbl_lista.color = self.app.G360_SUCCESS
        elif tipo == "sku":
            self.sku_filter_path = ruta
            self.lbl_sku_filter.value = f"✓ {name}"
            self.lbl_sku_filter.color = self.app.G360_SUCCESS
            self.sku_filter_clear_btn.visible = True
        elif tipo == "cantidad":
            self.stock_cliente_path = ruta
            self.lbl_stock_cliente.value = f"✓ {name}"
            self.lbl_stock_cliente.color = self.app.G360_SUCCESS
            self.stock_cliente_clear_btn.visible = True
        elif tipo == "porcentaje":
            self.desc_file_path = ruta
            self.lbl_desc_file.value = f"✓ {name}"
            self.lbl_desc_file.color = self.app.G360_SUCCESS
        elif tipo == "mecanica":
            if Path(ruta) not in self.requerimientos_paths:
                self.requerimientos_paths.append(Path(ruta))
            self._actualizar_lista_requerimientos()
        # El % global de DO es la alternativa al archivo por SKU: al cargar
        # un archivo el campo se deshabilita. Va DESPUÉS de la cadena para
        # no cortarla (antes un `if` aquí impedia asignar cantidad/porcentaje).
        if self._tipo_incluye("descuento_precio"):
            self.descuento_pct.disabled = True

        self._insumo_files.append(
            {
                "ruta": ruta,
                "tipo": labels[tipo],
                "lbl": name,
                "preview": None,
                "preview_loading": True,
            }
        )
        self._renderizar_lista_insumos()
        self._verificar_puede_ejecutar()

    def _quitar_insumo(self, e, ruta: str):
        """Quita un archivo de la lista y libera su insumo asignado."""
        self._insumo_files = [f for f in self._insumo_files if f["ruta"] != ruta]
        if self.lista_path == ruta:
            self.lista_path = None
            self.lbl_lista.value = "Ninguno"
            self.lbl_lista.color = ft.Colors.ON_SURFACE_VARIANT
        if self.desc_file_path == ruta:
            self.desc_file_path = None
            self.lbl_desc_file.value = "Ninguno"
            self.lbl_desc_file.color = ft.Colors.ON_SURFACE_VARIANT
        if self.sku_filter_path == ruta:
            self.sku_filter_path = None
            self.lbl_sku_filter.value = "Ninguno"
            self.lbl_sku_filter.color = ft.Colors.ON_SURFACE_VARIANT
            self.sku_filter_clear_btn.visible = False
        if self.stock_cliente_path == ruta:
            self.stock_cliente_path = None
            self.lbl_stock_cliente.value = "Ninguno"
            self.lbl_stock_cliente.color = ft.Colors.ON_SURFACE_VARIANT
            self.stock_cliente_clear_btn.visible = False
        if Path(ruta) in self.requerimientos_paths:
            self.requerimientos_paths = [p for p in self.requerimientos_paths if str(p) != ruta]
            self._actualizar_lista_requerimientos()
        if self._tipo_incluye("descuento_precio") and not (
            self.desc_file_path or self.sku_filter_path
        ):
            # Sin archivo de descuentos vuelve a estar disponible el % global
            # (es la alternativa, no un sustituto permanente).
            self.descuento_pct.disabled = False
        self._renderizar_lista_insumos()
        self._verificar_puede_ejecutar()
        if self.app.page:
            self.app.page.update()

    def _cargar_preview_insumo(self, ruta: str, idx: int, key: str):
        """Lee primeras 5 filas del XLSX en thread separado."""
        try:
            import pandas as pd

            df = pd.read_excel(ruta, nrows=5, engine="openpyxl")
            if df.empty:
                headers, rows = [], []
            else:
                headers = [str(c).strip() for c in df.columns]
                rows = [[str(v) if pd.notna(v) else "" for v in row] for row in df.values]
            if idx < len(self._insumo_files) and self._insumo_files[idx]["ruta"] == key:
                self._insumo_files[idx]["preview"] = {"headers": headers, "rows": rows}
                self._insumo_files[idx]["preview_loading"] = False
        except Exception:
            if idx < len(self._insumo_files):
                self._insumo_files[idx]["preview"] = {"headers": [], "rows": []}
                self._insumo_files[idx]["preview_loading"] = False

    def _aplicar_fragmento(self, e=None):
        """Convierte el ultimo resultado de Buscar en el insumo HISTORIAL."""
        df = self._busq_df
        if df is None or df.empty:
            self.app.show_snackbar("Primero busca un fragmento de historial", self.app.G360_ERROR)
            return
        self.df_historial = df
        self._hist_fragmento = self._fragmento_stats(df)
        self.lbl_historial.value = f"✓ SQLite ({len(df):,} filas)"
        self.lbl_historial.color = self.app.G360_SUCCESS
        try:
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
        except Exception:
            pass
        self._renderizar_ui()
        self._auto_seleccionar_unicos()
        self._refrescar_card_db()
        self._verificar_puede_ejecutar()
        # Limpiar estado de búsqueda para que la reconstrucción no repita badges/preview
        self._busq_df = None
        self._sel_facturas = []
        self._sel_clientes = []
        self._sel_pedidos = []
        self._sel_ordenes = []
        if hasattr(self, "_detalle_docs"):
            self._detalle_docs.clear()
        # Limpiar UI visual de la card DATOS DEL CASO
        if hasattr(self, "busq_cli_chips"):
            self.busq_cli_chips.controls.clear()
            self.busq_cli_chips.visible = False
        if hasattr(self, "busq_ped_chips"):
            self.busq_ped_chips.controls.clear()
            self.busq_ped_chips.visible = False
        if hasattr(self, "busq_oc_chips"):
            self.busq_oc_chips.controls.clear()
            self.busq_oc_chips.visible = False
        if hasattr(self, "busq_fac_chips"):
            self.busq_fac_chips.controls.clear()
            self.busq_fac_chips.visible = False
        if hasattr(self, "busq_vend_dd"):
            self.busq_vend_dd.options.clear()
            self.busq_vend_dd.value = None
            self.busq_vend_dd.visible = False
            self.busq_vend_dd.disabled = True
        if hasattr(self, "busq_vend_status"):
            self.busq_vend_status.value = (
                "Historial aplicado. Busca un cliente para seleccionar otro fragmento."
            )
            self.busq_vend_status.visible = True
        if hasattr(self, "btn_pin_vend"):
            self.btn_pin_vend.visible = False
        if hasattr(self, "btn_pin_cli"):
            self.btn_pin_cli.visible = False
        if hasattr(self, "busq_doc_row"):
            self.busq_doc_row.visible = False
        if hasattr(self, "busq_doc_prompt"):
            self.busq_doc_prompt.value = (
                "Selecciona un cliente para filtrar por pedido, O/C o factura."
            )
            self.busq_doc_prompt.visible = True
        for attr in ("busq_doc_ped_btn", "busq_doc_oc_btn", "busq_doc_fac_btn"):
            if hasattr(self, attr):
                getattr(self, attr).visible = False
        # Resetear status y badges
        if hasattr(self, "busq_status"):
            self.busq_status.value = "Historial aplicado a este caso"
            self.busq_status.color = ft.Colors.ON_SURFACE_VARIANT
        if hasattr(self, "busq_badges"):
            self.busq_badges.visible = False
            self.busq_badges.controls.clear()
        if hasattr(self, "busq_preview_tbl"):
            self.busq_preview_tbl.visible = False
            if hasattr(self, "busq_preview_rows"):
                self.busq_preview_rows.controls.clear()
        if hasattr(self, "busq_exec_btn"):
            self.busq_exec_btn.visible = False
            self.busq_exec_btn.disabled = True
        if self.app.page:
            self.app.page.update()
        self.app.show_snackbar(
            f"Historial aplicado: {len(df):,} filas — completa insumos y ejecuta",
            self.app.G360_SUCCESS,
        )

    def _auto_seleccionar_unicos(self):
        """Si el fragmento tiene 1 cliente/1 factura, preseleccionarlos en la UI."""
        try:
            opts = self.cliente_dropdown_sf.options
            if opts and len(opts) == 1:
                self.cliente_dropdown_sf.value = opts[0].key
                self.selector_sf.cargar_facturas(self.df_historial)
                fopts = self.factura_dropdown_sf.options
                if fopts and len(fopts) == 1:
                    self.factura_dropdown_sf.value = fopts[0].key
                    self._render_sku_table_sf(fopts[0].key)
                    self._check_existing_notes_sf(fopts[0].key)
            opts_ci = self.cliente_dropdown_ci.options
            if opts_ci and len(opts_ci) == 1:
                self.cliente_dropdown_ci.value = opts_ci[0].key
                self.selector_ci.cargar_facturas(self.df_historial)
                fopts = self.factura_dropdown_ci.options
                if fopts and len(fopts) == 1:
                    self.factura_dropdown_ci.value = fopts[0].key
            opts_df = self.cliente_dropdown_df.options
            if opts_df and len(opts_df) == 1:
                self.cliente_dropdown_df.value = opts_df[0].key
                self.selector_df.cargar_facturas(self.df_historial)
                fopts = self.factura_dropdown_df.options
                if fopts and len(fopts) == 1:
                    self.factura_dropdown_df.value = fopts[0].key
            if self.app.page:
                self.app.page.update()
        except Exception:
            pass

    def _quitar_stock_cliente(self, e):
        self.stock_cliente_path = None
        self.lbl_stock_cliente.value = "Ninguno"
        self.lbl_stock_cliente.color = ft.Colors.ON_SURFACE_VARIANT
        self.stock_cliente_clear_btn.visible = False
        self._verificar_puede_ejecutar()
        if self.app.page:
            self.app.page.update()

    def _cargar_stock_cliente(self, e):
        es_vrs = self._tipo_incluye("diferencia_stock")
        self.app.show_loading(
            "Seleccionando archivo de cantidades..."
            if es_vrs
            else "Seleccionando archivo de stock..."
        )

        def pick():
            try:
                ruta = self.app._pick_file(
                    "Seleccionar archivo por SKU (cantidad + precio)"
                    if es_vrs
                    else "Seleccionar Stock del Cliente"
                )
                if ruta:
                    self.stock_cliente_path = ruta
                    self.lbl_stock_cliente.value = f"✓ {Path(ruta).name}"
                    self.lbl_stock_cliente.color = self.app.G360_SUCCESS
                    self.stock_cliente_clear_btn.visible = True
                    self._verificar_puede_ejecutar()
            except Exception as ex:
                from src.ui.mensajes import mensaje

                self.app.show_snackbar(
                    mensaje(ex, "consultar el stock del cliente"), self.app.G360_ERROR
                )
            finally:
                self.app.hide_loading()
                if self.app.page:
                    self.app.page.update()

        threading.Thread(target=pick, daemon=True).start()

    @safe_handler
    def _verificar_puede_ejecutar(self):
        if not self.app.page:
            return
        cfg = self._caso_de_tipo()
        necesita_lista = self._insumo_necesita("lista_precios", cfg)
        puede = self.df_historial is not None
        if necesita_lista:
            puede = puede and self.lista_path is not None
        if cfg.get("strategy") == "feria_preventa":
            puede = puede and len(self.requerimientos_paths) > 0
        # La seleccion de cliente/factura vive en el fragmento de busqueda
        # (USAR COMO HISTORIAL); no se exigen dropdowns manuales.
        fragmento = self._hist_fragmento is not None
        if self.tipo_actual == "ANF":
            puede = puede and fragmento and self._hist_fragmento.get("facturas", 0) == 1
        if self.tipo_actual == "DO":
            try:
                tiene_pct = float(self.descuento_pct.value or "0") > 0
            except ValueError:
                tiene_pct = False
            puede = puede and (
                self.desc_file_path is not None or self.sku_filter_path is not None or tiene_pct
            )
        if self.tipo_actual == "CMV":
            try:
                meta_val = float(self.meta_monto.value or "0")
                pct_val = float(self.rebate_pct.value or "0")
                puede = puede and meta_val > 0 and pct_val > 0
            except ValueError:
                puede = False
        self.btn_ejecutar.disabled = not puede
        if puede:
            self.lbl_ejecutar_hint.value = "Datos e insumos listos. Puedes ejecutar el cálculo."
            self.lbl_ejecutar_hint.color = G360Theme.ok_color()
        elif self.df_historial is None:
            self.lbl_ejecutar_hint.value = (
                "En Datos del caso, busca y aplica un fragmento del historial."
            )
            self.lbl_ejecutar_hint.color = G360Theme.warning_color()
        elif necesita_lista and not self.lista_path:
            if self._tipo_incluye("diferencia_stock"):
                self.lbl_ejecutar_hint.value = (
                    "Carga el archivo por SKU: CANTIDAD, PRECIO_BASE y descuentos."
                )
            else:
                self.lbl_ejecutar_hint.value = (
                    "Falta cargar la lista de precios requerida por este caso."
                )
            self.lbl_ejecutar_hint.color = G360Theme.warning_color()
        elif self.tipo_actual == "ANF" and (
            not fragmento or self._hist_fragmento.get("facturas", 0) != 1
        ):
            self.lbl_ejecutar_hint.value = (
                "Anulación requiere exactamente una factura en el historial."
            )
            self.lbl_ejecutar_hint.color = G360Theme.warning_color()
        elif self.tipo_actual == "DO":
            self.lbl_ejecutar_hint.value = (
                "Indica un descuento o agrega el archivo de porcentaje/SKU."
            )
            self.lbl_ejecutar_hint.color = G360Theme.warning_color()
        elif self.tipo_actual == "CMV":
            self.lbl_ejecutar_hint.value = (
                "Completa una meta y un porcentaje de rebate mayores que cero."
            )
            self.lbl_ejecutar_hint.color = G360Theme.warning_color()
        elif cfg.get("strategy") == "feria_preventa" and not self.requerimientos_paths:
            self.lbl_ejecutar_hint.value = (
                "Agrega el archivo de requerimientos de la feria/preventa."
            )
            self.lbl_ejecutar_hint.color = G360Theme.warning_color()
        else:
            self.lbl_ejecutar_hint.value = "Completa los insumos requeridos por este caso."
            self.lbl_ejecutar_hint.color = G360Theme.warning_color()
        try:
            self.btn_ejecutar.update()
            self.lbl_ejecutar_hint.update()
        except AssertionError:
            pass
        if self.app and self.app.page:
            self.app.page.update()

    @safe_handler
    def _ejecutar(self, e):
        self.app.show_loading("Ejecutando reconocimiento...")

        def task():
            try:
                from src.ui.config_builder import build_config, build_datos_exp

                # Map catalog case + modalidad → legacy tipo for config_builder compat
                legacy_tipo = self._legacy_tipo()
                estrategia, variante = ESTRATEGIA_POR_TIPO.get(legacy_tipo, ("", ""))

                ui = self._collect_ui_values()
                config = build_config(legacy_tipo, ui)
                if legacy_tipo in (
                    "diferencia_precio",
                    "diferencia_stock",
                    "diferencia_cantidad",
                    "descuento_precio",
                    "feria_preventa",
                ) and not config.get("documentos_historial", {}).get("facturas", {}).get(
                    "usar", True
                ):
                    self.app.show_snackbar(
                        "Este cálculo requiere Facturas: Usar; activa el check para continuar.",
                        self.app.G360_WARNING,
                    )
                    return
                datos_exp = build_datos_exp(legacy_tipo, self.df_historial, config, ui)

                exp = ExpedienteComercial(
                    nombre=(
                        CATALOGO.get(self.tipo_actual) or SimpleNamespace(label=self.tipo_actual)
                    ).label,
                    familia=(
                        CATALOGO.get(self.tipo_actual) or SimpleNamespace(label=self.tipo_actual)
                    ).label,
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
                lt = self._legacy_tipo()
                cfg = TIPO_CONFIG.get(lt, {})
                if cfg.get("necesita_lista", False) and self.lista_path:
                    cond_df = read_erp_file(self.lista_path)
                    condiciones.append(cond_df)
                if lt in ("diferencia_cantidad", "diferencia_stock") and self.stock_cliente_path:
                    # Override opcional de cantidades (SKU + CANTIDAD):
                    # condiciones[1] del motor CantidadDeterminada. El
                    # archivo combinado de condiciones[0] manda si no hay
                    # este. `diferencia_cantidad` es alias oculto de VRS.
                    condiciones.append(read_erp_file(self.stock_cliente_path))
                if self.tipo_actual == "DO" and self.desc_file_path:
                    cond_df = read_erp_file(self.desc_file_path)
                    condiciones.append(cond_df)
                exp.condiciones = condiciones

                pipeline = Pipeline()
                exp = pipeline.ejecutar(exp)
                self.resultado = exp

                # Generate exp_id for audit evidence file naming
                if exp.resultado and exp.resultado.dataframe is not None:
                    df_res = exp.resultado.dataframe
                    cliente_id = ""
                    for col in ("COD_CLIENTE", "id_cliente"):
                        if col in df_res.columns:
                            vals = df_res[col].dropna().unique()
                            if len(vals) > 0:
                                cliente_id = str(vals[0]).strip()
                                break
                    doc_ref = ui.get("factura_ci") or ui.get("factura_df") or ""
                    serie, nro = "", ""
                    if doc_ref and "-" in doc_ref:
                        parte, nro = doc_ref.rsplit("-", 1)
                        serie = parte[1:] if len(parte) > 1 else parte
                    if cliente_id and serie and nro:
                        exp.exp_id = build_expediente_id(self.tipo_actual, cliente_id, serie, nro)

                if exp.resultado and not exp.resultado.dataframe.empty:
                    df_res = exp.resultado.dataframe
                    from src.core.document_classifier import resumen_global

                    historial_visible = _historial_para_auditoria(
                        self.df_historial, config.get("documentos_historial", {})
                    )
                    solo_ncnd = (
                        historial_visible[
                            historial_visible["TIPO_CLASE"].astype(str).str.lower() != "factura"
                        ]
                        if (
                            historial_visible is not None
                            and "TIPO_CLASE" in historial_visible.columns
                        )
                        else historial_visible
                    )
                    if (
                        not solo_ncnd.empty
                        and "CODIGO" in solo_ncnd.columns
                        and "SKU" in df_res.columns
                    ):
                        skus_procesados = set(df_res["SKU"].astype(str).str.strip())
                        solo_ncnd = solo_ncnd[
                            solo_ncnd["CODIGO"].astype(str).str.strip().isin(skus_procesados)
                        ]
                    full_resumen = resumen_global(solo_ncnd)
                    if full_resumen:
                        exp.resultado.metricas["nc_detalle"] = full_resumen
                    hay_notas_visibles = (
                        historial_visible is not None
                        and "TIPO_CLASE" in historial_visible.columns
                        and bool(
                            (
                                historial_visible["TIPO_CLASE"].astype(str).str.lower() != "factura"
                            ).any()
                        )
                    )
                    if (
                        historial_visible is not None
                        and "NC_ASOCIADAS" in historial_visible.columns
                        and hay_notas_visibles
                    ):
                        doc_to_nc = dict(
                            zip(
                                historial_visible["DOC_ID"],
                                historial_visible["NC_ASOCIADAS"].apply(
                                    lambda x: ", ".join(x) if x else ""
                                ),
                            )
                        )

                        def _map_nc_existente(factura_val):
                            return mapear_nc_existente(factura_val, doc_to_nc)

                        factura_col = (
                            "FACTURAS"
                            if "FACTURAS" in df_res.columns
                            else "FACTURA"
                            if "FACTURA" in df_res.columns
                            else None
                        )
                        if factura_col:
                            df_res["NC_EXISTENTE"] = df_res[factura_col].apply(_map_nc_existente)
                    # AL12: cruce NC/NDB por (factura, SKU) con cantidades
                    # FAE. Solo informativa: se anexa a la ALERTA sin
                    # excluir la fila del expediente.
                    from src.core.detector import (
                        detectar_notas_en_historial,
                        resumen_notas_por_factura,
                    )
                    from src.core.nc_auditor import CreditNoteAuditor as _CNA
                    from src.domain import BusinessAlert as _BA12
                    from src.domain import generar_texto_alerta as _gta12

                    historial_para_notas = (
                        historial_visible
                        if historial_visible is not None
                        else self.df_historial.iloc[0:0]
                    )
                    _resumen_nc = resumen_notas_por_factura(
                        detectar_notas_en_historial(historial_para_notas)
                    )
                    _resumen_norm = {
                        fac: {_CNA._norm_sku(s): info for s, info in data.get("skus", {}).items()}
                        for fac, data in _resumen_nc.items()
                    }
                    _num_a_docs = {}
                    if self.df_historial is not None and not self.df_historial.empty:
                        for _, _hr in self.df_historial.iterrows():
                            _nro = str(_hr.get("NUMERO", "")).strip()
                            if not _nro:
                                continue
                            _doc = (
                                f"{str(_hr.get('TIPO_DOC', '')).strip()[:1]}"
                                f"{str(_hr.get('SERIE', '')).strip()}-"
                                f"{_nro}"
                            ).strip("-")
                            for _key in {_nro, _nro.lstrip("0")}:
                                _lst = _num_a_docs.setdefault(_key, [])
                                if _doc not in _lst:
                                    _lst.append(_doc)
                    _al12_vistas = set()

                    def _al12_row(_df, _fac_col, _ale_col, _anexar_alerta=True):
                        if (
                            _fac_col is None
                            or _ale_col not in _df.columns
                            or "SKU" not in _df.columns
                        ):
                            return
                        for _idx, _fila in _df.iterrows():
                            _sku_raw = str(_fila.get("SKU", "")).strip()
                            _sku = _CNA._norm_sku(_fila.get("SKU", ""))
                            if not _sku:
                                continue
                            _segs = []
                            for _fac in expandir_facturas(_fila.get(_fac_col, ""), _num_a_docs):
                                _info = _resumen_norm.get(_fac, {}).get(_sku)
                                if _info and _info.get("docs"):
                                    _segs.append(
                                        _gta12(
                                            "AL12", factura=_fac, docs=_info["docs"], sku=_sku_raw
                                        )
                                    )
                            if not _segs:
                                continue
                            _al12 = " | ".join(_segs)
                            _base = str(_fila.get(_ale_col, "") or "")
                            if not _base.strip() or _base.strip().upper() == "OK":
                                _df.at[_idx, _ale_col] = _al12
                            elif "AL12" not in _base:
                                _df.at[_idx, _ale_col] = f"{_base} | {_al12}"
                            if _anexar_alerta and _al12 not in _al12_vistas:
                                _al12_vistas.add(_al12)
                                exp.resultado.alertas.append(
                                    _BA12(
                                        codigo="AL12",
                                        tipo="warning",
                                        severidad="media",
                                        sku=_sku_raw,
                                        mensaje=_al12,
                                        motor=exp.estrategia if exp.estrategia else "N/A",
                                    )
                                )

                    if _resumen_norm and not config.get("_reconciliar_nc_factura_sku"):
                        _al12_row(df_res, factura_col, "ALERTA")
                        _df_x = (
                            exp.resultado.get_excel()
                            if hasattr(exp.resultado, "get_excel")
                            else None
                        )
                        if _df_x is not None and _df_x is not df_res and not _df_x.empty:
                            _fx_col = next(
                                (
                                    c
                                    for c in ("FACTURAS", "FACTURA", "Facturas (qty)")
                                    if c in _df_x.columns
                                ),
                                None,
                            )
                            _al_col = next(
                                (c for c in ("ALERTA", "Alerta") if c in _df_x.columns), None
                            )
                            if _fx_col and _al_col:
                                _al12_row(_df_x, _fx_col, _al_col, _anexar_alerta=False)
                    from src.core.nc_auditor import CreditNoteAuditor

                    audit_historial = historial_visible
                    nc_alertas = CreditNoteAuditor().auditar(
                        audit_historial,
                        df_res,
                        documentos_historial=config.get("documentos_historial"),
                        modalidad=ui.get("modalidad", "individual"),
                    )
                    exp.resultado.metricas["nc_alertas"] = nc_alertas
                    _df_x = (
                        exp.resultado.get_excel() if hasattr(exp.resultado, "get_excel") else None
                    )
                    CreditNoteAuditor.combinar_auditoria(df_res, nc_alertas)
                    if _df_x is not None and _df_x is not df_res:
                        CreditNoteAuditor.combinar_auditoria(_df_x, nc_alertas)

                    if (
                        historial_visible is not None
                        and "TIPO_CLASE" in historial_visible.columns
                        and not config.get("_reconciliar_nc_factura_sku")
                    ):
                        notas_df = historial_visible[historial_visible["TIPO_CLASE"] != "factura"]
                        if not notas_df.empty:
                            facturas_con_nc = notas_df["FACTURA_REF"].dropna().unique()
                            if len(facturas_con_nc) > 0:
                                from src.domain import BusinessAlert

                                exp.resultado.alertas.insert(
                                    0,
                                    BusinessAlert(
                                        tipo="warning",
                                        severidad="baja",
                                        mensaje=f"Facturas con NC/ND existentes en el rango: "
                                        f"{', '.join(sorted(facturas_con_nc)[:5])}"
                                        f"{'...' if len(facturas_con_nc) > 5 else ''}. "
                                        f"Evaluar ajustes manuales.",
                                        motor=exp.estrategia if exp.estrategia else "N/A",
                                    ),
                                )

                if exp.resultado and not exp.resultado.dataframe.empty:
                    self._mostrar_resultado()
                else:
                    alertas_ordenadas = sorted(
                        exp.alertas or [],
                        key=lambda a: 0 if a.tipo == "error" else 1 if a.tipo == "warning" else 2,
                    )
                    alert_msgs = [a.mensaje for a in alertas_ordenadas][:3]
                    detail = " | ".join(alert_msgs) if alert_msgs else "Sin alertas"
                    self.app.show_snackbar(f"Sin resultados: {detail}", self.app.G360_WARNING)

            except Exception as ex:
                from src.ui.mensajes import mensaje

                self.app.show_snackbar(
                    mensaje(ex, "cargar el detalle del cliente"), self.app.G360_ERROR
                )
                self.app.hide_loading()
                if self.app.page:
                    self.app.page.update()

        threading.Thread(target=task, daemon=True).start()

    def _omitir_activo(self):
        chk = getattr(self, "chk_omitir_sin_dif", None)
        return bool(chk is not None and chk.value)

    def _nd_activo(self):
        chk = getattr(self, "chk_gen_nd", None)
        return bool(chk is not None and chk.value)

    def _resultado_efectivo(self):
        """Resultado para pantalla aplicando las OPCIONES DE CÁLCULO.

        Con "Omitir SKUs sin diferencia" activo se clona el resultado ya
        filtrado (mismo criterio que usa la generación), para que la tabla,
        totales y la verificación pre-exportación coincidan con el expediente.
        Devuelve None si tras omitir no queda nada visible.
        """
        if not self._omitir_activo():
            return self.resultado
        try:
            from src.ui.expediente_service import _clonar_sin_diferencia
        except ImportError:
            return self.resultado
        try:
            clon = _clonar_sin_diferencia(self.resultado)
        except Exception:
            return self.resultado
        if clon is None or not clon.resultado:
            return None
        df = clon.resultado.dataframe
        if df is None or getattr(df, "empty", True):
            return None
        return clon

    def _on_opciones_calculo_change(self, e=None):
        """Actualiza la vista de resultados al cambiar los checks de cálculo."""
        if not (self.resultado and self.resultado.resultado is not None):
            return
        df = self.resultado.resultado.dataframe
        if df is None or getattr(df, "empty", True):
            return
        self._mostrar_resultado()

    def _mostrar_resultado(self):
        from src.ui.resultados_view import render_resultado

        if not self.resultado or not self.resultado.resultado:
            return

        vista = self._resultado_efectivo()
        if vista is None:
            self.app.show_snackbar(
                "Tras omitir SKUs sin diferencia no quedan filas visibles.",
                self.app.G360_WARNING,
            )
            return

        result = render_resultado(
            vista,
            self.tipo_actual,
            self.df_historial,
            self.app.G360_ACCENT,
            self.app.G360_SUCCESS,
            marcar_nd=self._nd_activo() and not self._omitir_activo(),
        )

        # ── Update always-relevant labels ──
        self.lbl_total_nc.value = result["total_nc"]
        self.lbl_skus.value = result["skus_label"]
        self.lbl_alertas_count.value = result["alertas_label"]

        if result["content"] is None:
            self.resultados_container.visible = False
            return

        # ── Toggles (rebate) ──
        if result["aplicar_toggles"]:
            self.aplicar_toggles = result["aplicar_toggles"]
            for linea, (cb, _) in self.aplicar_toggles.items():

                def _make_on_toggle(ln):
                    def _on_toggle(e):
                        checked = sum(
                            float(val) for l, (c, val) in self.aplicar_toggles.items() if c.value
                        )
                        self.lbl_total_nc.value = f"S/ {checked:,.2f}"
                        if self.app.page:
                            self.app.page.update()

                    return _on_toggle

                cb.on_change = _make_on_toggle(linea)

        # ── Table: rebuild columns + initial rows ──
        self.resultados_table.columns.clear()
        self.resultados_table.rows.clear()
        columns, rows = result["table_columns"], result["table_rows"]
        for col in columns:
            self.resultados_table.columns.append(col)
        for row in rows:
            self.resultados_table.rows.append(row)

        # ── Column header tap → sort (API Flet: DataColumn.on_sort) ──
        for col in self.resultados_table.columns:
            col.on_sort = lambda e: self._ordenar_por(getattr(e.control, "data", None))

        # ── Lazy load button ──
        lazy = result.get("lazy_info", {})
        if lazy.get("has_more", False):
            self._result_load_more_btn.text = f"Ver más ({lazy['shown_rows']}/{lazy['total_rows']})"
            self._result_load_more_btn.visible = True
        else:
            self._result_load_more_btn.visible = False

        # ── CSV & Audit buttons ──
        has_data = len(rows) > 0
        self._result_audit_btn.visible = has_data

        # ── Warning banner ──
        warn = result.get("expediente_warning")
        if warn:
            self._result_warn_row.controls.clear()
            self._result_warn_row.controls.append(warn)
            self._result_warn_row.visible = True
        else:
            self._result_warn_row.visible = False

        # ── Alertas panel ──
        if result["alertas_visible"] and result["alertas_content"]:
            self.alertas_container.content = result["alertas_content"]
            self.alertas_container.visible = True
        else:
            self.alertas_container.visible = False

        # ── Build or reuse the stable panel structure ──
        self._rearmar_result_summary(result)
        self.resultados_container.content = self._result_summary
        self.resultados_container.visible = True
        self.btn_expediente.disabled = False

    def _rearmar_result_summary(self, result):
        """Arma el panel de resultados actualizando las piezas variables.

        Reusa la estructura estable (tabla, avisos, carga perezosa y botones)
        para evitar duplicados: cada control variable del contenido renderizado
        se ubica en un índice fijo dentro de _result_summary.
        """
        content = result["content"]
        n = len(content.controls)
        header = content.controls[0]
        summary_cards = content.controls[1]
        audit_panel = content.controls[2] if n > 2 else None
        divider1 = content.controls[3] if n > 3 else None
        legend = content.controls[4] if n > 4 else None
        divider2 = content.controls[5] if n > 5 else None
        divider3 = content.controls[7] if n > 7 else None
        verification = content.controls[8] if n > 8 else None

        self._result_summary = ft.Column(
            [
                header,
                summary_cards,
                audit_panel,
                divider1,
                legend,
                divider2,
                self._result_table_wrap,
                self._result_warn_row,
                self._result_load_more_row,
                divider3,
                verification,
                self._result_btn_row,
            ],
            spacing=10,
        )

    def _result_load_more(self, e):
        """Carga el siguiente lote de filas de la tabla bajo demanda."""
        from src.ui.resultados_view import _build_standard_table

        res = self.resultado.resultado
        df = res.dataframe
        current = len(self.resultados_table.rows)
        batch = 30
        max_rows = current + batch
        columns, new_rows, _ = _build_standard_table(
            df,
            self.df_historial,
            self.tipo_actual,
            max_rows=max_rows,
            sort_col=self._result_sort_col,
            sort_asc=self._result_sort_asc,
        )
        # Only append new rows (columns already set)
        for row in new_rows[current:]:
            self.resultados_table.rows.append(row)
        lazy = {
            "total_rows": len(df),
            "shown_rows": len(new_rows),
            "has_more": len(df) > len(new_rows),
        }
        if lazy["has_more"]:
            self._result_load_more_btn.text = f"Ver más ({len(new_rows)}/{len(df)})"
        else:
            self._result_load_more_btn.visible = False
        if self.app.page:
            self.app.page.update()

    def _ordenar_por(self, col_name):
        """Alterna el orden de la tabla de resultados por columna (header tap)."""
        if not col_name:
            return
        if self._result_sort_col == col_name:
            self._result_sort_asc = not self._result_sort_asc
        else:
            self._result_sort_col = col_name
            self._result_sort_asc = True
        self._refresh_sorted_table()

    def _refresh_sorted_table(self):
        """Reconstruye las filas de la tabla aplicando el orden actual (solo visibles)."""
        from src.ui.resultados_view import _build_standard_table

        res = self.resultado.resultado
        df = res.dataframe
        # Rebuild from scratch with sort
        columns, rows, info = _build_standard_table(
            df,
            self.df_historial,
            self.tipo_actual,
            max_rows=len(df),
            sort_col=self._result_sort_col,
            sort_asc=self._result_sort_asc,
        )
        self.resultados_table.columns.clear()
        for col in columns:
            col.on_sort = lambda e: self._ordenar_por(getattr(e.control, "data", None))
            self.resultados_table.columns.append(col)
        self.resultados_table.rows.clear()
        for row in rows:
            self.resultados_table.rows.append(row)
        if self.app.page:
            self.app.page.update()

    def _generar_expediente(self, e):
        if not self.resultado or not self.resultado.resultado:
            return
        self.app.show_loading("Generando Expediente...")

        def task():
            try:
                from src.ui.expediente_service import generar_expediente

                # Resolve cliente from appropriate dropdown based on caso
                cliente_map = {
                    "DC": self.cliente_dropdown_pd,
                    "DO": self.cliente_dropdown_df,
                    "VRS": self.cliente_dropdown_fp,
                    "FPE": self.cliente_dropdown_fp,
                    "PROM": self.cliente_dropdown_pb,
                    "ANF": self.cliente_dropdown_ci,
                    "CMV": self.cliente_dropdown_pb,
                    "DF": self.cliente_dropdown_fp,
                }
                dd = cliente_map.get(self.tipo_actual)
                cliente_value = dd.value if dd and dd.value else ""
                # Fallback: extraer clientes del historial ya cargado (los dropdowns legacy
                # quedan vacios cuando el fragmento se aplico desde la busqueda segmentada).
                if (
                    not cliente_value
                    and self.df_historial is not None
                    and not self.df_historial.empty
                ):
                    for col in ("CLIENTE", "nom_cliente"):
                        if col in self.df_historial.columns:
                            nombres = sorted(
                                {
                                    str(v).strip()
                                    for v in self.df_historial[col].dropna().unique()
                                    if str(v).strip()
                                }
                            )
                            if nombres:
                                cliente_value = ", ".join(nombres)
                            break
                vendedor_key = self.vendedor_dropdown.value or ""
                vendedor_display = vendedor_key
                if vendedor_key:
                    for opt in self.vendedor_dropdown.options:
                        if opt.key == vendedor_key:
                            vendedor_display = opt.text or vendedor_key
                            break
                if not vendedor_key:
                    vid = getattr(self, "_search_vend_id", None)
                    vnom = getattr(self, "_search_vend_nom", None) or ""
                    if not vnom and vid and hasattr(self, "busq_vend_dd"):
                        vnom = next(
                            (o.text for o in self.busq_vend_dd.options if o.key == vid), vid or ""
                        )
                    vendedor_key = vid or ""
                    vendedor_display = vnom or vid or ""

                exp_dirs = generar_expediente(
                    resultado=self.resultado,
                    tipo_actual=self.tipo_actual,
                    modalidad=self.modalidad_actual,
                    historico_config=self._leer_historico_config(),
                    df_historial=self.df_historial,
                    cliente_value=cliente_value,
                    vendedor_value=vendedor_key,
                    vendedor_display=vendedor_display,
                    antecedentes=self.antecedentes.value or "",
                    observaciones=self.observaciones.value or "",
                    desktop_path=self.app._get_desktop_path(),
                    omitir_sin_diferencia=self.chk_omitir_sin_dif.value,
                    generar_nota_debito=self.chk_gen_nd.value,
                )

                if len(exp_dirs) == 1:
                    self.app.show_snackbar(
                        f"✅ Expediente generado: {exp_dirs[0].name}",
                        self.app.G360_SUCCESS,
                    )
                else:
                    self.app.show_snackbar(
                        f"✅ {len(exp_dirs)} expedientes generados (uno por cliente) en "
                        f"{exp_dirs[0].parent.name}",
                        self.app.G360_SUCCESS,
                    )
                if os.name == "nt":
                    target = exp_dirs[0] if len(exp_dirs) == 1 else exp_dirs[0].parent
                    os.startfile(str(target))

            except Exception as ex:
                from src.ui.mensajes import mensaje

                self.app.show_snackbar(
                    mensaje(ex, "abrir el archivo"), self.app.G360_ERROR
                )
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
        # Reset general: limpia Y colapsa la card de reportes (la card
        # tiene su propio Limpiar que solo limpia y la deja abierta).
        if hasattr(self, "reporte_panel"):
            self.reporte_panel.limpiar(colapsar=True)
        if hasattr(self, "cliente_dropdown_pd"):
            self.cliente_dropdown_pd.value = None
        if hasattr(self, "cliente_dropdown_ci"):
            self.cliente_dropdown_ci.value = None
        if hasattr(self, "factura_dropdown_ci"):
            self.factura_dropdown_ci.value = None
        if hasattr(self, "factura_dropdown"):
            self.factura_dropdown.options = []
            self.factura_dropdown.value = None
        if hasattr(self, "vendedor_dropdown"):
            self.vendedor_dropdown.value = None
        if hasattr(self, "modalidad_radio"):
            self.modalidad_radio.value = MODALIDAD_INDIVIDUAL

        if hasattr(self, "mecanica_dropdown"):
            self.mecanica_dropdown.value = "12+1"
        if hasattr(self, "mecanica_personalizada"):
            self.mecanica_personalizada.value = ""
            self.mecanica_personalizada.visible = False
        if hasattr(self, "fecha_desde"):
            self.fecha_desde.value = ""
        if hasattr(self, "fecha_hasta"):
            self.fecha_hasta.value = ""
        if hasattr(self, "_periodo_row"):
            self._periodo_row.visible = False
        if hasattr(self, "observaciones"):
            self.observaciones.value = ""
        if hasattr(self, "lbl_historial"):
            self.lbl_historial.value = "Ninguno"
        if hasattr(self, "lbl_lista"):
            self.lbl_lista.value = "Ninguno"
        if hasattr(self, "lbl_requerimientos_count"):
            self.lbl_requerimientos_count.value = "Ninguno"
        if hasattr(self, "lbl_requerimientos_list"):
            self.lbl_requerimientos_list.controls.clear()
        if hasattr(self, "cliente_dropdown_df"):
            self.cliente_dropdown_df.value = None
        if hasattr(self, "factura_dropdown_df"):
            self.factura_dropdown_df.options = []
            self.factura_dropdown_df.value = None
        if hasattr(self, "cliente_dropdown_pb"):
            self.cliente_dropdown_pb.value = None
        if hasattr(self, "descuento_pct"):
            self.descuento_pct.value = ""
            self.descuento_pct.disabled = False
        if hasattr(self, "lbl_sku_filter"):
            self.lbl_sku_filter.value = "Ninguno"
        if hasattr(self, "sku_filter_clear_btn"):
            self.sku_filter_clear_btn.visible = False
        self.sku_filter_path = None
        if hasattr(self, "lbl_stock_cliente"):
            self.lbl_stock_cliente.value = "Ninguno"
        if hasattr(self, "stock_cliente_clear_btn"):
            self.stock_cliente_clear_btn.visible = False
        self.stock_cliente_path = None
        if hasattr(self, "tipo_dropdown"):
            if self._legacy_tipo() != "diferencia_precio":
                self.tipo_dropdown.value = "DC"
                self._on_tipo_change(None)
            else:
                # Mismo caso (DC): evita el re-render completo (card DB +
                # layout). Replica solo la parte de estado de _on_tipo_change.
                self.tipo_dropdown.value = "DC"
                self.tipo_actual = "DC"
                caso_data = self._caso_de_tipo()
                mods = caso_data.get("modalidades", (MODALIDAD_INDIVIDUAL, MODALIDAD_CONSOLIDADO))
                if self.modalidad_actual not in mods:
                    self.modalidad_actual = mods[0]
                    self.modalidad_radio.value = mods[0]
                self._historico_config = HistorialConfig.from_caso(CATALOGO[self.tipo_actual])
                self.resultado = None
                self.resultados_container.visible = False
                self.alertas_container.visible = False
                self._verificar_puede_ejecutar()
                if self.container:
                    self.container.update()
                if self.app.page:
                    self.app.page.update()
