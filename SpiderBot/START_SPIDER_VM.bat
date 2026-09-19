@echo off
setlocal
set "VM=SpiderBot-Windows"
set "VBOX=%ProgramFiles%\Oracle\VirtualBox\VBoxManage.exe"
if not exist "%VBOX%" set "VBOX=%ProgramFiles(x86)%\Oracle\VirtualBox\VBoxManage.exe"
if not exist "%VBOX%" (
  echo VirtualBox nicht gefunden. Zuerst CREATE_WINDOWS_VM.bat ausfuehren.
  pause
  exit /b 1
)

"%VBOX%" showvminfo "%VM%" --machinereadable | findstr /c:"VMState=\"running\"" >nul
if errorlevel 1 (
  "%VBOX%" startvm "%VM%" --type gui
  timeout /t 10 /nobreak >nul
)
start "" http://127.0.0.1:8011
endlocal
