@echo off
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"
"%~dp0.venv\Scripts\python.exe" "%~dp0qq_bridge.py"
pause
