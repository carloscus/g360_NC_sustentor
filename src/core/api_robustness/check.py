"""Diagnóstico de robustez del API Go de ventas.

Cubre dos roles:
  - Servidor: WSL, API, forwarder, snapshot frescura, backups, errores.
  - Cliente:  HTTP al servidor, token, frescura local vs remoto.

Uso desde terminal:
  python -m src.core.api_robustness check
  python -m src.core.api_robustness check --json
  python -m src.core.api_robustness check --server
  python -m src.core.api_robustness check --client
"""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ServerState:
    """Estado del lado servidor (máquina con WSL + API)."""

    wsl_running: bool = False
    api_pid: Optional[int] = None
    api_process_name: Optional[str] = None
    api_8091_health: Optional[Dict[str, Any]] = None
    forwarder_8090_reachable: bool = False
    snapshot_path: str = ""
    snapshot_size_mb: float = 0.0
    snapshot_mtime: Optional[str] = None
    ntfs_derived_path: str = ""
    ntfs_derived_mtime: Optional[str] = None
    snapshot_desfase_horas: float = 0.0
    backup_count: int = 0
    backup_total_mb: float = 0.0
    api_log_errors_24h: int = 0
    notes: List[str] = field(default_factory=list)


@dataclass
class ClientState:
    """Estado del lado cliente (solo HTTP, sin WSL)."""

    server_url: str = ""
    http_8090_reachable: bool = False
    health_ok: bool = False
    health_response_time_s: float = 0.0
    token_valid: bool = False
    token_age_hours: float = 0.0
    status_filas: int = 0
    status_capturado_en: Optional[str] = None
    status_fecha_hasta: Optional[str] = None
    local_filas: int = 0
    local_fecha_max: Optional[str] = None
    # Antigüedad del snapshot: horas desde capturado_en hasta ahora.
    # No se compara contra fecha_max local (mezclaría hora de captura con
    # fecha de venta). Los días que le faltan a local van en dias_desfasados.
    desfase_horas: float = 0.0
    dias_desfasados: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


