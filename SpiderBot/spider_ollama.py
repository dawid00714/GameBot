from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.request
from typing import Any

import cv2
import numpy as np

from spider_state import RANK_VALUE, SpiderState, VisibleCard

OLLAMA_URL = "http://127.0.0.1:11434"
_KNOWN_VISION_HINTS = (
    "vision",
    "llava",
    "bakllava",
    "minicpm-v",
    "moondream",
    "qwen2-vl",
    "qwen2.5-vl",
    "qwen3-vl",
    "vl:",
    "-vl",
    "gemma3",
    "gemma-3",
    "lfm2.5-vl",
)


class OllamaVisionError(RuntimeError):
    pass


def _request(path: str, payload: dict[str, Any] | None = None, timeout: float = 8.0) -> dict[str, Any]:
    url = OLLAMA_URL.rstrip("/") + path
    if payload is None:
        req = urllib.request.Request(url, method="GET")
    else:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise OllamaVisionError(
            "Ollama ist unter http://127.0.0.1:11434 nicht erreichbar. "
            "Bitte die Ollama-App starten."
        ) from exc
    except Exception as exc:
        raise OllamaVisionError(f"Ollama-Fehler: {type(exc).__name__}: {exc}") from exc


def _looks_like_vision(name: str) -> bool:
    n = name.lower()
    return any(hint in n for hint in _KNOWN_VISION_HINTS)


def list_vision_models() -> dict[str, Any]:
    tags = _request("/api/tags", timeout=5.0)
    raw_models = tags.get("models") or []
    models: list[dict[str, Any]] = []

    for item in raw_models:
        name = str(item.get("name") or item.get("model") or "").strip()
        if not name:
            continue

        capabilities: list[str] = []
        details: dict[str, Any] = {}
        show_error = None
        try:
            shown = _request("/api/show", {"model": name}, timeout=6.0)
            capabilities = [str(x).lower() for x in (shown.get("capabilities") or [])]
            details = shown.get("details") or {}
        except Exception as exc:
            show_error = str(exc)

        vision = "vision" in capabilities or _looks_like_vision(name)
        if vision:
            models.append(
                {
                    "name": name,
                    "vision": True,
                    "capabilities": capabilities,
                    "family": details.get("family"),
                    "parameter_size": details.get("parameter_size"),
                    "quantization_level": details.get("quantization_level"),
                    "show_error": show_error,
                }
            )

    return {
        "ok": True,
        "url": OLLAMA_URL,
        "vision_models": models,
        "count": len(models),
    }


def _frame_to_base64(frame: np.ndarray) -> str:
    # A 1280-wide JPEG is enough for card ranks and keeps local VLM latency sane.
    h, w = frame.shape[:2]
    if w > 1280:
        scale = 1280.0 / float(w)
        frame = cv2.resize(
            frame,
            (1280, max(1, int(round(h * scale)))),
            interpolation=cv2.INTER_AREA,
        )
    ok, encoded = cv2.imencode(
        ".jpg",
        frame,
        [int(cv2.IMWRITE_JPEG_QUALITY), 88],
    )
    if not ok:
        raise OllamaVisionError("Screenshot konnte nicht für Ollama kodiert werden.")
    return base64.b64encode(encoded.tobytes()).decode("ascii")


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass

    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        raise OllamaVisionError("Das Vision-Modell hat kein JSON zurückgegeben.")
    try:
        return json.loads(match.group(0))
    except Exception as exc:
        raise OllamaVisionError(
            "Das Vision-Modell hat ungültiges JSON zurückgegeben."
        ) from exc


