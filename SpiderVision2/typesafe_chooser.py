from __future__ import annotations

import os
import threading
from typing import Any

from solver import Move
from vision import BoardObservation


TYPESAFE_MODEL = os.getenv("TYPESAFE_MODEL") or None

_lock = threading.Lock()
_key = os.getenv("TYPESAFE_API_KEY", "").strip() or None
_validated = False
_validation_error: str | None = None
_available_models: list[str] = []


class TypeSafeError(RuntimeError):
    pass


def _normalize_key(key: str | None) -> str | None:
    value = (key or "").strip()
    if not value:
        return None
    if value.lower().startswith("bearer "):
        value = value[7:].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1].strip()

    try:
        value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise TypeSafeError(
            "Der TypeSafe API-Key enthält ein nicht-ASCII-Zeichen. "
            "Bitte nur den reinen API-Key einfügen."
        ) from exc

    if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        raise TypeSafeError(
            "Der TypeSafe API-Key enthält Leer-/Steuerzeichen. "
            "Bitte nur den reinen API-Key einfügen."
        )
    return value


def set_key(key: str | None) -> None:
    global _key, _validated, _validation_error, _available_models
    value = _normalize_key(key)
    with _lock:
        _key = value
        _validated = False
        _validation_error = None
        _available_models = []


def status() -> dict[str, Any]:
    with _lock:
        return {
            "configured": bool(_key),
            "validated": bool(_validated),
            "validation_error": _validation_error,
            "key_length": len(_key) if _key else 0,
            "available_models": list(_available_models),
            "model": TYPESAFE_MODEL or "server default (Jev)",
        }


def validate() -> dict[str, Any]:
    global _validated, _validation_error, _available_models

    with _lock:
        key = _key
    if not key:
        raise TypeSafeError("TypeSafe API-Key fehlt.")

    try:
        from typesafe_sdk import TypeSafeClient
    except Exception as exc:
        raise TypeSafeError(f"typesafe-sdk fehlt: {exc}") from exc

    kwargs: dict[str, Any] = {"api_key": key, "timeout": 20.0}
    if TYPESAFE_MODEL:
        kwargs["model"] = TYPESAFE_MODEL

    try:
        with TypeSafeClient(**kwargs) as client:
            response = client.models.list()

        names: list[str] = []
        try:
            for item in response:
                name = getattr(item, "name", None) or getattr(item, "model", None)
                if name:
                    names.append(str(name))
        except Exception:
            pass

        with _lock:
            _validated = True
            _validation_error = None
            _available_models = names

    except Exception as exc:
        with _lock:
            _validated = False
            _validation_error = f"{type(exc).__name__}: {exc}"
        raise TypeSafeError(
            f"TypeSafe-Verbindungstest fehlgeschlagen: {type(exc).__name__}: {exc}"
        ) from exc

    return status()


def _state_payload(board: BoardObservation, moves: list[Move]) -> dict[str, Any]:
    return {
        "game": "Microsoft Spider Solitaire",
        "variant": "one-suit spades",
        "goal": "Win by completing and removing descending K-to-A sequences.",
        "vision": {
            "complete": board.complete,
            "uncertain_cards": board.uncertain_cards,
            "reader": "SpiderVision2 screenshot/OpenCV/template classifier",
        },
        "columns": [
            {
                "column": col.index + 1,
                "hidden_cards_above_visible_run": bool(col.hidden_above),
                "empty": bool(col.empty_confident and not col.cards),
                "visible_cards_top_to_bottom": [card.rank for card in col.cards],
            }
            for col in board.columns
        ],
        "candidate_count": len(moves),
        "important": (
            "Every supplied option has already passed deterministic Spider rules. "
            "Choose exactly one option; never invent a move."
        ),
    }


def _criteria(moves: list[Move]) -> dict[str, str]:
    criteria: dict[str, str] = {}
    for i, move in enumerate(moves):
        option_id = f"m{i}"
        criteria[option_id] = (
            f"{move.notation()}; heuristic_score={move.score:.2f}; "
            f"reason={move.reason}; moving={move.moving}"
        )
    return criteria


def choose(board: BoardObservation, moves: list[Move]) -> tuple[Move, dict[str, Any]]:
    if not board.complete:
        raise TypeSafeError("TypeSafe darf kein unvollständig erkanntes Brett bewerten.")
    if not moves:
        raise TypeSafeError("Es gibt keine legalen Tableau-Züge.")

    with _lock:
        key = _key
        validated = _validated

    if not key:
        raise TypeSafeError("TypeSafe API-Key fehlt.")
    if not validated:
        raise TypeSafeError(
            "TypeSafe/Jev ist noch nicht verbunden. Bitte zuerst 'API verbinden' drücken."
        )

    try:
        from typesafe_sdk import Choice, TypeSafeClient
    except Exception as exc:
        raise TypeSafeError(f"typesafe-sdk fehlt: {exc}") from exc

    criteria = _criteria(moves)
    question = Choice(
        instructions=(
            "Choose the legal move most likely to improve the chance of eventually "
            "winning one-suit Spider Solitaire. Prefer exposing hidden cards, "
            "building longer descending runs, preserving mobility, and avoiding "
            "wasteful use of empty columns. Return exactly one supplied option."
        ),
        criteria=criteria,
    )

    kwargs: dict[str, Any] = {"api_key": key, "timeout": 45.0}
    if TYPESAFE_MODEL:
        kwargs["model"] = TYPESAFE_MODEL

    try:
        with TypeSafeClient(**kwargs) as client:
            response = client.system_one(
                state=_state_payload(board, moves),
                questions={"move": question},
            )
        answer = response.choices["move"]
        selected = str(answer.choice)
        probabilities = getattr(answer, "probabilities", {})
        if hasattr(probabilities, "model_dump"):
            probabilities = probabilities.model_dump()
        confidence = getattr(answer, "confidence", None)
    except Exception as exc:
        raise TypeSafeError(
            f"TypeSafe/Jev API-Fehler: {type(exc).__name__}: {exc}"
        ) from exc

    by_id = {f"m{i}": move for i, move in enumerate(moves)}
    if selected not in by_id:
        raise TypeSafeError(f"TypeSafe/Jev wählte unbekannte Option {selected!r}.")

    return by_id[selected], {
        "model": "TypeSafe/Jev",
        "checkpoint": TYPESAFE_MODEL or "server default (Jev)",
        "selected": selected,
        "confidence": confidence,
        "probabilities": probabilities,
        "criteria": criteria,
    }
