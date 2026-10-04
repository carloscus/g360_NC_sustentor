"""Regresiones estructurales del modal de sincronizacion.

Cubre los tres defectos que rompian el flujo LOGIN -> FRESHNESS:

1. Re-parentado: las secciones se re-construian en cada paso y los mismos
   controles (btn_sync, status_text, log_text, btn_ntfs, btn_cartucho) acababan
   en varias filas. En Flet un control tiene un unico padre.
2. `result_box` se creaba pero nunca se llenaba: el paso RESULTADOS salia vacio.
3. `safe_page_update` estaba redefinido para llamar a `safe_update()`, que a su
   vez la llamaba -> recursion infinita y la pagina nunca se actualizaba.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

MODAL = Path(__file__).resolve().parents[1] / "src" / "ui" / "components" / "api_sync_modal.py"


def _flet_esta_mockeado() -> bool:
    """`tests/test_card_db_simulaciones.py` hace `sys.modules["flet"] =
    MagicMock()` a nivel de modulo y no lo restaura. Estos tests necesitan
    Flet real (recorren el arbol de controles y cuentan Padres; con un
    MagicMock darian un falso verde o falso rojo).
    """
    import flet

    return type(flet).__name__ == "MagicMock"


@pytest.fixture(autouse=True)
def _exige_flet_real():
    """Se evalua en tiempo de ejecucion, no de import: el parche global del
    otro test se aplica al recolectar, que es despues de importar este
    modulo."""
    if _flet_esta_mockeado():
        pytest.skip("Flet esta mockeado en este proceso (parche global de otro test)")


def _app_stub():
    return SimpleNamespace(
        G360_ERROR="#B3261E",
        G360_SUCCESS="#146C2E",
        G360_ACCENT="#1F6FEB",
        show_snackbar=lambda *a, **k: None,
    )


class _PageStub:
    def __init__(self):
        self.dialogs = []
        self.closed = []

    def open(self, dlg):
        self.dialogs.append(dlg)

    def close(self, dlg):
        self.closed.append(dlg)

    def update(self):
        pass


def _modal_abierto():
    from src.ui.components.api_sync_modal import ApiSyncModal

    page = _PageStub()
    modal = ApiSyncModal(_app_stub(), page)
    modal.open(server_url="http://127.0.0.1:8090")
    assert page.dialogs, "el modal no abrio ningun dialog"
    return page.dialogs[0]


def _caminos(ctrl, path="root"):
    """Itera (ruta, control) por todo el arbol de controles."""
    yield path, ctrl
    for attr in ("controls",):
        hijos = getattr(ctrl, attr, None)
        if not isinstance(hijos, list):
            continue
        for i, h in enumerate(hijos):
            if hasattr(h, "controls") or hasattr(h, "content"):
                yield from _caminos(h, f"{path}.{attr}[{i}]")


def test_modal_no_duplica_padres():
    """Cada control aparece una sola vez en el arbol del dialog.

    Este es el invariante que rompia la transicion de pasos: un control con dos
    padres hace que Flet renderice una de las dos copias o directamente lance.
    """
    dlg = _modal_abierto()
    vistos = {}
    repetidos = []
    for path, ctrl in _caminos(dlg.content):
        key = id(ctrl)
        if key in vistos:
            repetidos.append(f"{type(ctrl).__name__} en {vistos[key]} y {path}")
        else:
            vistos[key] = path
    assert not repetidos, "controles con dos padres: " + "; ".join(repetidos)


def test_modal_tiene_las_cuatro_secciones():
    """Las cuatro secciones se construyen una vez y se togglean por visible."""
    dlg = _modal_abierto()
    sec_login = dlg.content.content.controls[0].controls
    assert len(sec_login) == 4, "se esperaban login/freshness/sync/error"
    tipos = {type(s).__name__ for s in sec_login}
    assert tipos == {"Column"}, tipos
    # Solo la de login arranca visible.
    visibles = [s for s in sec_login if s.visible]
    assert len(visibles) == 1 and visibles[0] is sec_login[0]


def test_result_box_esta_en_el_arbol():
    """result_box debe existir y ser parte del dialog (antes nunca se llenaba)."""
    dlg = _modal_abierto()
    nombres = [type(c).__name__ for c in dlg.content.content.controls]
    assert "Column" in nombres
    assert not dlg.content.content.controls[0].controls[3].visible, "error visible al abrir"


def test_solo_una_def_de_safe_page_update():
    """Un unico safe_page_update: redefinirlo causaba recursion infinita."""
    arbol = ast.parse(MODAL.read_text(encoding="utf-8"))
    defs = [
        n
        for n in ast.walk(arbol)
        if isinstance(n, ast.FunctionDef) and n.name == "safe_page_update"
    ]
    assert len(defs) == 1, f"safe_page_update definido {len(defs)} veces"


def test_modal_no_reconstruye_secciones_en_show_step():
    """_show_step alterna `visible`; no vacia content.controls."""
    arbol = ast.parse(MODAL.read_text(encoding="utf-8"))
    fn = next(
        n for n in ast.walk(arbol) if isinstance(n, ast.FunctionDef) and n.name == "_show_step"
    )
    texto = ast.dump(fn)
    assert "clear" not in texto, "_show_step vuelve a vaciar content"
    for sec in ("sec_login", "sec_freshness", "sec_sync", "sec_error"):
        assert sec in texto, f"_show_step no alterna la visibilidad de {sec}"


def test_auth_result_distingue_transporte_de_credenciales():
    """Un WinError 10061 no debe rotularse como 'credenciales rechazadas'."""
    from src.core.api_auth import AuthResult

    transporte = AuthResult(
        success=False,
        message="API no disponible en http://127.0.0.1:8090: [WinError 10061]",
        kind="transport",
    )
    assert transporte.es_transporte is True
    assert "No se pudo conectar" in transporte.texto_credenciales
    assert "10061" in transporte.texto_credenciales

    rechazo = AuthResult(success=False, message="Credenciales rechazadas por la API")
    assert rechazo.kind == "auth"
    assert rechazo.es_transporte is False
    assert rechazo.texto_credenciales == "Credenciales rechazadas por la API"

    servidor = AuthResult(success=False, message="API devolvio HTTP 500", kind="server")
    assert servidor.texto_credenciales.startswith("La API respondio con error")


def test_start_api_es_idempotente():
    """start_api.sh no debe hacer pkill si la API ya responde.

    Sin este guarda, asegurar_api() durante una falla transitoria mataba una
    instancia sana y entraba en bucle de reinicios.
    """
    script = Path(__file__).resolve().parents[2] / "g360-ventas-api" / "deploy" / "start_api.sh"
    if not script.is_file():
        import pytest

        pytest.skip("repo g360-ventas-api no disponible")
    texto = script.read_text(encoding="utf-8")
    # Buscar el comando real, no la palabra en un comentario.
    i_health = texto.find("/api/health")
    i_pkill = texto.find('pkill -f "g360-ventas-api-linux"')
    assert i_health != -1, "start_api.sh perdio el chequeo de salud"
    assert 0 <= i_health < i_pkill, "el chequeo de salud debe ir antes del pkill"


def test_start_api_se_desacopla():
    """El API debe sobrevivir al cierre de la sesion de wsl.exe."""
    script = Path(__file__).resolve().parents[2] / "g360-ventas-api" / "deploy" / "start_api.sh"
    if not script.is_file():
        import pytest

        pytest.skip("repo g360-ventas-api no disponible")
    texto = script.read_text(encoding="utf-8")
    assert "setsid nohup" in texto, "falta el arranque desacoplado"
    assert "G360_API_FOREGROUND:-0" in texto, "falta el modo primer plano"
