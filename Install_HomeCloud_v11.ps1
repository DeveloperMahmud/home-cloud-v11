param(
    [ValidateSet('Standard','FullAI')]
    [string]$Mode = 'Standard',
    [string]$InstallPath = "$env:LOCALAPPDATA\HomeCloud",
    [string]$LibraryPath = '',
    [switch]$OfflineOnly,
    [ValidateSet('Auto','Online','Offline')]
    [string]$DependencySource = 'Auto'
)

$ErrorActionPreference = 'Stop'
$packageRoot = $PSScriptRoot
$target = [System.IO.Path]::GetFullPath($InstallPath)
$required = @('app.py','serve.py','templates\index.html','requirements.txt','HomeCloud_Start.cmd')
foreach ($name in $required) {
    if (-not (Test-Path -LiteralPath (Join-Path $packageRoot $name))) {
        throw "The v11 package is incomplete: $name was not found."
    }
}

if ($OfflineOnly) { $DependencySource = 'Offline' }
$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
$pythonExe = if ($pythonCommand) { $pythonCommand.Source } else { $null }
if ($pythonExe) {
    & $pythonExe -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" 2>$null
    if ($LASTEXITCODE -ne 0) { $pythonExe = $null }
}
if (-not $pythonExe) {
    $bundledPython = Get-ChildItem -LiteralPath (Join-Path $packageRoot 'offline\runtime') -Filter 'python-*-amd64.exe' -File -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($bundledPython) {
        Write-Host 'Python was not detected. Installing the bundled official Python runtime...' -ForegroundColor Yellow
        $process = Start-Process -FilePath $bundledPython.FullName -ArgumentList '/quiet InstallAllUsers=0 PrependPath=1 Include_pip=1 Include_test=0' -Wait -PassThru
        if ($process.ExitCode -ne 0) { throw "Bundled Python installation failed with exit code $($process.ExitCode)." }
        $pythonExe = Get-ChildItem -LiteralPath (Join-Path $env:LOCALAPPDATA 'Programs\Python') -Filter 'python.exe' -Recurse -File -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
    }
}
if (-not $pythonExe -and $DependencySource -ne 'Offline') {
    $pythonUrl = 'https://www.python.org/ftp/python/3.14.7/python-3.14.7-amd64.exe'
    $pythonSha256 = '9D9EB2709EF81BF5CD30DB3C2096BDBC4EA10087C22E62F27D356B36F6AE9649'
    $downloadedPython = Join-Path ([IO.Path]::GetTempPath()) "homecloud-python-$([guid]::NewGuid().ToString('N')).exe"
    try {
        Write-Host 'Python was not detected. Downloading the official Python runtime...' -ForegroundColor Yellow
        Invoke-WebRequest -Uri $pythonUrl -OutFile $downloadedPython -UseBasicParsing
        if ((Get-FileHash -Algorithm SHA256 -LiteralPath $downloadedPython).Hash -ne $pythonSha256) {
            throw 'The downloaded Python installer failed SHA-256 verification.'
        }
        $process = Start-Process -FilePath $downloadedPython -ArgumentList '/quiet InstallAllUsers=0 PrependPath=1 Include_pip=1 Include_test=0' -Wait -PassThru
        if ($process.ExitCode -ne 0) { throw "Python installation failed with exit code $($process.ExitCode)." }
        $pythonExe = Get-ChildItem -LiteralPath (Join-Path $env:LOCALAPPDATA 'Programs\Python') -Filter 'python.exe' -Recurse -File -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
    }
    finally {
        if (Test-Path -LiteralPath $downloadedPython) { Remove-Item -LiteralPath $downloadedPython -Force }
    }
}
if (-not $pythonExe) {
    throw 'Python was not found. Use an offline package that includes the Python runtime, or install Python 3.11 or newer.'
}

