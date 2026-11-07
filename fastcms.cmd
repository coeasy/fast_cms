@echo off
REM Fast CMS 统一命令行入口（Windows）
REM 用法：fastcms.cmd init --server
setlocal EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

set "PY="
if exist "backend\.venv\Scripts\python.exe" set "PY=backend\.venv\Scripts\python.exe"
if defined PY goto run

where python >nul 2>nul
if not errorlevel 1 set "PY=python"

if not defined PY (
  echo 未找到 python，请先安装 Python 3.12+ 并加入 PATH。
  exit /b 1
)

:run
"%PY%" scripts\fastcms.py %*
exit /b %ERRORLEVEL%
