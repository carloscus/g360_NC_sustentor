"""La barra de filtros tiene que ser simetrica y construir sin errores.

Dos defectos reales que esto fija:

1. `ft.Dropdown` en Flet 0.28.3 NO acepta `height` (si, `TextField`). Pasarselo
   reventaba al abrir los filtros, y no lo cubria ningun test porque los
   controles se construyen en runtime.
2. La simetria: mismo ancho, mismo radio, mismo `dense`, mismo `text_size` y
   misma altura para dropdown, campo de texto y boton. Si uno se copia a mano
   con otras medidas, la fila deja de alinear y no se nota hasta que se mira.
"""

from __future__ import annotations

import inspect
import ast
from pathlib import Path

import pytest

from src.ui.widgets import control_factory as cf

RAIZ = Path(__file__).resolve().parents[1]


def _vendedor(**kw):
    return cf.dropdown("Vendedor", width=cf.WIDTH_FILTER, **kw)


# ── Construccion (guarda contra argumentos inexistentes) ──────────────────────


def _kwargs_de_llamadas(ruta, nombre_func, attr):
    """Keywords que se le pasan a `attr(...)` dentro de `nombre_func`.

    Se usa ast y no texto: un chequeo por substringeria con `menu_height=320`
    (contiene "height=") y con los docstrings que describen el defecto.

    La fabrica construye con `ft.X(**kwargs)`, asi que ademas de los keywords
    explicitos hay que leer el dict literal asignado a `kwargs`.
    """
    arbol = ast.parse(Path(ruta).read_text(encoding="utf-8"))
    encontrados = {}
    for fn in ast.walk(arbol):
        if not (isinstance(fn, ast.FunctionDef) and fn.name == nombre_func):
            continue
        # 1) keywords literales en la llamada
        for nodo in ast.walk(fn):
            if isinstance(nodo, ast.Call) and ast.unparse(nodo.func).endswith(attr):
                for kw in nodo.keywords:
                    if kw.arg:
                        encontrados[kw.arg] = ast.unparse(kw.value)
        # 2) el dict que se le pasa con ** (la fabrica usa `kwargs = dict(...)`)
        for asig in ast.walk(fn):
            if not isinstance(asig, ast.Assign):
                continue
            valor = asig.value
            if isinstance(valor, ast.Call) and ast.unparse(valor.func) == "dict":
                for kw in valor.keywords:
                    if kw.arg:
                        encontrados[kw.arg] = ast.unparse(kw.value)
            elif isinstance(valor, ast.Dict):
                for k, v in zip(valor.keys, valor.values):
                    if isinstance(k, ast.Constant) and isinstance(k.value, str):
                        encontrados[k.value] = ast.unparse(v)
    return encontrados


def test_dropdown_no_le_pasa_height():
    """ft.Dropdown no acepta height: pasarselo es TypeError al construir."""
    import flet as ft

    assert "height" not in inspect.signature(ft.Dropdown.__init__).parameters
    assert "height" in inspect.signature(ft.TextField.__init__).parameters

    kwargs = dict(_kwargs_de_llamadas(cf.__file__, "dropdown", "ft.Dropdown"))
    assert "height" not in kwargs, "dropdown() no debe pasar height a ft.Dropdown"
    # Y el campo de texto si lo lleva, que es lo que hace que la fila mida igual.
    kwargs_tf = dict(_kwargs_de_llamadas(cf.__file__, "text_field", "ft.TextField"))
    assert kwargs_tf.get("height") == "HEIGHT"


@pytest.mark.parametrize(
    "construir",
    [
        lambda: cf.dropdown("Vendedor", width=cf.WIDTH_FILTER),
        lambda: cf.dropdown("Vendedor", icon=None, search=True, editable=True, hint="x"),
        lambda: cf.dropdown("Tipo de caso", expand=True, on_change=lambda e: None),
        lambda: cf.dropdown("Vendedor", value="01188"),
        lambda: cf.text_field("Desde (dd-mm-aaaa)", width=cf.WIDTH_DATE),
        lambda: cf.text_field("% Descuento", width=cf.WIDTH_FIELD, on_change=lambda e: None),
        lambda: cf.text_field("x", keyboard=1, on_submit=lambda e: None),
        lambda: cf.search_button("Buscar cliente", None),
        lambda: cf.date_button(lambda e: None),
        lambda: cf.date_label("Desde: 01-01-2026"),
    ],
)
def test_todo_se_construye(construir):
    assert construir() is not None


