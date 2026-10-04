"""Despierta la API de ventas cuando el servidor no responde.

El API (`g360-ventas-api`) corre dentro de WSL2 y su proceso Go **no sobrevive**
al apagado de la distro: WSL para los procesos al cerrarse, asi que tras un
reinicio de Windows el puerto queda muerto. El forwarder de Windows (0.0.0.0:8090)
sigue escuchando, pero sin upstream: acepta la conexion y la cierra a los ~2 s.
Por eso "esta caido" casi siempre significa "WSL esta detenido".

Este modulo comprueba `/api/health` y, si falla, arranca la API dentro de WSL y
espera a que responda. Es idempotente: si ya responde, no toca nada.

Importante: despertar NO implica frescura. La API sirve un snapshot en ext4
(`~/g360data/historial.db`) que solo se actualiza con
`deploy/refresh_snapshot.ps1`. Despertar da disponibilidad, no data nueva.
"""

from __future__ import annotations

import logging
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger(__name__)

DISTRO_WSL = "Ubuntu"
_PUERTO_WSL = "8091"
_PUERTO_WINDOWS = "8090"


# FLETE / G360 (env sobreescribible para tests y para maquinas distintas)
def _env(nombre: str, por_defecto: str = "") -> str:
    import os

    return os.environ.get(nombre, por_defecto)


def proyecto_ventas_api() -> Optional[Path]:
    """Ruta del repo `g360-ventas-api`, o None si no se encuentra.

    Busca primero la variable `G360_VENTAS_API_DIR`; si no, sube desde este
    archivo buscando un repo hermano con `deploy/start_api.sh`.
    """
    explicita = _env("G360_VENTAS_API_DIR")
    if explicita:
        p = Path(explicita)
        return p if (p / "deploy" / "start_api.sh").is_file() else None
    aqui = Path(__file__).resolve()
    for padre in aqui.parents:
        for hijo in padre.iterdir() if padre.is_dir() else []:
            if hijo.name == "g360-ventas-api" and (hijo / "deploy" / "start_api.sh").is_file():
                return hijo
    return None


def _ruta_wsl(p: Path) -> str:
    """Convierte una ruta Windows al formato /mnt/c/... que entiende WSL."""
    return "/mnt/" + str(p).replace("\\", "/")[0].lower() + str(p).replace("\\", "/")[2:]


def _health(url: str, timeout: float) -> Optional[dict]:
    """GET {url}/api/health. None si no responde (API caida o sin upstream).

    Importante pegarle al path exacto y no a la raiz: la raiz responde 200 con
    el indice de rutas, asi que consultarla daria un falso "la API esta viva".
    """
    import httpx

    try:
        r = httpx.get(f"{url.rstrip('/')}/api/health", timeout=timeout)
        if r.status_code != 200:
            return None
        data = r.json()
        # status=ok es lo que distingue /api/health de cualquier otra respuesta
        # 200; sin esto, un proxy o la raiz con el indice de rutas cuelan igual.
        if not isinstance(data, dict) or data.get("status") != "ok":
            return None
        return data
    except Exception:
        return None


def puerto_escucha(host: str, puerto: int) -> bool:
    """True si algo acepta TCP en host:puerto.

    Distingue "no hay quien escuche" (forwarder caido) de "escucha pero sin
    upstream" (forwarder vivo, API WSL dormida): el primero hay que
    relanzar tambien, el segundo solo despertar la API.
    """
    try:
        with socket.create_connection((host, puerto), timeout=1.5):
            return True
    except OSError:
        return False


