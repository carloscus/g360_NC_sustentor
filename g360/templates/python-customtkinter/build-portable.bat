@echo off
chcp 65001 >nul
title G360 - Portable CustomTkinter

echo.
echo ==============================================
echo   G360 CustomTkinter - Portable
echo   (Requires Python installed)
echo ==============================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python no encontrado
    pause
    exit /b 1
)

pip show customtkinter >nul 2>&1
if errorlevel 1 (
    pip install customtkinter
)

echo.
echo Ejecutando G360 App...
python src\main.py

pause