# SpiderVision2

Komplett neues Spider-Solitaire-Tool. Es ist absichtlich **nicht** auf dem alten `SpiderBot` aufgebaut.

## Architektur

```
Solitaire-Fenster
  -> direkter Windows-Screenshot
  -> OpenCV erkennt 10 feste Tableau-Spalten
  -> Kartenkoepfe / Rang-Ausschnitte werden aus Pixeln gefunden
  -> Template-Klassifikator erkennt A,2,...,10,J,Q,K
  -> deterministische Spider-Regeln erzeugen legale Zuege
  -> vor Ausfuehrung wird das Brett erneut gelesen
  -> nur bei identischem, vollstaendigem Brett wird die echte Maus bewegt
```

Es verwendet fuer die Kartenerkennung:

- **kein UI Automation (UIA)**
- **kein OCR**
- **kein LLM / Ollama**
- keine Accessibility-Namen der Karten

## Start

```bat
cd SpiderVision2
start.bat
```

Danach: http://127.0.0.1:8020

## Empfohlener erster Test

1. Solitaire & Casual Games offen und sichtbar lassen.
2. Im Tool das Solitaire-Fenster auswaehlen.
3. **Brett analysieren** druecken.
4. Pruefen, ob alle 10 Spalten korrekt angezeigt werden.
5. Falsch erkannte oder mit `?` markierte Karten unten mit dem richtigen Rang anlernen.
6. Erst wenn **BOARD OK** angezeigt wird, **Zug planen** testen.
7. **Sicherer Schritt** zeigt den Zug 1,5 Sekunden als START/ZIEL an und fuehrt ihn danach nur aus, wenn ein frischer Screenshot exakt denselben Brettzustand ergibt.

## Template-Lernen

Gelernte Kartenausschnitte werden lokal unter

```
SpiderVision2/data/templates/<RANG>/
```

gespeichert. Dadurch passt sich der Reader an genau das Microsoft-Solitaire-Theme und die verwendete Fenstergroesse an.

## Sicherheitsregeln

Die Maus bleibt stehen, wenn:

- eine Spalte nicht sicher erkannt wurde,
- ein Rang unsicher ist,
- der geplante Zug nach erneutem Lesen nicht mehr legal ist,
- sich das Brett zwischen Planung und Ausfuehrung geaendert hat.

Der alte `SpiderBot` wird von diesem Tool nicht veraendert oder importiert.
