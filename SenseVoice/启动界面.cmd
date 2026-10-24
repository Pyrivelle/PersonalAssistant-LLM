@echo off
cd /d "%~dp0"
if exist "%~dp0..\data\memory_runtime.json" goto memory
if exist "%~dp0..\storage_location.json" goto memory
goto standard
:memory
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\windows\start-memory-enabled.ps1"
    exit /b
:standard
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0dashboard.py"
