@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title G360 NC-Sustentor (Portable)
cd /d "%~dp0"

REM Configuración de Rutas (Portable)
set "LOG_FILE=run_log.txt"

echo ============================================= >> "%LOG_FILE%"
echo [%date% %time%] INICIANDO VERSION PORTABLE >> "%LOG_FILE%"

REM ---------------------------------------------
REM PASO 1: ASEGURAR UV (Motor Portable)
REM ---------------------------------------------
echo Preparando entorno inteligente...
where uv >nul 2>&1
if errorlevel 1 (
    echo Configurando motor de ejecucion...
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex" >nul 2>&1
    set "PATH=%USERPROFILE%\.cargo\bin;%LOCALAPPDATA%\Programs\uv;%PATH%"
)

REM ---------------------------------------------
REM PASO 2: PYTHON Y ENTORNO VIRTUAL
REM ---------------------------------------------
if not exist ".venv\Scripts\python.exe" (
    echo Configurando entorno virtual por primera vez...
    uv venv .venv --python 3.10 --seed >> "%LOG_FILE%" 2>&1
)

REM ---------------------------------------------
REM PASO 3: LIBRERIAS
REM ---------------------------------------------
echo Verificando componentes...
uv pip install -r requirements.txt --quiet >> "%LOG_FILE%" 2>&1

REM ---------------------------------------------
REM PASO 4: ACCESO DIRECTO
REM ---------------------------------------------
if exist "create_shortcut.vbs" (
    cscript //nologo "create_shortcut.vbs" >nul 2>&1
)

REM ---------------------------------------------
REM PASO 5: LANZAMIENTO
REM ---------------------------------------------
echo Iniciando Aplicacion...
start /min .venv\Scripts\pythonw.exe main.py

echo [%date% %time%] Lanzamiento exitoso. >> "%LOG_FILE%"
exit
