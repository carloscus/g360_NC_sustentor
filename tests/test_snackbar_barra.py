"""Barra de mensajes inferior: icono por tipo, cola en vez de pisar.

Antes: sin icono (solo fondo), texto sin color fijo (ilegible en tema
claro), y cada `show_snackbar` reemplazaba al anterior al instante — por
eso el export metia un `sleep(0.8)` a mano entre avisos. Ahora el segundo
espera su turno y el ACCENT informativo lleva icono info, no tilde.
"""

import time

import pytest

from src.core.g360_theme import G360Theme


class _Page:
    def __init__(self):
        self.overlay = []
        self.updates = 0

    def update(self):
        self.updates += 1


def _app():
    import main as M

    app = M.G360App.__new__(M.G360App)
    app.page = _Page()
    app.G360_ACCENT = G360Theme.accent_color()
    app.G360_SUCCESS = G360Theme.ok_color()
    app.G360_WARNING = G360Theme.warning_color()
    app.G360_ERROR = G360Theme.error_color()
    return app


def _texto(sb) -> str:
    return sb.content.controls[1].value


def _icono(sb) -> str:
    return sb.content.controls[0].name.value


class TestIconoPorTipo:
    def test_success_lleva_tilde(self):
        app = _app()
        app.show_snackbar("todo bien", app.G360_SUCCESS)
        assert _icono(app._snackbar) == "check_circle_outlined"

    def test_error_warning_info_distintos(self):
        app = _app()
        casos = [
            (app.G360_ERROR, "error_outline"),
            (app.G360_WARNING, "warning_amber_outlined"),
            (app.G360_ACCENT, "info_outlined"),
        ]
        for color, esperado in casos:
            a = _app()
            a.show_snackbar("x", color)
            assert _icono(a._snackbar) == esperado, color

    def test_accent_es_informativo_no_exito(self):
        """El ACCENT (progreso, avisos neutros) no lleva tilde."""
        app = _app()
        app.show_snackbar("Creando cartucho...", app.G360_ACCENT)
        assert _icono(app._snackbar) == "info_outlined"

    def test_icono_explicito_manda(self):
        import flet as ft

        app = _app()
        app.show_snackbar("x", app.G360_SUCCESS, icon=ft.Icons.ROCKET_LAUNCH)
        assert _icono(app._snackbar) == "rocket_launch"


class TestTextoLegible:
    @pytest.mark.parametrize("color_attr", ["G360_SUCCESS", "G360_WARNING", "G360_ERROR"])
    def test_texto_blanco_en_fondos_saturados(self, color_attr):
        app = _app()
        app.show_snackbar("x", getattr(app, color_attr))
        txt = app._snackbar.content.controls[1]
        assert (txt.color or "").upper() == "#FFFFFF"


class TestCola:
    def test_el_segundo_espera_no_pisa(self):
        app = _app()
        app.show_snackbar("primero", app.G360_SUCCESS)
        app.show_snackbar("segundo", app.G360_ERROR)
        assert _texto(app._snackbar) == "primero"
        assert len(app._snack_queue) == 1

    def test_al_terminar_sale_el_segundo(self):
        app = _app()
        app.show_snackbar("primero", app.G360_SUCCESS)
        app.show_snackbar("segundo", app.G360_ERROR)
        fin = time.time() + 10
        while _texto(app._snackbar) != "segundo" and time.time() < fin:
            time.sleep(0.1)
        assert _texto(app._snackbar) == "segundo"
        assert _icono(app._snackbar) == "error_outline"

    def test_llamada_vieja_sigue_funcionando(self):
        """Compatibilidad: los ~100 llamados con (msg, color) no se tocan."""
        app = _app()
        app.show_snackbar("hola")
        assert _texto(app._snackbar) == "hola"
        assert app._snackbar.open is True
