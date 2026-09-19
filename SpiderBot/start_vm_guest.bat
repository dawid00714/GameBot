@echo off
setlocal
cd /d "%~dp0"

echo ===============================================
echo   SpiderBot VM Guest Mode
echo   REAL drag INSIDE VM only
echo ===============================================
echo.

set "SPIDER_VM_GUEST=1"

echo [1/4] Prüfe, ob dieses Windows in einer virtuellen Maschine läuft...
for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command "$c=Get-CimInstance Win32_ComputerSystem; Write-Output ($c.Manufacturer + '|' + $c.Model)"`) do set "VMDESC=%%i"
echo     %VMDESC%

echo [2/4] Starte vorhandene SpiderBot-Umgebung...
if not exist ".venv\Scripts\python.exe" (
  echo Fehler: .venv fehlt. Bitte zuerst start.bat einmal ausführen.
  pause
  exit /b 1
)

echo [3/4] VM-Eingabemodus ist aktiviert.
echo     Die Maus, die SpiderBot bewegt, ist NUR die Maus dieses VM-Gasts.
echo     Auf Bare-Metal verweigert der Code den SendInput-Drag automatisch.

echo [4/4] Starte http://0.0.0.0:8010
start "" http://127.0.0.1:8010
".venv\Scripts\python.exe" -m uvicorn spider_app:app --host 0.0.0.0 --port 8010

endlocal