$existing = Test-Path -LiteralPath (Join-Path $target 'app.py')
$backup = $null
if ($existing) {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $backup = Join-Path (Split-Path $target -Parent) "HomeCloud_Pre_v11_$stamp"
    New-Item -ItemType Directory -Path $backup -Force | Out-Null
    foreach ($name in @('app.py','serve.py','backup_local.py','requirements.txt','requirements-ai.txt','HomeCloud_Start.cmd','config.json','secret.key','assets.db')) {
        $source = Join-Path $target $name
        if (Test-Path -LiteralPath $source) { Copy-Item -LiteralPath $source -Destination (Join-Path $backup $name) -Force }
    }
    if (Test-Path -LiteralPath (Join-Path $target 'templates\index.html')) {
        New-Item -ItemType Directory -Path (Join-Path $backup 'templates') -Force | Out-Null
        Copy-Item -LiteralPath (Join-Path $target 'templates\index.html') -Destination (Join-Path $backup 'templates\index.html') -Force
    }
}

New-Item -ItemType Directory -Path $target -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $target 'templates') -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $target 'static') -Force | Out-Null

foreach ($name in @('app.py','serve.py','backup_local.py','requirements.txt','requirements-ai.txt','HomeCloud_Start.cmd','HomeCloud_Backup.cmd','Create_Daily_Backup_Task.cmd','Remove_Daily_Backup_Task.cmd','Configure_HomeCloud_Firewall_v11.ps1','Configure_HomeCloud_Firewall_v11.cmd')) {
    $source = Join-Path $packageRoot $name
    if (Test-Path -LiteralPath $source) { Copy-Item -LiteralPath $source -Destination (Join-Path $target $name) -Force }
}
$modelCacheSource = Join-Path $packageRoot 'offline-model-cache'
if (Test-Path -LiteralPath $modelCacheSource) {
    Copy-Item -LiteralPath $modelCacheSource -Destination (Join-Path $target 'offline-model-cache') -Recurse -Force
}
Copy-Item -LiteralPath (Join-Path $packageRoot 'templates\index.html') -Destination (Join-Path $target 'templates\index.html') -Force
if (Test-Path -LiteralPath (Join-Path $packageRoot 'static\favicon.svg')) {
    Copy-Item -LiteralPath (Join-Path $packageRoot 'static\favicon.svg') -Destination (Join-Path $target 'static\favicon.svg') -Force
}

if (-not (Test-Path -LiteralPath (Join-Path $target 'config.json'))) {
    $library = if ($LibraryPath.Trim()) { [IO.Path]::GetFullPath($LibraryPath) } else { Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'Home Cloud Library' }
    New-Item -ItemType Directory -Path $library -Force | Out-Null
    @{ asset_root = $library; port = 8080; app_name = 'Home Cloud'; ai_mode = $(if ($Mode -eq 'FullAI') {'full'} else {'standard'}); chat_enabled = $true; chat_allow_anonymous = $true; chat_allow_guests = $true; chat_retention_days = 30; thumbnail_quality = 85 } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $target 'config.json') -Encoding UTF8
}
$configPath = Join-Path $target 'config.json'
$configData = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
$configData | Add-Member -MemberType NoteProperty -Name ai_mode -Value $(if ($Mode -eq 'FullAI') {'full'} else {'standard'}) -Force
$configData | Add-Member -MemberType NoteProperty -Name port -Value $(if ($configData.port) {[int]$configData.port} else {8080}) -Force
$configData | Add-Member -MemberType NoteProperty -Name app_name -Value $(if ($configData.app_name) {[string]$configData.app_name} else {'Home Cloud'}) -Force
$configData | ConvertTo-Json | Set-Content -LiteralPath $configPath -Encoding UTF8
$installedPort = [int]$configData.port

