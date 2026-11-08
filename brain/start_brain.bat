@echo off
REM City Brain on Windows (no Godot Twin): the orchestrator (mock source unless brain\config\brain.yaml says live),
REM the three axis engines and the control center. Each service opens its own window; stop_brain.bat closes them.
setlocal
cd /d "%~dp0"
set "B=%~dp0"
if not defined BRAIN_TOKEN set "BRAIN_TOKEN=tsyp14-brain-dev-token"
if not defined ORCHESTRATOR_TOKEN set "ORCHESTRATOR_TOKEN=tsyp14-control-center-token"
set "PYTHONIOENCODING=utf-8"
set "NO_BROWSER=1"
where python >nul 2>nul || (echo Python was not found. Install Python 3.10+ and tick "Add python.exe to PATH". & pause & exit /b 1)
if not exist "%B%control_center" (echo Old folder layout: run brain\reorganize.sh in Git Bash or WSL first. & pause & exit /b 1)

echo [1/5] City Brain orchestrator on http://localhost:8500
start "CB City Brain orchestrator (8500)" /D "%B%" cmd /k "python -m pip install --quiet -r requirements.txt && python run_brain.py"

echo [2/5] Energy engine on http://localhost:8400
set "PYTHONPATH=src;."
start "CB Energy engine (8400)" /D "%B%axes\energy\engine\brain" cmd /k "python -m pip install --quiet -r requirements.txt && python -m sim.server --port 8400 --speed 4"
set "PYTHONPATH="

echo [3/5] Transportation engine on http://localhost:8000, with its mock Twin (9001) and mock Guardian (9002)
set "T=%B%axes\transportation\engine\brain"
set "LOGS_DIR=%B%axes\transportation\engine\logs"
set "BRAIN_URL=http://127.0.0.1:8000"
start "CB Transport mock Twin (9001)" /D "%T%" cmd /k "python -m mocks.mock_twin"
start "CB Transport mock Guardian (9002)" /D "%T%" cmd /k "python -m mocks.mock_guardian"
start "CB Transport engine (8000)" /D "%T%" cmd /k "run_city.bat"

echo [4/5] Pollution engine (waste and air) on http://localhost:8100
start "CB Pollution engine (8100)" /D "%B%axes\pollution\engine\brain" cmd /k "python -m pip install --quiet -r requirements.txt && python -m demo.live --no-browser"

echo [5/5] Control center on http://localhost:8765
timeout /t 8 /nobreak >nul
start "CB Control center (8765)" /D "%B%control_center" cmd /k "python server.py"
timeout /t 3 /nobreak >nul
start "" "http://localhost:8765"
echo All started. Close everything with stop_brain.bat.
timeout /t 6 >nul
