@echo off
setlocal enabledelayedexpansion
title G360 NC-Sustentor
cd /d "%~dp0"

REM ✅ FIX COMPLETO: Eliminar UV y usar pip estandar que funciona siempre
if not exist ".venv\Scripts\python.exe" (
    echo.
    echo =========================================
    echo   PRIMERA EJECUCION - PREPARANDO
    echo =========================================
    echo.
    
    echo Creando entorno virtual...
    python -m venv .venv
    
    echo Instalando paquetes...
    call .venv\Scripts\activate.bat
    python -m pip install --upgrade pip
    pip install -r requirements.txt
    
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

REM ✅ FIX: No minimizar ventana, mostrar salida de errores y mantener abierto si falla
python main.py

REM Si hay error, mantener ventana abierta para ver el mensaje
if errorlevel 1 (
    echo.
    echo ERROR: La aplicacion se cerro inesperadamente.
    echo Presione cualquier tecla para salir...
    pause >nul
)

exit /b
