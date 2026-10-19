param(
    [ValidateSet('Online','StandardOffline','FullAIOffline','All')]
    [string]$Package = 'All'
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$dist = Join-Path $root 'dist'
$baseStage = Join-Path $dist 'HomeCloud-v11'
$packageRoot = Join-Path $dist 'packages'
$distFull = [IO.Path]::GetFullPath($dist).TrimEnd('\')
$packageFull = [IO.Path]::GetFullPath($packageRoot)
if (-not $packageFull.StartsWith($distFull + '\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe package output path.' }
$appText = Get-Content -LiteralPath (Join-Path $root 'app.py') -Raw -Encoding UTF8
$versionMatch = [regex]::Match($appText, '(?m)^APP_VERSION\s*=\s*["'']([^"'']+)["'']')
if (-not $versionMatch.Success) { throw 'Could not read APP_VERSION from app.py.' }
$appVersion = $versionMatch.Groups[1].Value.TrimStart('v')

& (Join-Path $root 'Build_Release_v11.ps1')
if (-not (Test-Path -LiteralPath $baseStage)) { throw 'Base release staging folder was not created.' }
if (Test-Path -LiteralPath $packageRoot) { Remove-Item -LiteralPath $packageRoot -Recurse -Force }
New-Item -ItemType Directory -Path $packageRoot -Force | Out-Null

function Copy-BasePackage([string]$Name) {
    $target = Join-Path $packageRoot $Name
    Copy-Item -LiteralPath $baseStage -Destination $target -Recurse -Force
    return $target
}

function Add-Payload([string]$Target,[string[]]$RelativePaths) {
    foreach ($relative in $RelativePaths) {
        $source = Join-Path $root $relative
        if (-not (Test-Path -LiteralPath $source)) { throw "Required offline payload is missing: $relative" }
        $destination = Join-Path $Target $relative
        New-Item -ItemType Directory -Path (Split-Path $destination -Parent) -Force | Out-Null
        Copy-Item -LiteralPath $source -Destination $destination -Recurse -Force
    }
}

function Complete-Package([string]$Target,[string]$Flavor,[bool]$Offline,[bool]$FullAI) {
    @{version=$appVersion;flavor=$Flavor;offline=$Offline;full_ai=$FullAI;built_at=(Get-Date).ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Target 'package-manifest.json') -Encoding UTF8
    $zip = Join-Path $dist "$Flavor.zip"
    if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
    Compress-Archive -LiteralPath $Target -DestinationPath $zip -CompressionLevel Optimal
    return $zip
}

$outputs = [System.Collections.Generic.List[string]]::new()
if ($Package -in @('Online','All')) {
    $target = Copy-BasePackage 'HomeCloud-v11-Online-Installer'
    $outputs.Add((Complete-Package $target 'HomeCloud-v11-Online-Installer' $false $false))
}
if ($Package -in @('StandardOffline','All')) {
    $target = Copy-BasePackage 'HomeCloud-v11-Standard-Offline'
    Add-Payload $target @('offline\runtime','offline\wheelhouse\standard')
    $outputs.Add((Complete-Package $target 'HomeCloud-v11-Standard-Offline' $true $false))
}
if ($Package -in @('FullAIOffline','All')) {
    $target = Copy-BasePackage 'HomeCloud-v11-FullAI-Offline'
    Add-Payload $target @('offline\runtime','offline\wheelhouse\standard','offline\wheelhouse\full-ai','offline-model-cache')
    $outputs.Add((Complete-Package $target 'HomeCloud-v11-FullAI-Offline' $true $true))
}

& (Join-Path $root 'Build_Update_Package_v11.ps1') -Version $appVersion -Channel $(if ($appVersion -match '-') {'beta'} else {'stable'})
$outputs.Add((Join-Path $dist 'HomeCloud-v11-update.zip'))

$checksumFile = Join-Path $dist 'SHA256SUMS.txt'
$checksumInputs = [System.Collections.Generic.List[string]]::new()
$outputs | ForEach-Object { $checksumInputs.Add($_) }
$checksumInputs.Add((Join-Path $dist 'update-manifest.json'))
$checksumInputs | ForEach-Object { $hash=(Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash.ToLowerInvariant(); "$hash  $([IO.Path]::GetFileName($_))" } | Set-Content -LiteralPath $checksumFile -Encoding ASCII
Write-Host ''
Write-Host 'Installer packages created:' -ForegroundColor Green
$outputs | ForEach-Object { $item=Get-Item -LiteralPath $_; Write-Host ("- {0} ({1:N2} GB)" -f $item.FullName,($item.Length/1GB)) }
Write-Host "Checksums: $checksumFile"
