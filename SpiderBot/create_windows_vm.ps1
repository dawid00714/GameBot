param(
    [string]$VmName = "SpiderBot-Windows",
    [string]$GuestUser = "spider",
    [string]$GuestPassword = "SpiderBot-VM_2026!",
    [int]$DiskGB = 80
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Write-Step([string]$Text) {
    Write-Host ""
    Write-Host "=== $Text ===" -ForegroundColor Cyan
}

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = New-Object Security.Principal.WindowsPrincipal($id)
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-Admin)) {
    Write-Host "Administratorrechte werden angefordert..."
    $args = @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ('"{0}"' -f $PSCommandPath),
        "-VmName", ('"{0}"' -f $VmName),
        "-GuestUser", ('"{0}"' -f $GuestUser),
        "-GuestPassword", ('"{0}"' -f $GuestPassword),
        "-DiskGB", $DiskGB
    )
    Start-Process powershell.exe -Verb RunAs -ArgumentList ($args -join " ")
    exit
}

$SpiderDir = Split-Path -Parent $PSCommandPath
$RepoRoot = Split-Path -Parent $SpiderDir
$IsoUrl = "https://aka.ms/Win11E-ISO-25H2-de-de"
$IsoDir = Join-Path $env:USERPROFILE "Downloads\SpiderBotVM"
$IsoPath = Join-Path $IsoDir "Win11_Enterprise_25H2_DE.iso"
$VmBase = Join-Path $env:USERPROFILE "VirtualBox VMs\$VmName"
$DiskPath = Join-Path $VmBase "$VmName.vdi"
$pf = [Environment]::GetFolderPath("ProgramFiles")
$pfx86 = [Environment]::GetFolderPath("ProgramFilesX86")
$VBoxCandidates = @(
    (Join-Path $pf "Oracle\VirtualBox\VBoxManage.exe"),
    (Join-Path $pfx86 "Oracle\VirtualBox\VBoxManage.exe")
)

Write-Step "1/8 VirtualBox pruefen"
$VBox = $VBoxCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $VBox) {
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $winget) {
        throw "winget wurde nicht gefunden. VirtualBox kann nicht automatisch installiert werden."
    }
    Write-Host "VirtualBox wird installiert..."
    & winget install --id Oracle.VirtualBox -e --accept-package-agreements --accept-source-agreements --silent
    if ($LASTEXITCODE -ne 0) {
        throw "VirtualBox-Installation ist fehlgeschlagen (ExitCode $LASTEXITCODE)."
    }
    $VBox = $VBoxCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $VBox) {
        throw "VirtualBox wurde installiert, VBoxManage.exe wurde aber nicht gefunden. Windows eventuell einmal neu starten."
    }
}
Write-Host "VirtualBox: $VBox" -ForegroundColor Green

