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

_RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]

_SPIDER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "s": {"type": "boolean"},
        "c": {
            "type": "array",
            "minItems": 10,
            "maxItems": 10,
            "items": {
                "type": "object",
                "properties": {
                    "h": {"type": "boolean"},
                    "r": {
                        "type": "array",
                        "items": {"type": "string", "enum": _RANKS},
                        "maxItems": 13,
                    },
                },
                "required": ["h", "r"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["s", "c"],
    "additionalProperties": False,
}

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
    # Spider has a fixed, high-contrast card UI. 800px width keeps rank glyphs
    # readable while cutting multimodal prompt cost substantially.
    h, w = frame.shape[:2]
    if w > 800:
        scale = 800.0 / float(w)
        frame = cv2.resize(
            frame,
            (800, max(1, int(round(h * scale)))),
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
    text = (text or "").strip()
    if not text:
        raise OllamaVisionError("Das Vision-Modell hat eine leere Antwort geliefert.")

    text = re.sub(r"^\s*```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```\s*$", "", text)

    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass

    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if match:
        try:
            obj = json.loads(match.group(0))
            if isinstance(obj, dict):
                return obj
        except Exception as exc:
            preview = text[:240].replace("\n", " ")
            raise OllamaVisionError(
                f"Vision-JSON war ungültig. Antwortanfang: {preview!r}"
            ) from exc

    preview = text[:240].replace("\n", " ")
    raise OllamaVisionError(
        f"Das Vision-Modell hat kein JSON geliefert. Antwortanfang: {preview!r}"
    )


def _normalize_result(raw: dict[str, Any]) -> dict[str, Any]:
    # Compact helper schema used by Build 4.9:
    # {"s":true,"c":[{"h":true,"r":["A"]}, ... exactly 10 columns]}
    if "c" in raw and isinstance(raw.get("c"), list):
        columns: list[dict[str, Any]] = []
        for idx, col in enumerate((raw.get("c") or [])[:10], start=1):
            if not isinstance(col, dict):
                continue
            ranks = []
            for rank in col.get("r") or []:
                rank = str(rank or "").upper().strip()
                if rank in RANK_VALUE:
                    ranks.append({"rank": rank, "y": None})
            columns.append({
                "column": idx,
                "hidden": bool(col.get("h", False)),
                "cards": ranks,
            })
        return {
            "confidence": 0.85,
            "stock_visible": bool(raw.get("s", False)),
            "columns": columns,
            "notes": "compact-helper",
        }

    # Backward compatibility with older verbose responses.
    return {
        "confidence": raw.get("confidence", 0.8),
        "stock_visible": bool(raw.get("stock_visible", False)),
        "columns": raw.get("columns") or [],
        "notes": raw.get("notes", ""),
    }


def analyze_spider(frame: np.ndarray, model: str) -> dict[str, Any]:
    model = (model or "").strip()
    if not model:
        raise OllamaVisionError("Kein Ollama-Vision-Modell ausgewählt.")

    image = _frame_to_base64(frame)
    prompt = (
        "Microsoft Spider Solitaire, one-suit. Read only the 10 tableau columns "
        "from left to right. For each column return h=true if purple face-down "
        "cards remain above the visible cards, and r as the visible face-up ranks "
        "from top to bottom. Also return s=true if the purple stock pile is visible. "
        "Do not include coordinates, confidence, explanations, score, clock or menus."
    )

    payload = {
        "model": model,
        "prompt": prompt,
        "images": [image],
        "stream": False,
        "format": _SPIDER_SCHEMA,
        "think": False,
        "options": {
            "temperature": 0,
            "num_predict": 220,
            "num_ctx": 2048,
        },
        "keep_alive": "60m",
    }

    data = _request("/api/generate", payload, timeout=75.0)

    candidates = [
        str(data.get("response") or ""),
        str(data.get("thinking") or ""),
    ]
    parsed: dict[str, Any] | None = None
    errors: list[str] = []

    for candidate in candidates:
        if not candidate.strip():
            continue
        try:
            parsed = _extract_json(candidate)
            break
        except Exception as exc:
            errors.append(str(exc))

    if parsed is None:
        detail = "; ".join(errors) if errors else "Antwort war leer"
        raise OllamaVisionError(
            f"Kein verwertbares strukturiertes Ergebnis von {model}. "
            f"done_reason={data.get('done_reason')!r}, "
            f"eval_count={data.get('eval_count')!r}. {detail}"
        )

    result = _normalize_result(parsed)
    result["_model"] = model
    result["_timing"] = {
        "total_ms": round(float(data.get("total_duration") or 0) / 1_000_000, 1),
        "load_ms": round(float(data.get("load_duration") or 0) / 1_000_000, 1),
        "prompt_eval_ms": round(float(data.get("prompt_eval_duration") or 0) / 1_000_000, 1),
        "eval_ms": round(float(data.get("eval_duration") or 0) / 1_000_000, 1),
        "eval_count": data.get("eval_count"),
        "done_reason": data.get("done_reason"),
    }
    return result


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
            raw_y = card.get("y")
            y = None
            if raw_y is not None:
                try:
                    y = float(raw_y)
                except Exception:
                    y = None
                if y is not None and not 0.05 <= y <= 0.90:
                    y = None
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

        # Ollama is now a rank helper, not a geometry source. It only replaces
        # ranks when UIA/OCR already found the same number of physical cards.
        # This avoids invented coordinates and keeps real mouse drags accurate.
        if model_cards and col.cards and len(model_cards) == len(col.cards):
            rebuilt: list[VisibleCard] = []
            for current, card in zip(col.cards, model_cards):
                rebuilt.append(
                    VisibleCard(
                        rank=card["rank"],
                        suit="S",
                        x=current.x,
                        y=current.y,
                        width=current.width,
                        height=current.height,
                        source=f"{current.source}+ollama:{model}",
                        confidence=max(current.confidence, confidence),
                    )
                )
            col.cards = rebuilt
            col.hidden_above = bool(model_col["hidden"]) or col.hidden_above
            applied_columns.append(idx1)

    if bool(hint.get("stock_visible")):
        state.stock_available = True

    if applied_columns:
        state.reader = state.reader + "+ollama"

    timing = hint.get("_timing") or {}
    state.diagnostics.append(
        f"Ollama Vision {model}: confidence={confidence:.2f}, "
        f"angewendet={applied_columns or 'nur Prüfung'}, "
        f"total={timing.get('total_ms', '?')}ms"
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
        "timing": hint.get("_timing") or {},
        "raw": hint,
    }
