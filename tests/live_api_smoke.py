"""Prueba de humo del cliente Python contra el API vivo.

No usa mocks ni fixtures: login real, filtros reales. El objetivo es confirmar
que el contrato que el codigo de produccion construye (`col.op.valor`) coincide
con lo que el API acepta, sobre todo en las columnas de fecha.
"""

import os
import pathlib
import sys

RAIZ = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from src.core.ventas_api_client import VentaAPIClient  # noqa: E402

cli = VentaAPIClient("http://127.0.0.1:8090")

print("health :", cli.health())
cli.login(os.environ["G360_USER"], os.environ["G360_PASS"])
print("login  : ok")

st = cli.status()
print("status :", {k: st.get(k) for k in ("filas", "desde", "hasta", "capturado_en_ultimo")})

print("\n--- filtros de fecha que el codigo de produccion construye ---")
casos = [
    # (descripcion, filtros, debe_aceptar)
    ("fecha_orig eq ISO (dia_historico)", {"fecha_orig": "eq.2025-06-01"}, True),
    ("fecha_orig gte ISO", {"fecha_orig": "gte.2025-01-01"}, True),
    ("fecha_orig eq dd/mm", {"fecha_orig": "eq.01/06/2025"}, False),
    ("fecha_ref gte ISO", {"fecha_ref": "gte.2025-06-01"}, True),
    ("fecha_venc lte ISO", {"fecha_venc": "lte.2025-06-01"}, True),
    ("fec_cargo gte ISO", {"fec_cargo": "gte.2025-06-15"}, True),
    ("fec_cargo eq 2025/06/15", {"fec_cargo": "eq.2025/06/15"}, False),
    ("mes_ref eq yyyy-mm", {"mes_ref": "eq.2025-06"}, True),
    ("capturado_en gte solo fecha", {"capturado_en": "gte.2026-10-01"}, True),
    ("capturado_en gte con T", {"capturado_en": "gte.2026-10-01T00:00:00"}, False),
    ("capturado_en eq datetime espacio", {"capturado_en": "eq.2026-10-01 01:19:30"}, True),
]
ok = ko = 0
for nombre, filtros, debe in casos:
    try:
        r = cli.data("ventas", limit=1, filtros=filtros)
        res = f"HTTP 200  filas={len(r.get('datos', []))}"
        acepta = True
    except Exception as e:  # noqa: BLE001
        res = str(e).split("\n")[0][:96]
        acepta = False
    if acepta == debe:
        ok += 1
        print(f"  ok   {nombre:<36} {res}")
    else:
        ko += 1
        print(f"  FAIL {nombre:<36} {res}  (esperaba {'aceptar' if debe else 'rechazar'})")

print("\n--- endpoints de lectura ---")
for nombre, fn in [
    ("stats", lambda: cli.stats()),
    ("checksums", lambda: cli.checksums()),
    ("model", lambda: cli.model()),
    ("folios", lambda: cli.folios(desde="2025-01-01", hasta="2025-12-31")),
]:
    try:
        v = fn()
        claves = list(v)[:6] if isinstance(v, dict) else type(v).__name__
        print(f"  ok   {nombre:<10} claves={claves}")
        ok += 1
    except Exception as e:  # noqa: BLE001
        ko += 1
        print(f"  FAIL {nombre:<10} {str(e)[:96]}")

print("\n--- vista de negocio ---")
try:
    v = cli.query("vw_dim_articulo", limit=3)
    print(f"  ok   vw_dim_articulo filas={len(v.get('datos', []))}")
    ok += 1
except Exception as e:  # noqa: BLE001
    ko += 1
    print(f"  FAIL vw_dim_articulo {str(e)[:96]}")

print(f"\n=== ok={ok} fail={ko} ===")
sys.exit(1 if ko else 0)
