@echo off
setlocal
title G360 NC-Sustentor - Portable Launcher

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
:: 1. VERIFICAR MOTOR UV
:: ==============================================
where uv >nul 2>&1
if %errorlevel% neq 0 (
    echo [i] Instalando motor de ejecucion UV (G360 Lightweight)...
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    
    :: Deteccion dinamica de la ruta de instalacion de usuario
    if exist "%LOCALAPPDATA%\uv\uv.exe" (
        set "UV_PATH=%LOCALAPPDATA%\uv\uv.exe"
    ) else if exist "%USERPROFILE%\.cargo\bin\uv.exe" (
        set "UV_PATH=%USERPROFILE%\.cargo\bin\uv.exe"
    ) else (
        set "UV_PATH=uv"
    )
) else (
    set "UV_PATH=uv"
)

:: ==============================================
:: 2. EJECUTAR APLICACION Y CERRAR CMD
:: ==============================================
echo [i] Iniciando aplicacion...

:: ✅ Ejecutamos en proceso separado, MINIMIZADO, y cerramos este CMD inmediatamente
:: ✅ Cuando cierres la aplicacion Flet, el CMD oculto se cerrara automaticamente tambien
start /min "" cmd /c ""%UV_PATH%" run --python 3.12 main.py"

:: Salir y cerrar esta ventana CMD inmediatamente
exit /b