Write-Step "2/8 Hardware pruefen"
$totalRamGB = [math]::Floor((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)
$vmRamMB = if ($totalRamGB -ge 24) { 8192 } elseif ($totalRamGB -ge 16) { 6144 } else { 4096 }
$cpuTotal = [Environment]::ProcessorCount
$vmCpus = [math]::Max(2, [math]::Min(6, [math]::Floor($cpuTotal / 2)))
Write-Host "VM: $vmRamMB MB RAM, $vmCpus CPU-Kerne, dynamische $DiskGB GB Disk"

Write-Step "3/8 Windows-11-Enterprise-ISO von Microsoft"
New-Item -ItemType Directory -Force -Path $IsoDir | Out-Null
if (-not (Test-Path $IsoPath) -or (Get-Item $IsoPath).Length -lt 4GB) {
    if (Test-Path $IsoPath) { Remove-Item $IsoPath -Force }
    Write-Host "Windows 11 Enterprise 25H2 Deutsch wird direkt von Microsoft geladen."
    try {
        Import-Module BitsTransfer -ErrorAction Stop
        Start-BitsTransfer -Source $IsoUrl -Destination $IsoPath -DisplayName "SpiderBot Windows 11 VM"
    } catch {
        Write-Host "BITS fehlgeschlagen, nutze curl.exe..."
        & curl.exe -L --fail --retry 5 --retry-delay 3 -o $IsoPath $IsoUrl
        if ($LASTEXITCODE -ne 0) { throw "Windows-ISO konnte nicht geladen werden." }
    }
}
Write-Host "ISO: $IsoPath" -ForegroundColor Green

Write-Step "4/8 Vorhandene VM pruefen"
$existing = & $VBox list vms 2>$null
if ($existing -match ('"' + [regex]::Escape($VmName) + '"')) {
    Write-Host "VM '$VmName' existiert bereits." -ForegroundColor Yellow
    $answer = Read-Host "Neu erstellen? Alte VM wird geloescht. (J/N)"
    if ($answer -match '^[JjYy]') {
        try { & $VBox controlvm $VmName poweroff 2>$null | Out-Null } catch {}
        Start-Sleep -Seconds 2
        & $VBox unregistervm $VmName --delete
        Start-Sleep -Seconds 2
    } else {
        & $VBox startvm $VmName --type gui
        Start-Process "http://127.0.0.1:8011"
        exit
    }
}

Write-Step "5/8 Virtuelle Maschine erstellen"
New-Item -ItemType Directory -Force -Path $VmBase | Out-Null
& $VBox createvm --name $VmName --ostype Windows11_64 --basefolder (Split-Path -Parent $VmBase) --register
& $VBox modifyvm $VmName --memory $vmRamMB --cpus $vmCpus --vram 128 --graphicscontroller vboxsvga --firmware efi --ioapic on --paravirtprovider hyperv --nic1 nat --clipboard-mode bidirectional --draganddrop bidirectional
try { & $VBox modifyvm $VmName --tpm-type 2.0 2>$null | Out-Null } catch {}

& $VBox createmedium disk --filename $DiskPath --size ($DiskGB * 1024) --format VDI
& $VBox storagectl $VmName --name "SATA" --add sata --controller IntelAhci --portcount 4 --bootable on
& $VBox storageattach $VmName --storagectl "SATA" --port 0 --device 0 --type hdd --medium $DiskPath
try { & $VBox modifyvm $VmName --natpf1 delete "SpiderBot-Web" 2>$null | Out-Null } catch {}
& $VBox modifyvm $VmName --natpf1 "SpiderBot-Web,tcp,127.0.0.1,8011,,8010"
& $VBox sharedfolder add $VmName --name "GameBot" --hostpath $RepoRoot --automount

Write-Step "6/8 Windows unbeaufsichtigt installieren"
$gaIso = Join-Path (Split-Path $VBox -Parent) "VBoxGuestAdditions.iso"
$unattended = @(
    "unattended", "install", $VmName,
    "--iso=$IsoPath",
    "--user=$GuestUser",
    "--password=$GuestPassword",
    "--full-user-name=SpiderBot",
    "--country=DE",
    "--locale=de_DE",
    "--time-zone=Europe/Berlin",
    "--hostname=spiderbot-vm.local",
    "--start-vm=gui"
)
if (Test-Path $gaIso) { $unattended += "--install-additions" }
& $VBox @unattended
if ($LASTEXITCODE -ne 0) {
    throw "VirtualBox Unattended-Installation konnte nicht gestartet werden (ExitCode $LASTEXITCODE)."
}

Write-Host "Windows wird jetzt in der VM installiert." -ForegroundColor Green
Write-Host "Die echte Host-Maus bleibt davon getrennt."
Write-Host "Warte auf Windows + Guest Additions..."

Write-Step "7/8 Warten auf fertiges Windows"
$deadline = (Get-Date).AddMinutes(45)
$guestReady = $false
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 20
    try {
        $out = & $VBox guestcontrol $VmName run --username $GuestUser --password $GuestPassword --wait-stdout --wait-stderr --exe "C:\Windows\System32\cmd.exe" -- "cmd.exe" "/c" "echo SPIDERBOT_GUEST_READY" 2>$null
        if ($out -match "SPIDERBOT_GUEST_READY") {
            $guestReady = $true
            break
        }
    } catch {}
    Write-Host "." -NoNewline
}
Write-Host ""

if (-not $guestReady) {
    Write-Host "Windows ist noch nicht per GuestControl erreichbar." -ForegroundColor Yellow
    Write-Host "Wenn der VM-Desktop sichtbar ist: FINISH_VM_SETUP.bat ausfuehren."
    exit
}

Write-Step "8/8 SpiderBot im VM-Gast einrichten"
$guestBootstrap = "\\VBOXSVR\GameBot\SpiderBot\vm_guest_bootstrap.ps1"
& $VBox guestcontrol $VmName run --username $GuestUser --password $GuestPassword --wait-stdout --wait-stderr --exe "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" -- "powershell.exe" "-NoProfile" "-ExecutionPolicy" "Bypass" "-File" $guestBootstrap

Write-Host ""
Write-Host "===============================================" -ForegroundColor Green
Write-Host " SpiderBot Windows-VM ist eingerichtet." -ForegroundColor Green
Write-Host "===============================================" -ForegroundColor Green
Write-Host "VM-Benutzer: $GuestUser"
Write-Host "VM-Passwort: $GuestPassword"
Write-Host "Host-Web-UI: http://127.0.0.1:8011"
Write-Host ""
Start-Process "http://127.0.0.1:8011"
