# -*- coding: utf-8 -*-
"""Arma el paquete portable mínimo de G360 NC Sustentor.

Whitelist, no blacklist: solo entra lo que está en INCLUIR. Si mañana alguien
agrega una carpeta nueva al repo, NO viaja hasta que se la declare acá. Eso es
lo que mantiene al paquete en ~4 MB en vez de arrastrar tests, docs o cachés.

Uso:
    python portable/build.py [--out RUTA] [--check RUTA] [--max-mb N]

El destino por defecto es ../g360-nc-sustentor-portable (ignorado por git
vía `*-portable/`), que es donde `sync_portable.py` ya despliega.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DESTINO_DEFAULT = RAIZ.parent / "g360-nc-sustentor-portable"

# Lo único que viaja. Rutas relativas a la raíz del repo.
ARCHIVOS = [
    "main.py",
    "run.bat",
    "launch.vbs",
    "create_shortcut.vbs",
    "pyproject.toml",
    "uv.lock",
    ".python-version",
]
DIRS = [
    "src",
    "assets",
]

# Basura que nunca debe aparecer, aunque esté dentro de un DIR incluido.
BASURA_NOMBRES = {"__pycache__", ".pytest_cache", ".ruff_cache", ".venv", ".git"}
BASURA_EXT = {".pyc", ".pyo", ".db", ".db-wal", ".db-shm", ".log"}

# Techo de tamaño. Si el paquete lo supera, algo se coló (un .db, un .venv).
MAX_MB_DEFAULT = 50


def _es_basura(p: Path) -> bool:
    if p.name in BASURA_NOMBRES:
        return True
    if p.suffix.lower() in BASURA_EXT:
        return True
    if any(parte in BASURA_NOMBRES for parte in p.parts):
        return True
    return False


def construir(destino: Path) -> Path:
    destino.mkdir(parents=True, exist_ok=True)
    copiados = 0
    for rel in ARCHIVOS:
        src = RAIZ / rel
        if not src.is_file():
            print(f"  AVISO: falta {rel}, se salta")
            continue
        shutil.copy2(src, destino / rel)
        copiados += 1
    for rel in DIRS:
        src = RAIZ / rel
        if not src.is_dir():
            print(f"  AVISO: falta {rel}/, se salta")
            continue
        dst = destino / rel
        for f in sorted(src.rglob("*")):
            if f.is_dir():
                continue
            if _es_basura(f.relative_to(src)):
                continue
            objetivo = dst / f.relative_to(src)
            objetivo.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, objetivo)
            copiados += 1
    print(f"  {copiados} archivos copiados -> {destino}")
    return destino


def verificar(destino: Path, max_mb: float) -> list[str]:
    """Devuelve la lista de problemas. Vacía = el paquete está sano."""
    problemas: list[str] = []
    if not destino.is_dir():
        return [f"no existe: {destino}"]
    # 1. Todo lo obligatorio está.
    for rel in ARCHIVOS + DIRS + ["src/core/cartucho.py"]:
        if not (destino / rel).exists():
            problemas.append(f"falta: {rel}")
    # 2. Nada de lo prohibido está.
    for f in destino.rglob("*"):
        rel = f.relative_to(destino)
        if _es_basura(rel):
            problemas.append(f"basura presente: {rel}")
        if f.is_file() and f.suffix.lower() == ".xlsx":
            # Las plantillas de assets/templates SÍ viajan (son insumos, no datos).
            if "templates" not in {p.lower() for p in f.parts}:
                problemas.append(f"xlsx de datos en el paquete: {rel}")
    # 3. No hay tests (no se testean en la PC cliente).
    if (destino / "tests").exists():
        problemas.append("lleva tests/: no corresponde en un paquete cliente")
    # 4. Tamaño acotado.
    mb = sum(f.stat().st_size for f in destino.rglob("*") if f.is_file()) / 1024**2
    if mb > max_mb:
        problemas.append(f"pesa {mb:.1f} MB, techo {max_mb:.0f} MB: algo se coló")
    print(f"  paquete: {mb:.1f} MB")
    return problemas


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(DESTINO_DEFAULT), help="carpeta destino")
    ap.add_argument("--check", default=None, help="solo verificar un paquete ya armado")
    ap.add_argument("--max-mb", type=float, default=MAX_MB_DEFAULT)
    args = ap.parse_args(argv)

    if args.check:
        problemas = verificar(Path(args.check), args.max_mb)
    else:
        destino = construir(Path(args.out))
        problemas = verificar(destino, args.max_mb)
    if problemas:
        print("\nPROBLEMAS:")
        for p in problemas:
            print(f"  - {p}")
        return 1
    print("\nPaquete OK: mínimo, completo y verificable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
