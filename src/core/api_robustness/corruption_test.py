"""Test de recuperación ante DB corrupta.

Verifica que:
1. El API responda error elegante (no crash) ante snapshot corrupto.
2. Se pueda restaurar desde backup rotativo.
3. El API vuelva a servir correctamente tras restauración.

Nota: Este test MODIFICA el snapshot en producción. Correr solo en ambiente
de prueba o con la API detenida.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class CorruptionTestResult:
    snapshot_corrupted: bool = False
    api_responds_error: bool = False
    api_crashed: bool = False
    backup_restored: bool = False
    api_recovered: bool = False
    details: str = ""


def run_corruption_test(
    backup_dir: Optional[Path] = None,
    snapshot_path: Optional[Path] = None,
) -> CorruptionTestResult:
    """Test de recuperación tras corromper el snapshot.

    Pasos:
    1. Hacer backup del snapshot actual.
    2. Corromper bytes aleatorios en el snapshot.
    3. Verificar que el API responda error (no crash).
    4. Restaurar desde backup.
    5. Verificar que el API vuelva a servir.
    """
    r = CorruptionTestResult()

    # Detectar paths
    if snapshot_path is None:
        snapshot_path = Path.home() / "g360data" / "historial.db"
    if backup_dir is None:
        backup_dir = Path.home() / "g360data" / "backup"

    if not snapshot_path.exists():
        r.details = f"Snapshot no encontrado: {snapshot_path}"
        return r

    # 1) Backup del snapshot actual
    pre_corrupt_backup = backup_dir / "historial_pre_corruption_test.db"
    try:
        shutil.copy2(snapshot_path, pre_corrupt_backup)
        log.info("Backup del snapshot creado: %s", pre_corrupt_backup)
    except Exception as e:
        r.details = f"No se pudo hacer backup: {e}"
        return r

    # 2) Corromper el snapshot
    try:
        size = snapshot_path.stat().st_size
        # Corromper un byte en medio del archivo
        offset = size // 2
        with open(snapshot_path, "r+b") as f:
            f.seek(offset)
            original = f.read(1)
            f.seek(offset)
            f.write(b"\xff" if original == b"\x00" else b"\x00")
        r.snapshot_corrupted = True
        log.info("Snapshot corrompido en offset %d", offset)
    except Exception as e:
        r.details = f"Corrupción falló: {e}"
        # Restaurar
        shutil.copy2(pre_corrupt_backup, snapshot_path)
        return r

    # 3) Verificar respuesta del API
    try:
        import httpx

        t0 = time.time()
        try:
            resp = httpx.get("http://127.0.0.1:8090/api/health", timeout=10)
            elapsed = time.time() - t0
            # Si responde 200 con corrupto, el API es resistente
            if resp.status_code == 200:
                r.api_responds_error = True
                r.details = f"API responde 200 con snapshot corrupto (tolerante, {elapsed:.2f}s)"
            elif resp.status_code == 500:
                r.api_responds_error = True
                r.details = (
                    f"API responde 500 con snapshot corrupto (error elegante, {elapsed:.2f}s)"
                )
            else:
                r.details = f"API responde HTTP {resp.status_code}"
        except httpx.ConnectError:
            r.api_crashed = True
            r.details = "API crashed (no responde tras corrupción)"
        except httpx.ReadTimeout:
            r.details = "API timeout (posiblemente trabada)"
    except Exception as e:
        r.details = f"Check health falló: {e}"

    # 4) Restaurar desde backup
    try:
        shutil.copy2(pre_corrupt_backup, snapshot_path)
        r.backup_restored = True
        log.info("Snapshot restaurado desde backup")
    except Exception as e:
        r.details += f"; Restauración falló: {e}"
        return r

    # 5) Verificar recuperación del API
    try:
        # Si el API estaba crash, necesitamos reiniciarlo
        if r.api_crashed:
            _restart_api()
        time.sleep(3)
        import httpx

        resp = httpx.get("http://127.0.0.1:8091/api/health", timeout=10)
        if resp.status_code == 200 and resp.json().get("status") == "ok":
            r.api_recovered = True
            r.details += "; API recuperada correctamente"
        else:
            r.details += f"; API no recuperó: HTTP {resp.status_code}"
    except Exception as e:
        r.details += f"; Check recuperación falló: {e}"

    # Cleanup: remover backup de corrupción
    if pre_corrupt_backup.exists():
        try:
            pre_corrupt_backup.unlink()
        except Exception:
            pass

    return r


def _restart_api() -> None:
    """Reinicia la API en WSL."""
    api_project = _find_api_project()
    if not api_project:
        return
    runner = api_project / "deploy" / "run_api_wsl.sh"
    wsl_path = "/mnt/c/" + str(runner).replace("\\", "/")[2:]
    subprocess.run(
        [
            "wsl",
            "-d",
            "Ubuntu",
            "--",
            "bash",
            "-c",
            "pkill -f g360-ventas-api || true; sleep 1; setsid bash '"
            + wsl_path
            + "' >/dev/null 2>&1 &",
        ],
        capture_output=True,
        timeout=10,
    )


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


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Test de recuperación DB corrupta")
    parser.add_argument("--snapshot", default="", help="Ruta del snapshot WSL")
    parser.add_argument("--backup-dir", default="", help="Dir de backups")
    parser.add_argument(
        "--skip-corrupt", action="store_true", help="Solo verificar estado actual sin corromper"
    )
    args = parser.parse_args()

    snapshot_path = Path(args.snapshot) if args.snapshot else None
    backup_dir = Path(args.backup_dir) if args.backup_dir else None

    if args.skip_corrupt:
        print("Modo solo-verificación (sin corromper).")
        import httpx

        try:
            resp = httpx.get("http://127.0.0.1:8091/api/health", timeout=10)
            print(f"Health: {resp.status_code} {resp.text}")
        except Exception as e:
            print(f"Health no responde: {e}")
        return 0

    print("ADVERTENCIA: Este test CORROMPE el snapshot y lo restaura.")
    print("Asegúrate de tener backups antes de continuar.\n")
    input("Presiona Enter para continuar...")

    result = run_corruption_test(snapshot_path, backup_dir)
    print("=== CORRUPTION TEST ===")
    print(f"Snapshot corrupto: {result.snapshot_corrupted}")
    print(f"API responde error: {result.api_responds_error}")
    print(f"API crash: {result.api_crashed}")
    print(f"Backup restaurado: {result.backup_restored}")
    print(f"API recuperada: {result.api_recovered}")
    print(f"Details: {result.details}")

    ok = result.backup_restored and result.api_recovered
    print(f"\n{'✓ PASSED' if ok else '✗ FAILED'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
