@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [1/3] Erstelle virtuelle Python-Umgebung...
  py -m venv .venv
  if errorlevel 1 python -m venv .venv
)

echo [2/3] Installiere/aktualisiere Abhaengigkeiten...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo Installation fehlgeschlagen.
  pause
  exit /b 1
)

echo [3/3] Starte Laya Checkers auf http://127.0.0.1:8000
start "" http://127.0.0.1:8000
".venv\Scripts\python.exe" -m uvicorn app:app --host 127.0.0.1 --port 8000
