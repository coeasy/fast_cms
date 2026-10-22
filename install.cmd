@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
title Fast CMS Installer
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

set "PY="
if exist "backend\.venv\Scripts\python.exe" set "PY=backend\.venv\Scripts\python.exe"
if defined PY goto run

rem 已有 Miniconda/Anaconda/Miniforge 时优先使用其 base 环境
for %%B in (
  "%USERPROFILE%\miniconda3"
  "%USERPROFILE%\anaconda3"
  "%USERPROFILE%\miniforge3"
  "%USERPROFILE%\AppData\Local\miniconda3"
  "%LOCALAPPDATA%\miniconda3"
  "%ProgramData%\miniconda3"
  "%ProgramData%\anaconda3"
  "D:\miniconda3" "D:\anaconda3" "C:\ProgramData\Miniconda3"
) do if not defined PY if exist "%%~B\python.exe" set "PY=%%~B\python.exe"
if defined PY goto run

if defined CONDA_EXE for /f "delims=" %%I in ('"%CONDA_EXE%" info --base 2^>nul') do if not defined PY if exist "%%I\python.exe" set "PY=%%I\python.exe"
if defined PY goto run

where python >nul 2>nul
if not errorlevel 1 set "PY=python"
if defined PY goto run

where py >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if defined PY goto run

echo 未找到 Python 3.12+，也没有检测到 Miniconda/Anaconda。
echo 请先安装 Miniconda（https://docs.conda.io/en/latest/miniconda.html）或 Python 3.12+，再重新运行本脚本。
pause
exit /b 1

:run
echo 使用解释器：%PY%
%PY% scripts\install.py %*
set "CODE=%ERRORLEVEL%"
if not "%CODE%"=="0" (
  echo.
  echo 安装脚本以退出码 %CODE% 结束
  pause
)
exit /b %CODE%
