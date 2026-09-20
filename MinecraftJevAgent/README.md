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


## Alle lokalen Ollama-Modelle benchmarken

Der Benchmark erkennt standardmäßig automatisch **alle lokal installierten Ollama-Modelle** über `/api/tags` und testet sie nacheinander mit mehreren Minecraft-Planer-Aufgaben.

Start:

```powershell
npm run benchmark:ollama
```

Gemessen werden unter anderem:

- Zeit bis zum fertigen Plan
- Ollama Load-Zeit
- Ausgabe-Tokens pro Sekunde
- gültige Planner-JSON-Struktur
- Befolgung von Minecraft-Zielen
- korrekte Verwendung eines bekannten Waypoints
- Vermeidung erfundener Waypoints
- Fehler und Timeouts

Am Ende erscheinen drei relevante Ergebnisse:

```
BESTE BALANCE
BESTE QUALITÄT
SCHNELLSTES
```

Zusätzlich wird eine vollständige JSON- und CSV-Datei unter `benchmarks/` gespeichert.

Der Standard-Benchmark setzt `think=false`, weil der Minecraft-Planer möglichst schnell reagieren soll. Das ist besonders für Qwen3 wichtig.

Optionen:

```powershell
# Nur bestimmte Modelle:
$env:BENCH_MODELS="qwen3:1.7b,qwen3:4b,qwen3:8b"
npm run benchmark:ollama

# Zwei Durchläufe pro Test:
$env:BENCH_RUNS="2"
npm run benchmark:ollama

# 90 Sekunden Timeout je Anfrage:
$env:BENCH_TIMEOUT_MS="90000"
npm run benchmark:ollama

# Thinking explizit mitbenchmarken:
$env:BENCH_THINK="true"
npm run benchmark:ollama
```

Für den normalen Agenten kann Thinking separat gesteuert werden:

```env
OLLAMA_THINK=false
OLLAMA_TIMEOUT_MS=60000
```

Für Echtzeit-Minecraft ist `OLLAMA_THINK=false` normalerweise die sinnvollere Einstellung.


## Architektur v0.4: Ollama + TypeSafe auf zwei Ebenen

Der Agent verwendet jetzt TypeSafe/JEV nicht nur fuer einzelne Aktionen, sondern auch fuer die Auswahl des High-Level-Plans:

```
Minecraft-Zustand
      |
      v
Ollama erzeugt 3 unterschiedliche Plan-Kandidaten
      |
      v
Code prueft Targets + erzeugt pro Plan die aktuell verfuegbaren Aktionen
      |
      v
TypeSafe/JEV PLAN CHOICE
      |
      v
gewaehlter High-Level-Plan
      |
      v
Action Generator
      |
      v
TypeSafe/JEV ACTION CHOICE
      |
      v
Mineflayer fuehrt genau eine begrenzte Aktion aus
      |
      v
neuer Minecraft-Zustand
```

Im Terminal erscheinen deshalb nun zwei getrennte JEV-Entscheidungen:

```
[OLLAMA] PLAN-KANDIDATEN:
...
[JEV] Waehle High-Level-Plan aus 3 Kandidaten...
[JEV] PLAN GEWAEHLT: p1
...
[JEV] Waehle Aktion aus 7 Aktionen...
STEP 0 | ...
```

Vor der Plan-Auswahl werden bereits erreichte numerische Targets entfernt. JEV bekommt fuer jeden Plan zusaetzlich die aktuell wirklich verfuegbaren Aktionen und kann dadurch einen Plan bevorzugen, der im aktuellen Zustand konkret ausfuehrbar ist.

Die alte einzelne Planner-Funktion bleibt fuer den Ollama-Benchmark erhalten; der laufende Minecraft-Agent benutzt die neue 3-Kandidaten-Planung.


## Minecraft 26.2 Protocol-Fix

Der verwendete Complexity-Minecraft-Data-Stack hat in seiner 26.2-Serverbound-Paketliste einen bekannten lokalen Mapping-Fehler: ab den Spectator-/Use-Item-Paketen waren IDs verschoben. Dadurch konnte ein normales `block_place`-Paket vom Vanilla-26.2-Server als `test_instance_block_action` interpretiert werden. Das fuehrte zu Kicks wie:

```
DecoderException
serverbound/minecraft:test_instance_block_action
extra whilst reading packet
```

Der Agent korrigiert diese IDs jetzt **vor dem Laden von Mineflayer und minecraft-protocol direkt im Speicher**:

```
0x3e spectator_action
0x3f arm_animation
0x40 spectate
0x41 test_instance_block_action
0x42 block_place
0x43 use_item
0x44 custom_click_action
```

Pruefung:

```powershell
npm run check:protocol
```

Beim normalen Start muss vor dem Minecraft-Connect stehen:

