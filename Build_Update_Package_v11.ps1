param(
    [string]$Version = '',
    [ValidateSet('stable','beta')]
    [string]$Channel = 'beta',
    [switch]$IncludeDependencies
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$dist = Join-Path $root 'dist'
$stage = Join-Path $dist 'update-stage'
$payload = Join-Path $stage 'payload'
$zip = Join-Path $dist 'HomeCloud-v11-update.zip'
$manifestFile = Join-Path $dist 'update-manifest.json'
$appText = Get-Content -LiteralPath (Join-Path $root 'app.py') -Raw -Encoding UTF8
$versionMatch = [regex]::Match($appText, '(?m)^APP_VERSION\s*=\s*["'']([^"'']+)["'']')
if (-not $versionMatch.Success) { throw 'Could not read APP_VERSION from app.py.' }
$appVersion = $versionMatch.Groups[1].Value.TrimStart('v')
if (-not $Version) { $Version = $appVersion }
if ($Version.TrimStart('v') -ne $appVersion) { throw "Requested version $Version does not match app.py version $appVersion." }
$Version = $appVersion
$distFull = [IO.Path]::GetFullPath($dist).TrimEnd('\')
$stageFull = [IO.Path]::GetFullPath($stage)
if (-not $stageFull.StartsWith($distFull + '\',[StringComparison]::OrdinalIgnoreCase)) {
    throw 'Unsafe update staging path.'
}
if ($Version -notmatch '^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$') {
    throw 'Version must use semantic version format, for example 11.0.1 or 11.0.0-beta.2.'
}

if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
if (Test-Path -LiteralPath $manifestFile) { Remove-Item -LiteralPath $manifestFile -Force }
New-Item -ItemType Directory -Path $payload -Force | Out-Null

$files = @(
    '.gitignore','app.py','apply_update.py','backup_local.py','config.example.json','homecloud_updater.py',
    'README.md','requirements.txt','requirements.lock.txt','requirements-ai.txt','requirements-ai.lock.txt',
    'serve.py','V11_RELEASE_NOTES.md','HomeCloud_Start.cmd','HomeCloud_Backup.cmd',
    'Configure_HomeCloud_Firewall_v11.cmd','Configure_HomeCloud_Firewall_v11.ps1'
)
foreach ($name in $files) {
    $source = Join-Path $root $name
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "Required update file is missing: $name" }
    $destination = Join-Path $payload $name
    New-Item -ItemType Directory -Path (Split-Path $destination -Parent) -Force | Out-Null
    Copy-Item -LiteralPath $source -Destination $destination -Force
}
foreach ($folder in @('templates','static')) {
    $source = Join-Path $root $folder
    Copy-Item -LiteralPath $source -Destination (Join-Path $payload $folder) -Recurse -Force
}
if ($IncludeDependencies) {
    $wheelhouse = Join-Path $root 'offline\wheelhouse\standard'
    if (-not (Test-Path -LiteralPath $wheelhouse -PathType Container)) { throw 'Standard offline wheelhouse is missing.' }
    Copy-Item -LiteralPath $wheelhouse -Destination (Join-Path $payload 'update-wheelhouse') -Recurse -Force
}

Get-ChildItem -LiteralPath $payload -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force
$hashes = [ordered]@{}
Get-ChildItem -LiteralPath $payload -Recurse -File | Sort-Object FullName | ForEach-Object {
    $relative = $_.FullName.Substring($payload.Length + 1).Replace('\','/')
    $hashes[$relative] = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
}
$package = [ordered]@{
    schema = 1
    version = $Version
    channel = $Channel
    created_at = (Get-Date).ToUniversalTime().ToString('o')
    install_dependencies = [bool]$IncludeDependencies
    files = $hashes
}
$package | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $stage 'update-package.json') -Encoding UTF8
Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $zip -CompressionLevel Optimal

$zipItem = Get-Item -LiteralPath $zip
$manifest = [ordered]@{
    schema = 1
    version = $Version
    channel = $Channel
    asset = $zipItem.Name
    sha256 = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant()
    size = $zipItem.Length
    requires_restart = $true
    published_at = (Get-Date).ToUniversalTime().ToString('o')
}
$manifest | ConvertTo-Json | Set-Content -LiteralPath $manifestFile -Encoding UTF8

$checksumFile = Join-Path $dist 'SHA256SUMS.txt'
$checksumLines = @()
if (Test-Path -LiteralPath $checksumFile) {
    $checksumLines = @(Get-Content -LiteralPath $checksumFile | Where-Object { $_ -notmatch '  (HomeCloud-v11-update\.zip|update-manifest\.json)$' })
}
$checksumLines += "$((Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant())  $([IO.Path]::GetFileName($zip))"
$checksumLines += "$((Get-FileHash -LiteralPath $manifestFile -Algorithm SHA256).Hash.ToLowerInvariant())  $([IO.Path]::GetFileName($manifestFile))"
$checksumLines | Set-Content -LiteralPath $checksumFile -Encoding ASCII

Write-Host 'Update release assets created:' -ForegroundColor Green
Write-Host "- $zip"
Write-Host "- $manifestFile"
Write-Host "- $checksumFile"
Write-Host ("- {0} application files, {1:N2} MB" -f $hashes.Count,($zipItem.Length/1MB))
