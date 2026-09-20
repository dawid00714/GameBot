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
  echo [FEHLER] Node.js wurde nicht gefunden. Node.js 22 oder neuer wird benoetigt.
  pause
  exit /b 1
)

echo.
echo Aktualisiere Abhaengigkeiten fuer Minecraft Java 26.2...
call npm install
if errorlevel 1 (
  echo [FEHLER] npm install ist fehlgeschlagen.
  pause
  exit /b 1
)

echo.
echo Pruefe Ollama...
curl -s http://127.0.0.1:11434/api/tags >nul 2>nul
if errorlevel 1 (
  echo [FEHLER] Ollama ist unter http://127.0.0.1:11434 nicht erreichbar.
  echo Starte Ollama und versuche es erneut.
  pause
  exit /b 1
)

echo.
echo Starte Minecraft JEV + Ollama Agent fuer Minecraft Java 26.2...
node src/agent.mjs
pause
