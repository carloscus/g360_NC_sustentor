# Paquete portable mínimo

Copia mínima y autocontenida de la app para llevar a una PC nueva en un
pendrive o por red, que **se autoinstala y arranca con doble clic**.

## Qué lleva (y por qué pesa ~4 MB)

| Incluye | Motivo |
|---|---|
| `main.py`, `src/`, `assets/` | La app. Sin tests, sin docs, sin cachés |
| `run.bat` | Autoinstalación en 6 pasos: uv → Python 3.11 → venv → dependencias → migraciones → acceso directo → arranque |
| `launch.vbs` | Arranque silencioso (consola minimizada). En la primera corrida pasa modo completo; después, modo `fast` |
| `create_shortcut.vbs` | Acceso directo en el escritorio con el icono |
| `pyproject.toml`, `uv.lock`, `.python-version` | Lo que `uv sync` necesita para recrear el entorno |

| NO lleva | Motivo |
|---|---|
| `.venv/` (405 MB) | Lo recrea `run.bat` en destino |
| `tests/` (5.7 MB) | No se testean en la PC cliente |
| `*.db`, `*.xlsx` de datos | La DB vive en `%APPDATA%`, no en el repo; el cliente arranca vacío e importa un cartucho |
| `__pycache__/`, `*.pyc`, `.git/` | Regenerables o innecesarios |
| `requirements.txt` | `run.bat` usa `uv sync`, no lo lee |

## Cómo se usa

```powershell
# Armar el paquete (sale en ..\g360-nc-sustentor-portable, ignorado por git)
python portable/build.py

# A otra carpeta
python portable/build.py --out C:\Temp\paquete

# Solo verificar un paquete ya armado
python portable/build.py --check C:\Temp\paquete
```

En la PC destino: copiar la carpeta, doble clic en **`launch.vbs`**.
La primera vez instala todo (pide internet para uv/Python); después arranca
en modo rápido. Si algo falla, la consola minimizada se puede restaurar para
ver el error, y el detalle está en `%APPDATA%\g360-erp-nc-sustentor\run_log.txt`.

## Cadena de actualización

La PC nueva arranca con la DB vacía. Para ponerla al día:

```
PC origen → Exportar cartucho (incremental) → ZIP ~0.8 GB
    → copiar a la PC nueva → Importar cartucho → mergea
```

Cada cliente conserva sus líneas (la allowlist es local y el import no la
toca). Ver `tests/test_cartucho_incremental.py` y `test_cartucho_merge_derivados.py`.
