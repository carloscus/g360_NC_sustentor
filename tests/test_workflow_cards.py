"""Verifica la jerarquía del flujo principal: banner + card de definición en 2 columnas + sticky bar.

La card única "Definición del caso" sustituye a las dos cards apiladas
("Preparar expediente" / "Datos y sustento"): menos scroll y la relación
parámetros↔datos a la vista. La barra inferior fija,与 scroll aparte.
"""

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
    view.btn_gen_exp = ft.ElevatedButton("Generar expediente")
    view.lbl_ejecutar_hint = ft.Text("Completa los datos.")
    view.antecedentes = ft.TextField(label="Antecedentes")
    view.observaciones = ft.TextField(label="Observaciones")
    view.resultados_container = ft.Container(
        visible=False, content=ft.Text("Resultado del reconocimiento")
    )
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


def _cfg():
    return {"resultado": "NC", "descripcion": "Diferencia entre precio histórico y lista vigente."}


def ReconocimientoViewReal():
    """Vista real (no stubs): los stubs no construyen botones de verdad."""
    from src.ui.reconocimiento_view import ReconocimientoView
    from tests.test_render_parentdata import _AppStub

    app = _AppStub()
    app.page = None
    view = ReconocimientoView(app)
    view.build()
    return view


def _layout():
    return _vista()._construir_layout(_cfg())


def test_raiz_de_dos_pisos_scroll_mas_sticky():
    """El scroll y la barra fija son hermanos: la barra no scrollea."""
    view = _vista()
    layout = view._construir_layout(_cfg())
    assert isinstance(layout, ft.Column)
    assert len(layout.controls) == 2
    scroll, sticky = layout.controls
    assert scroll.expand == 1, "el scroll debe ocupar el piso de arriba"
    assert isinstance(sticky, ft.Container)
    # La barra lleva el hint de validación y los dos pasos, en orden.
    fila = sticky.content
    assert isinstance(fila, ft.Row)
    assert view.btn_ejecutar in fila.controls
    assert view.btn_gen_exp in fila.controls
    # Secuencia 1 -> 2: Ejecutar antes que Generar expediente.
    assert fila.controls.index(view.btn_ejecutar) < fila.controls.index(view.btn_gen_exp)


def test_el_hint_de_la_barra_se_encoge_y_no_empuja_los_botones():
    """El hint va en un Container expand=1.

    Sin expand, el texto de validacion (hasta ~51 caracteres) empujaba los
    dos botones fuera de la ventana minima (950px) en vez de recortarse: los
    botones son el paso 1 y 2 del flujo y no pueden perder visibilidad.
    """
    view = _vista()
    layout = view._construir_layout(_cfg())
    fila = layout.controls[1].content

    hint_portador = fila.controls[0]
    assert isinstance(hint_portador, ft.Container)
    assert hint_portador.expand == 1
    assert hint_portador.content is view.lbl_ejecutar_hint


def test_los_botones_de_la_barra_no_superan_el_ancho_util():
    """Los dos pasos ocupan 300px cada uno y caben en la ventana minima.

    `accent_button` declara 450x65 por defecto: en la barra sticky dos botones
    de 450 sumaban 900 y no entraban en los 886px utiles (950 de ventana
    menos 64 de la vista), empujando el hint fuera de pantalla.
    """
    view = ReconocimientoViewReal()
    ANCHO_UTIL = 950 - 64  # ventana minima menos el padding lateral de la vista
    e, g = view.btn_ejecutar, view.btn_gen_exp

    assert e.width == 300 and g.width == 300, "70% del ancho por defecto (450)"
    # El texto debe caber dentro de la caja: si no, se recorta.
    for b in (e, g):
        texto = b.content.controls[1].value
        need = int(len(texto) * 8.6) + b.content.controls[0].size + 6 + b.content.spacing + 56
        assert need <= b.width, f"{texto!r} necesita ~{need}px y la caja es {b.width}px"

    # Los dos botones + spacing + el hint elastico entran en el ancho util.
    fijos = e.width + g.width + 24
    assert fijos < ANCHO_UTIL, f"{fijos}px fijos no entran en {ANCHO_UTIL}px"
    assert ANCHO_UTIL - fijos > 200, "el hint necesita margen para leerse"


def test_definicion_del_caso_en_dos_columnas():
    """Parámetros y justificación a la izquierda; datos e insumos a la derecha."""
    layout = _layout()
    definicion = layout.controls[0].content.controls[2]
    assert "Definición del caso" in _textos(definicion)

    fila = definicion.content.controls[2]
    assert isinstance(fila, ft.Row)
    assert len(fila.controls) == 2
    izq, der = fila.controls
    assert izq.expand == 1 and der.expand == 1, "las dos columnas pesan igual"

    assert "Caso y modalidad" in _textos(izq)
    assert "Justificación y observaciones" in _textos(izq)
    assert "Datos del caso" in _textos(der)
    assert "Insumos" in _textos(der)
    # Las columnas no se mezclan: lo de definición no vive en datos.
    assert "Caso y modalidad" not in _textos(der)
    assert "Datos del caso" not in _textos(izq)


def test_orden_de_etapas_dentro_de_la_definicion():
    """Definir el caso se lee antes que cargar los datos; las reglas cierran."""
    textos = _textos(_layout())
    assert textos.index("Caso y modalidad") < textos.index("Tipo de caso")
    assert textos.index("Tipo de caso") < textos.index("Modalidad")
    assert textos.index("Caso y modalidad") < textos.index("Datos del caso")
    assert textos.index("Datos del caso") < textos.index("Insumos")
    assert textos.index("Insumos") < textos.index("Configuración del caso")


