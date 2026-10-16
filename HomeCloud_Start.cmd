@echo off
setlocal
cd /d "%~dp0"
title Home Cloud - Local Asset Library
if exist "%~dp0offline-model-cache" (
  set "HF_HOME=%~dp0offline-model-cache"
  set "HF_HUB_OFFLINE=1"
  set "TRANSFORMERS_OFFLINE=1"
)
set "HC_PYTHON=python"
if exist "%~dp0python-runtime.txt" set /p "HC_PYTHON="<"%~dp0python-runtime.txt"
"%HC_PYTHON%" --version >nul 2>nul
if errorlevel 1 (
  echo Python was not found in PATH.
  echo Run Install_HomeCloud_v11.cmd to install or configure the bundled runtime.
  pause
  exit /b 1
)
echo.
echo ================================================
echo        HOME CLOUD - LOCAL ASSET LIBRARY
echo ================================================
echo.
set "HC_PORT=8080"
for /f %%P in ('powershell -NoProfile -Command "try{(Get-Content -LiteralPath 'config.json' -Raw|ConvertFrom-Json).port}catch{8080}"') do set "HC_PORT=%%P"
echo Starting server on http://127.0.0.1:%HC_PORT%
start "Home Cloud Browser" /min cmd /c "timeout /t 2 /nobreak ^>nul ^& start "" http://127.0.0.1:%HC_PORT%"
"%HC_PYTHON%" -c "import waitress" >nul 2>nul
if errorlevel 1 (
  echo Waitress is not installed. Using the Flask compatibility server.
  echo Run Install_HomeCloud_v11.cmd to enable the production server.
  "%HC_PYTHON%" app.py
) else (
  echo Production server: Waitress
  "%HC_PYTHON%" serve.py
)
set "EXITCODE=%ERRORLEVEL%"
echo.
if not "%EXITCODE%"=="0" (
  echo Home Cloud stopped with error code %EXITCODE%.
  echo The window will remain open so you can read the error above.
) else (
  echo Home Cloud stopped normally.
)
pause
exit /b %EXITCODE%
