@echo off
setlocal
cd /d "%~dp0"
set "BACKUP_CMD=%~dp0HomeCloud_Backup.cmd"
schtasks /Create /TN "Home Cloud Daily Backup" /TR "\"%BACKUP_CMD%\" /quiet" /SC DAILY /ST 02:00 /F
if errorlevel 1 (
  echo Could not create the scheduled task. Try Run as administrator.
) else (
  echo Daily backup scheduled for 2:00 AM. The newest 10 backups are retained.
)
pause

