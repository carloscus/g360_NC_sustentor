"""Verifica que el flujo se parta en dos cards: caso y datos/sustento."""

from types import SimpleNamespace

import flet as ft

from src.core.g360_theme import G360Theme
from src.ui.view_panels import _ViewPanels
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


def _vista():
    view = _ViewPanels.__new__(_ViewPanels)
    view.tipo_dropdown = ft.Dropdown(label="Tipo de caso")
    view.modalidad_radio = ft.RadioGroup(content=ft.Text("Modalidad"))
    view.config_container = ft.Container(content=ft.Text("Parámetros"))
    view.btn_ejecutar = ft.ElevatedButton("Ejecutar reconocimiento")
    view.lbl_ejecutar_hint = ft.Text("Completa los datos.")
    view.resultados_container = ft.Container(visible=False)
    view.reporte_panel = SimpleNamespace(construir_card=lambda: ft.Text("Reporte de compras"))
    view._construir_card_db = lambda async_=False: ft.Text("Historial local")
    view._construir_strip_captura = lambda: ft.Text("Estado de captura")
    view._construir_seccion_datos = lambda: ft.Text("Filtro de datos")
    view._construir_seccion_insumos = lambda _cfg: workflow_section(
        "Insumos", ft.Icons.INVENTORY_2_OUTLINED, ft.Text("Archivos")
    )
    view._construir_seccion_configuracion = lambda: workflow_section(
        "Configuración del caso", ft.Icons.TUNE_OUTLINED, ft.Text("Reglas")
    )
    return view


def test_dos_cards_y_orden_de_etapas():
    layout = _vista()._construir_layout(
        {"resultado": "NC", "descripcion": "Diferencia entre precio histórico y lista vigente."}
    )
    textos = _textos(layout)

    assert "Preparar expediente" in textos
    assert "Datos y sustento" in textos
    # Orden: la definición del caso va antes que la carga de datos.
    assert textos.index("Preparar expediente") < textos.index("Caso y modalidad")
    assert textos.index("Caso y modalidad") < textos.index("Datos y sustento")
    assert textos.index("Datos y sustento") < textos.index("Datos del caso")
    assert textos.index("Datos del caso") < textos.index("Insumos")
    assert textos.index("Insumos") < textos.index("Configuración del caso")


def test_cada_etapa_es_una_card_distinta():
    """Cada card lleva su propio encabezado; no comparten el mismo Container."""
    layout = _vista()._construir_layout(
        {"resultado": "NC", "descripcion": "Diferencia entre precio histórico y lista vigente."}
    )

    def _cards(raiz):
        return [c for c in getattr(raiz, "controls", None) or [] if isinstance(c, ft.Container)]

    tarjetas = _cards(layout)
    portadoras = []
    for t in tarjetas:
        txt = _textos(t)
        if "Preparar expediente" in txt or "Datos y sustento" in txt:
            portadoras.append((t, txt))

    assert len(portadoras) == 2, [p[1][:2] for p in portadoras]
    caso, txt_caso = portadoras[0]
    datos, txt_datos = portadoras[1]
    assert caso is not datos

    # La card del caso solo lleva su etapa; la de datos, las tres de carga.
    assert "Caso y modalidad" in txt_caso
    assert "Datos del caso" not in txt_caso
    assert "Insumos" not in txt_caso

    assert "Datos del caso" in txt_datos
    assert "Insumos" in txt_datos
    assert "Configuración del caso" in txt_datos
    assert "Caso y modalidad" not in txt_datos

    # El botón de ejecutar vive en la card de datos (cierra el flujo).
    assert "¿Todo listo?" in txt_datos
    assert "¿Todo listo?" not in txt_caso
    assert G360Theme.RADIUS_CARD is not None
