# Minecraft JEV + Ollama Agent — Minecraft Java 26.2

Ein eigenständiger Minecraft-Agent nach dem Prinzip:

```
Minecraft 26.2 -> strukturierter Zustand -> Ollama-Planer
               -> Code erzeugt erlaubte Aktionen
               -> JEV Choice
               -> Mineflayer fuehrt die gewaehlte Aktion aus
               -> neuer Zustand
```

Der lokale **Ollama-LLM ersetzt die Astra/Sol-Planerrolle**. JEV bleibt der schnelle Controller, der aus einer begrenzten Liste konkreter Aktionen auswählt.

## Minecraft 26.2

Dieses Projekt ist auf **Minecraft Java 26.2** umgestellt.

Es verwendet nicht die normale npm-Mineflayer-Version, sondern die fuer 26.2 gepflegte Distribution:

```
https://github.com/Complexity-ML/mineflayer-26.2
```

Gepinnte Runtime:

```
mineflayer 4.37.1+complexity.26.2.3
Minecraft Java 26.2
Protocol 776
Node.js 22+
```

Der passende Protocol-, Minecraft-Data-, Chunk- und Physics-Stack wird durch dieses Mineflayer-Paket mitgebracht.

## Voraussetzungen

- Windows 10/11
- Node.js 22 oder neuer
- Minecraft Java 26.2
- eine geoeffnete Singleplayer-Welt mit **Im LAN oeffnen**
- Ollama
- TypeSafe/JEV API-Key

## 1. Lokalen Stand aktualisieren

Wenn du den Ordner bereits heruntergeladen hattest:

```powershell
cd $HOME\Desktop\GameBot
git checkout minecraft-jev-ollama-agent
git pull
cd MinecraftJevAgent
```

Beim ersten Wechsel von der alten 1.16.5-Version auf 26.2 empfehle ich einmal:

```powershell
Remove-Item -Recurse -Force node_modules -ErrorAction SilentlyContinue
Remove-Item package-lock.json -ErrorAction SilentlyContinue
npm install
```

Danach sollte dies eine Complexity-26.2-Version anzeigen:

```powershell
node -p "require('mineflayer/package.json').version"
```

Erwartet:

```
4.37.1+complexity.26.2.3
```

## 2. Ollama vorbereiten

Zum Beispiel:

```powershell
ollama pull qwen3:8b
```

## 3. Minecraft fuer LAN oeffnen

In deiner laufenden Minecraft-26.2-Singleplayer-Welt:

```
ESC -> Im LAN oeffnen -> Port festlegen -> Aenderungen uebernehmen
```

Wenn du Port `25565` waehlst, muss in `.env` ebenfalls `25565` stehen.

## 4. .env

`.env.example` nach `.env` kopieren und den TypeSafe-Key eintragen:

```env
MC_HOST=127.0.0.1
MC_PORT=25565
MC_USERNAME=JevOllama
MC_VERSION=26.2
MC_AUTH=offline

OLLAMA_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen3:8b
PLANNER_EVERY_N_ACTIONS=12

JEV_BASE_URL=https://api.typesafe.ai
JEV_PATH=/v1/systemone
JEV_MODEL=jev-latest
JEV_API_KEY=DEIN_TYPESAFE_KEY
```

`MC_AUTH=offline` ist fuer deine eigene lokale LAN-Welt gedacht. Fuer einen normalen authentifizierten Internet-Server muss stattdessen `MC_AUTH=microsoft` verwendet werden.

## 5. Erst nur Minecraft-Verbindung testen

Bevor Ollama und JEV den Agenten steuern:

```powershell
npm run check:minecraft
```

Bei Erfolg erscheint:

```
OK: Bot ist Minecraft Java 26.2 beigetreten.
```

Damit kann man Verbindungs-/Protokollfehler von JEV- oder Ollama-Fehlern trennen.

## 6. Agent starten

```powershell
npm start
```

Oder:

```
start.bat
```

`start.bat` fuehrt nun bei jedem Start `npm install` aus, damit nicht versehentlich die alte Mineflayer-1.16.5-Installation verwendet wird.

## Rollen

### Ollama

Ollama bekommt den strukturierten Minecraft-Zustand und liefert nur einen High-Level-Plan:

```json
{
  "objective": "Collect wood for basic tools",
  "targets": {
    "oak_log": 4,
    "crafting_table": 1
  },
  "waypoint": null,
  "desiredBlocks": ["oak_log"],
  "notes": "Use nearby observed trees only."
}
```

### JEV

JEV bekommt nur aktuell angebotene, begrenzte Aktionen:

```
a0: Mine one observed oak_log ...
a1: Collect nearby dropped item ...
a2: Explore east ...
a3: Wait briefly ...
```

JEV waehlt eine Option. Der Node-Code fuehrt ausschliesslich diese definierte Aktion aus.

### Mineflayer

Mineflayer ist die Minecraft-Schnittstelle fuer:

- Verbindung zur LAN-Welt
- Position und Weltzustand
- Inventar
- Block-Interaktionen
- Crafting
- Container
- Bewegung

## 26.2-Hinweis zum Pathfinder

Die 26.2-Verbindung und der zugrunde liegende Protocol-/World-State-Stack sind durch die spezielle Mineflayer-Distribution abgedeckt. Der separate `mineflayer-pathfinder` ist jedoch ein eigenstaendiges Upstream-Plugin und kann bei neueren Minecraft-Versionen noch Bewegungsprobleme haben.

Darum zuerst `npm run check:minecraft` ausfuehren. Wenn der Bot verbindet, aber spaeter bei Navigation haengen bleibt, ist das ein anderes Problem als der bisherige `ECONNRESET`-Fehler und wird getrennt behoben.

## Dateien

- `src/agent.mjs` – Hauptschleife
- `src/check-minecraft.mjs` – reiner 26.2-Verbindungstest
- `src/ollama-planner.mjs` – lokaler High-Level-Planer
- `src/jev-controller.mjs` – JEV-Choice-Anbindung
- `src/state.mjs` – strukturierte Minecraft-Beobachtung
- `src/actions.mjs` – Kandidaten + Ausfuehrung
- `src/config.mjs` – Konfiguration
- `runs/<timestamp>/events.jsonl` – Entscheidungs-/Ergebnisprotokoll

## Sicherheit

- API-Keys niemals committen.
- `.env` nicht auf GitHub hochladen.
- Microsoft-Token-Caches nicht committen.
- `offline`-Authentifizierung nur fuer lokale/private Welten verwenden, fuer die du berechtigt bist.
