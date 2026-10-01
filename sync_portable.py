import os
import shutil
from pathlib import Path

FILES_TO_SYNC = [
    "main.py",
    "run.bat",
    "pyproject.toml",
    "requirements.txt",
    ".python-version",
    "uv.lock",
]

DIRS_TO_SYNC = [
    "src",
    "assets",
]


def _default_portable_dir(base_dir: str) -> str:
    """Ubicación real de la copia portable que usa el usuario.

    La copia desplegada vive en ~/Documents/Herramientas (fuera del repo).
    Se prefiere esa ruta; si no existe (instalaciones anteriores), cae a la
    carpeta portable embebida dentro del repositorio.
    """
    env = os.environ.get("G360_PORTABLE_DIR")
    if env:
        return env
    user_dir = os.path.expanduser("~")
    documentos = os.environ.get("USERPROFILE", user_dir)
    live = os.path.join(documentos, "Documents", "Herramientas", "g360-nc-sustentor-portable")
    if os.path.isdir(live):
        return live
    return os.path.join(base_dir, "g360-nc-sustentor-portable")


def sync_dir(src, dst):
    """Sincroniza recursivamente el contenido de un directorio.

    Elimina los elementos que sobran en el destino y copia los que cambiaron
    (comparando mtime y tamaño) o faltan.
    """
    if not os.path.exists(src):
        return
    os.makedirs(dst, exist_ok=True)

    dst_items = set(os.listdir(dst))
    src_items = set(os.listdir(src))

    for item in dst_items - src_items:
        dst_item = os.path.join(dst, item)
        if os.path.isdir(dst_item):
            shutil.rmtree(dst_item)
            print(f"  Eliminado: {item}/")
        else:
            os.remove(dst_item)
            print(f"  Eliminado: {item}")

    for item in src_items:
        src_item = os.path.join(src, item)
        dst_item = os.path.join(dst, item)
        if os.path.isdir(src_item):
            sync_dir(src_item, dst_item)
        else:
            src_stat = os.stat(src_item)
            if (
                not os.path.exists(dst_item)
                or os.stat(dst_item).st_mtime != src_stat.st_mtime
                or os.stat(dst_item).st_size != src_stat.st_size
            ):
                shutil.copy2(src_item, dst_item)
                print(f"  Sincronizado: {item}")


def main():
    """Copia los archivos y directorios del repositorio a la carpeta portable."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    portable_dir = _default_portable_dir(base_dir)

    os.makedirs(portable_dir, exist_ok=True)

    print("Sincronizando versión portable...\n")

    for filename in FILES_TO_SYNC:
        src = os.path.join(base_dir, filename)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(portable_dir, filename))
            print(f"  Copiado: {filename}")

    for dirname in DIRS_TO_SYNC:
        sync_dir(os.path.join(base_dir, dirname), os.path.join(portable_dir, dirname))

    print(f"\n¡Sincronización finalizada con éxito!")
    print(f"Portable listo en: {portable_dir}")


if __name__ == "__main__":
    main()
