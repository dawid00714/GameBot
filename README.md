# Laya Checkers

Ein lokales 8×8-Dame-Spiel, bei dem **Laya** die Züge der KI auswählt.

## Architektur

- **Regel-Engine**: erzeugt ausschließlich legale Züge, Pflichtschläge, Mehrfachschläge und Damenumwandlung.
- **Laya**: bewertet die aktuell legalen Züge als strukturierte `choice`-Entscheidung und wählt den KI-Zug.
- **Fallback**: falls Laya noch nicht geladen werden kann oder einen ungültigen Wert liefert, wird ein deterministischer taktischer Ersatz-Zug benutzt.
- **Web-UI**: FastAPI + Browser-Oberfläche auf einem 8×8-Brett.
- **Laya-Debugpanel**: zeigt gewählten Zug, Confidence und Wahrscheinlichkeiten.

Das Spiel verwendet eine bewusst kompakte 8×8-Regelvariante: normale Steine ziehen und schlagen diagonal vorwärts, Damen in beide Richtungen; Schlagen ist Pflicht; Mehrfachschläge sind möglich.

## Start unter Windows

Doppelklick auf:

```
start.bat
```

Beim ersten Start werden Python-Pakete installiert. Laya lädt beim ersten KI-Zug außerdem das Modell von Hugging Face herunter.

Danach im Browser öffnen:

```
http://127.0.0.1:8000
```

## Manueller Start

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app:app --host 127.0.0.1 --port 8000
```

Linux/macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --host 127.0.0.1 --port 8000
```

## Modell wechseln

Standard:

```
convaiinnovations/laya
```

Alternativ vor dem Start:

Windows PowerShell:

```powershell
$env:LAYA_MODEL="convaiinnovations/laya-multilingual"
```

Linux/macOS:

```bash
export LAYA_MODEL="convaiinnovations/laya-multilingual"
```

## Was Laya tatsächlich macht

Laya bekommt nicht einfach das Brett und darf irgendeinen Text erzeugen. Die Engine erzeugt zuerst alle legalen Züge. Danach bekommt Laya eine `choice`-Frage, deren Optionen genau diese Züge sind. Zu jedem Zug werden taktische Merkmale mitgegeben, z. B. Schlaganzahl, Umwandlung, Materiallage und mögliche unmittelbare Gegenschläge.

Dadurch gilt:

```
Brett -> legale Züge -> Laya Choice -> ausgewählter legaler Zug
```

Laya kann deshalb keinen illegalen Zug spielen.

## API

- `GET /api/state` – aktueller Spielzustand
- `POST /api/new` – neues Spiel
- `POST /api/move` – menschlichen Zug ausführen, danach antwortet Laya
- `GET /health` – Healthcheck

## Hinweise

Das Basismodell ist nicht speziell auf Dame trainiert. Das Projekt ist daher vor allem ein funktionierender Test, um Laya als schnelle Decision Engine in einem Spiel einzusetzen. Für deutlich stärkeres Spiel wäre der nächste Schritt Fine-Tuning auf Dame-Positionen oder die Kombination mit tieferer Suche.

Laya-Projekt: https://github.com/NandhaKishorM/laya
