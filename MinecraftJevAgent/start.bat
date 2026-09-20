@echo off
setlocal
cd /d "%~dp0"

if not exist .env (
  echo.
  echo [FEHLER] .env fehlt.
  echo Kopiere zuerst .env.example zu .env und trage JEV_API_KEY ein.
  echo.
  pause
  exit /b 1
)

where node >nul 2>nul
if errorlevel 1 (
  echo [FEHLER] Node.js wurde nicht gefunden.
  pause
  exit /b 1
)

if not exist node_modules (
  echo Installiere Node-Abhaengigkeiten...
  call npm install
  if errorlevel 1 exit /b 1
)

echo Pruefe Ollama...
curl -s http://127.0.0.1:11434/api/tags >nul 2>nul
if errorlevel 1 (
  echo [FEHLER] Ollama ist unter http://127.0.0.1:11434 nicht erreichbar.
  echo Starte Ollama und versuche es erneut.
  pause
  exit /b 1
)

echo Starte Minecraft JEV + Ollama Agent...
node src/agent.mjs
pause
