# -*- coding: utf-8 -*-
import flet as ft
import pandas as pd
import logging

_logger = logging.getLogger("src.ui.reconocimiento_view")
from src.core.g360_theme import G360Theme
from src.ui.reconocimiento_config import TIPO_CONFIG
from src.ui.catalog import (
    CATALOGO,
    DOC_HISTORICO,
    HistorialConfig,
    MODALIDAD_INDIVIDUAL,
)


"""Métodos hoja y utilidades de ReconocimientoView (mixins).

Lógica pura o compositiva sin estado de layout: mapeo caso/legacy, badges,
estadísticas de fragmento y colección de valores UI. Heredado por
ReconocimientoView (src/ui/reconocimiento_view.py)."""


class _ViewHelpers:
    def _caso_de_tipo(self) -> dict:
        """Retorna el dict del caso canónico correspondiente a self.tipo_actual."""
        if self.tipo_actual in CATALOGO:
            c = CATALOGO[self.tipo_actual]
            return self._caso_dict(c)
        # Fallback: legacy type → caso (e.g. "diferencia_precio" → "DC")
        from src.ui.catalog import caso_de_legacy

        cod = caso_de_legacy(self.tipo_actual) or "DC"
        return self._caso_dict(CATALOGO[cod])

    @staticmethod
    def _caso_dict(c) -> dict:
        """Dict de caso canónico + flags de UI heredadas de TIPO_CONFIG legacy.

        Sin esto, secciones como META (CMV) o PERIODO nunca se renderizaban
        porque los flags viven en TIPO_CONFIG[legacy], no en el Caso.
        """
        flags = {}
        for lt in c.legacy_types:
            tc = TIPO_CONFIG.get(lt, {})
            for k, v in tc.items():
                if k.startswith(("tiene_", "necesita_")) and v:
                    flags[k] = True
        d = {
            "label": c.label,
            "descripcion": c.descripcion,
            "insumos": c.insumos,
            "resultado": c.resultado,
            "modalidades": c.modalidades,
            "strategy": c.strategy,
            "historico_default": c.historico_default,
        }
        d.update(flags)
        return d

    def _tipo_incluye(self, *legacy_keys: str) -> bool:
        """True si el tipo actual (canónico o legacy) mapea a alguno de los legacy_keys.

        Reemplaza los `self.tipo_actual == 'legacy'` que dejaban de coincidir
        cuando se usa el codigo canónico (ANF, DO, DC...).
        """
        if self.tipo_actual in legacy_keys:
            return True
        caso = CATALOGO.get(self.tipo_actual)
        if caso:
            return bool(set(caso.legacy_types) & set(legacy_keys))
        return False

    def _legacy_tipo(self) -> str:
        """Retorna el tipo legacy que config_builder y las strategies esperan."""
        if self.tipo_actual in TIPO_CONFIG:
            return self.tipo_actual
        # Catalog case code → pick first legacy_type as representative
        caso = CATALOGO.get(self.tipo_actual)
        if caso and caso.legacy_types:
            return caso.legacy_types[0]  # e.g. "DC" → "diferencia_precio"
        return "diferencia_precio"  # fallback

    def _preview_table_from_data(self, headers: list, rows: list, tipo: str) -> ft.Container:
        """Tabla compacta de preview desde datos ya leídos."""
        ncols = [
            ft.DataColumn(
                ft.Text(
                    h[:14], size=10, weight=ft.FontWeight.W_600, color=ft.Colors.ON_SURFACE_VARIANT
                )
            )
            for h in headers[:8]
        ]
        data_rows = []
        for r in rows[:5]:
            cells = []
            for val in r[:8]:
                v = str(val)[:20] if val else ""
                cells.append(ft.DataCell(ft.Text(v, size=10)))
            data_rows.append(ft.DataRow(cells=cells))
        return ft.Container(
            content=ft.DataTable(
                columns=ncols,
                rows=data_rows,
                heading_row_height=22,
                heading_row_color=ft.Colors.with_opacity(0.1, ft.Colors.WHITE),
                horizontal_lines=ft.border.BorderSide(0.5, G360Theme.border_subtle_color()),
                border_radius=8,
                data_row_max_height=20,
            ),
            padding=ft.padding.symmetric(horizontal=12, vertical=6),
            bgcolor=G360Theme.with_opacity(0.04, G360Theme.ACCENT),
            border_radius=8,
            margin=ft.margin.only(bottom=4, left=20, right=8),
        )

    @staticmethod
    def _badge(texto: str, color: str) -> ft.Container:
        return ft.Container(
            content=ft.Text(texto, size=10, weight=ft.FontWeight.W_700, color=color),
            padding=ft.padding.symmetric(horizontal=7, vertical=3),
            border_radius=6,
            bgcolor=G360Theme.with_opacity(0.12, color),
        )

    def _fragmento_stats(self, df) -> dict:
        """Detecta cardinalidad del fragmento: clientes/pedidos/OCs/facturas/skus/fechas."""
        s = {
            "filas": len(df),
            "clientes": 0,
            "pedidos": 0,
            "ordenes": 0,
            "facturas": 0,
            "skus": 0,
            "fmin": "",
            "fmax": "",
        }
        try:
            if "COD_CLIENTE" in df.columns:
                s["clientes"] = int(df["COD_CLIENTE"].astype(str).str.strip().nunique())
            if "ID_PEDIDO" in df.columns:
                s["pedidos"] = int(
                    df["ID_PEDIDO"]
                    .fillna("")
                    .astype(str)
                    .str.strip()
                    .replace("", pd.NA)
                    .dropna()
                    .nunique()
                )
            if "ORDEN_COMPRA" in df.columns:
                s["ordenes"] = int(
                    df["ORDEN_COMPRA"]
                    .fillna("")
                    .astype(str)
                    .str.strip()
                    .replace("", pd.NA)
                    .dropna()
                    .nunique()
                )
            if "CODIGO" in df.columns:
                s["skus"] = int(df["CODIGO"].astype(str).str.strip().nunique())
            if "TIPO_CLASE" in df.columns:
                fac = df[df["TIPO_CLASE"].astype(str).str.lower() == "factura"]
            elif "TIPO_DOC" in df.columns:
                fac = df[df["TIPO_DOC"].astype(str).str.upper().str.startswith(("F", "B"))]
            else:
                fac = df
            if "DOC_ID" in fac.columns:
                s["facturas"] = int(fac["DOC_ID"].nunique())
            if "FECHA" in df.columns:
                f = pd.to_datetime(df["FECHA"], errors="coerce").dropna()
                if not f.empty:
                    s["fmin"] = f.min().strftime("%Y-%m-%d")
                    s["fmax"] = f.max().strftime("%Y-%m-%d")
        except Exception:
            pass
        return s

    def _fragmento_badges(self, s: dict) -> list[ft.Control]:
        out = [
            self._badge(f"{s['filas']:,} filas", G360Theme.accent_text_color()),
            self._badge(
                f"{s['clientes']} cliente{'s' if s['clientes'] != 1 else ''}",
                G360Theme.accent_2_color() if s["clientes"] > 1 else ft.Colors.ON_SURFACE_VARIANT,
            ),
        ]
        if s.get("pedidos"):
            out.append(
                self._badge(
                    f"{s['pedidos']} pedido{'s' if s['pedidos'] != 1 else ''}",
                    G360Theme.accent_3_color(),
                )
            )
        if s.get("ordenes"):
            out.append(
                self._badge(
                    f"{s['ordenes']} orden{'es' if s['ordenes'] != 1 else ''}", G360Theme.ok_color()
                )
            )
        out.append(
            self._badge(
                f"{s['facturas']} factura{'s' if s['facturas'] != 1 else ''}",
                G360Theme.warning_color() if s["facturas"] > 1 else ft.Colors.ON_SURFACE_VARIANT,
            )
        )
        out.append(self._badge(f"{s['skus']} SKUs", G360Theme.accent_3_color()))
        if s["fmin"] and s["fmax"]:
            rango = s["fmin"] if s["fmin"] == s["fmax"] else f"{s['fmin']} → {s['fmax']}"
            out.append(self._badge(rango, G360Theme.accent_3_color()))
        return out

    def _insumo_necesita(self, ins: str, cfg: dict) -> bool:
        return ins in cfg.get("insumos", ())

    def _collect_ui_values(self) -> dict:
        """Recolecta valores de los controles UI en un dict para config_builder."""
        historico = self._leer_historico_config()
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
            "modalidad": self.modalidad_radio.value or MODALIDAD_INDIVIDUAL,
            "factura_id": self.factura_dropdown.value,
            "fecha_desde": self.fecha_desde.value,
            "fecha_hasta": self.fecha_hasta.value,
            "mecanica": self.mecanica_dropdown.value,
            "mecanica_personalizada": self.mecanica_personalizada.value,
            "meta_monto": self.meta_monto.value,
            "rebate_pct": self.rebate_pct.value,
            "requerimientos_paths": self.requerimientos_paths,
            "sort_mode": self.sort_mode_radio.value,
            "sort_mode_dc": self.sort_mode_dc_radio.value,
            "forzar_cantidad": self.chk_forzar_cant.value,
            "fp_desde": self.fp_desde.value,
            "fp_hasta": self.fp_hasta.value,
            "stock_cliente_path": self.stock_cliente_path,
            # Factura unica por fragmento (fallback a dropdown si existe legacy)
            "factura_ci": (
                self.factura_dropdown_ci.value
                or (self._sel_facturas[0][1] if len(self._sel_facturas) == 1 else None)
            ),
            "factura_sf": self.factura_dropdown_sf.value,
            "factura_df": (
                self.factura_dropdown_df.value
                or (self._sel_facturas[0][1] if len(self._sel_facturas) == 1 else None)
            ),
            "descuento_pct": self.descuento_pct.value,
            "desc_file_path": self.desc_file_path,
            "sku_filter_path": self.sku_filter_path,
            "historico_config": historico.as_dict(),
            "chk_incluir_nc": historico.considerar("nc"),
            "chk_incluir_ndb": historico.considerar("ndb"),
            "cliente_pb": self.cliente_dropdown_pb.value,
            "lineas_selected": [name for name, cb in self.linea_checkboxes.items() if cb.value],
            "categorias_nc": [cat for cat, cb in self.categoria_nc_checkboxes.items() if cb.value],
            "skus_incluidos": skus_incluidos,
            "vendedor_id": self.vendedor_dropdown.value,
            "df_historial_full": self.df_historial,
        }

    def _leer_historico_config(self) -> HistorialConfig:
        """Toma los checks activos; no reutiliza solo los defaults del caso."""
        base = getattr(self, "_historico_config", HistorialConfig())
        valores = {}
        for doc in DOC_HISTORICO:
            par = getattr(base, doc, (True, False))
            chk_mostrar = getattr(self, "historico_incluir_ctrls", {}).get(doc)
            chk_usar = getattr(self, "historico_calc_ctrls", {}).get(doc)
            valores[doc] = (
                bool(chk_mostrar.value) if chk_mostrar is not None else bool(par[0]),
                bool(chk_usar.value) if chk_usar is not None else bool(par[1]),
            )
        self._historico_config = HistorialConfig(**valores)
        return self._historico_config

    def _guardar_historico_config_desde_checks(self, e=None):
        """Persiste el snapshot al cambiar un check para resistir re-renders."""
        config = self._leer_historico_config()
        if e is not None and getattr(self, "resultado", None):
            invalidar = getattr(self, "_invalidar_resultado_por_config", None)
            if invalidar:
                invalidar("Cambió la configuración del historial; vuelve a ejecutar el cálculo.")
        return config