def asegurar_api(
    url: str = "",
    timeout_health: float = 5.0,
    espera_max_s: float = 60.0,
    lanzar=None,
    dormir: Callable[[float], None] = time.sleep,
    proyecto: Optional[Path] = None,
    escucha: Optional[Callable[[], bool]] = None,
    lanzar_fwd=None,
) -> dict:
    """Devuelve la API operativa, levantandola si hace falta.

    Returns {ok, db, arrancada, segundos, detalle, proyecto}.
    `arrancada=False` significa que ya estaba arriba y no se hizo nada.
    """
    url = url or f"http://127.0.0.1:{_PUERTO_WINDOWS}"
    lanzar = lanzar or _lanzar_en_wsl
    lanzar_fwd = lanzar_fwd or lanzar_forwarder
    escucha = escucha or (
        lambda: puerto_escucha(_env("G360_API_HOST", "127.0.0.1"), _PUERTO_WINDOWS)
    )
    t0 = time.time()

    salud = _health(url, timeout_health)
    if salud is not None:
        return {
            "ok": True,
            "db": salud.get("db", ""),
            "arrancada": False,
            "segundos": 0.0,
            "detalle": "ya estaba arriba",
            "proyecto": str(proyecto or ""),
        }

    proyecto = proyecto or proyecto_ventas_api()
    if proyecto is None:
        return {
            "ok": False,
            "db": "",
            "arrancada": False,
            "segundos": round(time.time() - t0, 1),
            "detalle": "no se encontro el repo g360-ventas-api (define G360_VENTAS_API_DIR)",
            "proyecto": "",
        }

    acciones = []
    # El forwarder de Windows es un proceso mas que se muere con la sesion.
    # Si nada escucha en 8090 no hay a quien preguntar por la API: hay que
    # relanzar los dos (forwarder primero, para que este listening cuando la
    # API empiece a responder).
    if not escucha():
        acciones.append(lanzar_fwd(proyecto))
        dormir(1.0)

    acciones.append(lanzar(proyecto))
    detalle = " + ".join(acciones)
    log.info("api_wake: levantando (%s): %s", proyecto.name, detalle)

    # La API tarda unos segundos en abrir el snapshot de 2.5 GB: se prueba
    # health en bucle hasta que responda o se agote la espera.
    restante = max(1.0, espera_max_s)
    paso = 1.5
    while restante > 0:
        dormir(min(paso, restante))
        restante -= paso
        salud = _health(url, timeout_health)
        if salud is not None:
            return {
                "ok": True,
                "db": salud.get("db", ""),
                "arrancada": True,
                "segundos": round(time.time() - t0, 1),
                "detalle": f"levantada ({detalle})",
                "proyecto": str(proyecto),
            }

    return {
        "ok": False,
        "db": "",
        "arrancada": False,
        "segundos": round(time.time() - t0, 1),
        "detalle": (
            f"no respondio en {espera_max_s:.0f}s (se lanzo: {detalle}). "
            "Revisa: wsl -l -v (Ubuntu debe estar Running) y "
            "wsl -d Ubuntu -- tail -20 /home/ccusi/g360data/api.log"
        ),
        "proyecto": str(proyecto),
    }


def lanzar_forwarder(proyecto: Path) -> str:
    """Relanza `deploy/forwarder.py` en Windows, desacoplado.

    Sin esto, con WSL despierto pero el forwarder muerto el puerto 8090 no
    acepta nada y la UI no tiene por donde entrar. Se lanza con el mismo
    interprete que corre esta app para no depender del PATH.
    """
    script = proyecto / "deploy" / "forwarder.py"
    if not script.is_file():
        raise RuntimeError(f"no existe {script}")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(  # noqa: S603 — comando fijo, sin shell, sin input
        [sys.executable, str(script), str(_PUERTO_WINDOWS)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )
    return f"forwarder :{_PUERTO_WINDOWS}"


def _lanzar_en_wsl(proyecto: Path) -> str:
    """Arranca `deploy/start_api.sh` dentro de WSL, desacoplado.

    Se usa `subprocess.Popen` con CREATE_NO_WINDOW y sin esperar: el proceso
    sigue vivo aunque este script termine. Es lo mismo que hace
    `refresh_snapshot.ps1`, pero sin refescar el snapshot (arrancar es barato,
    refrescar toma ~2.5 min).
    """
    if shutil.which("wsl") is None:
        raise RuntimeError("wsl.exe no encontrado en PATH")

    script = _ruta_wsl(proyecto / "deploy" / "start_api.sh")
    flags = 0
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        flags = subprocess.CREATE_NO_WINDOW
    subprocess.Popen(  # noqa: S603 — comando fijo, sin shell, sin input
        ["wsl", "-d", _env("G360_WSL_DISTRO", DISTRO_WSL), "--", "bash", script, _PUERTO_WSL],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )
    return f"wsl -d {_env('G360_WSL_DISTRO', DISTRO_WSL)} -- bash {script}"
