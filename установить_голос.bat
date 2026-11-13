@echo off
rem Voice for Yanka: vosk + sounddevice + Russian speech model (~50 MB). Free, works offline.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tmp\install_voice.ps1"
echo.
pause
