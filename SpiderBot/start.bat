@echo off
setlocal
cd /d "%~dp0"

echo ===============================================
echo   SpiderBot - Laya / TypeSafe Windows Agent
echo ===============================================
echo.

if not exist ".venv\Scripts\python.exe" (
  echo [1/4] Erstelle EIGENE virtuelle Umgebung in SpiderBot\.venv ...
  py -m venv .venv
  if errorlevel 1 python -m venv .venv
  if errorlevel 1 (
    echo Python-Umgebung konnte nicht erstellt werden.
    pause
    exit /b 1
  )
)

echo [2/4] Aktualisiere pip...
".venv\Scripts\python.exe" -m pip install --upgrade pip

echo [3/4] Installiere NUR SpiderBot-Abhaengigkeiten...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo Installation fehlgeschlagen.
  echo Verwendetes Python:
  ".venv\Scripts\python.exe" --version
  pause
  exit /b 1
)

echo [4/4] Starte SpiderBot auf http://127.0.0.1:8010
start "" http://127.0.0.1:8010
".venv\Scripts\python.exe" -m uvicorn spider_app:app --host 127.0.0.1 --port 8010
