@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title G360 App
cd /d "%~dp0"

echo.
echo ==============================================
echo   G360 - Running Application
echo ==============================================
echo.

REM --------------------------
REM VERIFICAR PYTHON (usando UV)
REM --------------------------
echo [1/4] Verificando entorno...

where python >nul 2>&1
if not errorlevel 1 (
    echo   Python encontrado, usando instalacion del sistema
    goto :run_app
)

where uv >nul 2>&1
if not errorlevel 1 (
    echo   UV encontrado, instalando Python...
    uv python install 3.10
    goto :run_app
)

echo   UV no encontrado, instalando...
powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; irm https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip -OutFile uv.zip"
tar -xf uv.zip >nul 2>&1
del uv.zip >nul 2>&1
set "PATH=%CD%;%PATH%"
uv python install 3.10

:run_app
REM --------------------------
REM CREAR ENTORNO VIRTUAL
REM --------------------------
echo [2/4] Preparando entorno virtual...

if not exist ".venv\Scripts\python.exe" (
    uv venv .venv --python 3.10 --seed 2>nul
    if not exist ".venv\Scripts\python.exe" (
        echo ERROR: No se pudo crear el entorno virtual
        pause
        exit /b
    )
)

REM --------------------------
REM INSTALAR DEPENDENCIAS
REM --------------------------
echo [3/4] Instalando dependencias...

call .venv\Scripts\activate.bat
uv pip install -r requirements.txt >nul 2>&1

REM --------------------------
REM CREAR ACCESO DIRECTO
REM --------------------------
echo [4/4] Verificando acceso directo...

if exist "%~dp0create_shortcut.vbs" (
    cscript //nologo "%~dp0create_shortcut.vbs" >nul 2>&1
)

REM --------------------------
REM INICIAR APLICACION
REM --------------------------
echo.
echo ==============================================
echo   Iniciando G360 App...
echo ==============================================
echo.

start /wait /min pythonw.exe src\main.py

exit