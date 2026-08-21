@echo off
REM Double-click this when Orderflow Station will not start.
REM Uses python.exe (not pythonw) so the report is visible in this window.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Could not find .venv\Scripts\python.exe
    echo The virtualenv is missing. From a terminal in this folder:
    echo     python -m venv .venv
    echo     .venv\Scripts\python.exe -m pip install -e .
    echo.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -m orderflow.app --doctor
echo.
echo If the app still will not start, the two logs above
echo ^(data\launch.log and data\crash.log^) hold the reason.
echo.
pause
