@echo off
chcp 65001 >nul
title G360 - Portable CLI Launcher

echo.
echo ==============================================
echo   G360 CLI - Portable Version
echo   (Requires Python installed)
echo ==============================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python no encontrado
    echo Instala Python desde: https://python.org
    echo O usa setup.bat para configurar ambiente
    pause
    exit /b 1
)

echo Buscando archivo principal...
if not exist "src\main.py" (
    echo ERROR: src\main.py no encontrado
    pause
    exit /b 1
)

echo Ejecutando G360 CLI...
echo.
python src\main.py %*

echo.
pause