@echo off
setlocal
title G360 NC-Sustentor - Portable Launcher

echo =======================================================
echo          G360 NC-SUSTENTOR - SISTEMA PORTABLE
echo =======================================================
echo.

o:: 0. Crear Acceso Directo en el Escritorio (si no existe)
set "SHORTCUT_NAME=G360 NC-Sustentor.lnk"
set "ICON_PATH=%~dp0assets\images\icon.ico"

if not exist "%USERPROFILE%\Desktop\%SHORTCUT_NAME%" (
    echo [i] Creando acceso directo en el escritorio...
 echo [OK] Acceso directo creado exitosamente.
)

:: 1. Verificar/Instalar motor ligero uv
where uv >nul 2>&1
if %errorlevel% neq 0 (
    echo [i] No se detecto el motor uv. Instalando entorno ligero G360...
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    :: Definir ruta local de uv para esta sesion por si el PATH no se refresca
    set "UV_PATH=%USERPROFILE%\.cargo\bin\uv.exe"
) else (
    set "UV_PATH=uv"
)

:: 2. Ejecutar la Aplicacion
echo [i] Sincronizando dependencias y lanzando interfaz...
echo.

:: Ejecutamos usando el puerto 8888 y forzando la instalacion de dependencias si faltan
"%UV_PATH%" run --python 3.12 main.py

if %ERRORLEVEL% neq 0 (
    echo.
    echo -------------------------------------------------------
    echo [!] ERROR: La aplicacion no pudo iniciar.
    echo Si el error es de Red (10013), ejecute como Administrador.
    echo -------------------------------------------------------
    pause
)
