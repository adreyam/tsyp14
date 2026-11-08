@echo off
title City Brain Autonomous Dashboard Server
echo ===================================================
echo Starting City Brain Live Dashboard Server...
echo ===================================================
cd /d "%~dp0"

:: Clear bytecode cache
for /d /r . %%d in (__pycache__) do @if exist "%%d" rd /s /q "%%d" 2>nul

python src/city_brain_dashboard.py
pause
