@echo off
setlocal
cd /d "%~dp0"
title SpiderVision2

echo.
echo ==========================================
echo   SpiderVision2 v1.1
echo   Kein UIA - kein OCR - kein LLM
echo ==========================================
echo.

python -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo [FEHLER] Abhaengigkeiten konnten nicht installiert werden.
  pause
  exit /b 1
)

start "" "http://127.0.0.1:8020"
python -m uvicorn app:app --host 127.0.0.1 --port 8020
pause
