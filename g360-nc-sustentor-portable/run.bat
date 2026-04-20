@echo off
setlocal enabledelayedexpansion
title G360 NC-Sustentor - Portable Launcher
cd /d "%~dp0"

:: ==============================================
:: 0. CREAR ACCESO DIRECTO EN ESCRITORIO
:: ==============================================
set "SHORTCUT_NAME=G360 NC-Sustentor.lnk"
set "ICON_PATH=%~dp0assets\images\favicon.ico"
set "TARGET_PATH=%~dp0run.bat"
set "WORKING_DIR=%~dp0"

if not exist "%USERPROFILE%\Desktop\%SHORTCUT_NAME%" (
    echo =======================================================
    echo          G360 NC-SUSTENTOR - PRIMERA EJECUCION
    echo =======================================================
    echo.
    echo [i] Creando acceso directo en el escritorio...
    powershell -Command "$WshShell = New-Object -ComObject WScript.Shell; $Shortcut = $WshShell.CreateShortcut('%USERPROFILE%\Desktop\%SHORTCUT_NAME%'); $Shortcut.TargetPath = '%TARGET_PATH%'; $Shortcut.WorkingDirectory = '%WORKING_DIR%'; $Shortcut.IconLocation = '%ICON_PATH%'; $Shortcut.Save()"
    echo [OK] Acceso directo creado exitosamente.
    echo.
    timeout /t 2 /nobreak >nul
)

:: ==============================================
:: 1. VERIFICAR PYTHON
:: ==============================================
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Python no esta instalado o no esta en PATH
    echo [i] Descargalo desde: https://www.python.org/downloads/
    echo [i] IMPORTANTE: Durante la instalacion, marca "Add Python to PATH"
    echo.
    pause
    exit /b 1
)

:: ==============================================
:: 2. CREAR VENV SI NO EXISTE
:: ==============================================
if not exist ".venv" (
    echo [i] Creando entorno virtual (.venv)...
    python -m venv .venv
    if !errorlevel! neq 0 (
        echo [ERROR] Fallo al crear el entorno virtual
        pause
        exit /b 1
    )
)

:: ==============================================
:: 3. ACTIVAR VENV E INSTALAR DEPENDENCIAS
:: ==============================================
echo [i] Activando entorno virtual...
call .venv\Scripts\activate.bat
if %errorlevel% neq 0 (
    echo [ERROR] Fallo al activar el entorno virtual
    pause
    exit /b 1
)

echo [i] Verificando dependencias...
pip install -q -r requirements.txt
if %errorlevel% neq 0 (
    echo [i] Instalando desde pyproject.toml...
    pip install -q .
    if !errorlevel! neq 0 (
        echo [ERROR] Fallo al instalar dependencias
        pause
        exit /b 1
    )
)

:: ==============================================
:: 4. EJECUTAR APLICACION
:: ==============================================
echo [i] Iniciando aplicacion G360 NC-Sustentor...
echo.
python main.py
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] La aplicacion termino con un error (codigo: %errorlevel%)
    pause
)

exit /b