@echo off
cd /d "%~dp0"
title FC27 Clubs Tracker setup
echo === FC27 Clubs Tracker setup ===
echo.
rem find Python 3.10 or newer (64 bit): the py launcher, the usual install folders, or python on PATH
set "PY="
for %%v in (3.12 3.13 3.11 3.14 3.10) do (
  if not defined PY (
    py -%%v -c "import sys; sys.exit(0 if sys.maxsize>2**32 else 1)" >nul 2>nul && set "PY=py -%%v"
  )
)
if not defined PY call :try "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY call :try "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PY call :try "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not defined PY call :try "%ProgramFiles%\Python312\python.exe"
if not defined PY call :trycmd python
if not defined PY call :trycmd python3
if not defined PY (
  echo Python 3.10 or newer, 64 bit, was not found on this PC.
  echo Download "Windows installer 64-bit" for Python 3.12 here, tick "Add python.exe to PATH",
  echo and run this file again:
  echo   https://www.python.org/downloads/release/python-31210/
  echo.
  start "" https://www.python.org/downloads/release/python-31210/
  pause
  exit /b 1
)
echo Using Python: %PY%
if exist .venv (
  .venv\Scripts\python.exe -c "import streamlit, curl_cffi, plotly" >nul 2>nul
  if not errorlevel 1 (
    echo The tracker is already set up: to open it, use start_app.bat.
    echo Setting it up again reinstalls the Python packages. Your matches and settings are kept.
    echo.
    choice /c YN /n /m "Set it up again anyway? [Y/N] "
    if errorlevel 2 exit /b 0
  )
  echo Stopping the collector if it is running...
  if exist data echo stop> data\collector_stop
  timeout /t 4 /nobreak >nul
  powershell -NoProfile -Command "$d=(Resolve-Path '.venv').Path; Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($d, [StringComparison]::OrdinalIgnoreCase) } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
  timeout /t 2 /nobreak >nul
  echo Removing the old environment...
  rmdir /s /q .venv
  if exist .venv (
    echo.
    echo Could not remove the old environment: a program is still using it.
    echo Close the tracker's windows, end pythonw.exe in Task Manager, and run setup.bat again.
    goto :fail
  )
)
echo Creating the environment...
%PY% -m venv .venv
if errorlevel 1 goto :fail
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
if errorlevel 1 goto :fail
python -c "import streamlit, plotly, pandas, curl_cffi, psutil; print('All packages installed OK')"
if errorlevel 1 goto :fail
echo.
python tools\first_check.py
set "CHECK=%errorlevel%"
echo.
if "%CHECK%"=="3" goto :done
choice /c YN /n /m "Start the collector now, and every time Windows starts? [Y/N] "
if errorlevel 2 goto :done
python -c "from clubs import collector_ctl as c; c.set_autostart(True); print('Collector started: it saves every match in the background.' if c.start() else 'The collector did not start: run start_collector.bat to see why.')"
:done
echo.
echo Setup finished. Opening the app in your browser (keep its black window open while you use it).
start "FC27 Clubs Tracker" "%~dp0start_app.bat"
pause
exit /b 0

:fail
echo.
echo *** Setup failed. Take a screenshot of this window and send it to Claude. ***
pause
exit /b 1

:try
if not exist "%~1" exit /b 0
"%~1" -c "import sys; sys.exit(0 if sys.version_info[:2]>=(3,10) and sys.maxsize>2**32 else 1)" >nul 2>nul || exit /b 0
set "PY="%~1""
exit /b 0

:trycmd
%1 -c "import sys; sys.exit(0 if sys.version_info[:2]>=(3,10) and sys.maxsize>2**32 else 1)" >nul 2>nul || exit /b 0
for /f "delims=" %%p in ('%1 -c "import sys; print(sys.executable)"') do set "PY="%%p""
exit /b 0
