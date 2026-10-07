"""Regresion: el panel de lineas de producto activas debe construir sin error.

`view_panels._panel_lineas` llama `ventas_db.actividad_anio()` (y sus vecinas)
para el dropdown del piso de actividad. Esas funciones viven en
`ventas_db_config` y `ventas_db` las reexporta; si falta alguna, el panel
revienta con AttributeError y queda sin construir ("config panel 1 fallo").
"""

import flet as ft

from src.core import ventas_db
from src.ui.reconocimiento_view import ReconocimientoView
from tests.test_render_parentdata import _AppStub


class _Page:
    """Page minima: el panel arma su worker en un hilo que termina en update()."""

    def update(self):
        return None

    def add(self, *a, **k):
        return None

    def run_task(self, *a, **k):
        return None

    def go(self, *a, **k):
        return None


def _dropdowns(ctrl):
    out = []
    pila = [ctrl]
    while pila:
        c = pila.pop()
        if isinstance(c, ft.Dropdown):
            out.append(c)
        for attr in ("controls", "content"):
            ch = getattr(c, attr, None)
            if isinstance(ch, ft.Control):
                pila.append(ch)
            elif isinstance(ch, (list, tuple)):
                pila.extend(x for x in ch if isinstance(x, ft.Control))
    return out


def test_ventas_db_reexporta_el_piso_de_actividad():
    """Los nombres que usa el panel existen en el modulo que el panel importa."""
    for nombre in (
        "DEFAULT_ACTIVIDAD_DESDE",
        "actividad_desde",
        "actividad_anio",
        "set_actividad_anio",
    ):
        assert hasattr(ventas_db, nombre), f"ventas_db no expone {nombre}"

    assert ventas_db.actividad_desde() >= "2021-01-01"
    assert isinstance(ventas_db.actividad_anio(), int)
    assert ventas_db.actividad_anio() == int(ventas_db.actividad_desde()[:4])


def test_panel_de_lineas_se_construye(tmp_db):
    """Construir el panel de allowlist no debe levantar AttributeError."""
    view = ReconocimientoView(_AppStub())
    view.build()
    panel = view._panel_lineas(_Page())
    assert panel is not None

    dds = _dropdowns(panel)
    assert dds, "el panel no traia ningun dropdown"
    piso = next(
        (d for d in dds if "visibles desde" in str(d.label or "")),
        None,
    )
    assert piso is not None, [str(d.label) for d in dds]
    assert str(piso.value) == str(ventas_db.actividad_anio())
