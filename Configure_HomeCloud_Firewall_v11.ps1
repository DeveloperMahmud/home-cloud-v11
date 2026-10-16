param([int]$Port = 8080)

$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $identity.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script as Administrator to create the private-network firewall rule.'
}
if ($Port -lt 1024 -or $Port -gt 65535) { throw 'Invalid Home Cloud port.' }
$name = "Home Cloud LAN TCP $Port"
Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName $name -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port -Profile Private,Public -RemoteAddress LocalSubnet | Out-Null
Write-Host "Local-subnet firewall access enabled for Home Cloud on TCP port $Port." -ForegroundColor Green
