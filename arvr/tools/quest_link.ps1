# Link a Quest to this machine's dev servers over USB.
#
# Why USB rather than the LAN: WebXR needs a secure context, and `localhost`
# is the one origin browsers trust without a certificate. `adb reverse` makes
# the *headset's* localhost forward back here, so the Quest browser sees
# https-grade trust on a plain http URL -- no self-signed cert to accept, no
# inbound firewall rule, and nothing for campus Wi-Fi client isolation to
# block. It also survives the laptop changing networks mid-session.
#
#   powershell -ExecutionPolicy Bypass -File tools/quest_link.ps1
#
# Leave it running is not required -- the forwards persist until the cable is
# unplugged or `adb reverse --remove-all` is run.

param(
    [int]$WebPort = 5273,
    [int]$ApiPort = 8000,
    [string]$Page = "sort-teleop.html"
)

$ErrorActionPreference = "Stop"

function Find-Adb {
    $onPath = Get-Command adb -ErrorAction SilentlyContinue
    if ($onPath) { return $onPath.Source }

    $candidates = @(
        "$env:LOCALAPPDATA\Microsoft\WinGet\Links\adb.exe",
        "$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe",
        "$env:ProgramFiles\Oculus\Support\oculus-drivers\adb.exe"
    )
    foreach ($c in $candidates) { if (Test-Path $c) { return $c } }

    $pkg = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Recurse -Filter adb.exe -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($pkg) { return $pkg.FullName }

    throw "adb not found. Install it with:  winget install --id Google.PlatformTools"
}

$adb = Find-Adb
Write-Host "adb: $adb" -ForegroundColor DarkGray

# `adb devices` reports three states that mean very different things, and
# saying which one it is saves the usual ten minutes of guessing.
$devices = & $adb devices | Select-Object -Skip 1 | Where-Object { $_.Trim() -ne "" }

if (-not $devices) {
    Write-Host ""
    Write-Host "No device. Check, in order:" -ForegroundColor Yellow
    Write-Host "  1. The Quest is plugged in with a DATA cable (charge-only cables"
    Write-Host "     enumerate power and nothing else -- this is the usual culprit)."
    Write-Host "  2. Developer Mode is on for this headset, set in the Meta Horizon"
    Write-Host "     phone app: Menu -> Devices -> your headset -> Headset Settings"
    Write-Host "     -> Developer Mode. It needs a (free) developer account."
    Write-Host "  3. The headset is powered on and worn -- it must be awake."
    Write-Host "  4. Put the headset on and accept 'Allow USB debugging'."
    exit 1
}

if ($devices -match "unauthorized") {
    Write-Host ""
    Write-Host "Device is UNAUTHORIZED." -ForegroundColor Yellow
    Write-Host "Put the headset on -- there is an 'Allow USB debugging' prompt"
    Write-Host "waiting inside it. Tick 'Always allow', accept, then re-run this."
    exit 1
}

Write-Host "device: $($devices -join '; ')" -ForegroundColor Green

# Reverse, not forward: the headset connects *out* to a port that this machine
# is listening on.
& $adb reverse "tcp:$WebPort" "tcp:$WebPort" | Out-Null
& $adb reverse "tcp:$ApiPort" "tcp:$ApiPort" | Out-Null

Write-Host ""
Write-Host "forwards now active:" -ForegroundColor Green
& $adb reverse --list

# A forward to a port nothing is serving looks identical, from the headset, to
# a forward that was never set up -- so check this side too.
foreach ($port in @($WebPort, $ApiPort)) {
    $listening = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
    if ($listening) {
        Write-Host "  localhost:$port is being served" -ForegroundColor Green
    } else {
        Write-Host "  WARNING: nothing is listening on localhost:$port" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "In the Quest browser, open:" -ForegroundColor Cyan
Write-Host "    http://localhost:$WebPort/$Page" -ForegroundColor White
Write-Host ""
Write-Host "Check the headset's capabilities first with:" -ForegroundColor Cyan
Write-Host "    http://localhost:$WebPort/probe.html" -ForegroundColor White
Write-Host ""
Write-Host "http, not https: over adb reverse the origin is localhost, which"
Write-Host "browsers already treat as a secure context. The https dev server"
Write-Host "would make you accept a self-signed cert for no benefit."
