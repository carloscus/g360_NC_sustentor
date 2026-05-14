@echo off
chcp 65001 >nul
title G360 - Portable Flet App

echo.
echo ==============================================
echo   G360 Flet - Portable Version
echo   (Requires Python + flet installed)
echo ==============================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python no encontrado
    echo Instala Python desde: https://python.org
    pause
    exit /b 1
)

echo Verificando flet...
pip show flet >nul 2>&1
if errorlevel 1 (
    echo   Flet no encontrado. Instalando...
    pip install flet
    if errorlevel 1 (
        echo ERROR: No se pudo instalar flet
        pause
        exit /b 1
    )
)

if not exist "src\main.py" (
    echo ERROR: src\main.py no encontrado
    pause
    exit /b 1
)

echo.
echo Ejecutando G360 Flet App...
python src\main.py %*

pause