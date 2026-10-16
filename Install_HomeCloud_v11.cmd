@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title Home Cloud v11 Installer
set "INSTALL_ARGS=%*"
if "%~1"=="" (
  echo.
  echo Choose installation mode:
  echo   1. Standard  - lightweight, no large AI download
  echo   2. Full AI   - semantic search and AI visual tagging
  echo.
  set /p "HC_MODE=Enter 1 or 2 [1]: "
  if "!HC_MODE!"=="2" (
    set "INSTALL_ARGS=-Mode FullAI"
  ) else (
    set "INSTALL_ARGS=-Mode Standard"
  )
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install_HomeCloud_v11.ps1" %INSTALL_ARGS%
set "EXITCODE=%ERRORLEVEL%"
echo.
if not "%EXITCODE%"=="0" echo Installation stopped with error code %EXITCODE%.
pause
exit /b %EXITCODE%
