@echo off
:: Script para ejecucion portable e independiente
title G360 Python Portable - %CD%

echo [G360] Iniciando aplicacion portable...

:: Aseguramos que se use el entorno sincronizado
uv run python src/main.py

if %errorlevel% neq 0 pause