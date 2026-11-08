@echo off
REM Closes every window opened by start_brain.bat (their titles start with "CB ").
for %%t in ("CB City Brain orchestrator*" "CB Energy engine*" "CB Transport mock Twin*" "CB Transport mock Guardian*" "CB Transport engine*" "CB Pollution engine*" "CB Control center*") do (
  taskkill /FI "WINDOWTITLE eq %%~t" /T /F >nul 2>nul
)
echo City Brain stopped.
timeout /t 3 >nul
