$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Step($t) {
    Write-Host ""
    Write-Host "=== $t ===" -ForegroundColor Cyan
}

Step "SpiderBot VM-Gast verifizieren"
$c = Get-CimInstance Win32_ComputerSystem
$desc = "$($c.Manufacturer) $($c.Model)"
if ($desc -notmatch "VirtualBox|VMware|Virtual Machine|KVM|QEMU|Parallels|Xen") {
    throw "Dieser Bootstrap darf nur in einer VM laufen. Erkannt: $desc"
}
Write-Host "VM erkannt: $desc" -ForegroundColor Green

Step "Arbeitskopie anlegen"
$src = "\\VBOXSVR\GameBot"
$dst = "C:\SpiderBotVM\GameBot"
New-Item -ItemType Directory -Force -Path "C:\SpiderBotVM" | Out-Null
if (-not (Test-Path $src)) {
    throw "VirtualBox Shared Folder $src ist nicht erreichbar. Guest Additions pruefen."
}
robocopy $src $dst /MIR /XD ".git" ".venv" "__pycache__" /R:2 /W:2 /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -gt 7) {
    throw "GameBot konnte nicht aus dem Shared Folder kopiert werden."
}

Step "Python installieren"
$pythonExe = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
if (-not $pythonExe) {
    & winget install --id Python.Python.3.11 -e --accept-package-agreements --accept-source-agreements --silent
    $env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")
    $pythonExe = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
}
if (-not $pythonExe) {
    $candidate = Get-ChildItem "$env:LOCALAPPDATA\Programs\Python" -Filter python.exe -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($candidate) { $pythonExe = $candidate.FullName }
}
if (-not $pythonExe) { throw "Python 3.11 konnte nicht gefunden werden." }
Write-Host "Python: $pythonExe" -ForegroundColor Green

Step "SpiderBot Python-Umgebung"
$bot = Join-Path $dst "SpiderBot"
Push-Location $bot
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    & $pythonExe -m venv .venv
}
& ".venv\Scripts\python.exe" -m pip install --upgrade pip
& ".venv\Scripts\python.exe" -m pip install -r requirements.txt
Pop-Location

Step "Firewall und Starter"
try {
    New-NetFirewallRule -DisplayName "SpiderBot VM Web 8010" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8010 -Profile Any -ErrorAction SilentlyContinue | Out-Null
} catch {}

$desktop = [Environment]::GetFolderPath("Desktop")
$launcher = Join-Path $desktop "START SpiderBot VM.bat"
@"
@echo off
cd /d C:\SpiderBotVM\GameBot\SpiderBot
set SPIDER_VM_GUEST=1
call start_vm_guest.bat
"@ | Set-Content -Encoding ASCII $launcher

Step "Microsoft Solitaire"
$solitaireInstalled = Get-AppxPackage -Name "Microsoft.MicrosoftSolitaireCollection" -ErrorAction SilentlyContinue
if (-not $solitaireInstalled) {
    Write-Host "Versuche Solitaire automatisch aus dem Microsoft Store zu installieren..."
    try {
        & winget install 9WZDNCRFHWD2 -s msstore --accept-package-agreements --accept-source-agreements
    } catch {}
    Start-Sleep -Seconds 2
    $solitaireInstalled = Get-AppxPackage -Name "Microsoft.MicrosoftSolitaireCollection" -ErrorAction SilentlyContinue
}

if (-not $solitaireInstalled) {
    Write-Host "Store-Seite wird geoeffnet. Dort nur noch 'Installieren' klicken." -ForegroundColor Yellow
    Start-Process "ms-windows-store://pdp/?ProductId=9WZDNCRFHWD2"
} else {
    Write-Host "Microsoft Solitaire ist installiert." -ForegroundColor Green
}

Step "SpiderBot starten"
Start-Process -FilePath "cmd.exe" -ArgumentList "/c", ('"{0}"' -f $launcher)
Write-Host "SpiderBot laeuft im VM-Gast auf Port 8010." -ForegroundColor Green
Write-Host "Vom Host erreichbar ueber: http://127.0.0.1:8011" -ForegroundColor Green
