@echo off
cd /d "%~dp0"
title SpiderBot - VM Einrichtung abschliessen
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0finish_vm_setup.ps1"
if errorlevel 1 pause
