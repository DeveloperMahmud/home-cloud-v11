@echo off
setlocal
cd /d "%~dp0"
python backup_local.py --keep 10
set "EXITCODE=%ERRORLEVEL%"
if /i "%~1"=="/quiet" exit /b %EXITCODE%
echo.
if not "%EXITCODE%"=="0" echo Backup failed. Read the error above.
pause
exit /b %EXITCODE%

