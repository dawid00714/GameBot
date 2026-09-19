@echo off
setlocal
cd /d "%~dp0"

rem Benutzer hat echte Host-Maussteuerung explizit angefordert.
set "SPIDER_REAL_MOUSE=1"
set "SPIDER_VM_GUEST=0"

echo ===============================================
echo   SpiderBot - Laya / TypeSafe Windows Agent
echo ===============================================
echo.

echo [0/5] Pruefe auf SpiderBot-Updates...
where git >nul 2>nul
if not errorlevel 1 (
  if exist "..\.git" (
    git -C ".." pull --ff-only
    if errorlevel 1 echo Hinweis: Auto-Update nicht moeglich. Lokale Version wird gestartet.
  )
)

if not exist ".venv\Scripts\python.exe" (
  echo [1/5] Erstelle EIGENE virtuelle Umgebung in SpiderBot\.venv ...
  py -m venv .venv
  if errorlevel 1 python -m venv .venv
  if errorlevel 1 (
    echo Python-Umgebung konnte nicht erstellt werden.
    pause
    exit /b 1
  )
)

echo [2/5] Aktualisiere pip...
".venv\Scripts\python.exe" -m pip install --upgrade pip

echo [3/5] Installiere NUR SpiderBot-Abhaengigkeiten...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo Installation fehlgeschlagen.
  echo Verwendetes Python:
  ".venv\Scripts\python.exe" --version
  pause
  exit /b 1
)

echo [4/5] Starte SpiderBot auf http://127.0.0.1:8010
echo       ECHTE WINDOWS-MAUS IST AKTIV.
echo       ALT+L = Agent START / STOP.
echo [5/5] Im Browser muss oben Build 5.5 stehen.
start "" http://127.0.0.1:8010
".venv\Scripts\python.exe" -m uvicorn spider_app:app --host 127.0.0.1 --port 8010