Push-Location $target
try {
    $venvPath = Join-Path $target '.venv'
    if (-not (Test-Path -LiteralPath (Join-Path $venvPath 'Scripts\python.exe'))) {
        & $pythonExe -m venv $venvPath
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the isolated Home Cloud Python environment.' }
    }
    $runtimePython = Join-Path $venvPath 'Scripts\python.exe'
    $standardRequirements = if (Test-Path -LiteralPath (Join-Path $packageRoot 'requirements.lock.txt')) { Join-Path $packageRoot 'requirements.lock.txt' } else { Join-Path $packageRoot 'requirements.txt' }
    $standardWheelhouse = Join-Path $packageRoot 'offline\wheelhouse\standard'
    if ($DependencySource -ne 'Online' -and (Test-Path -LiteralPath $standardWheelhouse)) {
        & $runtimePython -m pip install --no-index --find-links $standardWheelhouse -r $standardRequirements
    } elseif ($DependencySource -eq 'Offline') {
        throw 'Offline Standard wheelhouse was not found. Run Prepare_Offline_Package_v11.ps1 on an internet-connected build PC first.'
    } else {
        & $runtimePython -m pip install -r $standardRequirements
    }
    if ($LASTEXITCODE -ne 0) { throw 'Standard package installation failed.' }
    if ($Mode -eq 'FullAI') {
        $aiRequirements = if (Test-Path -LiteralPath (Join-Path $packageRoot 'requirements-ai.lock.txt')) { Join-Path $packageRoot 'requirements-ai.lock.txt' } else { Join-Path $packageRoot 'requirements-ai.txt' }
        $aiWheelhouse = Join-Path $packageRoot 'offline\wheelhouse\full-ai'
        if ($DependencySource -ne 'Online' -and (Test-Path -LiteralPath $aiWheelhouse)) {
            & $runtimePython -m pip install --no-index --find-links $aiWheelhouse -r $aiRequirements
        } elseif ($DependencySource -eq 'Offline') {
            throw 'Offline Full AI wheelhouse was not found. Prepare the FullAI offline package first.'
        } else {
            & $runtimePython -m pip install -r $aiRequirements
        }
        if ($LASTEXITCODE -ne 0) { throw 'Full AI package installation failed. Standard features remain installed.' }
        $installedModelCache = Join-Path $target 'offline-model-cache'
        if ($DependencySource -eq 'Offline' -and -not (Test-Path -LiteralPath $installedModelCache)) {
            throw 'Full AI offline model cache was not found. Prepare the FullAI offline package first.'
        }
        if ($DependencySource -ne 'Offline' -and -not (Test-Path -LiteralPath $installedModelCache)) {
            Write-Host 'Downloading and caching the AI model for offline runtime...' -ForegroundColor Cyan
            New-Item -ItemType Directory -Path $installedModelCache -Force | Out-Null
            $previousHfHome = $env:HF_HOME
            try {
                $env:HF_HOME = $installedModelCache
                & $runtimePython -c "import open_clip; open_clip.create_model_and_transforms('ViT-B-16', pretrained='laion400m_e32')"
                if ($LASTEXITCODE -ne 0) { throw 'The AI model download failed. Check the internet connection and run the installer again.' }
            }
            finally {
                $env:HF_HOME = $previousHfHome
            }
        }
    }
    & $runtimePython -c "from pathlib import Path; compile(Path('app.py').read_text(encoding='utf-8'),'app.py','exec'); import app; app.init_db(); print('Home Cloud database ready')"
    if ($LASTEXITCODE -ne 0) { throw 'Application validation failed.' }
}
finally { Pop-Location }

$lanIp = $null
try {
    $lanIp = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop | Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } | Sort-Object InterfaceMetric | Select-Object -First 1 -ExpandProperty IPAddress)
} catch {}

@{ install_path=$target; mode=$Mode; dependency_source=$DependencySource; backup=$backup; installed_at=(Get-Date).ToString('o') } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $target 'deployment-state-v11.json') -Encoding UTF8
$runtimePython | Set-Content -LiteralPath (Join-Path $target 'python-runtime.txt') -Encoding UTF8
Write-Host ''
Write-Host 'Home Cloud v11 installed successfully.' -ForegroundColor Green
Write-Host "Mode: $Mode"
Write-Host "Location: $target"
if ($backup) { Write-Host "Previous version backup: $backup" }
Write-Host "Local: http://127.0.0.1:$installedPort"
if ($lanIp) { Write-Host "LAN: http://${lanIp}:$installedPort" }
Write-Host 'Internet required: No'
Write-Host "Dependency source: $(if ($DependencySource -eq 'Offline' -or ($DependencySource -eq 'Auto' -and (Test-Path -LiteralPath $standardWheelhouse))) {'Offline wheelhouse'} else {'Python package index during installation'})"
Write-Host "Start with: $(Join-Path $target 'HomeCloud_Start.cmd')"
Write-Host "If another LAN device cannot connect, run Configure_HomeCloud_Firewall_v11.cmd as Administrator."
