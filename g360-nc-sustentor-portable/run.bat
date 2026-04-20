@echo off
setlocal enabledelayedexpansion
title G360 NC-Sustentor
cd /d "%~dp0"

REM Primera ejecucion
if not exist ".venv\Scripts\python.exe" (
    echo.
    echo =========================================
    echo   PRIMERA EJECUCION - PREPARANDO
    echo =========================================
    echo.
    
    echo Instalando UV...
    where uv >nul 2>&1
    if errorlevel 1 powershell -NoProfile -Command "irm https://astral.sh/uv/install.ps1 | iex" 2>nul
    
    echo Creando entorno virtual...
    uv venv .venv --python 3.12 >nul 2>&1
    if errorlevel 1 uv venv .venv >nul 2>&1
    
    echo Instalando paquetes...
    call .venv\Scripts\activate.bat
    uv pip install -r requirements.txt >nul 2>&1
    
    echo.
    REM Crear acceso directo
    if not exist "%USERPROFILE%\Desktop\G360 NC-Sustentor.lnk" (
        echo Creando acceso directo en escritorio...
        cscript //nologo "%~dp0create_shortcut.vbs" 2>nul
    )
    echo.
)

echo Iniciando aplicacion...
call .venv\Scripts\activate.bat
start /wait python main.py

REM Cerrar CMD automaticamente cuando termine la app
exit /b