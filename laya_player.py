from __future__ import annotations

import os
import traceback
from typing import Any

import engine

_MODEL_NAME = os.getenv("LAYA_MODEL", "convaiinnovations/laya")
_agent = None
_load_error: str | None = None


def _load_agent():
    global _agent, _load_error
    if _agent is not None:
        return _agent
    if _load_error is not None:
        raise RuntimeError(_load_error)

    try:
        import laya
        _agent = laya.load(_MODEL_NAME)
        return _agent
    except Exception as exc:
        _load_error = f"{type(exc).__name__}: {exc}"
        raise


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

    try:
        agent = _load_agent()
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
        }
        return by_id[selected], debug

    except Exception as exc:
        best = max(moves, key=lambda m: _fallback_score(features[m.id]))
        return best, {
            "source": "fallback",
            "model": _MODEL_NAME,
            "selected": best.id,
            "notation": engine.move_notation(best),
            "confidence": None,
            "probabilities": {},
            "features": features,
            "error": f"{type(exc).__name__}: {exc}",
            "trace": traceback.format_exc(limit=2),
        }
