@echo off
cd /d "%~dp0"
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY if exist "C:\Python315\python.exe" set "PY=C:\Python315\python.exe"
if not defined PY set "PY=python"
%PY% "%~dp0update.py"
echo.
pause
