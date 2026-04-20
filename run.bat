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
    powershell -NoProfile -Command "$WshShell = New-Object -ComObject WScript.Shell; $Shortcut = $WshShell.CreateShortcut('%USERPROFILE%\Desktop\%SHORTCUT_NAME%'); $Shortcut.TargetPath = '%TARGET_PATH%'; $Shortcut.WorkingDirectory = '%WORKING_DIR%'; $Shortcut.IconLocation = '%ICON_PATH%'; $Shortcut.Save()"
    echo [OK] Acceso directo creado exitosamente.
    echo.
    timeout /t 2 /nobreak >nul
)

:: ==============================================
:: 1. VERIFICAR E INSTALAR UV SI ES NECESARIO
:: ==============================================
echo [i] Verificando motor UV...
where uv >nul 2>&1
if %errorlevel% neq 0 (
    echo [i] Instalando UV (gestor de dependencias rapido)...
    powershell -NoProfile -ExecutionPolicy ByPass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    if !errorlevel! neq 0 (
        echo [ERROR] Fallo al instalar UV. Revisa tu conexion a internet.
        pause
        exit /b 1
    )
)

:: Detectar ruta de UV
set "UV_PATH=uv"
if exist "%LOCALAPPDATA%\uv\uv.exe" (
    set "UV_PATH=%LOCALAPPDATA%\uv\uv.exe"
) else if exist "%USERPROFILE%\.cargo\bin\uv.exe" (
    set "UV_PATH=%USERPROFILE%\.cargo\bin\uv.exe"
)

echo [i] UV encontrado: !UV_PATH!

:: ==============================================
:: 2. CREAR VENV CON UV SI ES NECESARIO
:: ==============================================
if not exist ".venv" (
    echo [i] Creando entorno virtual con UV...
    "!UV_PATH!" venv .venv --python 3.12
    if !errorlevel! neq 0 (
        echo [WARN] No se pudo crear venv con Python 3.12, intentando con Python por defecto...
        "!UV_PATH!" venv .venv
        if !errorlevel! neq 0 (
            echo [ERROR] Fallo al crear entorno virtual
            pause
            exit /b 1
        )
    )
)

:: ==============================================
:: 3. INSTALAR DEPENDENCIAS CON UV
:: ==============================================
echo [i] Instalando dependencias...
"!UV_PATH!" pip install -r requirements.txt
if !errorlevel! neq 0 (
    echo [ERROR] Fallo al instalar dependencias
    pause
    exit /b 1
)

:: ==============================================
:: 4. EJECUTAR APLICACION
:: ==============================================
echo [i] Iniciando aplicacion G360 NC-Sustentor...
echo.
"!UV_PATH!" run --python 3.12 main.py
if !errorlevel! neq 0 (
    echo.
    echo [ERROR] La aplicacion termino con un error (codigo: !errorlevel!)
    pause
)

exit /b