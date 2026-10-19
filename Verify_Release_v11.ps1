$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$errors = [System.Collections.Generic.List[string]]::new()

$required = @(
    'app.py', 'serve.py', 'homecloud_updater.py', 'apply_update.py', 'templates\index.html',
    'requirements.txt', 'requirements.lock.txt',
    'Install_HomeCloud_v11.cmd', 'Install_HomeCloud_v11.ps1',
    'README.md', '.gitignore', 'config.example.json', 'Build_Update_Package_v11.ps1'
)
foreach ($name in $required) {
    if (-not (Test-Path -LiteralPath (Join-Path $root $name))) {
        $errors.Add("Missing required release file: $name")
    }
}

$appText = Get-Content -LiteralPath (Join-Path $root 'app.py') -Raw -Encoding UTF8
if ($appText -match 'ipwho\.is|urlopen\(|Request\(f?"https?://') {
    $errors.Add('Runtime application still contains an outbound HTTP lookup.')
}
if ($appText -match '1\.1\.1\.1') {
    $errors.Add('Runtime LAN detection still references a public IP address.')
}

$updaterText = Get-Content -LiteralPath (Join-Path $root 'homecloud_updater.py') -Raw -Encoding UTF8
if ($updaterText -match 'http://' -or $updaterText -match 'TRUSTED_DOWNLOAD_HOSTS\s*=\s*\{[^}]*\*') {
    $errors.Add('Updater contains an insecure or unrestricted download target.')
}

if (Test-Path -LiteralPath (Join-Path $root '.git')) {
    $tracked = git -C $root ls-files
    $forbidden = @('assets.db','config.json','secret.key','deployment-state.json','deployment-state-v11.json')
    foreach ($name in $forbidden) {
        if ($tracked -contains $name) { $errors.Add("Private/generated file is tracked by Git: $name") }
    }
}

if ($errors.Count) {
    $errors | ForEach-Object { Write-Error $_ }
    exit 1
}
Write-Host 'Home Cloud v11 release verification passed.' -ForegroundColor Green
