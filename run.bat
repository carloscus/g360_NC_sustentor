@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
cd /d "%~dp0"

REM Log en APPDATA para evitar PermissionError cuando el bat tiene el archivo abierto.
REM OJO: esto va ANTES del salto a :faststart. Si %LOG_FILE% se define despues,
REM el modo fast redireccionaria a ">>" vacio y el .bat muere con
REM "The syntax of the command is incorrect" sin lanzar nada.
set "LOG_FILE=%APPDATA%\g360-erp-nc-sustentor\run_log.txt"
mkdir "%APPDATA%\g360-erp-nc-sustentor" >nul 2>&1

REM Modo rapido: "run.bat fast" salta uv/Python/venv/sync/migracion/acceso-directo
REM si el entorno ya existe. Uso diario: doble clic en launch.vbs (ya pasa fast).
if /i "%~1"=="fast" goto :checkenv
goto :fullstart

:checkenv
if exist ".venv\Scripts\python.exe" goto :faststart
echo   Modo fast pedido pero no hay entorno; arranque completo...
goto :fullstart

:fullstart
echo [%DATE% %TIME%] Inicio Reconocimiento Comercial - CIPSA > %LOG_FILE%
echo.
echo === Reconocimiento Comercial - CIPSA ===
echo.

REM ============================================
REM [1/6] Verificar / Instalar uv
echo [%DATE% %TIME%] [1/6] Verificando uv... >> %LOG_FILE%
echo [1/6] Verificando uv...

where uv >nul 2>&1
if errorlevel 1 (
    if exist "uv.exe" (
        echo   Usando uv.exe local...
        set "PATH=%~dp0;%PATH%"
    ) else (
        echo   uv no encontrado. Descargando e instalando...
        powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex" >> %LOG_FILE% 2>&1
        if errorlevel 1 (
            echo [%DATE% %TIME%] [ERROR] No se pudo instalar uv >> %LOG_FILE%
            echo   ERROR: No se pudo instalar uv automaticamente.
            pause
            exit /b 1
        )
        echo   uv instalado correctamente.
    )
) else (
    echo   uv encontrado.
)

for /f "tokens=*" %%i in ('where uv 2^>nul') do set "UV_PATH=%%~dpi"
if defined UV_PATH set "PATH=%UV_PATH%;%PATH%"

echo.

REM ============================================
REM [2/6] Verificar / Instalar Python 3.11
echo [%DATE% %TIME%] [2/6] Verificando Python 3.11... >> %LOG_FILE%
echo [2/6] Verificando Python 3.11...

where uv >nul 2>&1
if errorlevel 1 (
    echo   ERROR: uv no disponible. No se puede instalar Python.
    pause
    exit /b 1
)

uv python list --only-installed 2>nul | find "3.11" >nul
if errorlevel 1 (
    echo   Python 3.11 no encontrado. Instalando con uv...
    uv python install 3.11 >> %LOG_FILE% 2>&1
    if errorlevel 1 (
        echo [%DATE% %TIME%] [ERROR] No se pudo instalar Python 3.11 >> %LOG_FILE%
        echo   ERROR: No se pudo instalar Python 3.11.
        pause
        exit /b 1
    )
    echo   Python 3.11 instalado.
) else (
    echo   Python 3.11 encontrado.
)

echo.

REM ============================================
REM [3/6] Crear entorno virtual e instalar dependencias
echo [%DATE% %TIME%] [3/6] Configurando entorno virtual... >> %LOG_FILE%
echo [3/6] Configurando entorno virtual...
if not exist ".venv\Scripts\python.exe" (
    echo   Creando entorno virtual...
    uv venv .venv --python 3.11 >> %LOG_FILE% 2>&1
    if errorlevel 1 (
        echo [%DATE% %TIME%] [ERROR] No se pudo crear el entorno virtual >> %LOG_FILE%
        echo   ERROR: No se pudo crear el entorno virtual.
        pause
        exit /b 1
    )
    echo   Entorno virtual creado.
)

