@echo off
chcp 65001 >nul
title G360 - CustomTkinter App

echo.
echo ==============================================
echo   G360 CustomTkinter Application
echo ==============================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python no encontrado
    pause
    exit /b 1
)

echo Verificando customtkinter...
pip show customtkinter >nul 2>&1
if errorlevel 1 (
    echo   Instalando customtkinter...
    pip install customtkinter
)

echo.
echo Ejecutando aplicacion...
python src\main.py

pause