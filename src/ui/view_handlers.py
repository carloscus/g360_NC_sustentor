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

# Alto fijo del area de tabs de resultados (px). ft.Tabs es un viewport y
# necesita alto finito; ver _renderizar_tabs_resultado.
_ALTURA_TABS_RESULTADO = 640


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


def _limpiar_busqueda(vista):
    """Limpia controles, chips y estado de la sección de búsqueda.

    Función de módulo (no método) para ser invocable desde _reset_ui
    aun cuando self sea un mock SimpleNamespace en los tests.
    """
    for attr, kw in [
        ("busq_cli_chips", {"controls": [], "visible": False}),
        ("busq_ped_chips", {"controls": [], "visible": False}),
        ("busq_oc_chips", {"controls": [], "visible": False}),
        ("busq_fac_chips", {"controls": [], "visible": False}),
        ("busq_vend_dd", {"options": [], "value": None, "visible": False, "disabled": True}),
        (
            "busq_vend_status",
            {
                "value": "Historial aplicado. Busca un cliente para seleccionar otro fragmento.",
                "visible": True,
            },
        ),
        ("btn_pin_vend", {"visible": False}),
        ("btn_pin_cli", {"visible": False}),
        ("busq_doc_row", {"visible": False}),
        (
            "busq_doc_prompt",
            {
                "value": "Selecciona un cliente para filtrar por pedido, O/C o factura.",
                "visible": True,
            },
        ),
        (
            "busq_status",
            {"value": "Historial aplicado a este caso", "color": ft.Colors.ON_SURFACE_VARIANT},
        ),
        ("busq_badges", {"visible": False, "controls": []}),
        ("busq_preview_tbl", {"visible": False}),
        ("busq_exec_btn", {"visible": False, "disabled": True}),
    ]:
        ctrl = getattr(vista, attr, None)
        if ctrl is not None:
            for k, v in kw.items():
                try:
                    setattr(ctrl, k, v)
                except Exception:
                    pass
    for attr in ("busq_doc_ped_btn", "busq_doc_oc_btn", "busq_doc_fac_btn"):
        ctrl = getattr(vista, attr, None)
        if ctrl is not None:
            try:
                ctrl.visible = False
            except Exception:
                pass
    preview_rows = getattr(vista, "busq_preview_rows", None)
    if preview_rows is not None:
        try:
            preview_rows.controls.clear()
        except Exception:
            pass
    vista._sel_facturas = []
    vista._sel_clientes = []
    vista._sel_pedidos = []
    vista._sel_ordenes = []
    if hasattr(vista, "_detalle_docs"):
        vista._detalle_docs.clear()


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
        if hasattr(self, "_result_panel"):
            self._result_panel.visible = False
        try:
            self._renderizar_ui()
        except Exception as ex:
            from src.ui.mensajes import mensaje

            self.app.show_snackbar(mensaje(ex, "pintar la pantalla"), color=self.app.G360_ERROR)
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

    # Handlers legacy eliminados: _on_*_sf, _on_*_ci, _on_*_df, _on_*_fp,
    # _on_*_pb, _cargar_clientes_dropdown_* (todos referencian selector_*,
    # cliente_dropdown_* y factura_dropdown_* que nunca se montaron).
    # _render_sku_table_sf, _check_existing_notes_sf, _marcar_todas_sf,
    # _desmarcar_todas_sf, _cargar_*_dropdown (referencian selector_* y
    # dropdowns nunca montados).

    def _on_descuento_pct_change(self, e):
        self._verificar_puede_ejecutar()

    def _on_mecanica_change(self, e):
        es_personalizado = e.control.value == "personalizado"
        self.mecanica_personalizada.visible = es_personalizado
        if self.app.page:
            self.app.page.update()

    def _abrir_fp_desde(self, e):
        if self.app.page and hasattr(self, "fp_desde"):
            self.app.page.open(self.fp_desde)

    def _abrir_fp_hasta(self, e):
        if self.app.page and hasattr(self, "fp_hasta"):
            self.app.page.open(self.fp_hasta)

    def _on_fp_desde_change(self, e):
        val = self.fp_desde.value if hasattr(self, "fp_desde") else None
        label = fecha_ui(val) if val else "Sin filtro"
        if hasattr(self, "fecha_desde_fp"):
            self.fecha_desde_fp.text = f"Desde: {label}"
        self._verificar_puede_ejecutar()
        if self.app.page:
            self.app.page.update()

    def _on_fp_hasta_change(self, e):
        val = self.fp_hasta.value if hasattr(self, "fp_hasta") else None
        label = fecha_ui(val) if val else "Sin filtro"
        if hasattr(self, "fecha_hasta_fp"):
            self.fecha_hasta_fp.text = f"Hasta: {label}"
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

    def _marcar_todas_sf(self, e):
        pass

    def _desmarcar_todas_sf(self, e):
        pass

    def _invalidar_resultado_por_config(self, mensaje):
        """No permitir exportar un resultado calculado con checks anteriores."""
        self.resultado = None
        self._firma_ejecucion = None
        self._set_btn_expediente(False)
        if hasattr(self, "resultados_container"):
            self.resultados_container.visible = False
        if hasattr(self, "_result_panel"):
            self._result_panel.visible = False
        if hasattr(self, "lbl_ejecutar_hint"):
            self.lbl_ejecutar_hint.value = mensaje
            self.lbl_ejecutar_hint.color = G360Theme.warning_color()
        if self.app and self.app.page:
            self.app.page.update()

    def _on_cliente_change_pd(self, e):
        pass

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
        pass

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

    def _on_vendedor_change(self, e):
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
        pass

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
        """Agrega un archivo XLSX y lo asigna al insumo correspondiente del caso."""
        from pathlib import Path

        name = Path(ruta).name
        tipo = self._clasificar_archivo_insumo(ruta, name)
        labels = self._etiquetas_insumo(tipo)
        self._asignar_insumo(tipo, ruta)
        self._actualizar_ui_insumo(tipo, name, labels, ruta)
        self._renderizar_lista_insumos()
        # Un insumo entra al calculo: el resultado previo quedo obsoleto (se
        # ejecutaria con la lista anterior). Sin esto la vista previa sigue
        # mostrando los numeros viejos y el expediente sale con esa mezcla.
        if getattr(self, "resultado", None):
            self._invalidar_resultado_por_config(
                "Cambiaste los insumos; vuelve a ejecutar el cálculo."
            )
        self._verificar_puede_ejecutar()

    def _clasificar_archivo_insumo(self, ruta: str, name: str) -> str:
        """Determina qué tipo de insumo representa el archivo basado en su nombre."""
        from src.ui.catalog import CATALOGO

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

        # FPE: todo archivo externo es un requerimiento del evento.
        if self._tipo_incluye("feria_preventa"):
            return "mecanica"
        # VRS: distribuye por orden de carga (lista → cantidad).
        if self._tipo_incluye("diferencia_stock"):
            return "lista_precios" if not self.lista_path else "cantidad"
        # Match por nombre del archivo.
        if "lista" in lower or "precio" in lower or "condicion" in lower:
            return "lista_precios"
        if "sku" in lower or "filtro" in lower:
            return "sku"
        if "stock" in lower or "cantidad" in lower:
            return "cantidad"
        if "descuento" in lower or "%" in lower or "rebate" in lower:
            return "porcentaje"
        # Fallback: primer slot libre.
        for cand in ("lista_precios", "sku", "cantidad", "porcentaje", "mecanica"):
            if cand in needs and not (asignado[cand]() if cand in asignado else None):
                return cand
        return "extra"

    def _etiquetas_insumo(self, tipo: str) -> dict:
        """Retorna el mapeo tipo → label visible para los insumos del caso actual."""
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
        return labels

    def _asignar_insumo(self, tipo: str, ruta: str):
        """Asigna la ruta al atributo correspondiente según el tipo de insumo."""
        asignaciones = {
            "lista_precios": lambda: setattr(self, "lista_path", ruta),
            "sku": lambda: setattr(self, "sku_filter_path", ruta),
            "cantidad": lambda: setattr(self, "stock_cliente_path", ruta),
            "porcentaje": lambda: setattr(self, "desc_file_path", ruta),
            "mecanica": lambda: self._agregar_requerimiento(ruta),
        }
        fn = asignaciones.get(tipo)
        if fn:
            fn()

    def _agregar_requerimiento(self, ruta: str):
        """Añade un path a requerimientos_paths si aún no está."""
        from pathlib import Path

        if Path(ruta) not in self.requerimientos_paths:
            self.requerimientos_paths.append(Path(ruta))
        self._actualizar_lista_requerimientos()

    def _actualizar_ui_insumo(self, tipo: str, name: str, labels: dict, ruta: str):
        """Actualiza los labels y botones de UI tras asignar un insumo."""
        lbl_map = {
            "lista_precios": ("lbl_lista", "lista_path"),
            "sku": ("lbl_sku_filter", "sku_filter_path"),
            "cantidad": ("lbl_stock_cliente", "stock_cliente_path"),
            "porcentaje": ("lbl_desc_file", "desc_file_path"),
        }
        if tipo in lbl_map:
            lbl_attr, _ = lbl_map[tipo]
            lbl = getattr(self, lbl_attr, None)
            if lbl:
                lbl.value = f"✓ {name}"
                lbl.color = self.app.G360_SUCCESS
        # Botones clear
        clear_map = {
            "sku": "sku_filter_clear_btn",
            "cantidad": "stock_cliente_clear_btn",
        }
        if tipo in clear_map:
            btn = getattr(self, clear_map[tipo], None)
            if btn:
                btn.visible = True
        # DO: deshabilitar % global al cargar archivo
        if self._tipo_incluye("descuento_precio"):
            self.descuento_pct.disabled = True
        # Registrar para preview
        self._insumo_files.append(
            {
                "ruta": ruta,
                "tipo": labels[tipo],
                "lbl": name,
                "preview": None,
                "preview_loading": True,
            }
        )

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
        # Simetrico de _agregar_insumo: quitar un insumo tambien deja obsoleto
        # el resultado (puede cambiar el calculo entero).
        if getattr(self, "resultado", None):
            self._invalidar_resultado_por_config(
                "Cambiaste los insumos; vuelve a ejecutar el cálculo."
            )
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
        self._actualizar_historial_label(df)
        self._recargar_dropdowns_fragmento()
        self._renderizar_ui()
        self._auto_seleccionar_unicos()
        self._refrescar_card_db()
        self._verificar_puede_ejecutar()
        # Limpiar estado de búsqueda para que la reconstrucción no repita badges/preview
        self._busq_df = None
        _limpiar_busqueda(self)
        if self.app.page:
            self.app.page.update()
        self.app.show_snackbar(
            f"Historial aplicado: {len(df):,} filas — completa insumos y ejecuta",
            self.app.G360_SUCCESS,
        )

    def _actualizar_historial_label(self, df):
        """Actualiza el label del historial con la cantidad de filas aplicadas."""
        self.lbl_historial.value = f"✓ SQLite ({len(df):,} filas)"
        self.lbl_historial.color = self.app.G360_SUCCESS

    def _recargar_dropdowns_fragmento(self):
        """Recarga los dropdowns legacy y líneas tras aplicar un fragmento."""
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

    def _auto_seleccionar_unicos(self):
        """Sin legacy: sin dropdowns manuales, nada que preseleccionar."""

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
        """Habilita el botón de ejecutar si todos los insumos requeridos están presentes."""
        if not self.app.page:
            return
        cfg = self._caso_de_tipo()
        puede = self._historial_disponible()
        puede = puede and self._insumos_especificos_disponibles(cfg)
        self.btn_ejecutar.disabled = not puede
        self._actualizar_hint_ejecucion(puede, cfg)
        try:
            self.btn_ejecutar.update()
            self.lbl_ejecutar_hint.update()
        except AssertionError:
            pass
        if self.app and self.app.page:
            self.app.page.update()

    def _historial_disponible(self):
        """El historial debe estar cargado para cualquier cálculo."""
        return self.df_historial is not None

    def _insumos_especificos_disponibles(self, cfg):
        """Validaciones específicas por tipo de caso."""
        necesita_lista = self._insumo_necesita("lista_precios", cfg)
        puede = self.df_historial is not None
        if necesita_lista:
            puede = puede and self.lista_path is not None
        if cfg.get("strategy") == "feria_preventa":
            puede = puede and len(self.requerimientos_paths) > 0
        if self.tipo_actual == "ANF":
            puede = puede and self._hist_fragmento is not None
            puede = puede and self._hist_fragmento.get("facturas", 0) >= 1
        if self.tipo_actual == "DO":
            tiene_pct = self._descuento_pct_valido()
            puede = puede and (
                self.desc_file_path is not None or self.sku_filter_path is not None or tiene_pct
            )
        if self.tipo_actual == "CMV":
            vals_ok = self._cmv_vals_positivos()
            puede = puede and vals_ok
        return puede

    def _descuento_pct_valido(self):
        """El campo de porcentaje de descuento tiene un valor numérico > 0."""
        try:
            return float(self.descuento_pct.value or "0") > 0
        except ValueError:
            return False

    def _cmv_vals_positivos(self):
        """Meta y porcentaje de CMV deben ser numéricos > 0."""
        try:
            meta = float(self.meta_monto.value or "0")
            pct = float(self.rebate_pct.value or "0")
            return meta > 0 and pct > 0
        except ValueError:
            return False

    def _actualizar_hint_ejecucion(self, puede, cfg):
        """Actualiza el label de hint según el estado de validación."""
        if puede:
            self.lbl_ejecutar_hint.value = "Datos e insumos listos. Puedes ejecutar el cálculo."
            self.lbl_ejecutar_hint.color = G360Theme.ok_color()
            return
        self.lbl_ejecutar_hint.color = G360Theme.warning_color()
        if self.df_historial is None:
            self.lbl_ejecutar_hint.value = (
                "En Datos del caso, busca y aplica un fragmento del historial."
            )
        elif self._insumo_necesita("lista_precios", cfg) and not self.lista_path:
            if self._tipo_incluye("diferencia_stock"):
                self.lbl_ejecutar_hint.value = (
                    "Carga el archivo por SKU: CANTIDAD, PRECIO_BASE y descuentos."
                )
            else:
                self.lbl_ejecutar_hint.value = (
                    "Falta cargar la lista de precios requerida por este caso."
                )
        elif self.tipo_actual == "ANF" and (
            self._hist_fragmento is None or self._hist_fragmento.get("facturas", 0) < 1
        ):
            self.lbl_ejecutar_hint.value = (
                "Anulación requiere al menos una factura en el historial."
            )
        elif self.tipo_actual == "DO":
            self.lbl_ejecutar_hint.value = (
                "Indica un descuento o agrega el archivo de porcentaje/SKU."
            )
        elif self.tipo_actual == "CMV":
            self.lbl_ejecutar_hint.value = (
                "Completa una meta y un porcentaje de rebate mayores que cero."
            )
        elif cfg.get("strategy") == "feria_preventa" and not self.requerimientos_paths:
            self.lbl_ejecutar_hint.value = (
                "Agrega el archivo de requerimientos de la feria/preventa."
            )
        else:
            self.lbl_ejecutar_hint.value = "Completa los insumos requeridos por este caso."

    @safe_handler
    def _ejecutar(self, e):
        self.app.show_loading("Ejecutando reconocimiento...")
        threading.Thread(target=self._ejecutar_tarea, daemon=True).start()

    def _ejecutar_tarea(self):
        """Tarea principal ejecutada en hilo: configuración → pipeline → enrichment → display."""
        try:
            exp = self._construir_expediente()
            if exp is None:
                return
            self._ejecutar_pipeline(exp)
            self._enriquecer_resultado(exp)
            self._mostrar_resultado_post_ejecucion(exp)
        except Exception as ex:
            from src.ui.mensajes import mensaje

            self.app.show_snackbar(
                mensaje(ex, "cargar el detalle del cliente"), self.app.G360_ERROR
            )
            self.app.hide_loading()
            if self.app.page:
                self.app.page.update()

    def _construir_expediente(self):
        """Construye el ExpedienteComercial desde la UI: config → validación → datos → condiciones."""
        from src.ui.config_builder import build_config, build_datos_exp

        legacy_tipo = self._legacy_tipo()
        estrategia, variante = ESTRATEGIA_POR_TIPO.get(legacy_tipo, ("", ""))
        ui = self._collect_ui_values()
        config = build_config(legacy_tipo, ui)

        if self._requiere_facturas_activas(legacy_tipo) and not config.get(
            "documentos_historial", {}
        ).get("facturas", {}).get("usar", True):
            self.app.show_snackbar(
                "Este cálculo requiere Facturas: Usar; activa el check para continuar.",
                self.app.G360_WARNING,
            )
            return None

        datos_exp = build_datos_exp(legacy_tipo, self.df_historial, config, ui)
        tipo_info = CATALOGO.get(self.tipo_actual) or SimpleNamespace(label=self.tipo_actual)
        exp = ExpedienteComercial(
            nombre=tipo_info.label,
            familia=tipo_info.label,
            estrategia=estrategia,
            variante=variante,
            datos=datos_exp,
            contexto=PipelineContext(
                config=config,
                antecedentes=self.antecedentes.value or "",
                observaciones=self.observaciones.value or "",
            ),
        )
        exp.condiciones = self._construir_condiciones(legacy_tipo)
        return exp

    def _requiere_facturas_activas(self, legacy_tipo):
        """Tipos de cálculo que exigen Facturas: Usar = true."""
        return legacy_tipo in (
            "diferencia_precio",
            "diferencia_stock",
            "diferencia_cantidad",
            "descuento_precio",
            "feria_preventa",
        )

    def _construir_condiciones(self, legacy_tipo):
        """Arma la lista de DataFrames de condiciones según el tipo y los insumos cargados."""
        condiciones = []
        cfg = TIPO_CONFIG.get(legacy_tipo, {})
        if cfg.get("necesita_lista", False) and self.lista_path:
            # `leer_lista_precios` prueba primero la plantilla oficial y SOLO
            # si el archivo no la cumple cae a la contingencia posicional
            # (proveedores sin encabezados, con categorias entre filas). El
            # camino normal no cambia; `origen` se guarda para avisarlo.
            from src.core.lista_contingencia import leer_lista_precios

            df_lista, origen = leer_lista_precios(self.lista_path)
            self._origen_lista = origen
            condiciones.append(df_lista)
        if legacy_tipo in ("diferencia_cantidad", "diferencia_stock") and self.stock_cliente_path:
            # Override opcional de cantidades (SKU + CANTIDAD).
            # Condiciones[0] del motor CantidadDeterminada; este archivo manda si existe.
            condiciones.append(read_erp_file(self.stock_cliente_path))
        if self.tipo_actual == "DO" and self.desc_file_path:
            condiciones.append(read_erp_file(self.desc_file_path))
        return condiciones

    def _ejecutar_pipeline(self, exp):
        """Ejecuta el pipeline y almacena el resultado."""
        pipeline = Pipeline()
        self.resultado = pipeline.ejecutar(exp)
        self._asignar_exp_id(self.resultado)

    def _asignar_exp_id(self, exp):
        """Genera y asigna exp_id a partir del primer cliente, serie y número detectados."""
        if not exp.resultado or exp.resultado.dataframe is None:
            return
        df_res = exp.resultado.dataframe
        cliente_id = ""
        for col in ("COD_CLIENTE", "id_cliente"):
            if col in df_res.columns:
                vals = df_res[col].dropna().unique()
                if len(vals) > 0:
                    cliente_id = str(vals[0]).strip()
                    break
        if not cliente_id:
            return
        ui_exp = self._collect_ui_values()
        doc_ref = ui_exp.get("factura_ci") or ui_exp.get("factura_df") or ""
        serie, nro = "", ""
        if doc_ref and "-" in doc_ref:
            parte, nro = doc_ref.rsplit("-", 1)
            serie = parte[1:] if len(parte) > 1 else parte
        if cliente_id and serie and nro:
            exp.exp_id = build_expediente_id(self.tipo_actual, cliente_id, serie, nro)

    def _enriquecer_resultado(self, exp):
        """Añade metadatos de enriquecimiento: exp_id, NC/ND, AL12 y auditoría."""
        if not exp.resultado or exp.resultado.dataframe.empty:
            return
        df_res = exp.resultado.dataframe
        from src.ui.config_builder import build_config

        legacy_tipo = self._legacy_tipo()
        ui = self._collect_ui_values()
        config = build_config(legacy_tipo, ui)

        self._enriquecer_nc_nd(exp, df_res, config)
        self._enriquecer_al12(exp, df_res, config)
        self._enriquecer_auditoria_nc(exp, df_res, config, ui)
        self._señalizar_facturas_con_notas(exp)

    def _enriquecer_nc_nd(self, exp, df_res, config):
        """Añade resumen NC/ND y columnas auxiliares al dataframe de resultado."""
        from src.core.document_classifier import resumen_global

        historial_visible = _historial_para_auditoria(
            self.df_historial, config.get("documentos_historial", {})
        )
        solo_ncnd = (
            historial_visible[historial_visible["TIPO_CLASE"].astype(str).str.lower() != "factura"]
            if historial_visible is not None and "TIPO_CLASE" in historial_visible.columns
            else historial_visible
        )
        if not solo_ncnd.empty and "CODIGO" in solo_ncnd.columns and "SKU" in df_res.columns:
            skus_procesados = set(df_res["SKU"].astype(str).str.strip())
            solo_ncnd = solo_ncnd[solo_ncnd["CODIGO"].astype(str).str.strip().isin(skus_procesados)]
        full_resumen = resumen_global(solo_ncnd)
        if full_resumen:
            exp.resultado.metricas["nc_detalle"] = full_resumen

        if historial_visible is None or "NC_ASOCIADAS" not in historial_visible.columns:
            return
        # bool(...) iba sobre la Serie, no sobre el .any(): pandas levanta
        # "truth value of a Series is ambiguous" y el error terminaba como
        # snackbar genérico al ejecutar.
        hay_notas = bool(
            (historial_visible["TIPO_CLASE"].astype(str).str.lower() != "factura").any()
        )
        if not hay_notas:
            return

        doc_to_nc = dict(
            zip(
                historial_visible["DOC_ID"],
                historial_visible["NC_ASOCIADAS"].apply(lambda x: ", ".join(x) if x else ""),
            )
        )
        factura_col = (
            "FACTURAS"
            if "FACTURAS" in df_res.columns
            else "FACTURA"
            if "FACTURA" in df_res.columns
            else None
        )
        if factura_col:
            df_res["NC_EXISTENTE"] = df_res[factura_col].apply(
                lambda v: mapear_nc_existente(v, doc_to_nc)
            )

    def _enriquecer_al12(self, exp, df_res, config):
        """Cruza NC/NDB por (factura, SKU) y añade alertas AL12 sin modificar el expediente."""
        if config.get("_reconciliar_nc_factura_sku"):
            return
        from src.core.detector import detectar_notas_en_historial, resumen_notas_por_factura
        from src.core.nc_auditor import CreditNoteAuditor as _CNA
        from src.domain import BusinessAlert as _BA12
        from src.domain import generar_texto_alerta as _gta12

        historial_visible = _historial_para_auditoria(
            self.df_historial, config.get("documentos_historial", {})
        )
        historial_para_notas = (
            historial_visible if historial_visible is not None else self.df_historial.iloc[0:0]
        )
        _resumen_nc = resumen_notas_por_factura(detectar_notas_en_historial(historial_para_notas))
        _resumen_norm = {
            fac: {_CNA._norm_sku(s): info for s, info in data.get("skus", {}).items()}
            for fac, data in _resumen_nc.items()
        }
        _num_a_docs = self._mapear_num_a_docs()
        if not _resumen_norm:
            return

        _al12_vistas = set()

        def _al12_row(_df, _fac_col, _ale_col, _anexar_alerta=True):
            if _fac_col is None or _ale_col not in _df.columns or "SKU" not in _df.columns:
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
                        _segs.append(_gta12("AL12", factura=_fac, docs=_info["docs"], sku=_sku_raw))
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

        from src.ui.config_builder import build_config

        legacy_tipo = self._legacy_tipo()
        ui = self._collect_ui_values()
        cfg = build_config(legacy_tipo, ui)
        if cfg.get("_reconciliar_nc_factura_sku"):
            return

        factura_col = (
            "FACTURAS"
            if "FACTURAS" in df_res.columns
            else "FACTURA"
            if "FACTURA" in df_res.columns
            else None
        )
        _al12_row(df_res, factura_col, "ALERTA")
        _df_x = exp.resultado.get_excel() if hasattr(exp.resultado, "get_excel") else None
        if _df_x is not None and _df_x is not df_res and not _df_x.empty:
            _fx_col = next(
                (c for c in ("FACTURAS", "FACTURA", "Facturas (qty)") if c in _df_x.columns), None
            )
            _al_col = next((c for c in ("ALERTA", "Alerta") if c in _df_x.columns), None)
            if _fx_col and _al_col:
                _al12_row(_df_x, _fx_col, _al_col, _anexar_alerta=False)

    def _mapear_num_a_docs(self):
        """Convierte NUMERO → lista de formas documentales (para expansión de facturas AL12)."""
        _num_a_docs = {}
        if self.df_historial is None or self.df_historial.empty:
            return _num_a_docs
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
        return _num_a_docs

    def _enriquecer_auditoria_nc(self, exp, df_res, config, ui):
        """Ejecuta CreditNoteAuditor y fusiona resultados en el dataframe."""
        from src.core.nc_auditor import CreditNoteAuditor

        historial_visible = _historial_para_auditoria(
            self.df_historial, config.get("documentos_historial", {})
        )
        nc_alertas = CreditNoteAuditor().auditar(
            historial_visible,
            df_res,
            documentos_historial=config.get("documentos_historial"),
            modalidad=ui.get("modalidad", "individual"),
        )
        exp.resultado.metricas["nc_alertas"] = nc_alertas
        CreditNoteAuditor.combinar_auditoria(df_res, nc_alertas)
        _df_x = exp.resultado.get_excel() if hasattr(exp.resultado, "get_excel") else None
        if _df_x is not None and _df_x is not df_res:
            CreditNoteAuditor.combinar_auditoria(_df_x, nc_alertas)

    def _señalizar_facturas_con_notas(self, exp):
        """Añade una alerta de primer nivel si hay facturas con NC/ND en el historial."""
        from src.domain import BusinessAlert

        if self.df_historial is None:
            return
        ui_sn = self._collect_ui_values()
        historial_visible = _historial_para_auditoria(
            self.df_historial, ui_sn.get("historial_config", {})
        )
        if historial_visible is None or "TIPO_CLASE" not in historial_visible.columns:
            return
        legacy_tipo = self._legacy_tipo()
        from src.ui.config_builder import build_config

        cfg = build_config(legacy_tipo, ui_sn)
        if cfg.get("_reconciliar_nc_factura_sku"):
            return
        notas_df = historial_visible[historial_visible["TIPO_CLASE"] != "factura"]
        if notas_df.empty:
            return
        facturas_con_nc = notas_df["FACTURA_REF"].dropna().unique()
        if len(facturas_con_nc) == 0:
            return
        preview = ", ".join(sorted(facturas_con_nc)[:5])
        suffix = "..." if len(facturas_con_nc) > 5 else ""
        exp.resultado.alertas.insert(
            0,
            BusinessAlert(
                tipo="warning",
                severidad="baja",
                mensaje=(
                    f"Facturas con NC/ND existentes en el rango: {preview}{suffix}. "
                    f"Evaluar ajustes manuales."
                ),
                motor=exp.estrategia if exp.estrategia else "N/A",
            ),
        )

    def _mostrar_resultado_post_ejecucion(self, exp):
        """Muestra tabs multi-cliente o advertencia según el resultado."""
        if exp.resultado and not exp.resultado.dataframe.empty:
            self._agrupar_resultado_por_cliente(exp)
            self._mostrar_resultado()
            self._log_estado_resultado()
            self._desplazar_a_resultados()
        else:
            alertas_ordenadas = sorted(
                exp.alertas or [],
                key=lambda a: 0 if a.tipo == "error" else 1 if a.tipo == "warning" else 2,
            )
            alert_msgs = [a.mensaje for a in alertas_ordenadas][:3]
            detail = " | ".join(alert_msgs) if alert_msgs else "Sin alertas"
            self.app.show_snackbar(f"Sin resultados: {detail}", self.app.G360_WARNING)
        self.app.hide_loading()
        if self.app.page:
            self.app.page.update()

    def _log_estado_resultado(self):
        """Deja en el log COMO quedo la vista de resultados.

        El panel va al final de una columna muy larga: cuando el usuario no
        lo ve no hay forma de saber si fallo el render o si esta por debajo
        del pliegue. Este log dice que se pinto y con que datos, por tab.
        """
        import logging

        log = logging.getLogger(__name__)
        cont = getattr(self, "resultados_container", None)
        if cont is None:
            log.info("resultados: sin contenedor")
            return
        panel = getattr(self, "_result_panel", None)
        df = None
        res = getattr(self, "resultado", None)
        if res is not None and getattr(res, "resultado", None) is not None:
            df = res.resultado.dataframe
        n_filas = 0 if df is None else len(df)
        btn = getattr(self, "btn_expediente", None)
        tabs = getattr(self, "_result_tabs", None)
        n_tabs = len(tabs.tabs) if tabs is not None and getattr(tabs, "tabs", None) else 0
        donde = "%d tabs" % n_tabs if n_tabs else "panel unico"
        panel_content = getattr(panel, "content", None)
        resumen = getattr(self, "_result_summary", None)
        hijos = getattr(resumen, "controls", None)
        log.info(
            "resultados: visible=%s inner=%s tipo=%s modalidad=%s ocultar0=%s nd=%s "
            "filas=%s %s sel=%s tabh=%s boton=%s card=%s panel=%s summary_hijos=%s",
            getattr(cont, "visible", None),
            getattr(panel, "visible", None),
            getattr(self, "tipo_actual", "?"),
            getattr(self, "modalidad_actual", "?"),
            self._ocultar_cero_activo(),
            self._marcar_nd(),
            n_filas,
            donde,
            (getattr(tabs, "selected_index", "?") if n_tabs else "-"),
            (_ALTURA_TABS_RESULTADO if n_tabs else "-"),
            ("habilitado" if btn is not None and not btn.disabled else "DESHABILITADO"),
            type(getattr(cont, "content", None)).__name__,
            type(panel_content).__name__,
            (len(hijos) if isinstance(hijos, list) else "?"),
        )
        if n_tabs:
            sets = getattr(self, "_result_controles_por_cliente", {}) or {}
            por_cli = getattr(self, "_resultado_por_cliente", {}) or {}
            for i, tab in enumerate(tabs.tabs):
                cid = (getattr(self, "_resultado_clientes_order", []) or [None] * n_tabs)[i]
                contenido = getattr(tab, "content", None)
                contenido_int = getattr(contenido, "content", None)
                tbl = (sets.get(cid) or {}).get("tabla")
                exp_cli = por_cli.get(cid)
                df_cli = (
                    exp_cli.resultado.dataframe
                    if exp_cli is not None and getattr(exp_cli, "resultado", None) is not None
                    else None
                )
                bcli = self.btn_gen_exp
                log.info(
                    "resultados: tab[%d] cli=%s df_filas=%s tab_content=%s inner=%s "
                    "tabla_filas=%s btn=%s",
                    i,
                    cid,
                    (len(df_cli) if df_cli is not None else "?"),
                    type(contenido).__name__,
                    type(contenido_int).__name__,
                    (len(tbl.rows) if tbl is not None else "?"),
                    ("habilitado" if bcli is not None and not bcli.disabled else "DESHABILITADO"),
                )

    def _desplazar_a_resultados(self):
        """Lleva el scroll de la vista hasta la card de resultados.

        Tras ejecutar, el usuario se queda donde esta el boton (abajo) y la
        vista previa queda fuera de pantalla. No hace nada sin pagina
        (tests) ni si el scroll aun no existe.
        """
        self._desplazar_a("g360_resultados")

    def _desplazar_a(self, key: str) -> bool:
        """Scroll hasta el control con `key` dentro de la vista. False sin pagina."""
        try:
            col = getattr(self, "_workflow_scroll", None)
            page = getattr(self.app, "page", None)
            if col is None or page is None:
                return False
            col.scroll_to(key=key, duration=300)
            page.update()
            return True
        except Exception:
            logging.getLogger(__name__).exception("scroll a %s fallo", key)
            return False

    def ir_a_reporte(self) -> bool:
        """Entrada del boton del header: cambia a la vista de Reporte."""
        return self.mostrar_reporte()

    def mostrar_reporte(self) -> bool:
        """Cambia a la vista Reporte (la abre si esta colapsada).

        Las zonas no comparten scroll: el sustento queda montado en su
        raiz y el reporte en la suya, asi que ningun control cambia de
        padre al alternar. Devuelve False si la vista aun no se construyo.
        """
        try:
            panel = getattr(self, "reporte_panel", None)
            cont = getattr(self, "container", None)
            if panel is None or cont is None:
                return False
            abrir = getattr(panel, "abrir", None)
            if abrir is not None:
                abrir()
            self._vista_actual = "reporte"
            cont.content = self._raiz_reporte()
            if self.app.page:
                self.app.page.update()
            return True
        except Exception:
            logging.getLogger(__name__).exception("mostrar reporte fallo")
            return False

    def mostrar_sustento(self) -> bool:
        """Vuelve a la vista Sustento (flujo de creacion).

        Monta la raiz COMPLETA (scroll + barra sticky), no el scroll suelto:
        con solo `_workflow_scroll` los botones de la barra (Ejecutar /
        Generar expediente) quedaban fuera del arbol y solo volvian al
        cambiar el tipo de caso.
        """
        try:
            cont = getattr(self, "container", None)
            raiz = getattr(self, "_raiz_sustento", None)
            if cont is None or raiz is None:
                return False
            self._vista_actual = "sustento"
            cont.content = raiz
            if self.app.page:
                self.app.page.update()
            return True
        except Exception:
            logging.getLogger(__name__).exception("mostrar sustento fallo")
            return False

    def _ocultar_cero_activo(self):
        chk = getattr(self, "chk_ocultar_cero", None)
        return bool(chk is not None and chk.value)

    def _nd_activo(self):
        chk = getattr(self, "chk_gen_nd", None)
        return bool(chk is not None and chk.value)

    def _solo_nd_activo(self):
        chk = getattr(self, "chk_solo_ndb", None)
        return bool(chk is not None and chk.value)

    def _marcar_nd(self):
        """Vista con marcado ND: check ND o Solo-NDB (implica ND)."""
        return self._nd_activo() or self._solo_nd_activo()

    def _agrupar_resultado_por_cliente(self, exp):
        """Agrupar resultado por cliente para vista multi-cliente.

        Guarda en:
          - self._resultado_por_cliente: {cid: resultado_clonado}
          - self._resultado_clientes_order: lista de ids en orden alfabético
          - self._resultado_global: el resultado original completo
        """
        import copy

        df = exp.resultado.dataframe
        if df is None or df.empty:
            self._resultado_por_cliente = {}
            self._resultado_clientes_order = []
            self._resultado_global = exp
            return

        # Detectar columna de cliente
        cid_col = None
        for col in ("COD_CLIENTE", "id_cliente"):
            if col in df.columns:
                cid_col = col
                break
        if cid_col is None:
            self._resultado_por_cliente = {}
            self._resultado_clientes_order = []
            self._resultado_global = exp
            return

        # Agrupar por cliente, orden alfabético por nombre
        nombre_col = None
        for col in ("CLIENTE", "nom_cliente"):
            if col in df.columns:
                nombre_col = col
                break

        grupos = {}
        for cid, grp in df.groupby(cid_col):
            cid_str = str(cid).strip()
            grupos[cid_str] = grp

        # Ordenar por nombre si disponible, sino por id
        def _key(cid):
            if nombre_col and cid in grupos:
                nombres = grupos[cid][nombre_col].dropna().unique()
                if len(nombres) > 0:
                    return str(nombres[0]).strip().upper()
            return cid.upper()

        self._resultado_clientes_order = sorted(grupos.keys(), key=_key)
        self._resultado_por_cliente = {}
        self._resultado_global = exp

        for cid in self._resultado_clientes_order:
            grp_df = grupos[cid]
            # Clonar resultado con DF filtrado
            try:
                clon = copy.copy(exp)
                if clon.resultado:
                    clon.resultado = copy.copy(exp.resultado)
                if clon.resultado and clon.resultado.dataframe is not None:
                    clon.resultado.dataframe = grp_df.copy()
                self._resultado_por_cliente[cid] = clon
            except Exception:
                pass

    def _resultado_efectivo(self):
        """Resultado para pantalla aplicando las OPCIONES DE CÁLCULO.

        Con "Solo NDB" se clona solo con negativas (lo que se ve es lo que
        se genera en modo solo-NDB). Si no, con "Ocultar diferencias 0.00"
        se clona ya filtrado (mismo criterio que usa la generación), para
        que la tabla, totales y la verificación pre-exportación coincidan
        con el expediente. Devuelve None si no queda nada visible.
        """
        try:
            from src.ui.expediente_service import _clonar_sin_cero, _clonar_solo_nd
        except ImportError:
            return self.resultado

        def _valido(vista):
            if vista is None or not vista.resultado:
                return None
            df = vista.resultado.dataframe
            if df is None or getattr(df, "empty", True):
                return None
            return vista

        if self._solo_nd_activo():
            try:
                return _valido(_clonar_solo_nd(self.resultado))
            except Exception:
                return self.resultado
        if not self._ocultar_cero_activo():
            return self.resultado
        try:
            # La preview MUESTRA las negativas aunque NDB este apagado: es
            # donde el usuario las ve y decide si activar el check. Lo que
            # sale solo-positivo es el EXPEDIENTE (ver generar_expediente).
            return _valido(_clonar_sin_cero(self.resultado))
        except Exception:
            return self.resultado

    def _on_opciones_calculo_change(self, e=None):
        """Actualiza la vista de resultados al cambiar los checks de cálculo."""
        if not (self.resultado and self.resultado.resultado is not None):
            return
        df = self.resultado.resultado.dataframe
        if df is None or getattr(df, "empty", True):
            return
        self._mostrar_resultado()

    def _mostrar_resultado(self, cliente_id=None):
        """Renderiza el resultado. Si hay múltiples clientes, usa tabs.

        cliente_id: si se pasa, renderiza solo ese cliente (para tabs).
        Si es None y hay múltiples clientes, muestra el primer tab.
        """
        from src.ui.resultados_view import render_resultado

        if not self.resultado or not self.resultado.resultado:
            return

        multi_cliente = (
            getattr(self, "_resultado_por_cliente", None) and len(self._resultado_por_cliente) > 1
        )

        if multi_cliente and cliente_id is None:
            self._renderizar_tabs_resultado()
            return

        if multi_cliente and cliente_id:
            self._actualizar_tab_resultado(cliente_id)
            return

        # Single client path: el set global de controles (clave None).
        activar = getattr(self, "_activar_controles_resultado", None)
        if activar is not None:
            activar(None)

        vista = self._resultado_efectivo()
        if vista is None:
            self.app.show_snackbar(
                "Tras ocultar 0.00 no quedan filas visibles.",
                self.app.G360_WARNING,
            )
            return

        result = render_resultado(
            vista,
            self.tipo_actual,
            self.df_historial,
            self.app.G360_ACCENT,
            self.app.G360_SUCCESS,
            marcar_nd=self._marcar_nd(),
        )

        self._renderizar_panel_compartido(result)
        self._result_panel.content = self._result_summary
        # Firma de las entradas con las que se calculo: el paso final la
        # compara antes de generar (guard contra resultado desfasado).
        self._marcar_firma_ejecucion()
        # El panel interno nace visible=False: sin esto la card aparece con
        # solo el encabezado y la vista previa nunca se pinta.
        self._result_panel.visible = True
        self.resultados_container.visible = True
        self._set_btn_expediente(True)

    def _renderizar_panel_compartido(self, result):
        """Renderiza las piezas comunes entre single-client y multi-client tabs."""
        # Labels globales
        self.lbl_total_nc.value = result["total_nc"]
        self.lbl_skus.value = result["skus_label"]
        self.lbl_alertas_count.value = result["alertas_label"]

        if result["content"] is None:
            self.resultados_container.visible = False
            self._result_panel.visible = False
            return

        # Toggles (rebate)
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

        # Tabla
        self.resultados_table.columns.clear()
        self.resultados_table.rows.clear()
        for col in result["table_columns"]:
            self.resultados_table.columns.append(col)
        for row in result["table_rows"]:
            self.resultados_table.rows.append(row)
        for col in self.resultados_table.columns:
            col.on_sort = lambda e: self._ordenar_por(getattr(e.control, "data", None))

        # Lazy load
        lazy = result.get("lazy_info", {})
        if lazy.get("has_more", False):
            self._result_load_more_btn.text = f"Ver más ({lazy['shown_rows']}/{lazy['total_rows']})"
            self._result_load_more_btn.visible = True
        else:
            self._result_load_more_btn.visible = False

        # Audit button
        self._result_audit_btn.visible = len(result["table_rows"]) > 0

        # Warning banner
        self._renderizar_banner_warning(result)

        # Panel completo
        self._rearmar_result_summary(result)

    def _renderizar_tabs_resultado(self):
        """Renderiza tabs por cliente cuando hay múltiples."""
        clientes = getattr(self, "_resultado_clientes_order", [])
        if not clientes:
            return

        badge = self._construir_badge_clientes(clientes)
        tabs = self._construir_tabs(clientes)

        # Altura ACOTADA: ft.Tabs es un viewport y exige alto finito. Dentro
        # de la columna con scroll de la vista el alto es infinito y el
        # cliente Flet no lo puede maquetar: el area queda en blanco sin
        # ningun error en Python (por eso el log decia exito y los tests
        # pasaban). Mismo patron que el dialogo de Configuracion (480px):
        # contenedor fijo + scroll interno por tab.
        self._result_tabs_container = ft.Column(
            [
                ft.Row([badge], alignment=ft.MainAxisAlignment.END),
                ft.Container(content=tabs, height=_ALTURA_TABS_RESULTADO),
            ],
            spacing=8,
        )
        self._result_tabs = tabs
        self._result_clientes = clientes

        self._result_panel.content = self._result_tabs_container
        # Ver rama individual: el panel interno nace visible=False.
        self._result_panel.visible = True
        self.resultados_container.visible = True

        # Se pintan TODOS los tabs, no solo el primero: si no, el segundo
        # cliente se quedaba en el placeholder "Cargando..." hasta que el
        # usuario lo abria, y se leia como que la vista previa no existia.
        # Los datos ya estan en memoria, asi que no cuesta nada.
        # Se selecciona el primero CON contenido: si el tab 0 quedo vacio
        # (ej: ocultar 0.00 filtra todo) y se deja seleccionado, la vista previa
        # parece rota aunque el tab 1 si tenga la tabla.
        pintados = [cid for cid in clientes if self._actualizar_tab_resultado(cid)]
        if clientes:
            # Igual que en el panel unico: la firma se toma con las entradas
            # que efectivamente producen este resultado.
            self._marcar_firma_ejecucion()
            visible = pintados[0] if pintados else clientes[0]
            # Los handlers quedan apuntando al tab visible.
            self._activar_controles_resultado(visible)
            self._result_tabs.selected_index = clientes.index(visible)
        self._set_btn_expediente(True)

    def _construir_badge_clientes(self, clientes):
        """Badge que muestra la cantidad de clientes en el resultado."""
        return ft.Container(
            content=ft.Row(
                [
                    ft.Icon(
                        ft.Icons.EMOJI_PEOPLE_OUTLINED, size=14, color=G360Theme.accent_color()
                    ),
                    ft.Text(
                        f"{len(clientes)} cliente(s)",
                        size=12,
                        weight=ft.FontWeight.W_600,
                        color=G360Theme.accent_color(),
                    ),
                ],
                spacing=4,
            ),
            padding=ft.padding.symmetric(horizontal=10, vertical=4),
            bgcolor=G360Theme.accent_soft_color(0.15),
            border_radius=G360Theme.RADIUS_CHIP,
        )

    def _placeholder_carga_tab(self):
        """Placeholder 'Cargando...' NUEVO por tab.

        Compartir un mismo contenedor entre tabs repite el bug de controles
        compartidos: Flet lo reasigna al ultimo padre y el otro tab queda
        con contenido ajeno o vacio. Sin expand: dentro de ft.Tab no hay un
        Flex padre que lo resuelva.
        """
        import flet as ft

        return ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.ProgressRing(width=14, height=14, stroke_width=2),
                            ft.Text(
                                "Cargando…",
                                size=11,
                                color=G360Theme.text_muted_color(),
                            ),
                        ],
                        spacing=6,
                        alignment=ft.MainAxisAlignment.CENTER,
                    ),
                ],
            ),
        )

    def _construir_tabs(self, clientes):
        """Construye el widget ft.Tabs con placeholders de carga por cliente."""
        import flet as ft

        def _on_tab_change(e):
            idx = e.control.selected_index
            if idx >= 0 and idx < len(clientes):
                self._actualizar_tab_resultado(clientes[idx])

        return ft.Tabs(
            selected_index=0,
            on_change=_on_tab_change,
            animation_duration=200,
            tabs=[ft.Tab(text=cid, content=self._placeholder_carga_tab()) for cid in clientes],
        )

    def _filas_cliente(self, cliente_id) -> int:
        """Filas del resultado agrupado para un cliente (0 si no hay)."""
        por_cli = getattr(self, "_resultado_por_cliente", {}) or {}
        exp_cli = por_cli.get(cliente_id)
        if exp_cli is None or getattr(exp_cli, "resultado", None) is None:
            return 0
        df = exp_cli.resultado.dataframe
        return 0 if df is None else len(df)

    def _actualizar_tab_resultado(self, cliente_id) -> bool:
        """Refresca el contenido de un tab específico.

        Devuelve True si el tab quedo pintado con contenido, False si quedo
        con el aviso de vacio (para que el render multi seleccione el primer
        tab que si tenga que mostrar).
        """
        # Cada tab se arma con sus propios controles: compartir la tabla hacia
        # que los tabs se pisen entre si (un control de Flet no puede estar en
        # dos padres) y solo se viera el ultimo cliente pintado.
        # Va ANTES del chequeo de vista para que hasta los tabs vacios tengan
        # su set (log y boton uniformes en todos los tabs).
        activar = getattr(self, "_activar_controles_resultado", None)
        if activar is not None:
            activar(cliente_id)

        vista = self._obtener_vista_cliente(cliente_id)
        if vista is None:
            # Sin vista (ej: "Ocultar 0.00" vacio al cliente): avisar
            # en el tab en vez de dejar el "Cargando..." para siempre.
            n = self._filas_cliente(cliente_id)
            detalle = (
                f" ({n} {'fila' if n == 1 else 'filas'} omitidas por las opciones de cálculo)"
                if n
                else ""
            )
            self._mostrar_tab_vacio(
                cliente_id,
                "Sin filas visibles para este cliente con las opciones actuales." + detalle,
            )
            return False

        from src.ui.resultados_view import render_resultado

        result = render_resultado(
            vista,
            self.tipo_actual,
            self.df_historial,
            self.app.G360_ACCENT,
            self.app.G360_SUCCESS,
            marcar_nd=self._marcar_nd(),
        )

        if result["content"] is None:
            self._mostrar_tab_vacio(
                cliente_id,
                "Sin filas visibles para este cliente con las opciones actuales.",
            )
            return False

        self._renderizar_labels_cliente(result)
        self._renderizar_tabla_cliente(result)
        self._renderizar_lazy_load(result)
        self._renderizar_banner_warning(result)
        self._rearmar_result_summary(result)
        self._insertar_en_tab(cliente_id)
        return True

    def _mostrar_tab_vacio(self, cliente_id, mensaje):
        """Pinta un aviso en el tab cuando no hay nada que mostrar.

        Dejar el placeholder "Cargando..." se lee como vista previa rota.
        """
        import flet as ft

        if not hasattr(self, "_result_tabs") or not self._result_tabs:
            return
        try:
            idx = getattr(self, "_resultado_clientes_order", []).index(cliente_id)
        except ValueError:
            return
        self._result_tabs.tabs[idx].content = ft.Container(
            content=ft.Row(
                [
                    ft.Icon(ft.Icons.INFO_OUTLINED, size=16, color=G360Theme.text_muted_color()),
                    ft.Text(mensaje, size=12, color=G360Theme.text_muted_color()),
                ],
                spacing=8,
                alignment=ft.MainAxisAlignment.CENTER,
            ),
            padding=24,
        )
        try:
            if self.app.page:
                self.app.page.update()
        except Exception:
            logging.getLogger(__name__).exception("page.update tras tab vacio fallo")

    def _obtener_vista_cliente(self, cliente_id):
        """Retorna el expediente clonado con el DF del cliente y las opciones de cálculo aplicadas, o None."""
        por_cli = getattr(self, "_resultado_por_cliente", {})
        if cliente_id not in por_cli:
            return None
        exp_cli = por_cli[cliente_id]
        if not exp_cli.resultado or exp_cli.resultado.dataframe.empty:
            return None
        vista = exp_cli
        if self._solo_nd_activo():
            try:
                from src.ui.expediente_service import _clonar_solo_nd

                vista = _clonar_solo_nd(exp_cli)
            except Exception:
                vista = exp_cli
        elif self._ocultar_cero_activo():
            try:
                from src.ui.expediente_service import _clonar_sin_cero

                # Igual que la vista global: la preview muestra las
                # negativas para decidir sobre NDB; el expediente decide.
                vista = _clonar_sin_cero(exp_cli)
            except Exception:
                vista = exp_cli
        if vista is None or not vista.resultado or vista.resultado.dataframe.empty:
            return None
        return vista

    def _renderizar_labels_cliente(self, result):
        """Actualiza los labels globales con los totales del cliente actual."""
        self.lbl_total_nc.value = result["total_nc"]
        self.lbl_skus.value = result["skus_label"]
        self.lbl_alertas_count.value = result["alertas_label"]

    def _renderizar_tabla_cliente(self, result):
        """Renderiza columnas, filas y handlers de ordenamiento en la tabla."""
        self.resultados_table.columns.clear()
        self.resultados_table.rows.clear()
        for col in result["table_columns"]:
            self.resultados_table.columns.append(col)
        for row in result["table_rows"]:
            self.resultados_table.rows.append(row)
        for col in self.resultados_table.columns:
            col.on_sort = lambda e, c=col: self._ordenar_por(getattr(e.control, "data", None))

    def _renderizar_lazy_load(self, result):
        """Controla la visibilidad y texto del botón 'ver más' según lazy_info."""
        lazy = result.get("lazy_info", {})
        if lazy.get("has_more", False):
            self._result_load_more_btn.text = f"Ver más ({lazy['shown_rows']}/{lazy['total_rows']})"
            self._result_load_more_btn.visible = True
        else:
            self._result_load_more_btn.visible = False

    def _renderizar_banner_warning(self, result):
        """Muestra u oculta el banner de warning del expediente."""
        warn = result.get("expediente_warning")
        if warn:
            self._result_warn_row.controls.clear()
            self._result_warn_row.controls.append(warn)
            self._result_warn_row.visible = True
        else:
            self._result_warn_row.visible = False

    def _insertar_en_tab(self, cliente_id):
        """Inserta el contenido renderizado en el tab correspondiente y refresca la página."""
        import flet as ft

        if not hasattr(self, "_result_tabs") or not self._result_tabs:
            return
        # `ft.Tabs` expone sus paginas en `.tabs`, no en `.controls`: con
        # `.controls` esto reventaba con AttributeError y el tab se quedaba
        # en el placeholder "Cargando..." para siempre (solo con 2+ clientes,
        # porque con uno se usa el panel unico).
        idx = getattr(self, "_resultado_clientes_order", []).index(cliente_id)
        # Scroll interno: el tab vive en un area de alto fijo (ver
        # _ALTURA_TABS_RESULTADO) y el resumen con la tabla puede ser mucho
        # mas alto. Sin este scroll el contenido se recorta.
        tab_content = ft.Container(
            content=ft.Column([self._result_summary], scroll=ft.ScrollMode.AUTO),
            padding=ft.padding.only(top=8),
        )
        self._result_tabs.tabs[idx].content = tab_content
        try:
            if self.app.page:
                self.app.page.update()
        except Exception:
            # No tragar el error: si el arbol no serializa (ej: un None como
            # hijo), la pantalla queda intacta y el log es la unica pista.
            logging.getLogger(__name__).exception("page.update tras insertar tab fallo")

    def _extraer_componentes_resultado(self, content):
        """Extrae los componentes estáticos del panel renderizado por nombres.

        El content de render_resultado tiene estructura fija (arranca en el
        panel de resumen: el header "RESULTADO" se elimino por duplicar el
        titulo de la card); esta función lo mapea a claves legibles para
        evitar acceso por índices mágicos.
        """
        controls = content.controls if content is not None else []
        return {
            "summary_cards": controls[0] if len(controls) > 0 else None,
            "audit_panel": controls[1] if len(controls) > 1 else None,
            "divider1": controls[2] if len(controls) > 2 else None,
            "legend": controls[3] if len(controls) > 3 else None,
            "divider2": controls[4] if len(controls) > 4 else None,
            "divider3": controls[6] if len(controls) > 6 else None,
            "verification": controls[7] if len(controls) > 7 else None,
        }

    def _rearmar_result_summary(self, result):
        """Arma el panel de resultados actualizando las piezas variables.

        Reusa la estructura estable (tabla, avisos, carga perezosa y botones)
        para evitar duplicados. Los componentes estáticos provienen del
        contenido renderizado y se reconstruyen con los placeholders dinámicos.
        """
        content = result["content"]
        if content is None:
            return

        parts = self._extraer_componentes_resultado(content)

        # Cinturon: ninguna pieza None llega al arbol (Flet no serializa None
        # y el page.update() posterior fallaria dejando la pantalla intacta).
        self._result_summary = ft.Column(
            [
                c
                for c in [
                    parts["summary_cards"],
                    parts["audit_panel"],
                    parts["divider1"],
                    parts["legend"],
                    parts["divider2"],
                    self._result_table_wrap,
                    self._result_warn_row,
                    self._result_load_more_row,
                    parts["divider3"],
                    parts["verification"],
                    self._result_btn_row,
                ]
                if c is not None
            ],
            spacing=10,
        )

    def _result_load_more(self, e):
        """Carga el siguiente lote de filas de la tabla bajo demanda."""
        from src.ui.resultados_view import _build_standard_table, con_monto_nd

        res = self.resultado.resultado
        marcar = self._marcar_nd()
        df = con_monto_nd(res.dataframe, marcar_nd=marcar)
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
            marcar_nd=marcar,
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
        from src.ui.resultados_view import _build_standard_table, con_monto_nd

        res = self.resultado.resultado
        marcar = self._marcar_nd()
        df = con_monto_nd(res.dataframe, marcar_nd=marcar)
        # Rebuild from scratch with sort
        columns, rows, info = _build_standard_table(
            df,
            self.df_historial,
            self.tipo_actual,
            max_rows=len(df),
            sort_col=self._result_sort_col,
            sort_asc=self._result_sort_asc,
            marcar_nd=marcar,
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
        # Guard de firma (red de seguridad): los invalidadores por evento
        # cubren insumos, documentos, vendedor, modalidad e historial; si aun
        # asi las entradas cambiaron, el expediente se armaria con un
        # calculo viejo y el texto nuevo. Se corta aca en vez de escribir.
        if getattr(self, "_expediente_desfasado", None) and self._expediente_desfasado():
            self.app.show_snackbar(
                "Las entradas cambiaron después del último cálculo. "
                "Vuelve a ejecutar el reconocimiento antes de generar el expediente.",
                self.app.G360_WARNING,
            )
            return
        self.app.show_loading("Generando Expediente...")
        threading.Thread(target=self._tarea_generar_expediente, daemon=True).start()

    def _tarea_generar_expediente(self):
        """Tarea en hilo: resuelve datos, genera expedientes y notifica."""
        try:
            from src.ui.expediente_service import generar_expediente

            cliente_value = self._resolver_cliente_expediente()
            vendedor_key, vendedor_display = self._resolver_vendedor_expediente()

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
                ocultar_cero=self.chk_ocultar_cero.value,
                generar_nota_debito=self.chk_gen_nd.value or self.chk_solo_ndb.value,
                solo_ndb=self.chk_solo_ndb.value,
            )

            self._mostrar_notificacion_generacion(exp_dirs)
            self._abrir_carpeta_expediente(exp_dirs)
        except Exception as ex:
            from src.ui.mensajes import mensaje

            self.app.show_snackbar(mensaje(ex, "abrir el archivo"), self.app.G360_ERROR)
            self.app.hide_loading()

    def _resolver_cliente_expediente(self):
        """Resuelve el valor del cliente desde el dropdown o del historial."""
        # Dropdowns legacy (DC, DO, VRS, FPE, PROM, ANF, CMV, DF no usan dropdown).
        cliente_value = ""
        if not cliente_value and self.df_historial is not None and not self.df_historial.empty:
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
        return cliente_value

    def _resolver_vendedor_expediente(self):
        """Resuelve key y display del vendedor desde dropdown o búsqueda."""
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
                vnom = next((o.text for o in self.busq_vend_dd.options if o.key == vid), vid or "")
            vendedor_key = vid or ""
            vendedor_display = vnom or vid or ""
        return vendedor_key, vendedor_display

    def _mostrar_notificacion_generacion(self, exp_dirs):
        """Muestra snackbar de éxito con la cantidad de expedientes generados."""
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

    def _abrir_carpeta_expediente(self, exp_dirs):
        """Abre la carpeta del expediente en el explorador (Windows)."""
        if os.name == "nt":
            target = exp_dirs[0] if len(exp_dirs) == 1 else exp_dirs[0].parent
            os.startfile(str(target))
        self.app.hide_loading()

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
        # Reset general: limpia Y colapsa la card de reportes.
        if hasattr(self, "reporte_panel"):
            self.reporte_panel.limpiar(colapsar=True)
        # Controles básicos
        for attr, kw in [
            ("vendedor_dropdown", {"value": None}),
            ("modalidad_radio", {"value": MODALIDAD_INDIVIDUAL}),
            ("mecanica_dropdown", {"value": "12+1"}),
            ("mecanica_personalizada", {"value": "", "visible": False}),
            ("fecha_desde", {"value": ""}),
            ("fecha_hasta", {"value": ""}),
            ("_periodo_row", {"visible": False}),
            ("observaciones", {"value": ""}),
        ]:
            ctrl = getattr(self, attr, None)
            if ctrl is not None:
                for k, v in kw.items():
                    try:
                        setattr(ctrl, k, v)
                    except Exception:
                        pass
        # Insumos
        for attr, val in [
            ("lbl_lista", "Ninguno"),
            ("lbl_sku_filter", "Ninguno"),
            ("lbl_stock_cliente", "Ninguno"),
            ("lbl_historial", "Ninguno"),
        ]:
            ctrl = getattr(self, attr, None)
            if ctrl is not None:
                try:
                    ctrl.value = val
                except Exception:
                    pass
        for attr in ("sku_filter_clear_btn", "stock_cliente_clear_btn"):
            ctrl = getattr(self, attr, None)
            if ctrl is not None:
                try:
                    ctrl.visible = False
                except Exception:
                    pass
        req_list = getattr(self, "lbl_requerimientos_list", None)
        if req_list is not None:
            try:
                req_list.controls.clear()
            except Exception:
                pass
        req_count = getattr(self, "lbl_requerimientos_count", None)
        if req_count is not None:
            try:
                req_count.value = "Ninguno"
            except Exception:
                pass
        desc_pct = getattr(self, "descuento_pct", None)
        if desc_pct is not None:
            try:
                desc_pct.value = ""
                desc_pct.disabled = False
            except Exception:
                pass
        self.lista_path = None
        self.sku_filter_path = None
        self.stock_cliente_path = None
        self.desc_file_path = None
        self.requerimientos_paths = []
        # Limpiar búsqueda (función de módulo: invocable aun con mocks)
        _limpiar_busqueda(self)
        # Estado del tipo
        if hasattr(self, "tipo_dropdown"):
            if self._legacy_tipo() != "diferencia_precio":
                self.tipo_dropdown.value = "DC"
                self._on_tipo_change(None)
            else:
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
                if hasattr(self, "_result_panel"):
                    self._result_panel.visible = False
                self._verificar_puede_ejecutar()
                if self.container:
                    self.container.update()
                if self.app.page:
                    self.app.page.update()
