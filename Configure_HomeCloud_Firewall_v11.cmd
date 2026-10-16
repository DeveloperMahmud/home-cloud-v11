@echo off
setlocal
cd /d "%~dp0"
set "HC_PORT=8080"
for /f %%P in ('python -c "import app; print(app.config_value('port'))"') do set "HC_PORT=%%P"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Configure_HomeCloud_Firewall_v11.ps1" -Port %HC_PORT%
pause
