# Make this machine's dev servers reachable from a headset over Wi-Fi.
#
# Run this AFTER both the laptop and the headset are on the same network --
# a phone hotspot is the usual choice, because campus and office Wi-Fi
# frequently enable client isolation, which silently blocks device-to-device
# traffic no matter how the firewall is set.
#
# MUST BE RUN AS ADMINISTRATOR (it changes the network profile and adds
# firewall rules).
#
#   powershell -ExecutionPolicy Bypass -File tools/quest_wifi_link.ps1
#
# To undo everything afterwards:
#
#   powershell -ExecutionPolicy Bypass -File tools/quest_wifi_link.ps1 -Remove

param(
    [int]$WebPort = 5273,
    [int]$ApiPort = 8000,
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$RulePrefix = "struct-ar-"   # CLAUDE.md: AR/XR-owned rules carry this prefix

function Assert-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Run this in an Administrator PowerShell -- it changes firewall state."
    }
}

Assert-Admin

if ($Remove) {
    Get-NetFirewallRule -DisplayName "$RulePrefix*" -ErrorAction SilentlyContinue | ForEach-Object {
        Write-Host "removing $($_.DisplayName)"
        Remove-NetFirewallRule -Name $_.Name
    }
    Write-Host "Firewall rules removed. Network profile left as-is." -ForegroundColor Green
    exit 0
}

# --- which adapter is actually carrying us to the headset ------------------
# Virtual adapters (Hyper-V, VMware, WSL) hand out plausible-looking private
# addresses that no headset can reach. Pick the real, connected, physical one.
$profile = Get-NetConnectionProfile |
    Where-Object { $_.IPv4Connectivity -ne "Disconnected" } |
    Sort-Object { if ($_.InterfaceAlias -like "Wi-Fi*") { 0 } else { 1 } } |
    Select-Object -First 1

if (-not $profile) { throw "No connected network found." }

$address = Get-NetIPAddress -InterfaceIndex $profile.InterfaceIndex -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -notlike "169.254.*" } |
    Select-Object -First 1

if (-not $address) { throw "No usable IPv4 address on $($profile.InterfaceAlias)." }

Write-Host "interface : $($profile.InterfaceAlias)"
Write-Host "address   : $($address.IPAddress)"
Write-Host "category  : $($profile.NetworkCategory)"

# --- profile ---------------------------------------------------------------
# Windows blocks essentially all inbound traffic on a Public profile. On a
# hotspot shared only with your own headset, Private is the honest category.
if ($profile.NetworkCategory -eq "Public") {
    Write-Host "switching $($profile.InterfaceAlias) to Private..." -ForegroundColor Yellow
    Set-NetConnectionProfile -InterfaceIndex $profile.InterfaceIndex -NetworkCategory Private
    Write-Host "  done" -ForegroundColor Green
}

# --- firewall --------------------------------------------------------------
# Scoped to the local subnet rather than Any: even on a hotspot there is no
# reason to accept these from the wider internet.
$scope = "LocalSubnet"
foreach ($port in @($WebPort, $ApiPort)) {
    $name = "$RulePrefix$port"
    Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    New-NetFirewallRule -DisplayName $name `
        -Direction Inbound -Action Allow -Protocol TCP -LocalPort $port `
        -Profile Private -RemoteAddress $scope `
        -Description "STRUCT AR/XR dev server. Remove with quest_wifi_link.ps1 -Remove." | Out-Null
    Write-Host "allowed inbound TCP $port (Private, $scope)" -ForegroundColor Green
}

# --- is anything actually serving? -----------------------------------------
foreach ($port in @($WebPort, $ApiPort)) {
    $listening = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
    if ($listening) {
        Write-Host "  localhost:$port is being served" -ForegroundColor Green
    } else {
        Write-Host "  WARNING: nothing is listening on port $port" -ForegroundColor Yellow
    }
}

$ip = $address.IPAddress
Write-Host ""
Write-Host "In the Quest browser, open:" -ForegroundColor Cyan
Write-Host "    https://${ip}:${WebPort}/probe.html          (check this first)" -ForegroundColor White
Write-Host "    https://${ip}:${WebPort}/sort-teleop.html" -ForegroundColor White
Write-Host ""
Write-Host "https, and you WILL get a certificate warning -- the dev server uses a"
Write-Host "self-signed cert. Tap Advanced -> Proceed. WebXR refuses to start on a"
Write-Host "plain http LAN address, so the warning is the price of not using USB."
Write-Host ""
Write-Host "When you are done, remove the firewall rules:" -ForegroundColor DarkGray
Write-Host "    powershell -ExecutionPolicy Bypass -File tools/quest_wifi_link.ps1 -Remove" -ForegroundColor DarkGray
