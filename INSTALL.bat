@echo off
setlocal
cd /d "%~dp0"
py -3.12 --version >nul 2>&1
if errorlevel 1 (
  echo Python 3.12 was not found. Install 64-bit Python 3.12 with the Windows py launcher.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" py -3.12 -m venv .venv
if not exist ".venv\Scripts\python.exe" (
  echo Could not create the UI environment.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo Installation failed. See the error above.
  pause
  exit /b 1
)
echo Setup complete. Open START.bat to launch Nexus Desk.
pause
