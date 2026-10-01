"""Jerarquía visual del flujo principal (sin ejecutar lógica del caso)."""

from types import SimpleNamespace

import flet as ft

from src.ui.view_panels import _ViewPanels
from src.core.g360_theme import G360Theme
from src.ui.reconocimiento_view import ReconocimientoView
from src.ui.widgets.workflow_section import workflow_section


def _textos(control):
    out = []
    value = getattr(control, "value", None)
    if isinstance(value, str):
        out.append(value)
    for child in getattr(control, "controls", None) or []:
        out.extend(_textos(child))
    content = getattr(control, "content", None)
    if content is not None:
        out.extend(_textos(content))
    return out


def test_flujo_y_secciones_separadas():
    view = _ViewPanels.__new__(_ViewPanels)
    view.tipo_dropdown = ft.Dropdown(label="Tipo de caso")
    view.modalidad_radio = ft.RadioGroup(content=ft.Text("Modalidad"))
    view.config_container = ft.Container(content=ft.Text("Parámetros"))
    view.btn_ejecutar = ft.ElevatedButton("Ejecutar reconocimiento")
    view.lbl_ejecutar_hint = ft.Text("Completa los datos.")
    view.resultados_container = ft.Container(visible=False)
    view.reporte_panel = SimpleNamespace(construir_card=lambda: ft.Text("Reporte de compras"))
    view._construir_card_db = lambda: ft.Text("Historial local")
    view._construir_strip_captura = lambda: ft.Text("Estado de captura")
    view._construir_seccion_datos = lambda: ft.Text("Filtro de datos")
    view._construir_seccion_insumos = lambda _cfg: workflow_section(
        "Insumos", ft.Icons.INVENTORY_2_OUTLINED, ft.Text("Archivos")
    )
    view._construir_seccion_configuracion = lambda: workflow_section(
        "Configuración del caso", ft.Icons.TUNE_OUTLINED, ft.Text("Reglas")
    )

    layout = view._construir_layout(
        {
            "resultado": "NC",
            "descripcion": "Diferencia entre precio histórico y lista vigente.",
        }
    )
    textos = _textos(layout)

    assert textos.index("Historial local") < textos.index("Reporte de compras")
    assert textos.index("Reporte de compras") < textos.index("Preparar expediente")
    assert textos.index("Caso y modalidad") < textos.index("Datos del caso")
    assert textos.index("Datos del caso") < textos.index("Insumos")
    assert textos.index("Insumos") < textos.index("Configuración del caso")
    assert "3 · DATOS DEL CASO" not in textos
    assert "4 · CONFIGURACIÓN DEL CASO" not in textos


def test_lista_vacia_de_insumos_no_expande_el_panel_gris():
    view = _ViewPanels.__new__(_ViewPanels)
    view.insumo_list_wrapper = ft.Container(height=56)
    view.insumo_list = ft.Column([], scroll=ft.ScrollMode.AUTO)
    view.insumo_empty_state = ft.Row([], visible=False)
    view.insumo_browse_btn = ft.ElevatedButton("Añadir archivos", visible=True)
    view._insumo_files = []
    view._preview_threads = set()

    view._renderizar_lista_insumos()

    assert view.insumo_list_wrapper.visible is False
    assert view.insumo_empty_state.visible is True
    assert view.insumo_list.controls == []
    assert any("archivos de apoyo" in text for text in _textos(view.insumo_empty_state))

    # Un caso sin archivos externos no conserva ni la caja ni el aviso redundante.
    view.insumo_browse_btn.visible = False
    view._renderizar_lista_insumos()
    assert view.insumo_list_wrapper.visible is False
    assert view.insumo_empty_state.visible is False


def test_historial_no_seleccionado_en_insumos_es_mensaje_en_linea():
    view = _ViewPanels.__new__(_ViewPanels)
    view.insumo_hist_panel = ft.Column([], spacing=6)
    view._hist_fragmento = None
    view._busq_df = None

    view._pintar_panel_historial()

    assert len(view.insumo_hist_panel.controls) == 1
    row = view.insumo_hist_panel.controls[0]
    assert isinstance(row, ft.Row)
    assert "Historial aún no seleccionado" in _textos(row)


def test_datos_del_caso_oculta_vendedor_si_no_hay_opciones(tmp_db):
    class App:
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
            from pathlib import Path

            return Path.cwd()

    view = ReconocimientoView(App())
    view.build()

    import time

    fin = time.time() + 5
    while time.time() < fin and view.busq_vend_status.value == "Cargando vendedores…":
        time.sleep(0.05)

    assert view.busq_vend_dd.options == []
    assert view.busq_vend_dd.visible is False
    assert view.busq_vend_dd.disabled is True
    assert view.busq_vend_status.visible is True
    assert "Busca el cliente directamente" in view.busq_vend_status.value
    assert view.busq_doc_row.visible is False
    assert view.busq_doc_prompt.visible is True
    assert view.busq_preview_tbl.visible is False
    assert view.busq_exec_btn.visible is False
    assert view.insumo_list_wrapper.visible is False
    assert view.insumo_empty_state.visible is True
