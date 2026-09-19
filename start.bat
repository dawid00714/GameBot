@echo off
setlocal
cd /d "%~dp0"

echo [0/4] Pruefe auf Updates...
if exist ".git" (
  where git >nul 2>nul
  if not errorlevel 1 (
    git pull --ff-only
    if errorlevel 1 echo Hinweis: Git-Update konnte nicht automatisch eingespielt werden. Lokale Dateien werden verwendet.
  )
)

if not exist ".venv\Scripts\python.exe" (
  echo [1/4] Erstelle virtuelle Python-Umgebung...
  py -m venv .venv
  if errorlevel 1 python -m venv .venv
)

echo [2/4] Installiere/aktualisiere Abhaengigkeiten...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo Installation fehlgeschlagen.
  pause
  exit /b 1
)

echo [3/4] Starte Laya Checkers...
echo [4/4] Browser: http://127.0.0.1:8000
start "" http://127.0.0.1:8000
".venv\Scripts\python.exe" -m uvicorn app:app --host 127.0.0.1 --port 8000
