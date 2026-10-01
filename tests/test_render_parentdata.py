"""Regresion de parent data de Flutter.

Un hijo con expand=True dentro de ft.Row(wrap=True) hace que Flet lo envuelva
en Flexible/Expanded. Flutter castea WrapParentData a FlexParentData y el
render falla miles de veces por segundo, dejando la ventana en blanco/gris.
"""

from __future__ import annotations

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


def _hijos(control: ft.Control) -> list[ft.Control]:
    out: list[ft.Control] = []
    for attr in ("controls", "content"):
        ch = getattr(control, attr, None)
        if isinstance(ch, ft.Control):
            out.append(ch)
        elif isinstance(ch, (list, tuple)):
            out.extend(x for x in ch if isinstance(x, ft.Control))
    return out


def _recorrer(raiz: ft.Control):
    pila = [raiz]
    while pila:
        ctrl = pila.pop()
        yield ctrl
        pila.extend(_hijos(ctrl))


def _tarjeta(raiz: ft.Control, titulo: str) -> ft.Control | None:
    """Devuelve la seccion completa (encabezado + cuerpo).

    workflow_section arma Column[Row[icon, Column[Text(titulo), Text(desc)]], body].
    Devolver solo el Row del titulo dejaria fuera el cuerpo, que es donde viven
    las filas con wrap.
    """
    for col in (c for c in _recorrer(raiz) if isinstance(c, ft.Column)):
        for row in (h for h in _hijos(col) if isinstance(h, ft.Row)):
            for sub in _hijos(row):
                if not isinstance(sub, ft.Column):
                    continue
                textos = {t.value for t in _recorrer(sub) if isinstance(t, ft.Text)}
                if titulo in textos:
                    return col
    return None


def _expand_en_wrap(raiz: ft.Control) -> list[str]:
    """Etiquetas de hijos expandidos dentro de Rows con wrap=True."""
    fallos: list[str] = []
    for ctrl in _recorrer(raiz):
        if not (isinstance(ctrl, ft.Row) and ctrl.wrap):
            continue
        for h in _hijos(ctrl):
            if getattr(h, "expand", False):
                marca = getattr(h, "text", None) or getattr(h, "label", None) or ""
                fallos.append(f"{type(h).__name__}({str(marca)[:32]!r})")
    return fallos


def test_datos_del_caso_no_expande_hijos_en_filas_wrap(tmp_db):
    view = ReconocimientoView(_AppStub())
    arbol = view.build()
    card = _tarjeta(arbol, "Datos del caso")

    assert card is not None, "no se encontro la card 'Datos del caso'"
    assert _expand_en_wrap(card) == []


def test_insumos_no_expande_hijos_en_filas_wrap(tmp_db):
    view = ReconocimientoView(_AppStub())
    arbol = view.build()
    card = _tarjeta(arbol, "Insumos")

    assert card is not None, "no se encontro la card 'Insumos'"
    assert _expand_en_wrap(card) == []


def test_ninguna_seccion_expande_hijos_en_filas_wrap(tmp_db):
    """Cobertura global: evita que el bug reaparezca en otra seccion."""
    view = ReconocimientoView(_AppStub())
    arbol = view.build()

    assert _expand_en_wrap(arbol) == []
