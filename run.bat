@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  call setup.bat
  if errorlevel 1 (
    pause
    exit /b 1
  )
)
".venv\Scripts\python.exe" app.py %*
if errorlevel 1 pause
