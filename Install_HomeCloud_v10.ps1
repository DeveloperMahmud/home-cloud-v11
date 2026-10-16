param(
    [string]$ProductionPath = 'D:\Web Works\Local_Asset_Library\Filalize\Production'
)

$ErrorActionPreference = 'Stop'
$packageRoot = $PSScriptRoot
$production = [System.IO.Path]::GetFullPath($ProductionPath)

if (-not (Test-Path -LiteralPath (Join-Path $packageRoot 'app.py')) -or -not (Test-Path -LiteralPath (Join-Path $packageRoot 'serve.py'))) {
    throw 'The v10.1 package is incomplete: app.py or serve.py was not found.'
}
if (-not (Test-Path -LiteralPath (Join-Path $production 'assets.db'))) {
    throw "Home Cloud Production was not found at: $production"
}

$listener = Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    throw 'Home Cloud is still running on port 8080. Close the server window, then run this installer again.'
}

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backup = Join-Path (Split-Path $production -Parent) "HomeCloud_Pre_v10_$stamp"
New-Item -ItemType Directory -Path $backup | Out-Null
New-Item -ItemType Directory -Path (Join-Path $backup 'templates') | Out-Null

$backupFiles = @('app.py','serve.py','backup_local.py','requirements.txt','requirements-ai.txt','HomeCloud_Start.cmd','HomeCloud_Backup.cmd','Create_Daily_Backup_Task.cmd','Remove_Daily_Backup_Task.cmd','config.json','secret.key','assets.db')
foreach ($name in $backupFiles) {
    $source = Join-Path $production $name
    if (Test-Path -LiteralPath $source) {
        Copy-Item -LiteralPath $source -Destination (Join-Path $backup $name) -Force
    }
}
Copy-Item -LiteralPath (Join-Path $production 'templates\index.html') -Destination (Join-Path $backup 'templates\index.html') -Force

Copy-Item -LiteralPath (Join-Path $packageRoot 'app.py') -Destination (Join-Path $production 'app.py') -Force
Copy-Item -LiteralPath (Join-Path $packageRoot 'serve.py') -Destination (Join-Path $production 'serve.py') -Force
Copy-Item -LiteralPath (Join-Path $packageRoot 'templates\index.html') -Destination (Join-Path $production 'templates\index.html') -Force
Copy-Item -LiteralPath (Join-Path $packageRoot 'requirements.txt') -Destination (Join-Path $production 'requirements.txt') -Force
Copy-Item -LiteralPath (Join-Path $packageRoot 'requirements-ai.txt') -Destination (Join-Path $production 'requirements-ai.txt') -Force
Copy-Item -LiteralPath (Join-Path $packageRoot 'HomeCloud_Start.cmd') -Destination (Join-Path $production 'HomeCloud_Start.cmd') -Force
foreach ($name in @('backup_local.py','HomeCloud_Backup.cmd','Create_Daily_Backup_Task.cmd','Remove_Daily_Backup_Task.cmd')) {
    Copy-Item -LiteralPath (Join-Path $packageRoot $name) -Destination (Join-Path $production $name) -Force
}

Push-Location $production
try {
    python -c "from pathlib import Path; compile(Path('app.py').read_text(encoding='utf-8'),'app.py','exec'); print('Python syntax OK')"
    if ($LASTEXITCODE -ne 0) { throw 'The v10 Python validation failed.' }
    python -c "import app; app.init_db(); print('Database migration OK')"
    if ($LASTEXITCODE -ne 0) { throw 'The v10 database migration failed.' }
    # Check without importing the module. A failed import writes a traceback to
    # stderr, which Windows PowerShell turns into a terminating error because
    # this installer uses $ErrorActionPreference = 'Stop'.
    python -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('waitress') else 1)"
    if ($LASTEXITCODE -ne 0) {
        Write-Host 'Installing the Waitress production server...'
        python -m pip install "waitress>=3,<4"
        if ($LASTEXITCODE -ne 0) {
            Write-Warning 'Waitress could not be installed. Home Cloud will use its Flask compatibility server until dependencies are installed.'
        }
    }
}
catch {
    Copy-Item -LiteralPath (Join-Path $backup 'app.py') -Destination (Join-Path $production 'app.py') -Force
    Copy-Item -LiteralPath (Join-Path $backup 'templates\index.html') -Destination (Join-Path $production 'templates\index.html') -Force
    foreach ($name in @('requirements.txt','requirements-ai.txt','HomeCloud_Start.cmd','serve.py','backup_local.py','HomeCloud_Backup.cmd','Create_Daily_Backup_Task.cmd','Remove_Daily_Backup_Task.cmd')) {
        $source = Join-Path $backup $name
        if (Test-Path -LiteralPath $source) {
            Copy-Item -LiteralPath $source -Destination (Join-Path $production $name) -Force
        }
    }
    throw
}
finally {
    Pop-Location
}

@{
    production = $production
    backup = $backup
    installed_at = (Get-Date).ToString('o')
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $packageRoot 'deployment-state.json') -Encoding UTF8

Write-Host ''
Write-Host 'Home Cloud v10.1 installed successfully.' -ForegroundColor Green
Write-Host "Backup: $backup"
Write-Host 'Start Home Cloud normally and verify login, upload, folder upload, copy and move.'
