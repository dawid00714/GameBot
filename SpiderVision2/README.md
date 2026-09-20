# SpiderVision2

Komplett neues Spider-Solitaire-Tool. Es ist absichtlich **nicht** auf dem alten `SpiderBot` aufgebaut.

## Architektur

```
Solitaire-Fenster
  -> direkter Windows-Screenshot
  -> OpenCV erkennt 10 feste Tableau-Spalten
  -> Kartenkoepfe / Rang-Ausschnitte werden aus Pixeln gefunden
  -> Template-Klassifikator erkennt A,2,...,10,J,Q,K
  -> deterministische Spider-Regeln erzeugen ausschliesslich legale Zuege
  -> TypeSafe/Jev waehlt zwischen diesen legalen Zuegen
  -> vor Ausfuehrung wird das Brett erneut gelesen
  -> nur bei identischem, vollstaendigem Brett wird die echte Maus bewegt
```

Es verwendet fuer die Kartenerkennung:

- **kein UI Automation (UIA)**
- **kein OCR**
- **kein Vision-LLM / Ollama**
- keine Accessibility-Namen der Karten

**TypeSafe/Jev ist weiterhin Teil des Agenten.** Es sieht nicht den Screenshot, sondern bekommt nur den vollstaendig erkannten Brettzustand und die bereits regelgeprueften legalen Kandidaten. Ohne erfolgreich verbundenen TypeSafe-API-Key wird kein Zug geplant oder ausgefuehrt.

## Start

```bat
cd SpiderVision2
start.bat
```

Danach: http://127.0.0.1:8020

## Empfohlener erster Test

1. Solitaire & Casual Games offen und sichtbar lassen.
2. Im Tool den **TypeSafe API-Key** eintragen und **API verbinden** druecken.
3. Das Solitaire-Fenster auswaehlen.
4. **Brett analysieren** druecken.
5. Pruefen, ob alle 10 Spalten korrekt angezeigt werden.
6. Falsch erkannte oder mit `?` markierte Karten unten mit dem richtigen Rang anlernen.
7. Erst wenn **BOARD OK** angezeigt wird, **Zug planen** testen. TypeSafe/Jev waehlt dann zwischen den legalen Zuegen.
8. **Sicherer Schritt** zeigt den Zug 1,5 Sekunden als START/ZIEL an und fuehrt ihn danach nur aus, wenn ein frischer Screenshot exakt denselben Brettzustand ergibt.

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