# ── Simetria ──────────────────────────────────────────────────────────────────


def test_tokens_de_la_barra():
    assert cf.WIDTH_FILTER == 260  # fijado por test_geometria_del_card_de_busqueda
    assert cf.WIDTH_DATE == 180
    assert cf.RADIUS == 12
    # 38: con text_size=13 y padding 8+8 la caja pide ~37px; en 36 rozaba.


def test_inputs_comparten_caja():
    for c in (_vendedor(), cf.text_field("Desde (dd-mm-aaaa)", width=cf.WIDTH_DATE)):
        assert c.border_radius == cf.RADIUS
        assert c.dense is True
        assert c.text_size == cf.TEXT_SIZE
        assert c.content_padding == cf.CONTENT_PADDING


def test_botones_comparten_altura_con_los_campos():
    """La fila alinea porque inputs y botones miden lo mismo."""
    campo = cf.text_field("Desde (dd-mm-aaaa)", width=cf.WIDTH_DATE)
    assert campo.height == cf.HEIGHT
    assert cf.search_button("Buscar cliente", None).height == cf.HEIGHT
    assert cf.date_button(lambda e: None).height == cf.HEIGHT


def test_dos_vendedores_miden_lo_mismo():
    """El caso que reporto el usuario: el mismo control en dos secciones."""
    a = _vendedor()
    b = _vendedor(icon=None, hint="Todos los vendedores…")
    assert a.width == b.width == cf.WIDTH_FILTER
    assert a.border_radius == b.border_radius
    assert a.content_padding == b.content_padding


def test_radio_15_fuera_de_codigo():
    """El radio 15 era parte del desalineado visible entre filtros."""
    usos = []
    for f in sorted((RAIZ / "src" / "ui").rglob("*.py")) + [RAIZ / "main.py"]:
        for nodo in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if not isinstance(nodo, ast.Call):
                continue
            for kw in nodo.keywords:
                if kw.arg == "border_radius" and ast.unparse(kw.value) == "15":
                    usos.append(f"{f.name}:{kw.value.lineno}")
    assert not usos, f"quedan usos de border_radius=15 fuera de la fabrica: {usos}"


def _bloque_de_construction(ruta, atributo):
    """Nodo ast de `self.<atributo> = control_factory.dropdown(...)`.

    El nombre searched esta en el target de la asignacion; la llamada es
    `control_factory.dropdown`, asi que hay que mirar los Assign.
    """
    arbol = ast.parse((RAIZ / ruta).read_text(encoding="utf-8"))
    for nodo in ast.walk(arbol):
        if not isinstance(nodo, ast.Assign) or not isinstance(nodo.value, ast.Call):
            continue
        for tgt in nodo.targets:
            if getattr(tgt, "attr", None) == atributo:
                return nodo.value
    raise AssertionError(f"no se encontro la construccion de {atributo}() en {ruta}")


@pytest.mark.parametrize(
    "ruta,atributo",
    [
        ("src/ui/reporte_panel.py", "vend_dd"),
        ("src/ui/view_panels.py", "busq_vend_dd"),
    ],
)
def test_vendedor_de_compras_y_de_busqueda_coinciden(ruta, atributo):
    """Reporte de Compras y Busqueda: mismo ancho, mismo label, sin expand.

    Compras usaba `expand=True` (se estiraba a todo el espacio libre de la card)
    y Busqueda `width=260`, ademas con labels distintos. Dos dropdown del mismo
    tipo con medidas distintas: es la asimetria que se veia en pantalla.
    """
    nodo = _bloque_de_construction(ruta, atributo)
    kws = {kw.arg: ast.unparse(kw.value) for kw in nodo.keywords if kw.arg}

    assert kws.get("width") == "control_factory.WIDTH_FILTER", (
        f"{atributo} debe usar el ancho de la barra, no uno propio: {kws}"
    )
    assert "expand" not in kws, f"{atributo} no debe llevar expand: se estiraria"
    # El label es el primer argumento posicional de cf.dropdown(...).
    assert ast.literal_eval(nodo.args[0]) == "Vendedor (opcional)"
    assert kws.get("search") == "True"
    assert ast.literal_eval(kws["hint"]) == "Todos los vendedores\u2026"
