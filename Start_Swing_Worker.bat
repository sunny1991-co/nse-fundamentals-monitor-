@echo off
setlocal
cd /d "%~dp0"
set "SWING_PYTHON=%USERPROFILE%\nse-monitor\.venv\Scripts\python.exe"
if not exist "%SWING_PYTHON%" (
  echo Python virtual environment missing. Setup must be completed first.
  pause
  exit /b 1
)
"%SWING_PYTHON%" swing_worker.py
pause
