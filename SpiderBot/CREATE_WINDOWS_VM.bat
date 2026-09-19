@echo off
cd /d "%~dp0"
title SpiderBot - Windows VM erstellen
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0create_windows_vm.ps1"
if errorlevel 1 (
  echo.
  echo VM-Setup wurde mit einem Fehler beendet.
  pause
)
