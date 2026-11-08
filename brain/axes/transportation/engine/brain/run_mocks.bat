@echo off
REM Starts the stand-ins of the other two layers (mock Twin on 9001, mock Guardian on 9002) in two windows.
setlocal
cd /d "%~dp0"
if not defined LOGS_DIR set LOGS_DIR=..\logs
if not defined BRAIN_URL set BRAIN_URL=http://127.0.0.1:8000
python -m pip install --quiet -r requirements_city.txt
start "mock Twin (9001)" cmd /k python -m mocks.mock_twin
start "mock Guardian (9002)" cmd /k python -m mocks.mock_guardian
echo Mocks started. Now run run_city.bat, then from the repository root:  python scenarios\run.py T3
