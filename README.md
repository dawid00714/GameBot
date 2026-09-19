# Laya vs TypeSafe / Jev – Checkers Arena

Lokale 8×8-Dame-Arena, in der **Laya** gegen **TypeSafe System One / Jev** spielt.

## Neu in Arena Build 2.0

- Laya spielt automatisch gegen TypeSafe/Jev.
- TypeSafe-API-Key kann direkt in der lokalen Weboberfläche eingegeben werden.
- Der API-Key wird **nur im RAM** des laufenden Python-Prozesses gespeichert und weder in GitHub noch in der Lern-Datei abgelegt.
- Beide Agenten erhalten eine echte adversariale Vorausschau per Minimax/Alpha-Beta-Suche.
- Einstellbare Suchtiefe: 1 bis 6 Halbzüge.
- Beide Agenten bekommen zu jedem legalen Zug:
  - Lookahead-Score
  - Principal Variation
  - Schlag-/Promotionsinformationen
  - gegnerische Antwortmöglichkeiten
  - persistente Lernwerte aus früheren Partien
- Self-Play-Lernen wird lokal in `arena_learning.json` gespeichert.
- Seiten können zwischen Partien getauscht werden.
- Live-Anzeige von Zug, Confidence, Lookahead, Lernbonus und Partiestatistik.

## Start unter Windows

```
start.bat
```

Beim Start werden die Abhängigkeiten installiert bzw. aktualisiert. Danach:

```
http://127.0.0.1:8000
```

## TypeSafe API-Key

Du kannst den Key direkt in der Oberfläche unter **TypeSafe API** einfügen.

Alternativ kannst du ihn vor dem Start als Umgebungsvariable setzen:

PowerShell:

```powershell
$env:TYPESAFE_API_KEY="dein-key"
```

Optional kann ein bestimmtes TypeSafe-Modell gesetzt werden:

```powershell
$env:TYPESAFE_MODEL="jev-latest"
```

Wenn `TYPESAFE_MODEL` nicht gesetzt ist, verwendet das offizielle SDK den serverseitigen Standard.

## Laya

Standardmäßig wird lokal geladen:

```
convaiinnovations/laya-multilingual
```

Anderes Modell:

```powershell
$env:LAYA_MODEL="convaiinnovations/laya"
```

## Vorausschau

Die Modelle müssen den Spielbaum nicht selbst simulieren. Vor jedem Agentenzug analysiert `strategy.py` alle legalen Kandidaten adversarial:

```
aktuelle Stellung
   ↓
alle legalen Züge
   ↓
Minimax/Alpha-Beta bis Tiefe N
   ↓
Lookahead-Score + Principal Variation
   ↓
Lernwert aus früheren Partien
   ↓
Laya ODER TypeSafe/Jev wählt per Choice
```

Damit sind die Entscheidungen weiterhin echte Laya-/TypeSafe-`choice`-Entscheidungen, aber beide Modelle erhalten vorher berechnete Informationen darüber, was mehrere Züge in die Zukunft passieren kann.

## Was „selbst lernen“ hier bedeutet

Das Projekt verändert **nicht automatisch die Gewichte von Jev oder Laya** nach jeder Partie.

Stattdessen besitzt jeder Agent eine eigene persistente Experience-Memory:

- exakte State/Move-Q-Werte für wiederkehrende Stellungen
- Besuche pro Zug
- globale gelernte Feature-Gewichte
- Siege, Niederlagen und Remis
- Online-Update nach jeder abgeschlossenen Partie

Diese Lernwerte werden bei späteren Entscheidungen wieder an den jeweiligen Agenten übergeben. Dadurch kann sich das Verhalten über Self-Play-Partien verändern, ohne Jev selbst neu zu trainieren.

Für echtes Weight-Fine-Tuning von Laya wäre ein separater Trainingslauf nötig. Jev ist ein gehostetes TypeSafe-Modell; dieses Projekt kann dessen Gewichte nicht lokal verändern.

## Dateien

- `engine.py` – Dame-Regeln
- `strategy.py` – Minimax/Alpha-Beta-Vorausschau
- `learning.py` – persistentes Self-Play-Lernen
- `arena_agents.py` – Laya- und TypeSafe/Jev-Anbindung
- `app.py` – FastAPI + Arena-Weboberfläche
- `arena_learning.json` – lokale Lerndaten, wird nicht committed

## Sicherheit

Den TypeSafe-API-Key niemals direkt in Python-Dateien oder GitHub committen. Die Weboberfläche sendet ihn nur an deinen lokalen FastAPI-Prozess auf `127.0.0.1`.

## Separate Spider project

The Windows Spider Solitaire agent now lives in the standalone `SpiderBot/` folder and has its own `README.md`, `requirements.txt`, `start.bat`, and `.venv`.
