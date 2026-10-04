"""Comandos de recuperación para el API Go de ventas.

Cubre los casos donde el sync falla y el cliente necesita acciones de emergencia.
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


@dataclass
class RecoveryResult:
    success: bool
    action: str
    detail: str
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Trigger refresh del snapshot en el servidor
# ---------------------------------------------------------------------------


def trigger_refresh_snapshot(server_url: str = "") -> RecoveryResult:
    """Dispara el refresh del snapshot en el servidor.

    Como los clientes no tienen WSL, este comando:
    1. Intenta conectar al servidor y verificar si es accesible.
    2. Si el servidor es local (máquina con WSL), ejecuta refresh_snapshot.cmd.
    3. Si el servidor es remoto, retorna instrucciones para el admin.

    Returns:
        RecoveryResult con success=True si se ejecutó, o False con instrucciones.
    """
    url = server_url or os.getenv("G360_API_URL", "http://127.0.0.1:8090").rstrip("/")

    # ¿Es esta máquina el servidor?
    if _is_local_server():
        return _run_refresh_local()
    else:
        return RecoveryResult(
            success=False,
            action="trigger_refresh",
            detail=(
                f"Esta máquina es cliente, no tiene WSL. "
                f"El servidor está en {url}. "
                f"Instrucciones para el admin:\n"
                f"  1. En el servidor ejecutar:\n"
                f"     cd <repo-g360-ventas-api>/deploy\n"
                f"     cmd.exe /c refresh_snapshot.cmd\n"
                f"  2. O programar tarea periódica (12:15 y 20:15)."
            ),
            error=None,
        )


def _is_local_server() -> bool:
    """Detecta si esta máquina tiene WSL + repo de la API."""
    try:
        r = subprocess.run(["wsl", "--list", "--quiet"], capture_output=True, timeout=5)
        if r.returncode != 0:
            return False
    except Exception:
        return False
    api_dir = os.getenv("G360_VENTAS_API_DIR")
    if api_dir:
        return Path(api_dir, "deploy", "start_api.sh").is_file()
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "g360-ventas-api"
        if (candidate / "deploy" / "start_api.sh").is_file():
            return True
    return False


def _run_refresh_local() -> RecoveryResult:
    """Ejecuta refresh_snapshot.cmd localmente."""
    api_project = _find_api_project()
    if not api_project:
        return RecoveryResult(
            success=False,
            action="refresh",
            detail="",
            error="No se encontró el repo g360-ventas-api",
        )
    cmd = api_project / "deploy" / "refresh_snapshot.cmd"
    if not cmd.is_file():
        return RecoveryResult(success=False, action="refresh", detail="", error=f"No existe {cmd}")
    try:
        log.info("Iniciando refresh del snapshot...")
        t0 = time.time()
        r = subprocess.run(
            ["cmd.exe", "/c", str(cmd)],
            capture_output=True,
            text=True,
            timeout=600,
        )
        elapsed = round(time.time() - t0, 1)
        if r.returncode == 0:
            return RecoveryResult(
                success=True,
                action="refresh",
                detail=f"Refresh completado en {elapsed}s",
            )
        else:
            return RecoveryResult(
                success=False,
                action="refresh",
                detail=r.stdout[-500:] if r.stdout else "",
                error=f"Exit code {r.returncode}: {r.stderr[-200:] if r.stderr else ''}",
            )
    except subprocess.TimeoutExpired:
        return RecoveryResult(
            success=False,
            action="refresh",
            detail="",
            error="Timeout (600s) — el refresh está tardando demasiado",
        )
    except Exception as e:
        return RecoveryResult(success=False, action="refresh", detail="", error=str(e))


def _find_api_project() -> Optional[Path]:
    api_dir = os.getenv("G360_VENTAS_API_DIR")
    if api_dir:
        p = Path(api_dir)
        return p if (p / "deploy" / "start_api.sh").is_file() else None
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "g360-ventas-api"
        if (candidate / "deploy" / "start_api.sh").is_file():
            return candidate
    return None


# ---------------------------------------------------------------------------
# Restaurar DB local desde backup/cartucho
# ---------------------------------------------------------------------------


def restore_from_backup(db_path: Optional[Path] = None) -> RecoveryResult:
    """Restaura la DB local desde el último cartucho disponible.

    Busca en:
    1. %APPDATA%/g360-erp-nc-sustentor/data/export/ (cartuchos locales)
    2. Rutas UNC configuradas en db_origen
    """
    from src.core import ventas_db

    if db_path is None:
        db_path = ventas_db.db_path()

    # Buscar cartuchos previos
    export_dir = db_path.parent / "export"
    candidates = []
    if export_dir.is_dir():
        for f in sorted(
            export_dir.glob("cartucho-*.zip"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        ):
            candidates.append(f)

    if not candidates:
        return RecoveryResult(
            success=False, action="restore", detail="", error=f"No hay cartuchos en {export_dir}"
        )

    return RecoveryResult(
        success=True,
        action="restore",
        detail=(
            f"Se encontraron {len(candidates)} cartucho(s). "
            f"El más reciente: {candidates[0].name} "
            f"(modificado {datetime.fromtimestamp(candidates[0].stat().st_mtime).strftime('%Y-%m-%d %H:%M')}).\n"
            f"Para restaurar: usar el menú 'Configuración → Gestión → Importar cartucho'."
        ),
    )


# ---------------------------------------------------------------------------
# Force sync (ignora frescura del snapshot)
# ---------------------------------------------------------------------------


def force_sync(server_url: str = "", user: str = "", password: str = "") -> RecoveryResult:
    """Sync incremental forzado, ignorando frescura del snapshot.

    Útil cuando el snapshot está muy viejo pero se sabe que la data es confiable.
    Requiere credenciales de intranet.
    """
    from src.core import ventas_db
    from src.core.api_auth import APIAuthClient
    from src.core.sync_api import SyncAPI
    from src.core.ventas_api_client import VentaAPIClient

    url = server_url or os.getenv("G360_API_URL", "http://127.0.0.1:8090").rstrip("/")

    # Login
    auth_cli = APIAuthClient(url)
    if user and password:
        login_result = auth_cli.login(user, password)
    else:
        # Intentar con token cached
        login_result = _try_cached_token(auth_cli)

    if not login_result.success:
        return RecoveryResult(
            success=False,
            action="force_sync",
            detail="",
            error=f"Login falló: {login_result.message}",
        )

    # Sync
    try:
        cli = VentaAPIClient(base_url=url, api_token=login_result.token)
        sync = SyncAPI(cli)
        conn = ventas_db.connect()
        try:
            desde = (datetime.now() - __import__("datetime").timedelta(days=7)).strftime("%Y-%m-%d")
            hasta = datetime.now().strftime("%Y-%m-%d")
            result = sync.aplicar(desde, hasta, conn=conn, usar_dias=False)
        finally:
            conn.close()
        cli.close()

        return RecoveryResult(
            success=result.get("estado") == "ok",
            action="force_sync",
            detail=(
                f"{result.get('filas', 0):,} filas en "
                f"{len(result.get('dias', []))} días "
                f"({result.get('segundos', 0):.1f}s)"
            ),
        )
    except Exception as e:
        return RecoveryResult(
            success=False,
            action="force_sync",
            detail="",
            error=str(e)[:500],
        )


def _try_cached_token(auth_cli) -> Any:
    """Intenta usar token cached sin pedir credenciales."""
    try:
        from src.core.capture_service import CaptureService

        token = CaptureService.api_token()
        if token and CaptureService.is_api_token_valid():
            auth_cli._token = token
            return type("AuthResult", (), {"success": True, "token": token})()
    except Exception:
        pass
    return type("AuthResult", (), {"success": False, "message": "no token cached"})()


# ---------------------------------------------------------------------------
# Escaneo de red para encontrar servidor
# ---------------------------------------------------------------------------


def find_server_on_network(subnet: Optional[str] = None) -> List[Dict[str, Any]]:
    """Escanea la red local buscando el API en :8090."""
    import subprocess as _sp

    if subnet is None:
        # Detectar subnet local
        try:
            r = _sp.run(["ipconfig"], capture_output=True, text=True, timeout=5)
            for line in r.stdout.splitlines():
                if "IPv4" in line or "Address" in line:
                    parts = line.strip().split()
                    for p in parts:
                        if "." in p and all(c.isdigit() or c == "." for c in p):
                            octets = p.split(".")
                            if len(octets) == 4:
                                subnet = ".".join(octets[:3])
                                break
                if subnet:
                    break
        except Exception:
            subnet = "192.168.1"

    if not subnet:
        return []

    results = []
    log.info(f"Escaneando {subnet}.x en busca de API...")
    for i in range(1, 255):
        ip = f"{subnet}.{i}"
        try:
            with socket.create_connection((ip, 8090), timeout=0.5):
                try:
                    import httpx

                    resp = httpx.get(f"http://{ip}:8090/api/health", timeout=3)
                    if resp.status_code == 200 and resp.json().get("status") == "ok":
                        results.append(
                            {
                                "ip": ip,
                                "url": f"http://{ip}:8090",
                                "healthy": True,
                                "db": resp.json().get("db", ""),
                            }
                        )
                except Exception:
                    results.append({"ip": ip, "url": f"http://{ip}:8090", "healthy": False})
        except OSError:
            pass
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Recuperación de API G360")
    sub = parser.add_subparsers(dest="command", required=True)

    p_refresh = sub.add_parser("refresh", help="Forzar refresh del snapshot")
    p_refresh.add_argument("--url", default="", help="URL del servidor")

    p_restore = sub.add_parser("restore", help="Restaurar DB desde cartucho")
    p_restore.add_argument("--db", default="", help="Ruta de la DB local")

    p_sync = sub.add_parser("sync", help="Sync forzado ignorando frescura")
    p_sync.add_argument("--url", default="", help="URL del servidor")
    p_sync.add_argument("--user", default="", help="Usuario intranet")
    p_sync.add_argument("--password", default="", help="Contraseña intranet")

    p_find = sub.add_parser("find-server", help="Buscar servidor en la red")
    p_find.add_argument("--subnet", default="", help="Subnet a escanear")

    args = parser.parse_args()

    if args.command == "refresh":
        r = trigger_refresh_snapshot(args.url)
    elif args.command == "restore":
        db_path = Path(args.db) if args.db else None
        r = restore_from_backup(db_path)
    elif args.command == "sync":
        r = force_sync(args.url, args.user, args.password)
    elif args.command == "find-server":
        found = find_server_on_network(args.subnet or None)
        if found:
            print(f"Servidores encontrados ({len(found)}):")
            for s in found:
                status = "OK" if s["healthy"] else "NO RESPONDE"
                print(f"  {s['url']}  [{status}]  DB: {s.get('db', '?')}")
        else:
            print("No se encontró ningún servidor con API en :8090.")
        return 0 if found else 1
        return 0

    print(f"{'✓' if r.success else '✗'} [{r.action}] {r.detail}")
    if r.error:
        print(f"Error: {r.error}")
    return 0 if r.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