def test_orden_de_flujo_y_ausencia_de_numeracion():
    """Contexto -> entradas -> salida; sin "3 ·" / "4 ·" ni el Reporte."""
    textos = _textos(_layout())
    assert textos.index("Historial local") < textos.index("Definición del caso")
    assert textos.index("Definición del caso") < textos.index("Caso y modalidad")
    assert textos.index("Configuración del caso") < textos.index("Resultado del reconocimiento")
    assert "3 · DATOS DEL CASO" not in textos
    assert "4 · CONFIGURACIÓN DEL CASO" not in textos
    assert "Reporte de compras" not in textos


def test_el_boton_ya_no_vive_en_el_scroll():
    """El botón primario salió de la card: ahora está en la barra fija."""
    view = _vista()
    layout = view._construir_layout(_cfg())
    scroll = layout.controls[0].content
    assert view.btn_ejecutar not in _controles_de(scroll)
    assert view.btn_ejecutar in _controles_de(layout.controls[1])


def _controles_de(raiz):
    """Todos los controles del árbol (para preguntar si uno está montado)."""
    out = []
    for ch in getattr(raiz, "controls", None) or []:
        out.append(ch)
        out.extend(_controles_de(ch))
    ct = getattr(raiz, "content", None)
    if isinstance(ct, ft.Control):
        out.append(ct)
        out.extend(_controles_de(ct))
    return out


def test_las_secciones_llevan_numeracion_de_lectura():
    """1-2 columna izquierda, 3-4 derecha, 5 reglas: el numero dice el orden."""
    from src.ui.reconocimiento_view import ReconocimientoView
    from tests.test_render_parentdata import _AppStub

    view = ReconocimientoView(_AppStub())
    view.build()
    definicion = view.container.content.controls[0].content.controls[2]
    izq, der = definicion.content.controls[2].controls

    def _numeros(col):
        return [
            _textos(sec.controls[0].controls[0])[0]
            for sec in col.controls
            if isinstance(sec, ft.Column)
        ]

    assert _numeros(izq) == ["1", "2"]
    assert _numeros(der) == ["3", "4"]
    assert _textos(definicion.content.controls[3].controls[0].controls[0])[0] == "5"


def test_el_badge_de_numero_reemplaza_al_icono():
    """Con step, el badge muestra el numero: el icono no comunica orden."""
    sec = workflow_section("Insumos", ft.Icons.FOLDER_OPEN, ft.Text("x"), step=4)
    badge = sec.controls[0].controls[0]
    assert isinstance(badge.content, ft.Text)
    assert badge.content.value == "4"

    # Sin step sigue siendo el icono (compatibilidad de las secciones sueltas).
    sec_icono = workflow_section("Insumos", ft.Icons.FOLDER_OPEN, ft.Text("x"))
    assert isinstance(sec_icono.controls[0].controls[0].content, ft.Icon)


def test_el_banner_de_historial_es_compacto():
    """Bloque A: banner de 2 líneas, no card con título y KPIs apilados.

    Línea 1 = estado + Ver detalle + Actualizar hoy + config DB.
    Línea 2 = métricas (KPIs) + alerta en línea cuando se despliega.
    Con la card anterior, 4 KPIs + aviso + 2 botones no cabían en una fila
    sin comprimir y el detalle de meses se comía el alto.
    """
    from src.ui.reconocimiento_view import ReconocimientoView
    from tests.test_render_parentdata import _AppStub

    view = ReconocimientoView(_AppStub())
    view.build()
    layout = view.container.content
    banner = layout.controls[0].content.controls[0]

    assert isinstance(banner, ft.Container)
    assert G360Theme.RADIUS_CONTROL == banner.border_radius

    lineas = banner.content.controls
    assert len(lineas) == 2, "el banner son 2 líneas"
    assert all(isinstance(l, ft.Row) for l in lineas)

    # Línea 1: status + ver detalle + spacer + actualizar + config (icono)
    l1 = lineas[0]
    assert len(l1.controls) == 5
    assert l1.controls[0] is view.card_db_status
    assert isinstance(l1.controls[1], ft.TextButton), "falta Ver detalle"
    assert l1.controls[2].expand == 1, "spacer que empuja las acciones a la derecha"
    assert isinstance(l1.controls[3], ft.ElevatedButton), "falta Actualizar hoy"
    assert isinstance(l1.controls[4], ft.IconButton), "config DB debe ser botón de icono"

    # Línea 2: métricas + alerta inline
    l2 = lineas[1]
    assert len(l2.controls) == 2
    assert l2.controls[0] is view.card_db_kpis
    detalle = l2.controls[1]
    assert detalle.visible is False, "el detalle de meses arranca plegado"


def test_ver_detalle_alterna_el_mensaje_de_meses():
    """'Ver detalle' despliega la lista de meses incompletos sin crashear."""
    from src.ui.reconocimiento_view import ReconocimientoView
    from tests.test_render_parentdata import _AppStub

    app = _AppStub()
    app.page = None
    view = ReconocimientoView(app)
    view.build()

    assert view.btn_ver_detalle.text == "Ver detalle"
    view.btn_ver_detalle.on_click(None)
    assert view.btn_ver_detalle.text == "Ocultar"
    assert view._db_banner_detalle.visible is True
    view.btn_ver_detalle.on_click(None)
    assert view.btn_ver_detalle.text == "Ver detalle"
    assert view._db_banner_detalle.visible is False
