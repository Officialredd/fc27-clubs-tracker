@echo off
cd /d "%~dp0"
title FC27 Clubs collector
if not exist .venv\Scripts\python.exe (
  echo The tracker isn't installed yet. Run setup.bat first.
  pause
  exit /b 1
)
echo The collector checks EA for new matches and saves them. Close this window (or Ctrl+C) to stop it.
echo If it's already running in the background, this window says so and stops.
echo.
call .venv\Scripts\activate.bat
python collector.py
pause
