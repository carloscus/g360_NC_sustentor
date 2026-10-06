"""Sin API en la UI: el modal de sincronizacion se elimino.

El unico camino de actualizacion es intranet (`_actualizar_hoy` delega a
`_iniciar_descarga` -> `CaptureService.update_from_last`), mas copia manual
Tauri y cartucho. El backend API (`api_auth`, `sync_api`, `ventas_api_client`)
queda en cuarentena, sin entry points en la UI.

Estos tests impiden que el modal vuelva a entrar por accidente.
"""

from __future__ import annotations

from pathlib import Path

MODAL = Path(__file__).resolve().parents[1] / "src" / "ui" / "components" / "api_sync_modal.py"
VIEW_PANELS = Path(__file__).resolve().parents[1] / "src" / "ui" / "view_panels.py"


def test_modal_api_no_existe():
    """El archivo del modal API se elimino."""
    assert not MODAL.exists(), "api_sync_modal.py volvio a aparecer"


def test_view_panels_sin_referencias_api():
    """La UI no referencia el modal ni el sync API (solo en comentarios)."""
    lineas = [
        ln for ln in VIEW_PANELS.read_text(encoding="utf-8").splitlines() if ln.strip()[:1] != "#"
    ]
    # También excluyo docstrings: líneas que empiezan con `"""` o contienen solo texto entre comillas.
    codigo = "\n".join(ln for ln in lineas if not ln.strip().startswith('"""'))
    for prohibido in ("ApiSyncModal(", "api_sync_modal(", "btn_sync_api", "sincronizar_api"):
        assert prohibido not in codigo, f"referencia API en UI: {prohibido}"


def test_actualizar_hoy_delega_a_intranet():
    """`_actualizar_hoy` usa intranet, no API."""
    import ast

    arbol = ast.parse(VIEW_PANELS.read_text(encoding="utf-8"))
    fn = next(
        n for n in ast.walk(arbol) if isinstance(n, ast.FunctionDef) and n.name == "_actualizar_hoy"
    )
    texto = ast.dump(fn)
    assert "_iniciar_descarga" in texto, "_actualizar_hoy no delega a intranet"


def test_actualizar_hoy_no_abre_modal():
    """Delegacion real: con page sin API, llama a `_iniciar_descarga`."""
    from types import SimpleNamespace

    from src.ui.view_panels import _ViewPanels

    v = _ViewPanels.__new__(_ViewPanels)
    v.app = SimpleNamespace(page=SimpleNamespace())
    llamado = {}

    def fake_descarga(page):
        llamado["page"] = page

    v._iniciar_descarga = fake_descarga
    v._actualizar_hoy(None)
    assert llamado.get("page") is v.app.page


def test_auth_result_distingue_transporte_de_credenciales():
    """El backend de auth sigue intacto (en cuarentena, no eliminado)."""
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
    i_pkill = texto.find("pkill -f 'g360-ventas-api-lin[u]x'")
    assert i_health != -1, "start_api.sh perdio el chequeo de salud"
    assert 0 <= i_health < i_pkill, "el chequeo de salud debe ir antes del pkill"


def test_start_api_no_se_mata_a_si_mismo():
    """`pkill -f g360-ventas-api` mataba al shell que lo invocaba.

    El patron tiene que llevar el truco `lin[u]x`: coincide con el binario real
    pero no con la linea de comando que invoca el script (que si no, se mataria
    a si mismo y el API nunca relanzaria).
    """
    script = Path(__file__).resolve().parents[2] / "g360-ventas-api" / "deploy" / "start_api.sh"
    if not script.is_file():
        import pytest

        pytest.skip("repo g360-ventas-api no disponible")
    texto = script.read_text(encoding="utf-8")
    comandos = [
        ln.strip() for ln in texto.splitlines() if "pkill" in ln and not ln.strip().startswith("#")
    ]
    assert comandos, "start_api.sh deberia tener un pkill"
    for cmd in comandos:
        assert "lin[u]x" in cmd, f"pkill sin el truco lin[u]x (se auto-mata): {cmd}"


def test_start_api_se_desacopla():
    """El API debe sobrevivir al cierre de la sesion de wsl.exe."""
    script = Path(__file__).resolve().parents[2] / "g360-ventas-api" / "deploy" / "start_api.sh"
    if not script.is_file():
        import pytest

        pytest.skip("repo g360-ventas-api no disponible")
    texto = script.read_text(encoding="utf-8")
    assert "setsid nohup" in texto, "falta el arranque desacoplado"
    assert "G360_API_FOREGROUND:-0" in texto, "falta el modo primer plano"
