@echo off
REM Orderflow Station - double-click to launch.
REM pythonw.exe runs the GUI with no console window attached.
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
    echo Could not find .venv\Scripts\pythonw.exe
    echo.
    echo Run this once from a terminal in this folder:
    echo     python -m venv .venv
    echo     .venv\Scripts\python.exe -m pip install -e .
    echo.
    pause
    exit /b 1
)

start "" ".venv\Scripts\pythonw.exe" -m orderflow.app
