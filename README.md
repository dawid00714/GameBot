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


---

# Windows Spider Agent (Build 3.0)

Zusätzlich zur Dame-Arena enthält das Repository jetzt einen Agenten für das echte Windows-Spiel **Solitaire & Casual Games / Spider**.

## Start

```
start_spider.bat
```

Danach öffnet sich:

```
http://127.0.0.1:8010
```

## Bedienung

1. Spider Solitaire in Windows öffnen und sichtbar lassen.
2. Im Browser unter **Windows-Spielfenster** das Fenster `Solitaire & Casual Games` auswählen.
3. **Diagnose / Karten lesen** drücken.
4. Prüfen, ob im Bereich `Erkannter Spielzustand` die sichtbaren Karten erkannt werden.
5. Im Live-Screenshot einmal direkt auf den Stapel **Neue Karten** klicken. Damit wird die Stock-Position exakt kalibriert. Als Ausgangswert ist die Position aus dem bereitgestellten Screenshot hinterlegt.
6. Unter **Modell** entweder `Laya lokal` oder `TypeSafe / Jev` auswählen.
7. Für TypeSafe den API-Key im Feld einfügen und **API verbinden** drücken.
8. Mit **1 Aktion** zuerst einen einzelnen Zug testen. Danach mit **Start** automatisch spielen lassen.

## Virtuelle Maus

Der Agent benutzt standardmäßig Windows-`WM_MOUSE...`-Nachrichten direkt an das ausgewählte Spiel-Fenster:

- Linke Maustaste wird auf der Quellkarte gedrückt.
- Die Taste bleibt während der Bewegung gedrückt.
- Die Karte wird zum Ziel gezogen.
- Am Ziel wird die linke Maustaste losgelassen.
- Der physische Mauszeiger des Benutzers wird dabei nicht bewegt.

Ein Stock-Deal ist ein virtueller Linksklick auf den kalibrierten Punkt.

Einige Spiele blockieren absichtlich Hintergrund-Mausnachrichten. Das Tool erkennt einen solchen Fall daran, dass sich das Spielbild nach dem Drag nicht relevant ändert, sperrt die fehlgeschlagene Aktion für diese Stellung und zeigt eine Warnung an. Es gibt absichtlich keinen stillen Fallback, der den echten Mauszeiger übernimmt.

## Bilderkennung

Die Zustandslesung arbeitet in zwei Stufen:

1. **Windows UI Automation (UIA)**: bevorzugt. Falls Microsoft Solitaire Karten als Accessibility-Elemente veröffentlicht, werden Rang, Position und Spalte direkt gelesen.
2. **RapidOCR + OpenCV**: Fallback. Sichtbare Ränge werden aus dem Fenster-Screenshot erkannt und auf die zehn Spider-Spalten gruppiert.

Für den gezeigten **1-Suit-Spider** wird ein erkannter Rang ohne explizite Farbangabe als Pik behandelt.

## Vorausschau

Das Modell bekommt nicht einfach rohe Pixel und erfindet eine Mausbewegung. Die Pipeline ist:

```
Windows-Spiel
   ↓
Screenshot / UIA
   ↓
erkannte 10 Spalten + sichtbare Karten
   ↓
legale Spider-Züge erzeugen
   ↓
Lookahead-Suche über sichtbare, deterministische Züge
   ↓
persistente Erfahrungen aus früheren Partien
   ↓
Laya ODER TypeSafe/Jev wählt eine legale Aktion
   ↓
virtueller Drag / Stock-Klick
   ↓
neuen echten Bildschirmzustand beobachten
```

Die Vorausschau lässt sich auf 1 bis 5 Schritte einstellen. Verdeckte Karten sind unbekannt; ein Simulationszweig kann daher nicht so tun, als wüsste er die darunterliegende Karte. Das Aufdecken einer unbekannten Karte wird positiv bewertet, anschließend wird wieder der echte Windows-Bildschirm gelesen.

## Selbstlernen

`spider_learning.json` speichert getrennt für Laya und TypeSafe/Jev:

- State/Action-Q-Werte
- Anzahl Besuche bekannter Aktionen
- globale Feature-Gewichte
- Siege / Niederlagen
- Anzahl ausgeführter Züge

Diese Datei wird nicht nach GitHub committed.

Das Online-Lernen verändert nicht direkt die Gewichte von Jev oder Laya. Es bildet eine persistente Erfahrungsschicht um das jeweilige Entscheidungsmodell. Ein echtes Laya-Fine-Tuning kann später auf den gesammelten Partiedaten aufgebaut werden.

## Wichtige Dateien

- `spider_windows.py` – Windows-Fensteraufnahme und virtuelle Maus
- `spider_state.py` – UIA/OCR-Erkennung des Spider-Bretts
- `spider_solver.py` – legale Züge, Heuristiken und Lookahead
- `spider_models.py` – Laya / TypeSafe-Jev
- `spider_learning.py` – persistentes Lernen
- `spider_agent.py` – Observe → Decide → Act → Verify Schleife
- `spider_app.py` – Web-Steuerung
- `start_spider.bat` – Windows-Starter
