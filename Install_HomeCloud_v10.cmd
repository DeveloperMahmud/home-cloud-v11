@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install_HomeCloud_v10.ps1"
set "EXITCODE=%ERRORLEVEL%"
echo.
if not "%EXITCODE%"=="0" (
  echo Installation did not complete. Read the error above.
) else (
  echo Installation complete.
)
pause
exit /b %EXITCODE%

