@echo off
setlocal enabledelayedexpansion

echo [G360] Verificando entorno de desarrollo Python (uv)...

where uv >nul 2>nul
if %errorlevel% neq 0 (
    echo [G360] uv no detectado. Iniciando instalacion automatica...
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    set "PATH=%USERPROFILE%\.cargo\bin;%PATH%"
    echo [G360] uv instalado correctamente.
) else (
    echo [G360] uv ya esta instalado.
)

echo [G360] Sincronizando dependencias y entorno virtual...
uv sync
echo [G360] Entorno listo. Puedes usar 'uv run python src/main.py' para probar.
pause