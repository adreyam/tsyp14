@echo off
REM Opens the ambulance-priority demo in sumo-gui. Put this file in the sumo-rl-main folder (next to setup.py) and run it.
setlocal

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

REM 2. Check we are in the right folder
if not exist "experiments\emergency_preemption_demo.py" goto wrong_folder

REM 3. Install the Python packages if missing, then run
python -m pip install --quiet gymnasium pettingzoo pandas numpy traci sumolib
echo.
echo The demo runs twice in sumo-gui, in slow motion: first WITHOUT priority, then WITH priority.
echo It runs by itself (no Play button needed) and the camera follows the red ambulance.
echo Press Enter in this window when it asks, and again when each run has finished.
echo.
python experiments\emergency_preemption_demo.py --gui
goto end

:no_sumo
echo SUMO was not found on this computer. Install SUMO for Windows from https://sumo.dlr.de/Download and run this file again.
goto end

:wrong_folder
echo Run this file from inside the sumo-rl-main folder (the one that contains the experiments folder).

:end
pause
endlocal
