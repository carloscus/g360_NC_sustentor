"""El header habla con la vista MONTADA, no con una que quedó en el aire.

`reset_app` construye una `ReconocimientoView` nueva y la monta en
`body.content`, pero el header no lee `body`: lee `self.reco_view`. Sin
actualizar esa referencia, el boton "Reporte de compras" opera sobre el
container de la vista anterior, que ya no esta en el arbol. No lanza, no
avisa, no cambia de zona: el mismo sintoma que un clic que no llega, pero la
causa era de aqui.
"""

from __future__ import annotations

import flet as ft
import pytest

from src.ui.reconocimiento_view import ReconocimientoView


class _Page:
    def __init__(self):
        self.updates = 0

    def update(self):
        self.updates += 1


class _App:
    def __init__(self):
        from src.core.g360_theme import G360Theme

        self.page = _Page()
        self.G360_ACCENT = G360Theme.accent_color()
        self.G360_SUCCESS = G360Theme.ok_color()
        self.G360_WARNING = G360Theme.warning_color()
        self.G360_ERROR = G360Theme.error_color()
        self.G360_ACCENT_2 = G360Theme.ACCENT_2
        self.snacks: list[str] = []

    def show_snackbar(self, m, c=None):
        self.snacks.append(m)

    def show_loading(self, *a, **k):
        pass

    def hide_loading(self, *a, **k):
        pass

    def _pick_files(self, *a, **k):
        return []

    def _get_desktop_path(self):
        from pathlib import Path

        return Path.cwd()


def _montado(raiz, objetivo, depth=0) -> bool:
    """¿`objetivo` cuelga de `raiz`?"""
    if raiz is None or depth > 60:
        return False
    if raiz is objetivo:
        return True
    for attr in ("content", "controls", "leading", "title", "trailing", "actions"):
        v = getattr(raiz, attr, None)
        if isinstance(v, list):
            for h in v:
                if _montado(h, objetivo, depth + 1):
                    return True
        elif isinstance(v, ft.Control) and _montado(v, objetivo, depth + 1):
            return True
    return False


@pytest.fixture
def app_con_reset(tmp_db):
    """G360App con el reset ejecutado, sin levantar la ventana de verdad."""
    import main as app_main

    app = app_main.G360App.__new__(app_main.G360App)
    app.page = _Page()
    from src.core.g360_theme import G360Theme

    app.G360_ACCENT = G360Theme.accent_color()
    app.G360_SUCCESS = G360Theme.ok_color()
    app.G360_WARNING = G360Theme.warning_color()
    app.G360_ERROR = G360Theme.error_color()
    app.G360_ACCENT_2 = G360Theme.ACCENT_2
    app.snacks = []
    app.show_snackbar = lambda m, c=None: app.snacks.append(m)
    app.show_loading = lambda *a, **k: None
    app.hide_loading = lambda *a, **k: None
    app._pick_files = lambda *a, **k: []
    app._get_desktop_path = lambda: __import__("pathlib").Path.cwd()
    app._build_footer = lambda: ft.Text("footer")
    app._header = ft.Text("header")

    app.reco_view = ReconocimientoView(app)
    app.body = ft.Container(
        content=ft.Column([app.reco_view.build()], expand=True, spacing=0),
        expand=True,
    )
    return app


class TestResetDejaElHeaderHablandoConLaVistaMontada:
    def test_la_referencia_se_actualiza(self, app_con_reset):
        antes = app_con_reset.reco_view
        app_con_reset.reset_app()
        assert app_con_reset.reco_view is not antes, (
            "el header debe hablar de la vista recien montada"
        )

    def test_la_vista_del_header_esta_montada(self, app_con_reset):
        app_con_reset.reset_app()
        assert _montado(app_con_reset.body, app_con_reset.reco_view.container), (
            "la vista que el header usa tiene que estar en el arbol"
        )

    def test_el_reporte_sigue_navegando_despues_del_reset(self, app_con_reset):
        """El sintoma reportado: 'no cambia de zona' y sin error ni aviso."""
        app_con_reset.reset_app()
        app_con_reset.snacks.clear()

        app_con_reset._ir_a_reporte()

        assert app_con_reset.reco_view._vista_actual == "reporte"
        assert _montado(app_con_reset.body, app_con_reset.reco_view.container)
        assert app_con_reset.snacks == [], (
            f"no deberia avisar nada si entro bien: {app_con_reset.snacks}"
        )

    def test_volver_al_sustento_tambien_tras_el_reset(self, app_con_reset):
        app_con_reset.reset_app()
        app_con_reset._ir_a_reporte()
        assert app_con_reset.reco_view.mostrar_sustento() is True
        assert app_con_reset.reco_view._vista_actual == "sustento"