```
[PROTOCOL] Minecraft 26.2 serverbound packet map corrected in memory.
```

Ausserdem ist automatisches Pathfinder-Scaffolding/Pillaring deaktiviert. Blockplatzierung erfolgt damit nur ueber explizite Agent-Aktionen.


## Planner-Guard v0.4.2

Die Plan-Pipeline prueft jetzt nicht mehr nur, ob irgendeine Aktion existiert, sondern ob eine Aktion den vorgeschlagenen Plan **direkt ausfuehren kann**.

Zusaetzlich werden fehlerhafte Ollama-Namen normalisiert:

```
minecraft_dirt -> dirt
minecraft:dirt -> dirt
minecraft_crafting_table -> crafting_table
```

Beispiele fuer Plan-Relevanz:

```
Ziel: dirt sammeln       -> mine_dirt_...
Ziel: Spieler folgen     -> follow_player_...
Ziel: erkunden           -> explore_...
Ziel: crafting_table     -> craft_crafting_table
```

Plaene wie `craft a tool` ohne konkretes Crafting-Target oder `loot the player` werden als nicht sofort ausfuehrbar verworfen.

Wenn ein gewaehlter Plan konkrete relevante Aktionen besitzt, bekommt JEV fuer die Action-Choice nur noch diese Aktionen. `wait` und unpassende Exploration konkurrieren dann nicht mehr mit einer echten Fortschrittsaktion.


## In-Game Chat und Benutzeraufgaben

Version 0.5.0 kann direkt im Minecraft-Chat angesprochen werden. Der Bot antwortet selbst im Chat und meldet dort seinen aktuellen Plan bzw. den Fortschritt einer Benutzeraufgabe.

Unterstuetzte Befehle:

```
hilfe
status
folge mir
komm her
baue hier ein haus
stopp
weiter
autonom
```

Die Praefixe `!bot`, `bot`, `jev` oder `JevOllama` sind optional. Beide Varianten funktionieren:

```
folge mir
!bot folge mir
```

### Hausbau

`baue hier ein haus` erzeugt ein kleines 5x5-Starterhaus wenige Bloecke neben der Position des Spielers, der den Befehl gegeben hat.

Der Agent:

1. prueft den Bauplan,
2. nutzt vorhandene Planken/Cobblestone wenn genug vorhanden sind,
3. verwendet sonst Dirt und sammelt fehlendes Material,
4. setzt das Haus blockweise,
5. meldet den Fortschritt im Minecraft-Chat.

Der Bauplan besteht aus zwei Block hohen Waenden, einer Tuer-Oeffnung und einem flachen Dach (55 Bloecke).

### "Sprechblase"

Vanilla Minecraft hat fuer normale Spieler keine echte grafische Sprechblase ueber dem Kopf. Der Agent emuliert dies deshalb ohne Mod/Plugin ueber sichtbare In-Game-Chatmeldungen:

```
[JevOllama] Plan: Follow the visible player Dawid
[JevOllama] Hausbau: 25/55 Bloecke.
[JevOllama] Haus fertig.
```

Eine echte schwebende Textblase ueber dem Bot wuerde ein Client-Mod, Server-Plugin oder OP-Kommandos/Text-Display-Entities benoetigen.


## Follow-Verhalten v0.5.1

Sichtbare Spieler sind im autonomen Modus nur Beobachtungen. Der Agent darf ihnen **nicht selbststaendig folgen**.

Nur ausdrueckliche Chat-Befehle aktivieren Spieler-Navigation:

```
folge mir
```

bleibt aktiv, bis `stopp` oder `autonom` gesendet wird.

```
komm her
```

ist dagegen einmalig: Der Bot kommt bis auf ca. 2.5 Bloecke heran, meldet `Ich bin da.` und kehrt danach in den autonomen Modus zurueck.

Der autonome Fallback ist jetzt Exploration statt Spieler-Verfolgung.


## Tuereinbau v0.5.4

Der Chat-Befehl `baue eine tuer ein` ist jetzt eine echte Benutzeraufgabe statt nur Konversation.

Ablauf:

1. Der Agent verwendet das zuletzt gebaute Haus.
2. Er sucht eine normale `*_door` im eigenen Inventar.
3. Ohne vorhandene Tuer veraendert er **keinen** Wandblock.
4. Mit vorhandener Tuer bearbeitet er ausschliesslich die zwei gespeicherten Tueroeffnungs-Bloecke.
5. Er setzt die Tuer auf den Bodenblock der Oeffnung und prueft danach, ob ein Tuerblock existiert.

Beispiele:

```
baue eine tuer ein
setz eine tuer ein
installiere die tuer
hast du eine tuer im inventar?
```

Nach erfolgreichem Einbau wird die Tuer im Haus-Memory gespeichert.


## Ollama Chat Command Interpreter v0.6.0

