from __future__ import annotations

import os
import threading
import time
import traceback
from typing import Any

import engine

# The multilingual checkpoint is smaller/faster than the English large model and
# is a better default for a local Windows demo. Override with LAYA_MODEL if wanted.
_MODEL_NAME = os.getenv("LAYA_MODEL", "convaiinnovations/laya-multilingual")
_LOAD_WAIT_SECONDS = float(os.getenv("LAYA_LOAD_WAIT_SECONDS", "2.5"))

_agent = None
_load_error: str | None = None
_load_started_at: float | None = None
_load_finished_at: float | None = None
_loading = False
_load_lock = threading.Lock()
_load_event = threading.Event()


def _load_worker():
    global _agent, _load_error, _loading, _load_finished_at
    try:
        import laya
        agent = laya.load(_MODEL_NAME)
        with _load_lock:
            _agent = agent
            _load_error = None
    except Exception as exc:
        with _load_lock:
            _load_error = f"{type(exc).__name__}: {exc}"
    finally:
        with _load_lock:
            _loading = False
            _load_finished_at = time.time()
        _load_event.set()


def start_loading() -> None:
    """Start loading Laya in a daemon thread exactly once."""
    global _loading, _load_started_at
    with _load_lock:
        if _agent is not None or _loading or _load_error is not None:
            return
        _loading = True
        _load_started_at = time.time()
        _load_event.clear()
        thread = threading.Thread(target=_load_worker, name="laya-loader", daemon=True)
        thread.start()


def model_status() -> dict[str, Any]:
    with _load_lock:
        if _agent is not None:
            status = "ready"
        elif _load_error is not None:
            status = "error"
        elif _loading:
            status = "loading"
        else:
            status = "not_started"

        elapsed = None
        if _load_started_at is not None:
            end = _load_finished_at if _load_finished_at is not None else time.time()
            elapsed = round(max(0.0, end - _load_started_at), 1)

        return {
            "status": status,
            "model": _MODEL_NAME,
            "error": _load_error,
            "elapsed_seconds": elapsed,
        }


def _candidate_features(board: list[list[str]], side: str, move: engine.Move) -> dict[str, Any]:
    after = engine.apply_move(board, move)
    enemy = engine.opponent(side)
    replies = engine.legal_moves(after, enemy)
    max_reply_captures = max((len(m.captures) for m in replies), default=0)
    own_next_mobility = engine.mobility(after, side)

    er, ec = move.end
    center_distance = abs(3.5 - er) + abs(3.5 - ec)
    center_value = round(7 - center_distance, 2)

    return {
        "notation": engine.move_notation(move),
        "captures": len(move.captures),
        "promotes": move.promotes,
        "material_after": round(engine.material(after, side), 2),
        "opponent_legal_replies": len(replies),
        "opponent_max_capture_next": max_reply_captures,
        "own_future_mobility": own_next_mobility,
        "center_value": center_value,
    }


def _fallback_score(features: dict[str, Any]) -> float:
    return (
        features["captures"] * 100.0
        + (60.0 if features["promotes"] else 0.0)
        + features["material_after"] * 12.0
        - features["opponent_max_capture_next"] * 28.0
        + features["own_future_mobility"] * 1.5
        + features["center_value"] * 0.5
    )


def _fallback_move(
    moves: list[engine.Move],
    features: dict[str, dict[str, Any]],
    reason: str,
    source: str = "fallback_loading",
) -> tuple[engine.Move, dict]:
    best = max(moves, key=lambda m: _fallback_score(features[m.id]))
    return best, {
        "source": source,
        "model": _MODEL_NAME,
        "selected": best.id,
        "notation": engine.move_notation(best),
        "confidence": None,
        "probabilities": {},
        "features": features,
        "error": reason,
        "laya_status": model_status(),
    }


def choose_move(board: list[list[str]], side: str, moves: list[engine.Move]) -> tuple[engine.Move, dict]:
    if not moves:
        raise ValueError("No legal moves")

    features = {m.id: _candidate_features(board, side, m) for m in moves}

    criteria = {}
    for move in moves:
        f = features[move.id]
        criteria[move.id] = (
            f"{f['notation']}; captures={f['captures']}; promotes={f['promotes']}; "
            f"material_after={f['material_after']}; opponent_replies={f['opponent_legal_replies']}; "
            f"opponent_max_capture_next={f['opponent_max_capture_next']}; "
            f"own_future_mobility={f['own_future_mobility']}; center_value={f['center_value']}"
        )

    state = {
        "game": "8x8 checkers / draughts",
        "side_to_play": side,
        "board": engine.board_ascii(board),
        "piece_legend": {
            "r": "red man",
            "R": "red king",
            "b": "black man",
            "B": "black king",
            ".": "empty",
        },
        "legal_moves": criteria,
        "goal": "Win the game by capturing or immobilizing all opposing pieces.",
    }

    questions = {
        "move": {
            "type": "choice",
            "instructions": (
                f"Choose the strongest legal move for the {side.upper()} side. "
                "Only choose one supplied option. Prefer forced captures, promotion, material gain, "
                "king safety, avoiding immediate recapture, useful mobility and central control. "
                "Do not invent a move."
            ),
            "criteria": criteria,
        }
    }

    # Never freeze the game just because the first model download/load is slow.
    start_loading()
    if _agent is None:
        _load_event.wait(timeout=_LOAD_WAIT_SECONDS)

    with _load_lock:
        agent = _agent
        load_error = _load_error
        still_loading = _loading

    if agent is None:
        if load_error:
            return _fallback_move(
                moves,
                features,
                f"Laya konnte nicht geladen werden: {load_error}",
                source="fallback_error",
            )
        return _fallback_move(
            moves,
            features,
            "Laya wird noch im Hintergrund geladen. Dieser Zug nutzt vorübergehend den Fallback.",
            source="fallback_loading" if still_loading else "fallback",
        )

    try:
        result = agent.predict(state, questions)
        answer = result["answers"]["move"]
        selected = answer.get("choice")
        by_id = {m.id: m for m in moves}
        if selected not in by_id:
            raise RuntimeError(f"Laya returned unknown move: {selected!r}")

        debug = {
            "source": "laya",
            "model": _MODEL_NAME,
            "selected": selected,
            "notation": engine.move_notation(by_id[selected]),
            "confidence": answer.get("confidence"),
            "probabilities": answer.get("probabilities", {}),
            "features": features,
            "laya_status": model_status(),
        }
        return by_id[selected], debug

    except Exception as exc:
        best, debug = _fallback_move(
            moves,
            features,
            f"{type(exc).__name__}: {exc}",
            source="fallback_error",
        )
        debug["trace"] = traceback.format_exc(limit=2)
        return best, debug
