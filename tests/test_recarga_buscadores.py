"""Tras un cambio de datos, los buscadores se recargan sin reiniciar.

La card DB se refrescaba sola, pero los dropdowns de vendedores/clientes se
cargaban una vez al arrancar: lo nuevo (de una captura intranet o un import
de cartucho) no aparecia hasta reiniciar. `_recargar_buscadores_tras_datos`
dispara ambas recargas en background al terminar esas operaciones.
"""

from pathlib import Path

import pytest

from src.core.g360_theme import G360Theme
from src.ui.reconocimiento_view import ReconocimientoView


class _App:
    page = None
    G360_ACCENT = G360Theme.accent_color()
    G360_SUCCESS = G360Theme.ok_color()
    G360_WARNING = G360Theme.warning_color()
    G360_ERROR = G360Theme.error_color()
    G360_ACCENT_2 = G360Theme.accent_2_color()

    def show_snackbar(self, *a, **k):
        pass

    def show_loading(self, *a, **k):
        pass

    def hide_loading(self, *a, **k):
        pass

    def _pick_files(self, *a, **k):
        return []

    def _get_desktop_path(self):
        return Path.cwd()


@pytest.fixture
def vista(tmp_db):
    v = ReconocimientoView(_App())
    v.build()
    return v


class TestReferenciasGuardadas:
    def test_build_guarda_ambas_recargas(self, vista):
        assert callable(getattr(vista, "_recargar_clientes_dd", None)), (
            "sin la referencia guardada no hay a quien llamar tras la captura"
        )
        assert callable(getattr(vista, "_recargar_vendedores_dd", None))

    def test_el_metodo_existe(self, vista):
        assert callable(getattr(vista, "_recargar_buscadores_tras_datos", None))


class TestDisparaAmbas:
    def test_llama_a_las_dos_en_background(self, vista, monkeypatch):
        llamadas = []

        def _spy_clientes(search=""):
            llamadas.append("clientes")

        def _spy_vendedores():
            llamadas.append("vendedores")

        monkeypatch.setattr(vista, "_recargar_clientes_dd", _spy_clientes)
        monkeypatch.setattr(vista, "_recargar_vendedores_dd", _spy_vendedores)

        import threading

        hilos_antes = threading.active_count()
        vista._recargar_buscadores_tras_datos()

        fin = __import__("time").time() + 10
        while threading.active_count() > hilos_antes and __import__("time").time() < fin:
            __import__("time").sleep(0.05)

        assert "clientes" in llamadas
        assert "vendedores" in llamadas

    def test_tolera_referencias_ausentes(self, tmp_db):
        """Si el build no llego a guardarlas (vista parcial), no debe reventar."""
        v = ReconocimientoView.__new__(ReconocimientoView)
        v._recargar_buscadores_tras_datos()  # no levanta


class TestEnganches:
    """Los dos puntos de finalizacion llaman al metodo."""

    def test_la_captura_intranet_lo_llama(self):

        src = (Path(__file__).resolve().parent.parent / "src" / "ui" / "view_panels.py").read_text(
            encoding="utf-8"
        )
        # Tiene que estar despues de un _refrescar_card_db (el patron de
        # "cambio de datos confirmado"), no en cualquier lado.
        idx_card = [i for i, l in enumerate(src.splitlines()) if "_refrescar_card_db()" in l]
        idx_rec = [
            i for i, l in enumerate(src.splitlines()) if "_recargar_buscadores_tras_datos()" in l
        ]
        assert idx_rec, "nadie llama al metodo: el fix no esta enganchado"
        # Al menos un llamado esta justo despues de un refresh de card.
        assert any(any(0 < r - c <= 6 for c in idx_card) for r in idx_rec), (
            "el llamado tiene que ir junto al refresh de la card DB"
        )

    def test_hay_al_menos_dos_enganches(self):
        """Captura intranet Y import de cartucho, no solo uno.

        Cuenta llamados, no la definicion (`def ...(self):` no termina en `()`).
        """

        src = (Path(__file__).resolve().parent.parent / "src" / "ui" / "view_panels.py").read_text(
            encoding="utf-8"
        )
        n = src.count("_recargar_buscadores_tras_datos()")
        assert n >= 2, f"solo hay {n} llamados; faltan enganches"
        assert "def _recargar_buscadores_tras_datos(self):" in src
