@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run INSTALL.bat once before starting the workspace.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" main_flet.py
if errorlevel 1 pause
