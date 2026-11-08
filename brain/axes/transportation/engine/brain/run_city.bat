@echo off
REM City Brain - transportation layer: SUMO-RL traffic signals + smart parking, one dashboard.
REM Put nothing else to do: double-click this file or run it from CMD inside this folder.
setlocal
cd /d "%~dp0"

REM 1. Find SUMO (SUMO_HOME, the usual install folders, or PATH)
set "FOUND="
if defined SUMO_HOME if exist "%SUMO_HOME%\bin\sumo.exe" set "FOUND=%SUMO_HOME%"
if not defined FOUND if exist "C:\Program Files (x86)\Eclipse\Sumo\bin\sumo.exe" set "FOUND=C:\Program Files (x86)\Eclipse\Sumo"
if not defined FOUND if exist "C:\Program Files\Eclipse\Sumo\bin\sumo.exe" set "FOUND=C:\Program Files\Eclipse\Sumo"
if not defined FOUND (
  for /f "delims=" %%i in ('where sumo 2^>nul') do if not defined FOUND for %%j in ("%%~dpi..") do set "FOUND=%%~fj"
)
if not defined FOUND goto no_sumo
set "SUMO_HOME=%FOUND%"
set "PATH=%SUMO_HOME%\bin;%PATH%"
echo Using SUMO in: %SUMO_HOME%

REM 2. Python packages (only installs what is missing)
python -m pip install --quiet -r requirements_city.txt
if errorlevel 1 goto pip_fail

REM 3. Start the city (the dashboard opens in your browser)
if not defined PORT set PORT=8000
set PYTHONIOENCODING=utf-8
echo.
echo Starting City Brain on http://localhost:%PORT%  (Ctrl+C here to stop; the training is saved automatically)
echo The signals continue learning from runs\city (first start: from your saved runs\main training).
echo.
if not defined NO_BROWSER start "" "http://localhost:%PORT%"
python -m citybrain.server
goto end

:no_sumo
echo SUMO was not found. Install SUMO (https://eclipse.dev/sumo/) or set SUMO_HOME, then run this file again.
goto end
:pip_fail
echo Could not install the Python packages. Check your internet connection / that "python" is Python 3.9+.
:end
pause
