param(
    [ValidateSet('Standard','FullAI')]
    [string]$Mode = 'Standard',
    [switch]$SkipModel,
    [switch]$SkipPythonRuntime
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw 'Python was not found in PATH.'
}

$standard = Join-Path $root 'offline\wheelhouse\standard'
$fullAI = Join-Path $root 'offline\wheelhouse\full-ai'
$runtime = Join-Path $root 'offline\runtime'
$buildTemp = Join-Path $root 'offline\.temp'
New-Item -ItemType Directory -Path $standard -Force | Out-Null
$offlineRoot = [System.IO.Path]::GetFullPath((Join-Path $root 'offline')).TrimEnd('\')
$tempFull = [System.IO.Path]::GetFullPath($buildTemp)
if (-not $tempFull.StartsWith($offlineRoot + '\', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe offline build temporary path.' }
New-Item -ItemType Directory -Path $buildTemp -Force | Out-Null
$env:TEMP = $buildTemp
$env:TMP = $buildTemp

if (-not $SkipPythonRuntime) {
    New-Item -ItemType Directory -Path $runtime -Force | Out-Null
    $pythonInstaller = Join-Path $runtime 'python-3.14.7-amd64.exe'
    $pythonUrl = 'https://www.python.org/ftp/python/3.14.7/python-3.14.7-amd64.exe'
    $expectedHash = '9d9eb2709ef81bf5cd30db3c2096bdbc4ea10087c22e62f27d356b36f6ae9649'
    $validInstaller = (Test-Path -LiteralPath $pythonInstaller) -and ((Get-FileHash -LiteralPath $pythonInstaller -Algorithm SHA256).Hash.ToLowerInvariant() -eq $expectedHash)
    if (-not $validInstaller) {
        Invoke-WebRequest -Uri $pythonUrl -OutFile $pythonInstaller -UseBasicParsing
        $actualHash = (Get-FileHash -LiteralPath $pythonInstaller -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actualHash -ne $expectedHash) { throw 'Bundled Python installer checksum validation failed.' }
    }
    "$expectedHash  $([IO.Path]::GetFileName($pythonInstaller))" | Set-Content -LiteralPath (Join-Path $runtime 'python-3.14.7-amd64.exe.sha256') -Encoding ASCII
}

python -m pip download --no-cache-dir --only-binary=:all: --dest $standard -r (Join-Path $root 'requirements.lock.txt')
if ($LASTEXITCODE -ne 0) { throw 'Could not prepare the Standard wheelhouse.' }

if ($Mode -eq 'FullAI') {
    New-Item -ItemType Directory -Path $fullAI -Force | Out-Null
    python -m pip download --no-cache-dir --only-binary=:all: --dest $fullAI -r (Join-Path $root 'requirements-ai.lock.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Could not prepare the Full AI wheelhouse.' }
    if (-not $SkipModel) {
        $modelCache = Join-Path $root 'offline-model-cache'
        New-Item -ItemType Directory -Path $modelCache -Force | Out-Null
        $env:HF_HOME = $modelCache
        $prepVenv = Join-Path $root 'offline\.model-prep-venv'
        $prepFull = [System.IO.Path]::GetFullPath($prepVenv)
        if (-not $prepFull.StartsWith($offlineRoot + '\', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe temporary environment path.' }
        if (Test-Path -LiteralPath $prepVenv) { Remove-Item -LiteralPath $prepVenv -Recurse -Force }
        try {
            python -m venv $prepVenv
            $prepPython = Join-Path $prepVenv 'Scripts\python.exe'
            & $prepPython -m pip install --no-index --find-links $standard --find-links $fullAI -r (Join-Path $root 'requirements.lock.txt') -r (Join-Path $root 'requirements-ai.lock.txt')
            if ($LASTEXITCODE -ne 0) { throw 'Could not create the temporary Full AI preparation environment.' }
            & $prepPython -c "import open_clip; open_clip.create_model_and_transforms('ViT-B-16', pretrained='laion400m_e32'); print('Full AI model cached')"
            if ($LASTEXITCODE -ne 0) { throw 'Could not cache the Full AI model.' }
        } finally {
            if (Test-Path -LiteralPath $prepVenv) { Remove-Item -LiteralPath $prepVenv -Recurse -Force }
        }
    }
}

if (Test-Path -LiteralPath $buildTemp) { Remove-Item -LiteralPath $buildTemp -Recurse -Force }

Write-Host ''
Write-Host "Offline dependency package is ready: $Mode" -ForegroundColor Green
Write-Host 'Copy this entire Home Cloud folder to the offline PC, then run Install_HomeCloud_v11_Offline.cmd.'
