"""Icono de la ventana: assets/images/cipsa.ico (barra de titulo y tarea).

El acceso directo ya lo usa (create_shortcut.vbs); esto cubre la ventana en
ejecucion via page.window.icon. El helper degrada a None sin el archivo para
no tumbar el arranque.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from main import G360App, _icono_ventana  # noqa: E402


class _WinStub:
    def __init__(self):
        self.resizable = False
        self.width = 0
        self.height = 0
        self.min_width = 0
        self.min_height = 0
        self.icon = None


class _PageStub:
    def __init__(self):
        self.window = _WinStub()
        self.updated = 0

    def update(self):
        self.updated += 1


def test_icono_existe_y_es_ico_valido():
    ico = _icono_ventana()
    assert ico is not None and ico.endswith("cipsa.ico")
    data = Path(ico).read_bytes()
    assert data[:4] == b"\x00\x00\x01\x00"  # magia ICO
    assert len(data) > 1024


def test_setup_page_fija_icono_cipsa():
    app = G360App.__new__(G360App)
    app.page = _PageStub()
    app._is_dark = True
    app._setup_page()
    assert app.page.window.icon == _icono_ventana()
    assert app.page.window.icon.endswith("cipsa.ico")
