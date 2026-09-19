# SpiderBot

Eigenständiger Windows-Agent für **Microsoft Solitaire & Casual Games / Spider Solitaire**.

Dieser Ordner ist absichtlich vollständig vom Dame-/Checkers-Projekt im Repository-Stamm getrennt.

## Start

Doppelklick:

```
start.bat
```

SpiderBot erstellt seine **eigene** virtuelle Umgebung:

```
SpiderBot/.venv/
```

und installiert ausschließlich die Pakete aus:

```
SpiderBot/requirements.txt
```

Danach öffnet sich:

```
http://127.0.0.1:8010
```

## Modelle

In der Oberfläche kann gewählt werden:

- Laya lokal
- TypeSafe / Jev

Der TypeSafe API-Key wird nur im RAM des laufenden Prozesses gehalten.

## Windows-Steuerung

Der Agent wählt ein echtes Windows-Spiel-Fenster aus und benutzt eine eigene Hintergrund-Maus über Windows-Messages:

1. linke Maustaste auf der Quellkarte drücken,
2. gedrückt halten,
3. zur Zielspalte bewegen,
4. linke Maustaste loslassen.

Der Kartenstapel unten rechts wird automatisch im Screenshot erkannt.

SpiderBot ist strikt **background-only**: Er verwendet weder `SetCursorPos`, `SendInput`, `mouse_event` noch `SetForegroundWindow`. Wenn Microsoft Solitaire Hintergrundnachrichten ablehnt, meldet der Agent den Fehler, statt die echte Maus zu übernehmen.

## Python 3.13

SpiderBot verwendet `rapidocr` statt des alten Pakets `rapidocr-onnxruntime`.
Das alte Paket unterstützt Python 3.13 nicht. Der neue Stack ist für Python 3.13 ausgelegt und verwendet eine passende ONNX-Runtime-Version.

## Dateien

- `spider_app.py` – lokale Weboberfläche
- `spider_agent.py` – Observe → Decide → Act → Verify
- `spider_windows.py` – Windows-Fenster + virtuelle Maus
- `spider_state.py` – UIA/OCR-Kartenerkennung
- `spider_solver.py` – legale Züge + Vorausschau
- `spider_models.py` – Laya / TypeSafe-Jev
- `spider_learning.py` – persistentes Lernen
- `requirements.txt` – nur SpiderBot-Abhängigkeiten
- `start.bat` – eigenständiger Starter

## Ollama Vision

Optional kann SpiderBot ein lokal laufendes Ollama-Vision-Modell als zweite Bildanalyse verwenden.

Die Oberfläche fragt `http://127.0.0.1:11434/api/tags` und `/api/show` ab und zeigt nur Modelle an, die Vision unterstützen oder anhand ihres Modellnamens eindeutig als Vision-Modell erkannt werden.

Geeignete Beispiele:

- `qwen3.5:4b`
- `qwen3.5:0.8b`
- `gemma3:4b`
- `LFM2.5-VL-1.6B`-Varianten

Ablauf:

```
Screenshot
  ├─ UI Automation / OCR
  └─ optional Ollama Vision
            ↓
      konservative Fusion
            ↓
      legaler Spider-Zustand
            ↓
      Laya oder TypeSafe/Jev entscheidet
```

UIA bleibt bei vollständigen Accessibility-Daten die primäre Quelle. Ollama soll fehlende/unsichere Bildinformationen ergänzen, nicht zuverlässige Kartendaten blind überschreiben.


## TypeSafe-Verbindungstest

Ab Build 3.5 gilt **API bereit** erst nach einem echten Test gegen `GET /v1/models`.
Der API-Key wird beim Einfügen normalisiert (z. B. ein versehentlich mitkopiertes `Bearer ` oder äußere Anführungszeichen) und auf ungültige Nicht-ASCII-/Steuerzeichen geprüft. So schlägt die Partie nicht erst beim ersten Zug mit einem unklaren `UnicodeEncodeError` fehl.


## Laya wird nur bei Bedarf geladen

Ab Build 3.6 wird Laya **nicht mehr beim Start von SpiderBot automatisch geladen**. Das lokale Laya-Modell wird erst geladen, wenn in der Oberfläche tatsächlich `Laya lokal` gewählt wird. Wer TypeSafe/Jev verwendet, wartet daher nicht mehr auf den 322M-Laya-Checkpoint.


## UIA-first Eingabe

Ab Build 3.9 versucht SpiderBot Kartenaktionen zuerst über Windows UI Automation:
`SelectionItem.Select`, `InvokePattern.Invoke` und `LegacyIAccessible.DoDefaultAction`.
Diese Methoden bewegen die echte Maus nicht und holen das Spiel nicht in den Vordergrund.

Erst wenn UIA keine nutzbare Aktion liefert, wird der ältere Hintergrund-`WM_MOUSE`-Pfad versucht. Nach jeder Aktion wird der erkannte Karten-Zustand erneut gelesen; ein bloßer Auswahlrahmen zählt nicht als erfolgreicher Zug.
