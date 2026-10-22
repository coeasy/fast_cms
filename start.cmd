@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title Fast CMS Launcher
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

set "PY="
if exist "backend\.venv\Scripts\python.exe" set "PY=backend\.venv\Scripts\python.exe"
if defined PY goto run

where python >nul 2>nul
if not errorlevel 1 set "PY=python"
if defined PY goto run

where py >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if defined PY goto run

echo Python 3.12+ not found. Install it and add it to PATH.
pause
exit /b 1

:run
%PY% scripts\start.py %*
set "CODE=%ERRORLEVEL%"
if not "%CODE%"=="0" (
  echo.
  echo Launcher exited with code %CODE%
  pause
)
exit /b %CODE%
