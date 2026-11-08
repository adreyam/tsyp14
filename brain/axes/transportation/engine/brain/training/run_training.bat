@echo off
REM Train the traffic-signal agent with a LIVE page. Run it from inside the sumo-rl-main folder:
REM     training\run_training.bat            (50 more episodes)
REM     training\run_training.bat 200        (200 more episodes)
REM Run it again later: it continues from the saved training (runs\main). To start over: training\run_training.bat 50 --fresh
setlocal
set "EPS=%~1"
if "%EPS%"=="" set "EPS=50"
shift

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

if not exist "training\train.py" goto wrong_folder
python -m pip install --quiet gymnasium pettingzoo pandas numpy traci sumolib
if not exist "data\mock_traffic\manifest.csv" (
  echo Creating the mock training data ^(240 scenarios, about a minute^)...
  python training\mock_data.py
)
python training\train.py --episodes %EPS% --live %1 %2 %3 %4
goto end

:no_sumo
echo SUMO was not found. Install it from https://sumo.dlr.de/Download and run this file again.
goto end
:wrong_folder
echo Run this file from inside the sumo-rl-main folder.
:end
pause
endlocal
