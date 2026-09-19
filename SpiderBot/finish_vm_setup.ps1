param(
    [string]$VmName = "SpiderBot-Windows",
    [string]$GuestUser = "spider",
    [string]$GuestPassword = "SpiderBot-VM_2026!"
)
$ErrorActionPreference = "Stop"

$pf = [Environment]::GetFolderPath("ProgramFiles")
$pfx86 = [Environment]::GetFolderPath("ProgramFilesX86")
$VBox = @(
    (Join-Path $pf "Oracle\VirtualBox\VBoxManage.exe"),
    (Join-Path $pfx86 "Oracle\VirtualBox\VBoxManage.exe")
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $VBox) { throw "VBoxManage.exe nicht gefunden." }

$state = & $VBox showvminfo $VmName --machinereadable 2>$null
if (-not ($state -match 'VMState="running"')) {
    & $VBox startvm $VmName --type gui
    Start-Sleep -Seconds 15
}

$bootstrap = "\\VBOXSVR\GameBot\SpiderBot\vm_guest_bootstrap.ps1"
Write-Host "Warte auf Guest Additions/Windows..."
$deadline = (Get-Date).AddMinutes(20)
while ((Get-Date) -lt $deadline) {
    try {
        $args = @(
            "guestcontrol", $VmName, "run",
            "--username", $GuestUser,
            "--password", $GuestPassword,
            "--wait-stdout",
            "--wait-stderr",
            "--exe", "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
            "--",
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-File", $bootstrap
        )
        & $VBox @args
        if ($LASTEXITCODE -eq 0) {
            Write-Host "VM-Gast-Setup abgeschlossen." -ForegroundColor Green
            Start-Process "http://127.0.0.1:8011"
            exit 0
        }
    } catch {}
    Start-Sleep -Seconds 15
}
throw "VM-Gast wurde nicht erreichbar. Pruefe, ob Windows fertig installiert und angemeldet ist."
