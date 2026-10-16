$ErrorActionPreference = 'Stop'
$packageRoot = $PSScriptRoot
$statePath = Join-Path $packageRoot 'deployment-state.json'
if (-not (Test-Path -LiteralPath $statePath)) {
    throw 'No v10 deployment state was found. Rollback is available only after a successful install.'
}
$state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
$production = [System.IO.Path]::GetFullPath([string]$state.production)
$backup = [System.IO.Path]::GetFullPath([string]$state.backup)
if (-not (Test-Path -LiteralPath (Join-Path $backup 'app.py'))) {
    throw "Rollback backup was not found: $backup"
}
$listener = Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    throw 'Home Cloud is still running on port 8080. Close the server window, then run rollback again.'
}

Copy-Item -LiteralPath (Join-Path $backup 'app.py') -Destination (Join-Path $production 'app.py') -Force
Copy-Item -LiteralPath (Join-Path $backup 'templates\index.html') -Destination (Join-Path $production 'templates\index.html') -Force
foreach ($name in @('requirements.txt','requirements-ai.txt','HomeCloud_Start.cmd','serve.py','backup_local.py','HomeCloud_Backup.cmd','Create_Daily_Backup_Task.cmd','Remove_Daily_Backup_Task.cmd')) {
    $source = Join-Path $backup $name
    if (Test-Path -LiteralPath $source) {
        Copy-Item -LiteralPath $source -Destination (Join-Path $production $name) -Force
    }
}

Write-Host ''
Write-Host 'Home Cloud application code was rolled back successfully.' -ForegroundColor Green
Write-Host 'Current database and user data were preserved.'
Write-Host "The pre-v10 database snapshot remains available at: $backup\assets.db"
