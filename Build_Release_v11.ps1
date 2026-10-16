param([switch]$IncludeOfflinePayload)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$dist = Join-Path $root 'dist'
$stage = Join-Path $dist 'HomeCloud-v11'
$zip = Join-Path $dist 'HomeCloud-v11-source.zip'
$distFull = [System.IO.Path]::GetFullPath($dist).TrimEnd('\')
$stageFull = [System.IO.Path]::GetFullPath($stage)
if (-not $stageFull.StartsWith($distFull + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'Unsafe release staging path.'
}

if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
New-Item -ItemType Directory -Path $stage -Force | Out-Null

$files = @(
    'app.py','serve.py','backup_local.py','README.md','.gitignore','config.example.json',
    'requirements.txt','requirements.lock.txt','requirements-ai.txt','requirements-ai.lock.txt',
    'HomeCloud_Start.cmd','HomeCloud_Backup.cmd','Create_Daily_Backup_Task.cmd','Remove_Daily_Backup_Task.cmd',
    'Install_HomeCloud_v11.cmd','Install_HomeCloud_v11.ps1','Install_HomeCloud_v11_Offline.cmd','Install_HomeCloud_v11_Online.cmd',
    'Prepare_Offline_Package_v11.ps1','Build_Installer_Packages_v11.ps1','Configure_HomeCloud_Firewall_v11.cmd','Configure_HomeCloud_Firewall_v11.ps1',
    'Verify_Release_v11.ps1','Build_Release_v11.ps1','V11_RELEASE_NOTES.md'
)
foreach ($name in $files) {
    $source = Join-Path $root $name
    if (Test-Path -LiteralPath $source) { Copy-Item -LiteralPath $source -Destination (Join-Path $stage $name) -Force }
}
foreach ($folder in @('templates','static','tests')) {
    $source = Join-Path $root $folder
    if (Test-Path -LiteralPath $source) { Copy-Item -LiteralPath $source -Destination (Join-Path $stage $folder) -Recurse -Force }
}
if ($IncludeOfflinePayload) {
    foreach ($folder in @('offline','offline-model-cache')) {
        $source = Join-Path $root $folder
        if (Test-Path -LiteralPath $source) { Copy-Item -LiteralPath $source -Destination (Join-Path $stage $folder) -Recurse -Force }
    }
}

Get-ChildItem -LiteralPath $stage -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force
Get-ChildItem -LiteralPath $stage -Recurse -File | Where-Object { $_.Name -in @('assets.db','config.json','secret.key') -or $_.Name -like 'deployment-state*.json' } | Remove-Item -Force
Compress-Archive -LiteralPath $stage -DestinationPath $zip -CompressionLevel Optimal
Write-Host "Clean release created: $zip" -ForegroundColor Green
