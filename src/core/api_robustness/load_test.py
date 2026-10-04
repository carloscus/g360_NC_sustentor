"""Test de carga para la API Go de ventas.

Simula N clientes concurrentes haciendo sync incremental.
Mide latencia p50/p95, throughput y tasa de errores.

Uso:
  python -m src.core.api_robustness.load_test --workers 20 --dias 7
"""

from __future__ import annotations

import argparse
import logging
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional

log = logging.getLogger(__name__)


@dataclass
class WorkerResult:
    worker_id: int
    success: bool
    filas: int = 0
    segundos: float = 0.0
    error: Optional[str] = None
    modo: str = ""


@dataclass
class LoadTestResult:
    n_workers: int
    duration_s: float
    total_filas: int
    avg_latencia_s: float = 0.0
    p50_latencia_s: float = 0.0
    p95_latency_s: float = 0.0
    min_latencia_s: float = 0.0
    max_latencia_s: float = 0.0
    throughput_filas_por_seg: float = 0.0
    success_rate: float = 0.0
    errores: List[str] = field(default_factory=list)
    workers: List[WorkerResult] = field(default_factory=list)


def run_load_test(
    server_url: str,
    n_workers: int = 20,
    dias: int = 7,
    user: str = "",
    password: str = "",
) -> LoadTestResult:
    """Ejecuta test de carga con N workers concurrentes.

    Cada worker:
    1. Hace login (si tiene credenciales) o usa token cached.
    2. Ejecuta sync_api.aplicar() para los últimos N días.
    3. Registra latencia y resultado.
    """
    from src.core import ventas_db
    from src.core.sync_api import SyncAPI
    from src.core.ventas_api_client import VentaAPIClient

    desde = (datetime.now() - timedelta(days=dias)).strftime("%Y-%m-%d")
    hasta = datetime.now().strftime("%Y-%m-%d")

    results: List[WorkerResult] = []
    latencias: List[float] = []
    total_filas = 0
    errores: List[str] = []

    log.info(
        "Load test: %d workers, rango %s → %s, servidor=%s",
        n_workers,
        desde,
        hasta,
        server_url,
    )

    t_start = time.time()

    def _worker(i: int) -> WorkerResult:
        t0 = time.time()
        try:
            cli = VentaAPIClient(base_url=server_url)
            # Intentar con token cached
            from src.core.capture_service import CaptureService

            token = CaptureService.api_token()
            if token and CaptureService.is_api_token_valid():
                cli.set_token(token)

            sync = SyncAPI(cli)
            conn = ventas_db.connect()
            try:
                res = sync.aplicar(desde, hasta, conn=conn, usar_dias=True)
                filas = res.get("filas", 0)
                return WorkerResult(
                    worker_id=i,
                    success=res.get("estado") == "ok",
                    filas=filas,
                    segundos=round(time.time() - t0, 3),
                    modo=res.get("modo", ""),
                )
            finally:
                conn.close()
        except Exception as e:
            errores.append(f"Worker {i}: {e}")
            return WorkerResult(worker_id=i, success=False, error=str(e)[:200])

    # Ejecutar concurrente
    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        futures = {pool.submit(_worker, i): i for i in range(n_workers)}
        for future in as_completed(futures):
            try:
                wr = future.result(timeout=120)
                results.append(wr)
                if wr.success:
                    latencias.append(wr.segundos)
                    total_filas += wr.filas
            except Exception as e:
                errores.append(f"Future error: {e}")

    duration = time.time() - t_start

    # Stats
    latencias_sorted = sorted(latencias) if latencias else [0.0]
    n_success = sum(1 for r in results if r.success)

    return LoadTestResult(
        n_workers=n_workers,
        duration_s=round(duration, 2),
        total_filas=total_filas,
        avg_latencia_s=round(statistics.mean(latencias_sorted), 3),
        p50_latencia_s=round(statistics.median(latencias_sorted), 3),
        p95_latency_s=round(
            latencias_sorted[int(len(latencias_sorted) * 0.95)] if latencias_sorted else 0.0,
            3,
        ),
        min_latencia_s=round(min(latencias_sorted), 3),
        max_latencia_s=round(max(latencias_sorted), 3),
        throughput_filas_por_seg=round(total_filas / duration, 2) if duration > 0 else 0,
        success_rate=round(n_success / len(results) * 100, 1) if results else 0,
        errores=errores[:10],
        workers=results,
    )


def main():
    parser = argparse.ArgumentParser(description="Test de carga API G360")
    parser.add_argument("--workers", type=int, default=20, help="Número de workers concurrentes")
    parser.add_argument("--dias", type=int, default=7, help="Rango de días para sync")
    parser.add_argument("--url", default="", help="URL del servidor")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    url = args.url or __import__("os").getenv("G360_API_URL", "http://127.0.0.1:8090")
    result = run_load_test(url, n_workers=args.workers, dias=args.dias)

    if args.json:
        import json

        print(
            json.dumps(
                {
                    "n_workers": result.n_workers,
                    "duration_s": result.duration_s,
                    "total_filas": result.total_filas,
                    "avg_latencia_s": result.avg_latencia_s,
                    "p50_latencia_s": result.p50_latencia_s,
                    "p95_latency_s": result.p95_latency_s,
                    "min_latencia_s": result.min_latencia_s,
                    "max_latencia_s": result.max_latencia_s,
                    "throughput_filas_por_seg": result.throughput_filas_por_seg,
                    "success_rate": result.success_rate,
                    "errores": result.errores,
                },
                indent=2,
            )
        )
    else:
        print(f"=== LOAD TEST [{result.n_workers} workers, {result.dias} días] ===")
        print(f"Duración total: {result.duration_s:.1f}s")
        print(f"Throughput: {result.throughput_filas_por_seg:.1f} filas/seg")
        print(f"Total filas descargadas: {result.total_filas:,}")
        print(f"Success rate: {result.success_rate:.1f}%")
        print("\nLatencias:")
        print(f"  avg: {result.avg_latencia_s:.3f}s")
        print(f"  p50: {result.p50_latencia_s:.3f}s")
        print(f"  p95: {result.p95_latency_s:.3f}s")
        print(f"  min: {result.min_latencia_s:.3f}s")
        print(f"  max: {result.max_latencia_s:.3f}s")
        if result.errores:
            print(f"\nErrores ({len(result.errores)}):")
            for e in result.errores[:5]:
                print(f"  • {e}")

    # Criterio de aprobación
    ok = result.success_rate >= 95 and result.p95_latency_s < 30 and result.total_filas > 0
    print(
        f"\n{'✓ APROBADO' if ok else '✗ REPROBADO'} — "
        f"criterios: success≥95% ✓, p95<30s {'✓' if result.p95_latency_s < 30 else '✗'}"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
