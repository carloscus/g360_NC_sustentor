@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title G360 NC-Sustentor
cd /d "%~dp0"

REM =============================================
REM  RUN.BAT VERSION FINAL - FLUJO CORRECTO
REM =============================================

REM 📝 Log
echo. >> run_log.txt
echo ============================================= >> run_log.txt
echo [%date% %time%] INICIANDO RUN.BAT >> run_log.txt

REM --------------------------
REM PASO 1: VERIFICAR UV
REM --------------------------
echo [%date% %time%] Paso 1/5: Verificando UV >> run_log.txt

where uv >nul 2>&1
if errorlevel 1 (
    echo Instalando gestor de paquetes UV...
    echo [%date% %time%] Instalando UV >> run_log.txt
    
    powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; irm https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip -OutFile uv.zip"
    tar -xf uv.zip >nul
    del uv.zip >nul
    set "PATH=%CD%;!PATH!"
    
    REM Buscar UV despues de instalar
    if exist "%USERPROFILE%\.cargo\bin\uv.exe" set "PATH=%USERPROFILE%\.cargo\bin;!PATH!"
    if exist "%LOCALAPPDATA%\Programs\uv\uv.exe" set "PATH=%LOCALAPPDATA%\Programs\uv;!PATH!"
    
    timeout /t 2 /nobreak >nul
)

REM --------------------------
REM PASO 2: VERIFICAR PYTHON
REM --------------------------
echo [%date% %time%] Paso 2/5: Verificando Python >> run_log.txt

uv python install 3.10 >nul

REM --------------------------
REM PASO 3: CREAR ENTORNO VIRTUAL
REM --------------------------
echo [%date% %time%] Paso 3/5: Creando entorno virtual >> run_log.txt

if not exist ".venv\Scripts\python.exe" (
    echo Creando entorno virtual...
    uv venv .venv --python 3.10 --seed
    
    if not exist ".venv\Scripts\python.exe" (
        echo ERROR: No se pudo crear el entorno virtual
        echo [%date% %time%] ERROR: Fallo creacion venv >> run_log.txt
        pause
        exit /b
    )
)

REM --------------------------
REM PASO 4: INSTALAR DEPENDENCIAS
REM --------------------------
echo [%date% %time%] Paso 4/5: Instalando dependencias >> run_log.txt

call .venv\Scripts\activate.bat
uv pip install -r requirements.txt >nul

REM --------------------------
REM PASO 4.1: CREAR ACCESO DIRECTO EN ESCRITORIO
REM --------------------------
echo [%date% %time%] Paso 4.1/5: Creando acceso directo >> run_log.txt

if exist "%~dp0create_shortcut.vbs" (
    cscript //nologo "%~dp0create_shortcut.vbs" >nul 2>&1
)

REM --------------------------
REM PASO 5: INICIAR APLICACION
REM --------------------------
echo [%date% %time%] Paso 5/5: Iniciando aplicacion >> run_log.txt

REM Minimizar CMD
powershell -WindowStyle Minimized -Command "" >nul

echo Iniciando G360 NC-Sustentor...
start /wait /min pythonw.exe main.py

echo [%date% %time%] Aplicacion cerrada >> run_log.txt
exit