@dataclass
class DiagnoseResult:
    """Resultado completo del diagnóstico."""

    is_server: bool = False
    server: Optional[ServerState] = None
    client: Optional[ClientState] = None
    summary: str = ""
    severity: str = "ok"  # ok | warning | critical
    recommendations: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _parse_iso(s: str) -> Optional[datetime]:
    from datetime import timezone

    if not s:
        return None
    txt = s.strip()
    # Normalizar fracciones con mas de 6 digitos (stat de Linux da nanosegundos)
    # y sufijos comunes: 'Z', '+00:00', '-0500', ' UTC', ''.
    m = __import__("re").match(
        r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})(?:\.(\d+))?\s*(Z|[+-]\d{2}:?\d{2}|[A-Z]{2,5})?$",
        txt,
    )
    if m:
        date_part, time_part, frac, tz = m.groups()
        if frac:
            frac = (frac + "000000")[:6]
            txt = f"{date_part}T{time_part}.{frac}"
        else:
            txt = f"{date_part}T{time_part}"
        if tz and tz != "Z":
            tz_norm = tz.replace(":", "")
            if len(tz_norm) == 5 and tz_norm[0] in "+-":
                txt += tz_norm[:3] + ":" + tz_norm[3:]
            else:
                txt += "+00:00"
        else:
            txt += "+00:00"
        try:
            return datetime.fromisoformat(txt)
        except ValueError:
            pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(s.strip(), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


def _seconds_since(mtime_str: Optional[str]) -> float:
    if not mtime_str:
        return float("inf")
    dt = _parse_iso(mtime_str)
    if dt is None:
        return float("inf")
    return (datetime.now(dt.tzinfo) - dt).total_seconds()


def _is_server_machine() -> bool:
    """Detecta si esta máquina tiene WSL con el repo de la API."""
    try:
        result = subprocess.run(
            ["wsl", "--list", "--quiet"], capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
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


def _default_server_url() -> str:
    url = os.getenv("G360_API_URL", "").strip()
    if url:
        return url.rstrip("/")
    if _is_server_machine():
        return "http://127.0.0.1:8090"
    return ""


# ---------------------------------------------------------------------------
# Server checks
# ---------------------------------------------------------------------------


def check_server() -> ServerState:
    s = ServerState()
    s.is_server = True

    # 1) WSL running?
    try:
        r = subprocess.run(["wsl", "-l", "-v"], capture_output=True, text=True, timeout=10)
        for line in r.stdout.splitlines():
            if "Ubuntu" in line and "Running" in line:
                s.wsl_running = True
                break
    except Exception as e:
        s.notes.append(f"WSL check falló: {e}")

    # 2) API process
    try:
        r = subprocess.run(
            ["wsl", "-d", "Ubuntu", "--", "pgrep", "-f", "g360-ventas-api-linux"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            s.api_pid = int(r.stdout.strip().split()[0])
            s.api_process_name = "g360-ventas-api-linux"
    except Exception:
        pass

    # 3) Health en 8091
    try:
        import httpx

        t0 = time.time()
        resp = httpx.get("http://127.0.0.1:8091/api/health", timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") == "ok":
                s.api_8091_health = data
                s.api_8091_health["_response_time_s"] = round(time.time() - t0, 3)
    except Exception as e:
        s.notes.append(f"Health 8091 no responde: {e}")

    # 4) Forwarder 8090
    try:
        s.forwarder_8090_reachable = (
            socket.create_connection(("127.0.0.1", 8090), timeout=2).close() or True
        )
    except OSError:
        s.forwarder_8090_reachable = False

    # 5) Snapshot vs NTFS
    try:
        r = subprocess.run(
            [
                "wsl",
                "-d",
                "Ubuntu",
                "--",
                "bash",
                "-c",
                "stat -c '%y' ~/g360data/historial.db 2>/dev/null; "
                "stat -c '%s' ~/g360data/historial.db 2>/dev/null",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        lines = r.stdout.strip().splitlines()
        if lines:
            s.snapshot_mtime = lines[0].strip() if len(lines) > 0 else None
        if len(lines) > 1:
            try:
                s.snapshot_size_mb = int(lines[1]) / (1024 * 1024)
            except ValueError:
                pass
        s.snapshot_path = "/home/ccusi/g360data/historial.db"
    except Exception:
        pass

    ntfs_path = os.path.expandvars(r"%APPDATA%\g360-db-ventas\data\historial.db")
    if os.path.isfile(ntfs_path):
        s.ntfs_derived_path = ntfs_path
        s.ntfs_derived_mtime = (
            datetime.fromtimestamp(os.path.getmtime(ntfs_path))
            .astimezone()
            .strftime("%Y-%m-%d %H:%M:%S %z")
        )
        snap_dt = _parse_iso(s.snapshot_mtime or "")
        ntfs_dt = _parse_iso(s.ntfs_derived_mtime)
        if snap_dt is not None and ntfs_dt is not None:
            # Positivo = snapshot mas nuevo que NTFS; negativo = snapshot atrasado.
            s.snapshot_desfase_horas = round((snap_dt - ntfs_dt).total_seconds() / 3600, 1)
            if s.snapshot_desfase_horas < -2:
                s.notes.append(
                    f"Snapshot {abs(s.snapshot_desfase_horas):.1f}h atrasado respecto a NTFS"
                )
            elif s.snapshot_desfase_horas > 2:
                s.notes.append(
                    f"Snapshot {s.snapshot_desfase_horas:.1f}h mas nuevo que NTFS (inusual)"
                )
        else:
            s.notes.append("No se pudo comparar mtime snapshot vs NTFS")

    # 6) Backups rotativos
    try:
        r = subprocess.run(
            [
                "wsl",
                "-d",
                "Ubuntu",
                "--",
                "bash",
                "-c",
                "ls -1 ~/g360data/backup/historial_*.db 2>/dev/null | wc -l; "
                "du -sh ~/g360data/backup/ 2>/dev/null | cut -f1",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        blines = r.stdout.strip().splitlines()
        if blines:
            s.backup_count = int(blines[0]) if blines[0].strip().isdigit() else 0
        if len(blines) > 1:
            size_str = blines[1].strip()
            if size_str.endswith("G"):
                s.backup_total_mb = float(size_str[:-1]) * 1024
            elif size_str.endswith("M"):
                s.backup_total_mb = float(size_str[:-1])
    except Exception:
        pass

    # 7) Errores últimas 24h en api.log
    try:
        r = subprocess.run(
            [
                "wsl",
                "-d",
                "Ubuntu",
                "--",
                "bash",
                "-c",
                "grep -ciE 'error|fatal|panic|closed' ~/g360data/api.log 2>/dev/null || echo 0; "
                "tail -5 ~/g360data/api.log 2>/dev/null",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        err_lines = r.stdout.strip().splitlines()
        if err_lines:
            try:
                s.api_log_errors_24h = int(err_lines[0])
            except ValueError:
                pass
    except Exception:
        pass

    return s


# ---------------------------------------------------------------------------
# Client checks
# ---------------------------------------------------------------------------


def check_client(server_url: str = "") -> ClientState:
    cs = ClientState()
    cs.server_url = server_url or _default_server_url()

    # 1) TCP reachability
    if cs.server_url:
        try:
            url_clean = cs.server_url.replace("http://", "").replace("https://", "")
            host_part = url_clean.split("/")[0]  # hasta primer / o fin
            if ":" in host_part:
                host, port_str = host_part.rsplit(":", 1)
                port = int(port_str)
            else:
                host = host_part
                port = 443 if cs.server_url.startswith("https") else 80
            with socket.create_connection((host, port), timeout=5):
                cs.http_8090_reachable = True
        except OSError:
            cs.http_8090_reachable = False
            cs.errors.append(f"No se puede conectar a {cs.server_url}")

    if not cs.http_8090_reachable:
        return cs

    # 2) Health
    try:
        import httpx

        t0 = time.time()
        resp = httpx.get(f"{cs.server_url}/api/health", timeout=15)
        cs.health_response_time_s = round(time.time() - t0, 3)
        if resp.status_code == 200:
            data = resp.json()
            cs.health_ok = data.get("status") == "ok"
        else:
            cs.health_ok = False
            cs.errors.append(f"Health devolvió HTTP {resp.status_code}")
    except Exception as e:
        cs.health_ok = False
        cs.errors.append(f"Health falló: {e}")

    # 3) Token
    try:
        from src.core.capture_service import CaptureService

        token = CaptureService.api_token()
        cs.token_valid = CaptureService.is_api_token_valid()
        if token:
            cfg = __import__("src.core.ventas_db", fromlist=["load_app_config"]).load_app_config()
            t_time = float(cfg.get("api_token_time", 0) or 0)
            cs.token_age_hours = round((time.time() - t_time) / 3600, 1)
    except Exception:
        cs.token_valid = False

    # 4) Status (requiere token). Se mantiene el cliente abierto para reusarlo
    # en el paso 7 (day-checksums) y se cierra al final.
    cli = None
    if cs.token_valid and cs.health_ok:
        try:
            from src.core.ventas_api_client import VentaAPIClient

            cli = VentaAPIClient(base_url=cs.server_url, api_token=CaptureService.api_token())
            st = cli.status()
            cs.status_filas = int(st.get("filas", 0) or 0)
            cs.status_capturado_en = st.get("capturado_en_ultimo")
            cs.status_fecha_hasta = st.get("fecha_hasta")
        except Exception as e:
            cs.errors.append(f"Status falló: {e}")
            if cli is not None:
                try:
                    cli.close()
                except Exception:
                    pass
                cli = None

    # 5) Local DB info
    try:
        from src.core import ventas_db

        info = ventas_db.db_card_info()
        cs.local_filas = int(info.get("filas", 0) or 0)
        cs.local_fecha_max = info.get("fecha_max")
    except Exception as e:
        cs.errors.append(f"DB local no accesible: {e}")

    # 6) Antigüedad del snapshot: horas desde capturado_en hasta ahora.
    if cs.status_capturado_en:
        try:
            from datetime import timezone

            captured = _parse_iso(cs.status_capturado_en)
            if captured is not None:
                now = datetime.now(timezone.utc)
                cs.desfase_horas = round((now - captured).total_seconds() / 3600, 1)
        except Exception:
            pass

    # 7) Días desfasados (lite: solo últimos 7 días para no saturar)
    try:
        if cs.token_valid and cs.health_ok and cs.status_filas > 0 and cli is not None:
            try:
                from src.core import ventas_db
                from src.core.sync_api import SyncAPI

                conn = ventas_db.connect()
                try:
                    desde = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
                    hasta = datetime.now().strftime("%Y-%m-%d")
                    try:
                        dias_api = cli.day_checksums(desde, hasta).get("dias", [])
                    except Exception:
                        dias_api = []
                    cs.dias_desfasados = SyncAPI.dias_desfasados(desde, hasta, dias_api, conn)
                finally:
                    conn.close()
            except Exception as e:
                # DB sin schema (PC nueva): no es error, solo no hay con qué comparar.
                if "no such table" not in str(e).lower():
                    cs.errors.append(f"Checksums fallaron: {e}")
    finally:
        if cli is not None:
            try:
                cli.close()
            except Exception:
                pass

    return cs


# ---------------------------------------------------------------------------
# Orquestador
# ---------------------------------------------------------------------------


def check(server_url: str = "", json_output: bool = False) -> DiagnoseResult:
    result = DiagnoseResult()
    is_srv = _is_server_machine()
    result.is_server = is_srv

    if is_srv:
        result.server = check_server()
    else:
        result.server = ServerState()
        result.server.notes.append("Máquina detectada como cliente (sin repo g360-ventas-api)")

    url = server_url or _default_server_url()
    result.client = check_client(url)

    # Summary + severidad
    recommendations = []

    if not result.client.http_8090_reachable:
        result.severity = "critical"
        result.summary = f"🔴 No se puede conectar a {url}"
        recommendations.append(
            "Verificar que el servidor esté encendido y la red conectada. "
            "Si es necesario, configurar G360_API_URL con la IP correcta."
        )
    elif not result.client.health_ok:
        result.severity = "critical"
        result.summary = "🔴 API no responde health check"
        recommendations.append("El API puede estar caída. Ejecutar diagnóstico del servidor.")
    elif result.client.token_valid:
        if result.client.desfase_horas > 24:
            result.severity = "warning"
            result.summary = (
                f"🟡 Snapshot de la API tiene {result.client.desfase_horas:.0f}h de antigüedad "
                f"(capturado: {result.client.status_capturado_en})"
            )
            recommendations.append(
                "Forzar refresh del snapshot en el servidor "
                "(Configuración → Gestión → Forzar refresh)."
            )
        elif result.client.desfase_horas > 2:
            result.severity = "warning"
            result.summary = (
                f"🟡 Snapshot con {result.client.desfase_horas:.1f}h de antigüedad "
                f"(capturado: {result.client.status_capturado_en})"
            )
            recommendations.append(
                "El sync incremental seguirá funcionando pero con datos algo viejos. "
                "Considerar refrescar el snapshot."
            )
        else:
            result.severity = "ok"
            result.summary = "🟢 Todo OK — API disponible y snapshot fresco"
    else:
        result.severity = "warning"
        result.summary = "🟡 Sin token válido — se requiere login"
        recommendations.append("Iniciar sesión en 'Actualizar desde API' para renovar el token.")

    if result.client.dias_desfasados:
        recommendations.append(
            f"{len(result.client.dias_desfasados)} días con datos pendientes de sincronizar."
        )

    result.recommendations = recommendations
    result.summary += (
        (f"\n  Filas local: {result.client.local_filas:,}  |  API: {result.client.status_filas:,}")
        if result.client
        else result.summary
    )

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Diagnóstico de robustez API G360")
    parser.add_argument("--server", action="store_true", help="Solo chequeos de servidor")
    parser.add_argument("--client", action="store_true", help="Solo chequeos de cliente")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--url", default="", help="URL del servidor (override G360_API_URL)")
    args = parser.parse_args()

    is_srv = _is_server_machine()
    if args.server and not is_srv:
        print("ERROR: Esta máquina no tiene WSL ni el repo de la API (no es servidor).")
        return 1
    if args.client and is_srv:
        print("ERROR: Esta máquina es servidor. Usar --server o sin flags.")
        return 1

    if args.server:
        s = check_server()
        if args.json:
            print(json.dumps(asdict(s), indent=2, default=str))
        else:
            print("=== SERVIDOR ===")
            print(f"WSL running: {s.wsl_running}")
            print(f"API PID: {s.api_pid} ({s.api_process_name})")
            print(f"Health 8091: {s.api_8091_health is not None}")
            print(f"Forwarder 8090: {s.forwarder_8090_reachable}")
            print(f"Snapshot: {s.snapshot_mtime} ({s.snapshot_size_mb:.0f} MB)")
            print(f"NTFS derivado: {s.ntfs_derived_mtime}")
            print(f"Desfase snapshot: {s.snapshot_desfase_horas:.1f}h")
            print(f"Backups: {s.backup_count} copias, {s.backup_total_mb:.0f} MB total")
            print(f"Errores log 24h: {s.api_log_errors_24h}")
            if s.notes:
                print(f"Notas: {', '.join(s.notes)}")
        return 0

    url = args.url or _default_server_url()
    if not url:
        print("ERROR: No se pudo detectar URL del servidor. Define G360_API_URL.")
        return 1

    result = check(server_url=url, json_output=args.json)
    if args.json:
        out = asdict(result)
        print(json.dumps(out, indent=2, default=str))
    else:
        print(f"=== DIAGNÓSTICO [{result.severity.upper()}] ===")
        print(result.summary)
        if result.recommendations:
            print("\nRecomendaciones:")
            for i, rec in enumerate(result.recommendations, 1):
                print(f"  {i}. {rec}")
        if result.client and result.client.errors:
            print("\nErrores:")
            for e in result.client.errors:
                print(f"  • {e}")
        if result.client:
            print("\nDetalles cliente:")
            print(f"  Servidor: {result.client.server_url}")
            print(f"  Conectado: {result.client.http_8090_reachable}")
            print(f"  Health OK: {result.client.health_ok}")
            print(
                f"  Token válido: {result.client.token_valid} "
                f"(edad: {result.client.token_age_hours:.1f}h)"
            )
            print(f"  Filas API: {result.client.status_filas:,}")
            print(f"  Filas local: {result.client.local_filas:,}")
            print(f"  Capturado en API: {result.client.status_capturado_en}")
            print(f"  Fecha máx local: {result.client.local_fecha_max}")
            print(f"  Antigüedad snapshot: {result.client.desfase_horas:.1f}h")
            if result.client.dias_desfasados:
                print(f"  Días desfasados: {', '.join(result.client.dias_desfasados)}")
    return 0 if result.severity != "critical" else 1


if __name__ == "__main__":
    raise SystemExit(main())