Chat-Befehle werden nicht mehr ueber fest codierte deutsche Regex-Saetze erkannt.

Stattdessen bekommt Ollama bei jeder Spielernachricht:

- den Originaltext,
- das aktuelle Inventar inklusive Item-Namen und Slots,
- GameMode,
- Bot-Position,
- sichtbare Spieler,
- aktuellen Plan,
- aktive Benutzeraufgabe,
- Metadaten des zuletzt gebauten Hauses,
- die aktuell erlaubten High-Level-Aktionstypen.

Ollama erzeugt daraus einen strukturierten Aktionsbefehl, zum Beispiel:

```json
{
  "kind": "command",
  "action": "install_door",
  "arguments": {
    "material": null,
    "doorItem": null
  },
  "reply": "Okay, ich setze die vorhandene Tuer ein."
}
```

Der Node-Agent validiert nur noch den erzeugten Befehl gegen den echten Minecraft-Zustand und fuehrt ihn ueber die vorhandenen Skills aus. TypeSafe/JEV bleibt fuer die Auswahl der konkreten Low-Level-Aktion innerhalb einer Aufgabe zustaendig.

Fragen und Aussagen erzeugen keine Aufgabe:

```
"Du hast doch eine Tuer im Inventar."
-> conversation / none

"Hast du eine Tuer im Inventar?"
-> question / none

"Baue die Tuer ein."
-> command / install_door
```

Ein lokaler Regex bleibt nur als Not-Stopp-Fallback fuer `stop/stopp/pause`, falls Ollama selbst nicht erreichbar ist.


## Dream-RSI-inspirierte Selbstverbesserung v0.7.0

**Wichtig:** Das offizielle Dream-RSI-Repository von Google/DeepMind veroeffentlicht Stand September 2026 noch nicht den vollstaendigen Code. Diese Integration ist deshalb eine eigene, Minecraft-spezifische Reimplementierung der veroeffentlichten Idee und nicht der Originalcode.

Die Integration veraendert **nicht** die Gewichte von Ollama oder JEV. Sie verbessert die Meta-/Explorationsstrategie:

```
Minecraft online spielen
        |
        v
Transitionen + Kandidaten + Resultate als History speichern
        |
        v
History-Pool / Replay-Graph
        |
        v
Ollama erzeugt konservative Policy-Varianten
        |
        v
Offline-Replay ueber bereits beobachtete Outcomes
        |
        v
Incumbent + Kandidaten vergleichen
        |
        v
nur bei besserem Replay-Score Policy aktualisieren
        |
        v
neue Policy steuert Kandidatenmenge, Replan-Intervall,
Repeat-Schwelle und Action-Prioritaeten
        |
        v
TypeSafe/JEV trifft weiterhin die konkrete Choice
```

Gespeichert wird unter:

```
dream-rsi/history.jsonl
dream-rsi/policy.json
dream-rsi/policy-history.jsonl
```

Jede Transition speichert u.a.:

- Session-/Node-ID und Parent-ID
- abstrahierten State-Fingerprint
- aktuellen Plan
- angebotene Actions
- ausgewaehlte Action
- reales Ergebnis
- Reward
- Next-State-Fingerprint

Da eine laufende Minecraft-Welt nicht billig auf beliebige alte Snapshots verzweigt werden kann, ist der Replay-Simulator **konservativ**: Eine alternative Policy wird nur dort bewertet, wo fuer den entsprechenden abstrahierten Zustand und die ausgewaehlte Action bereits ein reales Outcome in der History existiert. Unbeobachtete Aeste werden nicht erfunden.

Die aktuell deployte Policy bleibt immer Teil des Vergleichs. Eine neue Policy wird nur aktiviert, wenn ihr Replay-Score den Incumbent um mindestens `DREAM_RSI_MIN_IMPROVEMENT` uebertrifft und genug Replay-Coverage vorhanden ist.

Konfiguration:

```env
DREAM_RSI_ENABLED=true
DREAM_RSI_DIR=./dream-rsi
DREAM_RSI_DREAM_EVERY=40
DREAM_RSI_MIN_HISTORY=25
DREAM_RSI_MAX_HISTORY=5000
DREAM_RSI_PROPOSALS=4
DREAM_RSI_MIN_IMPROVEMENT=0.03
```

Status anzeigen:

```powershell
npm run dream:status
```

Im normalen Log erscheinen Eintraege wie:

```
[DREAM-RSI] Record node=... action=explore_east reward=1.42 history=31
[DREAM-RSI] Dreaming ueber 40 gespeicherte Transitionen...
[DREAM-RSI] Replay-Rangliste:
  [candidate] avoid-wait score=1.123 ...
  [incumbent] baseline score=0.991 ...
[DREAM-RSI] POLICY UPDATE -> generation=1 ...
```
