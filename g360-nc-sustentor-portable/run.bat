@echo off
setlocal enabledelayedexpansion
title G360 NC-Sustentor
cd /d "%~dp0"

REM Verificar si es primera ejecucion
if not exist ".venv\Scripts\python.exe" (
    cls
    echo.
    echo =========================================
    echo   PRIMERA EJECUCION - CONFIGURANDO
    echo =========================================
    echo.
    
    echo 🔹 Instalando gestor de paquetes UV...
    where uv >nul 2>&1
    if errorlevel 1 (
        powershell -NoProfile -Command "irm https://astral.sh/uv/install.ps1 | iex" >nul 2>&1
        REM Actualizar PATH actual para que UV este disponible inmediatamente
        for /f "tokens=2*" %%a in ('reg query "HKCU\Environment" /v Path') do set "PATH=%%b;!PATH!"
    )
    
    echo 🔹 Creando entorno virtual...
    uv venv .venv --python 3.12 >nul 2>&1
    if errorlevel 1 uv venv .venv >nul 2>&1
    
    echo 🔹 Instalando dependencias...
    call .venv\Scripts\activate.bat
    uv pip install -r requirements.txt >nul 2>&1
    
    echo 🔹 Creando acceso directo personalizado...
    if exist "%~dp0create_shortcut.vbs" (
        cscript //nologo "%~dp0create_shortcut.vbs" >nul 2>&1
    )
    
    echo.
    echo ✅ CONFIGURACION COMPLETADA CORRECTAMENTE
    echo.
    echo ℹ️  Se ha creado un acceso directo en tu Escritorio
    echo ℹ️  Proximas veces abre directamente desde el icono del escritorio
    echo.
    timeout /t 3 /nobreak >nul
    cls
)

REM Iniciar aplicacion minimizada
echo 🚀 Iniciando G360 NC-Sustentor...
call .venv\Scripts\activate.bat

REM Minimizar esta ventana CMD automaticamente
powershell -WindowStyle Minimized -Command "" >nul

REM Ejecutar aplicacion
start /wait /min pythonw.exe main.py

REM Cerrar CMD inmediatamente cuando termine la aplicacion
exit