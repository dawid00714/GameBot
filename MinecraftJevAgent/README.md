# Minecraft JEV + Ollama Agent

Ein eigenständiger Minecraft-Agent nach dem Prinzip:

```
Minecraft -> strukturierter Zustand -> Ollama-Planer
          -> Code erzeugt erlaubte Aktionen
          -> JEV Choice
          -> Mineflayer fuehrt die gewaehlte Aktion aus
          -> neuer Zustand
```

Der lokale **Ollama-LLM ersetzt die Astra/Sol-Planerrolle**. JEV bleibt der schnelle Controller, der aus einer begrenzten Liste konkreter Aktionen auswählt.

## Wichtig

Dieses Verzeichnis ist eine neue, eigenständige Implementierung der Architektur. Es kopiert nicht den Quellcode von `rmalde/minecraft-agent`.

Der aktuelle Stand ist ein **funktionierendes Grundgerüst/MVP**, nicht bereits der komplette verifizierte Ender-Dragon-Speedrun des Referenzprojekts. Spezialisierte Module für feste Routen, Nether-Portale, Dragon-Bed-Combat, native Aufnahme und Run-Verifikation können darauf aufgebaut werden.

## Voraussetzungen

- Windows 10/11
- Node.js 22+
- Minecraft Java
- lokaler Minecraft-Server, standardmäßig Java 1.16.5 auf `127.0.0.1:25565`
- Ollama
- TypeSafe/JEV-Zugang mit TypeSafe API-Key

## 1. Ollama vorbereiten

Ollama installieren und ein Modell laden, zum Beispiel:

```powershell
ollama pull qwen3:8b
```

Der Agent verwendet standardmäßig:

```
http://127.0.0.1:11434
qwen3:8b
```

Jedes andere lokal installierte Ollama-Chatmodell kann über `.env` gewählt werden.

## 2. Konfiguration

`.env.example` nach `.env` kopieren.

Beispiel für die **native TypeSafe API**:

```env
MC_HOST=127.0.0.1
MC_PORT=25565
MC_USERNAME=JevOllama
MC_VERSION=1.16.5

OLLAMA_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen3:8b

JEV_BASE_URL=https://api.typesafe.ai
JEV_PATH=/v1/systemone
JEV_MODEL=jev-latest
JEV_API_KEY=DEIN_TYPESAFE_KEY
```

Der Request geht damit an:

```
POST https://api.typesafe.ai/v1/systemone
Authorization: Bearer <JEV_API_KEY>
```

Keine API-Keys nach GitHub committen oder in Screenshots veröffentlichen.

## 3. Start

Unter Windows:

```
start.bat
```

Oder:

```powershell
npm install
npm start
```

## Rollen

### Ollama

Ollama bekommt einen kompakten strukturierten Minecraft-Zustand und liefert JSON:

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

Ollama steuert nicht direkt Tastatur oder Maus.

### Aktionsgenerator

`src/actions.mjs` erzeugt aus dem echten Spielzustand eine begrenzte Menge ausführbarer Aktionen, z. B.:

- einen beobachteten Zielblock abbauen
- einen Drop einsammeln
- ein aktuell craftbares Ziel craften
- Crafting Table setzen
- Container plündern
- zum Planner-Waypoint laufen
- kurze Erkundungsbewegung
- warten

### JEV

JEV sieht den Zustand, das Planner-Ziel und nur die angebotenen Aktionen.

Beispiel:

```
a0: Mine one observed oak_log ...
a1: Collect nearby dropped item ...
a2: Explore east ...
a3: Wait briefly ...
```

JEV wählt eine Option. Der Node-Code führt ausschließlich die gewählte, vorher definierte Funktion aus.

### Mineflayer

Mineflayer ist die Motorik:

- Verbindung zum Minecraft-Server
- Inventar
- Block-Interaktionen
- Crafting
- Container
- Bewegung und Pathfinder

## Dateien

- `src/agent.mjs` – Hauptschleife
- `src/ollama-planner.mjs` – lokaler High-Level-Planer
- `src/jev-controller.mjs` – JEV-Choice-Anbindung
- `src/state.mjs` – strukturierte Minecraft-Beobachtung
- `src/actions.mjs` – Kandidaten + Ausführung
- `src/config.mjs` – Konfiguration
- `runs/<timestamp>/events.jsonl` – vollständiges Entscheidungs-/Ergebnisprotokoll

## Nächster Ausbau

Für eine Version nahe am Referenzprojekt fehlen insbesondere:

1. Stages/State-Machine für Preparation, Nether, Stronghold, End und Victory
2. feste oder dynamisch gefundene Routen
3. robuste Werkzeug- und Crafting-Recovery
4. Hostile-Mob- und Hazard-Safety
5. Portalbau und Nether-Travel
6. spezialisierter Ender-Dragon-Controller
7. native Minecraft-Aufnahme
8. Victory-/Run-Verifikation
9. UI zur Live-Anzeige von Ollama-Plan, JEV-Wahrscheinlichkeiten und Aktionen

Die Architektur ist bereits so getrennt, dass diese Module ergänzt werden können, ohne Ollama oder JEV neu zu bauen.
