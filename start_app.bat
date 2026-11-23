@echo off
cd /d "%~dp0"
title FC27 Clubs Tracker
if not exist .venv\Scripts\streamlit.exe (
  echo The app isn't installed yet. Run setup.bat first.
  pause
  exit /b 1
)
call .venv\Scripts\activate.bat
streamlit run app.py --server.port 8527 --browser.gatherUsageStats false
pause
