@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title G360 NC-Sustentor
cd /d "%~dp0"

REM Configuración de Rutas
set "LOG_DIR=dev_logs"
if not exist "!LOG_DIR!" mkdir "!LOG_DIR!"
set "LOG_FILE=!LOG_DIR!\run_log.txt"

echo ============================================= >> "%LOG_FILE%"
echo [%date% %time%] INICIANDO SISTEMA G360 >> "%LOG_FILE%"

REM ---------------------------------------------
REM PASO 1: ASEGURAR UV (Gestor de Paquetes)
REM ---------------------------------------------
echo Verificando entorno de ejecucion...
where uv >nul 2>&1
if errorlevel 1 (
    echo Instalando motor de dependencias G360 (uv)...
    echo [%date% %time%] Instalando UV... >> "%LOG_FILE%"
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex" >nul 2>&1
    
    REM Añadir rutas comunes de UV al PATH temporal
    set "PATH=%USERPROFILE%\.cargo\bin;%LOCALAPPDATA%\Programs\uv;%PATH%"
    
    where uv >nul 2>&1
    if errorlevel 1 (
        echo ERROR: No se pudo instalar 'uv'. Verifique su conexion a internet.
        echo [%date% %time%] ERROR: Fallo instalacion UV >> "%LOG_FILE%"
        pause
        exit /b
    )
)

REM ---------------------------------------------
REM PASO 2: GESTION DE PYTHON Y ENTORNO VIRTUAL
REM ---------------------------------------------
echo [%date% %time%] Paso 2: Configurando Python y VENV >> "%LOG_FILE%"

REM UV se encarga de descargar Python si no existe
if not exist ".venv\Scripts\python.exe" (
    echo Creando entorno virtual seguro...
    uv venv .venv --python 3.10 --seed >> "%LOG_FILE%" 2>&1
)

REM ---------------------------------------------
REM PASO 3: ACTUALIZAR DEPENDENCIAS
REM ---------------------------------------------
echo [%date% %time%] Paso 3: Instalando librerias >> "%LOG_FILE%"
echo Optimizando librerias...

REM Usar uv pip para instalar/actualizar de forma ultra rapida
uv pip install -r requirements.txt --quiet >> "%LOG_FILE%" 2>&1

if errorlevel 1 (
    echo ERROR: No se pudieron instalar las dependencias.
    echo [%date% %time%] ERROR: Fallo pip install >> "%LOG_FILE%"
    pause
    exit /b
)

REM ---------------------------------------------
REM PASO 4: ACCESO DIRECTO (Opcional)
REM ---------------------------------------------
if exist "create_shortcut.vbs" (
    echo Creando acceso directo en escritorio...
    cscript //nologo "create_shortcut.vbs" >nul 2>&1
)

REM ---------------------------------------------
REM PASO 5: ARRANQUE DE APLICACION
REM ---------------------------------------------
echo [%date% %time%] Paso 5: Lanzando App >> "%LOG_FILE%"
echo Iniciando G360 NC-Sustentor...

REM Lanzar en modo ventana sin consola (pythonw)
start /min .venv\Scripts\pythonw.exe main.py

echo [%date% %time%] Sesion iniciada correctamente. >> "%LOG_FILE%"
exit
