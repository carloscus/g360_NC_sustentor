@echo off
setlocal enabledelayedexpansion
title G360 NC-Sustentor
cd /d "%~dp0"

:: ==============================================
:: 0. CREAR ACCESO DIRECTO EN ESCRITORIO (SOLO PRIMERA VEZ)
:: ==============================================
set "SHORTCUT_NAME=G360 NC-Sustentor.lnk"
set "SHORTCUT_PATH=%USERPROFILE%\Desktop\%SHORTCUT_NAME%"
set "ICON_PATH=%~dp0assets\images\favicon.ico"

if not exist "!SHORTCUT_PATH!" (
    echo.
    echo ===============================================
    echo          PRIMERA EJECUCION - INSTALANDO
    echo ===============================================
    echo.
    echo [*] Creando acceso directo en el escritorio...
    powershell -NoProfile -Command "$WshShell = New-Object -ComObject WScript.Shell; $Shortcut = $WshShell.CreateShortcut('!SHORTCUT_PATH!'); $Shortcut.TargetPath = '%~dp0run.bat'; $Shortcut.WorkingDirectory = '%~dp0'; $Shortcut.IconLocation = '!ICON_PATH!'; $Shortcut.Save()"
)

:: ==============================================
:: 1. VERIFICAR E INSTALAR UV SI ES NECESARIO
:: ==============================================
where uv >nul 2>&1
if %errorlevel% neq 0 (
    echo [*] Instalando UV (gestor de paquetes rapido)...
    powershell -NoProfile -ExecutionPolicy ByPass -Command "irm https://astral.sh/uv/install.ps1 | iex" >nul 2>&1
)

:: Detectar ruta de UV (refrescar PATH)
for /f "delims=" %%i in ('powershell -NoProfile -Command "[System.Environment]::GetEnvironmentVariable('Path', 'User')" 2^>nul') do set "USER_PATH=%%i"
set "PATH=!USER_PATH!;!PATH!"

:: Buscar UV
set "UV_PATH=uv"
if exist "%LOCALAPPDATA%\uv\uv.exe" set "UV_PATH=%LOCALAPPDATA%\uv\uv.exe"
if exist "%USERPROFILE%\.cargo\bin\uv.exe" set "UV_PATH=%USERPROFILE%\.cargo\bin\uv.exe"

:: ==============================================
:: 2. CREAR VENV Y INSTALAR DEPENDENCIAS (SOLO SI NO EXISTE)
:: ==============================================
if not exist ".venv\Scripts\python.exe" (
    echo [*] Creando entorno virtual e instalando dependencias...
    "!UV_PATH!" venv .venv --python 3.12 >nul 2>&1
    if !errorlevel! neq 0 (
        "!UV_PATH!" venv .venv >nul 2>&1
    )
    
    echo [*] Instalando paquetes...
    call .venv\Scripts\activate.bat
    "!UV_PATH!" pip install -q -r requirements.txt >nul 2>&1
    if !errorlevel! neq 0 (
        echo [ERROR] Fallo al instalar dependencias
        pause
        exit /b 1
    )
)

:: ==============================================
:: 3. EJECUTAR APLICACION
:: ==============================================
echo [*] Iniciando G360 NC-Sustentor...
call .venv\Scripts\activate.bat
python main.py

exit /b