def analyze_spider(frame: np.ndarray, model: str) -> dict[str, Any]:
    model = (model or "").strip()
    if not model:
        raise OllamaVisionError("Kein Ollama-Vision-Modell ausgewählt.")

    image = _frame_to_base64(frame)
    prompt = """
Du siehst einen Screenshot von Microsoft Spider Solitaire mit genau 10 Tableau-Spalten.
Analysiere NUR die Karten im Spielfeld und den violetten Nachziehstapel.

Gib ausschließlich JSON in diesem Schema zurück:
{
  "confidence": 0.0,
  "stock_visible": true,
  "columns": [
    {
      "column": 1,
      "hidden": true,
      "cards": [
        {"rank": "K", "y": 0.31}
      ]
    }
  ],
  "notes": ""
}

Regeln:
- Es müssen genau die Spalten 1 bis 10 vorhanden sein, von links nach rechts.
- cards enthält nur offen sichtbare Karten einer Spalte, von oben nach unten.
- rank ist exakt A,2,3,4,5,6,7,8,9,10,J,Q oder K.
- y ist die vertikale Position des Karten-ZENTRUMS relativ zur Bildhöhe von 0 bis 1.
- hidden=true, wenn oberhalb der offenen Karten noch violette verdeckte Karten liegen.
- stock_visible=true, wenn unten/rechts noch der violette Nachziehstapel sichtbar ist.
- Dieses Spiel ist im gezeigten Modus 1-Suit Spider; die Farbe ist daher für die Zuglogik nicht nötig.
- Menüs, Punktzahl, Uhrzeit und Buttons NICHT als Karten interpretieren.
- Erfinde keine verdeckten Kartenwerte.
""".strip()

    payload = {
        "model": model,
        "stream": False,
        "format": "json",
        "messages": [
            {
                "role": "user",
                "content": prompt,
                "images": [image],
            }
        ],
        "options": {
            "temperature": 0,
        },
    }

    data = _request("/api/chat", payload, timeout=90.0)
    message = data.get("message") or {}
    content = str(message.get("content") or "")
    parsed = _extract_json(content)
    parsed["_model"] = model
    return parsed


def _valid_model_columns(hint: dict[str, Any]) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    cols = hint.get("columns")
    if not isinstance(cols, list):
        return result

    for raw in cols:
        if not isinstance(raw, dict):
            continue
        try:
            idx = int(raw.get("column"))
        except Exception:
            continue
        if not 1 <= idx <= 10:
            continue

        cards: list[dict[str, Any]] = []
        for card in raw.get("cards") or []:
            if not isinstance(card, dict):
                continue
            rank = str(card.get("rank") or "").upper().strip()
            if rank not in RANK_VALUE:
                continue
            try:
                y = float(card.get("y"))
            except Exception:
                continue
            if not 0.05 <= y <= 0.90:
                continue
            cards.append({"rank": rank, "y": y})

        result[idx] = {
            "hidden": bool(raw.get("hidden", False)),
            "cards": cards,
        }
    return result


def apply_hint(state: SpiderState, hint: dict[str, Any]) -> dict[str, Any]:
    """Conservatively fuse local VLM output into UIA/OCR state.

    UIA remains authoritative when it provides a complete column. The vision
    model fills missing/partial visual state and can recover the stock flag.
    """
    model = str(hint.get("_model") or "ollama")
    try:
        confidence = float(hint.get("confidence", 0.0))
    except Exception:
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    cols = _valid_model_columns(hint)
    applied_columns: list[int] = []
    disagreements: list[str] = []

    for idx1, model_col in cols.items():
        col = state.columns[idx1 - 1]
        model_cards = model_col["cards"]

        if col.cards and model_cards:
            current_ranks = [c.rank for c in col.cards]
            model_ranks = [c["rank"] for c in model_cards]
            if current_ranks != model_ranks:
                disagreements.append(
                    f"C{idx1}: reader={current_ranks}, ollama={model_ranks}"
                )

        # Strong UIA data wins. Ollama is a helper, not permission to overwrite
        # reliable accessibility information with a hallucination.
        strong_uia = bool(col.cards) and all(c.source == "uia" for c in col.cards)
        if strong_uia:
            if model_col["hidden"]:
                col.hidden_above = True
            continue

        # For OCR/partial/missing columns, a reasonably confident local VLM may
        # replace the visible-card sequence while preserving the stable X center.
        if confidence >= 0.70 and model_cards:
            rebuilt: list[VisibleCard] = []
            for card in model_cards:
                rebuilt.append(
                    VisibleCard(
                        rank=card["rank"],
                        suit="S",
                        x=col.x,
                        y=float(card["y"]) * state.height,
                        width=max(24.0, state.width * 0.045),
                        height=max(40.0, state.height * 0.11),
                        source=f"ollama:{model}",
                        confidence=confidence,
                    )
                )
            col.cards = rebuilt
            col.hidden_above = bool(model_col["hidden"])
            applied_columns.append(idx1)

    if bool(hint.get("stock_visible")):
        state.stock_available = True

    if applied_columns:
        state.reader = state.reader + "+ollama"

    state.diagnostics.append(
        f"Ollama Vision {model}: confidence={confidence:.2f}, "
        f"angewendet={applied_columns or 'nur Prüfung'}"
    )
    if disagreements:
        state.diagnostics.append(
            "Ollama-Abweichungen: " + " | ".join(disagreements[:5])
        )

    return {
        "model": model,
        "confidence": confidence,
        "stock_visible": bool(hint.get("stock_visible")),
        "applied_columns": applied_columns,
        "disagreements": disagreements,
        "notes": str(hint.get("notes") or ""),
        "raw": hint,
    }
