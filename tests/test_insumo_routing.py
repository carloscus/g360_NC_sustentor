"""Routing de insumos: FPE ejecutable y DO sin(slot) ni % global.

Regresiones que cubre:
- FPE (Feria/Preventa) exige `requerimientos_paths` para habilitar Ejecutar, así
  que todo archivo externo debe caer en ese slot. Antes ningún nombre producía
  "mecanica" para FPE y el caso quedaba sin poder ejecutarse.
- El `if _tipo_incluye("descuento_precio")` estaba en medio de la cadena
  if/elif de Asignar insumo y la cortaba: en DO un archivo tipo "porcentaje"
  nunca llenaba `desc_file_path`.
- El % global de DO es la alternativa al archivo por SKU: se deshabilita al
  cargar un archivo (después de la cadena, no dentro).
"""

from pathlib import Path

import flet as ft

from src.core.g360_theme import G360Theme
from src.ui.reconocimiento_view import ReconocimientoView


class _AppStub:
    page = None
    G360_ACCENT = G360Theme.accent_color()
    G360_SUCCESS = G360Theme.ok_color()
    G360_WARNING = G360Theme.warning_color()
    G360_ERROR = G360Theme.error_color()
    G360_ACCENT_2 = G360Theme.accent_2_color()

    def show_snackbar(self, *args, **kwargs):
        pass

    def show_loading(self, *args, **kwargs):
        pass

    def hide_loading(self, *args, **kwargs):
        pass

    def _pick_files(self, *args, **kwargs):
        return []

    def _get_desktop_path(self):
        return Path.cwd()


class _PageStub:
    """Control.update() solo necesita que page exista y sepa actualizar."""

    def __init__(self):
        self.actualizaciones = 0

    def update(self):
        self.actualizaciones += 1


def _view(tipo: str) -> ReconocimientoView:
    """Vista con el layout construido (labels, gate y botón Ejecutar)."""
    view = ReconocimientoView(_AppStub())
    view.build()
    view.tipo_actual = tipo
    return view


def _view_con_page(tipo: str) -> tuple[ReconocimientoView, _PageStub]:
    """Para ejercitar _verificar_puede_ejecutar (sale temprano sin page)."""
    view = _view(tipo)
    page = _PageStub()
    view.app.page = page
    return view, page


class TestFpeRouting:
    def test_cualquier_archivo_va_a_requerimientos(self):
        view = _view("FPE")
        view._agregar_insumo("requerimientos_feria.xlsx")
        assert [p.name for p in view.requerimientos_paths] == ["requerimientos_feria.xlsx"]

    def test_no_depende_del_nombre_del_archivo(self):
        """El nombreponía "descuento" o "sku": igual va a requerimientos."""
        view = _view("FPE")
        view._agregar_insumo("lista_sku_con_descuento.xlsx")
        assert len(view.requerimientos_paths) == 1
        assert view.sku_filter_path is None
        assert view.desc_file_path is None

    def test_varios_archivos_se_acumulan(self):
        view = _view("FPE")
        view._agregar_insumo("lote1.xlsx")
        view._agregar_insumo("lote2.xlsx")
        assert len(view.requerimientos_paths) == 2

    def test_no_repetir_el_mismo_archivo(self):
        view = _view("FPE")
        view._agregar_insumo("lote1.xlsx")
        view._agregar_insumo("lote1.xlsx")
        assert len(view.requerimientos_paths) == 1

    def test_se_etiqueta_como_requerimientos(self):
        view = _view("FPE")
        view._agregar_insumo("feria.xlsx")
        assert view._insumo_files[-1]["tipo"] == "Requerimientos"

    def test_quitar_el_archivo_libera_el_slot(self):
        view = _view("FPE")
        view._agregar_insumo("feria.xlsx")
        view._quitar_insumo(None, "feria.xlsx")
        assert view.requerimientos_paths == []

    def test_habilita_ejecutar_con_solo_requerimientos(self):
        """El gate de FPE solo exige requerimientos_paths (+ historial)."""
        view, _page = _view_con_page("FPE")
        view.df_historial = object()
        view._agregar_insumo("feria.xlsx")
        view._verificar_puede_ejecutar()
        assert view.btn_ejecutar.disabled is False

    def test_sin_requerimientos_sigue_bloqueado(self):
        view, _page = _view_con_page("FPE")
        view.df_historial = object()
        view._verificar_puede_ejecutar()
        assert view.btn_ejecutar.disabled is True

    def test_muestra_los_dos_radios_de_modalidad(self):
        view = _view("FPE")
        cfg = view._caso_de_tipo()
        assert set(cfg["modalidades"]) == {"individual", "consolidado"}


class TestDoRouting:
    def _desc_txt_field(self, view):
        return view.descuento_pct

    def test_archivo_de_descuentos_ocupa_el_slot(self):
        view = _view("DO")
        view.descuento_pct = ft.TextField(value="")
        view._agregar_insumo("descuentos_comerciales.xlsx")
        assert view.desc_file_path == "descuentos_comerciales.xlsx"
        assert view.sku_filter_path is None

    def test_archivo_por_sku_ocupa_el_slot_sku(self):
        view = _view("DO")
        view.descuento_pct = ft.TextField(value="")
        view._agregar_insumo("sku_con_descuento.xlsx")
        assert view.sku_filter_path == "sku_con_descuento.xlsx"
        assert view.desc_file_path is None

    def test_porcentaje_global_se_deshabilita_con_archivo(self):
        view = _view("DO")
        view.descuento_pct = ft.TextField(value="5")
        view._agregar_insumo("descuentos_comerciales.xlsx")
        assert view.descuento_pct.disabled is True

    def test_el_porcentaje_global_no_se_toca_en_otros_casos(self):
        view = _view("DC")
        view.descuento_pct = ft.TextField(value="5")
        view._agregar_insumo("lista_precios.xlsx")
        assert view.descuento_pct.disabled is False

    def test_al_quitar_el_archivo_vuelve_el_porcentaje_global(self):
        view = _view("DO")
        view.descuento_pct = ft.TextField(value="5")
        view._agregar_insumo("descuentos_comerciales.xlsx")
        assert view.descuento_pct.disabled is True
        view._quitar_insumo(None, "descuentos_comerciales.xlsx")
        assert view.descuento_pct.disabled is False

    def test_gate_acepta_el_archivo_de_descuentos(self):
        view, _page = _view_con_page("DO")
        view.descuento_pct = ft.TextField(value="")
        view.df_historial = object()
        view._agregar_insumo("descuentos_comerciales.xlsx")
        view._verificar_puede_ejecutar()
        assert view.btn_ejecutar.disabled is False

    def test_gate_acepta_el_porcentaje_global_sin_archivo(self):
        view, _page = _view_con_page("DO")
        view.descuento_pct = ft.TextField(value="5")
        view.df_historial = object()
        view._verificar_puede_ejecutar()
        assert view.btn_ejecutar.disabled is False

    def test_gate_bloquea_sin_descuento_ni_archivo(self):
        view, _page = _view_con_page("DO")
        view.descuento_pct = ft.TextField(value="")
        view.df_historial = object()
        view._verificar_puede_ejecutar()
        assert view.btn_ejecutar.disabled is True