echo   Instalando dependencias...
uv sync >> %LOG_FILE% 2>&1
if errorlevel 1 (
    echo [%DATE% %TIME%] [ERROR] Error al sincronizar dependencias >> %LOG_FILE%
    echo   ERROR: No se pudieron instalar las dependencias.
    echo   Revise %LOG_FILE% para mas detalles.
    pause
    exit /b 1
)
echo [%DATE% %TIME%] [3/6] Dependencias instaladas >> %LOG_FILE%
echo   Dependencias instaladas.

echo.

REM ============================================
REM [4/6] Migrar esquema DB si hay cambios
echo [%DATE% %TIME%] [4/6] Verificando migraciones DB... >> %LOG_FILE%
echo [4/6] Verificando migraciones DB...
"%~dp0.venv\Scripts\python.exe" -c "import sys; sys.path.insert(0,'src'); from src.core import ventas_db; ventas_db.init_db(); print('  DB migrada correctamente')" >> %LOG_FILE% 2>&1
if errorlevel 1 (
    echo [%DATE% %TIME%] [ERROR] Fallo en migracion DB >> %LOG_FILE%
    echo   ERROR: No se pudo migrar la DB local.
    pause
    exit /b 1
)
echo   DB migrada correctamente.
echo [%DATE% %TIME%] [4/6] Migraciones DB OK >> %LOG_FILE%

echo.

REM ============================================
REM [5/6] Crear acceso directo
echo [%DATE% %TIME%] [5/6] Creando acceso directo... >> %LOG_FILE%
echo [5/6] Creando acceso directo...
if exist "create_shortcut.vbs" (
    cscript //nologo create_shortcut.vbs >> %LOG_FILE% 2>&1
    echo [%DATE% %TIME%] [5/6] Acceso directo creado >> %LOG_FILE%
    echo   Acceso directo creado en el escritorio.
) else (
    echo   create_shortcut.vbs no encontrado - omitiendo.
)

echo.

REM ============================================
REM [6/6] Iniciar aplicacion
:faststart
echo [%DATE% %TIME%] [6/6] Iniciando Reconocimiento Comercial - CIPSA... >> %LOG_FILE%
echo [6/6] Iniciando Reconocimiento Comercial - CIPSA...
echo.

REM Verificar DB local (solo informativo)
set "DB_FILE=%APPDATA%\g360-erp-nc-sustentor\data\historial.db"
if exist "%DB_FILE%" (
    echo [%DATE% %TIME%] DB local encontrada >> %LOG_FILE%
    echo   DB local: existe en %APPDATA%\g360-erp-nc-sustentor\data
) else (
    echo [%DATE% %TIME%] DB local no existe - primera carga pendiente >> %LOG_FILE%
    echo   DB local: sin datos - usa el portal de conexion para descargar el historial
)
echo.

REM Nota informativa sobre lock de captura (el app lo valida por PID)
if exist "%APPDATA%\g360-erp-nc-sustentor\data\raw\capture.lock" (
    echo [%DATE% %TIME%] capture.lock presente - el app lo validara por PID >> %LOG_FILE%
)

REM Lanzar con el python del venv directo (rapido; evita el sync implicito de "uv run").
REM El sync de dependencias ya se hizo en el paso [3/6] del arranque completo.
echo [%DATE% %TIME%] Lanzando aplicacion... >> %LOG_FILE%
echo   App en ejecucion. Cierre la ventana de la app para salir.
echo.
if exist ".venv\Scripts\python.exe" (
    .venv\Scripts\python.exe main.py >> %LOG_FILE% 2>&1
) else (
    uv run python main.py >> %LOG_FILE% 2>&1
)
if errorlevel 1 (
    echo.
    echo [%DATE% %TIME%] [ERROR] La aplicacion fallo >> %LOG_FILE%
    echo   ERROR: La aplicacion fallo al iniciar.
    echo   Revise %LOG_FILE% para mas detalles.
    echo.
    pause
) else (
    echo [%DATE% %TIME%] Aplicacion cerrada por el usuario >> %LOG_FILE%
)

echo [%DATE% %TIME%] Reconocimiento Comercial - CIPSA terminado normalmente >> %LOG_FILE%
echo.
echo === Reconocimiento Comercial - CIPSA - Terminado ===
echo.
