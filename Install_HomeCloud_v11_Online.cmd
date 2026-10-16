@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title Home Cloud v11 Online Installer
echo.
echo ================================================
echo       HOME CLOUD v11 - ONLINE INSTALLER
echo ================================================
echo.
echo 1. Standard - lightweight, no semantic AI
echo 2. Full AI  - downloads semantic AI packages/model
echo.
set /p "HC_CHOICE=Choose 1 or 2 [1]: "
if not defined HC_CHOICE set "HC_CHOICE=1"
set "HC_MODE=Standard"
if "!HC_CHOICE!"=="2" set "HC_MODE=FullAI"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install_HomeCloud_v11.ps1" -Mode "!HC_MODE!" -DependencySource Online
set "HC_EXIT=!ERRORLEVEL!"
echo.
if not "!HC_EXIT!"=="0" echo Online installation failed. Read the message above.
pause
exit /b !HC_EXIT!
