from __future__ import annotations

import os
import threading
import time
from typing import Any

import engine

_LAYA_MODEL = os.getenv("LAYA_MODEL", "convaiinnovations/laya-multilingual")
_TYPESAFE_MODEL = os.getenv("TYPESAFE_MODEL") or None

_laya_agent = None
_laya_error: str | None = None
_laya_loading = False
_laya_started: float | None = None
_laya_lock = threading.Lock()

_typesafe_api_key = os.getenv("TYPESAFE_API_KEY", "").strip() or None
_typesafe_lock = threading.Lock()


class AgentUnavailable(RuntimeError):
    pass


def _load_laya_worker() -> None:
    global _laya_agent, _laya_error, _laya_loading
    try:
        import laya
        agent = laya.load(_LAYA_MODEL)
        with _laya_lock:
            _laya_agent = agent
            _laya_error = None
    except Exception as exc:
        with _laya_lock:
            _laya_error = f"{type(exc).__name__}: {exc}"
    finally:
        with _laya_lock:
            _laya_loading = False


def start_laya_loading() -> None:
    global _laya_loading, _laya_started
    with _laya_lock:
        if _laya_agent is not None or _laya_loading or _laya_error is not None:
            return
        _laya_loading = True
        _laya_started = time.time()
        threading.Thread(target=_load_laya_worker, name="laya-arena-loader", daemon=True).start()


def laya_status() -> dict[str, Any]:
    with _laya_lock:
        if _laya_agent is not None:
            status = "ready"
        elif _laya_error:
            status = "error"
        elif _laya_loading:
            status = "loading"
        else:
            status = "not_started"
        return {
            "status": status,
            "model": _LAYA_MODEL,
            "error": _laya_error,
            "elapsed_seconds": round(time.time() - _laya_started, 1) if _laya_started else 0.0,
        }


def set_typesafe_api_key(api_key: str | None) -> None:
    global _typesafe_api_key
    clean = (api_key or "").strip()
    with _typesafe_lock:
        _typesafe_api_key = clean or None


def typesafe_status() -> dict[str, Any]:
    with _typesafe_lock:
        configured = bool(_typesafe_api_key)
    return {
        "configured": configured,
        "model": _TYPESAFE_MODEL or "server default (Jev)",
    }


def _criteria_text(a: dict[str, Any]) -> str:
    pv = " -> ".join(a.get("principal_variation", [])) or "(none)"
    return (
        f"move={a['notation']}; captures={a['captures']}; promotes={a['promotes']}; "
        f"material_after={a['material_after']}; opponent_replies={a['opponent_replies']}; "
        f"opponent_max_capture_next={a['opponent_max_capture_next']}; "
        f"own_future_mobility={a['own_future_mobility']}; center={a['center_value']}; "
        f"lookahead_score={a['lookahead_score']}; learned_bonus={a.get('learning_bonus', 0.0)}; "
        f"combined_score={a.get('combined_score', a['lookahead_score'])}; "
        f"learned_q={a.get('learned_q', 0.0)} visits={a.get('visits', 0)}; "
        f"principal_variation={pv}"
    )


def _state(board: list[list[str]], side: str, analyses: list[dict[str, Any]], agent_name: str) -> dict[str, Any]:
    return {
        "game": "8x8 checkers / draughts",
        "agent": agent_name,
        "side_to_play": side,
        "board": engine.board_ascii(board),
        "legend": {"r": "red man", "R": "red king", "b": "black man", "B": "black king", ".": "empty"},
        "goal": "Win by capturing or immobilizing the opponent.",
        "search": {
            "depth": analyses[0]["search_depth"] if analyses else 0,
            "meaning": "lookahead_score already includes adversarial minimax search; higher is better for the side to play",
        },
        "learning": {
            "meaning": "learned_bonus and learned_q come from this agent's persisted self-play experience; higher is better",
        },
    }


def _question(analyses: list[dict[str, Any]]) -> dict[str, Any]:
    criteria = {a["id"]: _criteria_text(a) for a in analyses}
    return {
        "type": "choice",
        "instructions": (
            "Choose the strongest legal checkers move. Think strategically, not just one move ahead. "
            "The options already contain adversarial lookahead search, a principal variation, tactical features, "
            "and values learned from previous games. Prefer winning/forcing lines, avoid tactical losses, and use "
            "the learned signal as additional experience. Return exactly one supplied option."
        ),
        "criteria": criteria,
    }


def choose_laya(board: list[list[str]], side: str, analyses: list[dict[str, Any]]) -> tuple[engine.Move, dict[str, Any]]:
    start_laya_loading()
    with _laya_lock:
        agent = _laya_agent
        err = _laya_error

    if agent is None:
        if err:
            raise AgentUnavailable(f"Laya konnte nicht geladen werden: {err}")
        raise AgentUnavailable("Laya lädt noch im Hintergrund.")

    questions = {"move": _question(analyses)}
    result = agent.predict(_state(board, side, analyses, "laya"), questions)
    answer = result["answers"]["move"]
    selected = answer.get("choice")
    by_id = {a["id"]: a for a in analyses}
    if selected not in by_id:
        raise AgentUnavailable(f"Laya lieferte einen unbekannten Zug: {selected!r}")

    chosen = by_id[selected]
    return chosen["move"], {
        "agent": "laya",
        "model": _LAYA_MODEL,
        "selected": selected,
        "notation": chosen["notation"],
        "confidence": answer.get("confidence"),
        "probabilities": answer.get("probabilities", {}),
        "lookahead_score": chosen["lookahead_score"],
        "learning_bonus": chosen.get("learning_bonus", 0.0),
        "combined_score": chosen.get("combined_score"),
        "learned_q": chosen.get("learned_q", 0.0),
        "visits": chosen.get("visits", 0),
        "principal_variation": chosen.get("principal_variation", []),
        "search_depth": chosen["search_depth"],
    }


def choose_typesafe(board: list[list[str]], side: str, analyses: list[dict[str, Any]]) -> tuple[engine.Move, dict[str, Any]]:
    with _typesafe_lock:
        key = _typesafe_api_key
    if not key:
        raise AgentUnavailable("TypeSafe API-Key fehlt. Bitte oben im Arena-Panel eintragen.")

    try:
        from typesafe_sdk import Choice, TypeSafeClient
    except Exception as exc:
        raise AgentUnavailable(f"typesafe-sdk konnte nicht importiert werden: {exc}") from exc

    criteria = {a["id"]: _criteria_text(a) for a in analyses}
    question = Choice(
        instructions=(
            "Choose the strongest legal checkers move. Think strategically, not just one move ahead. "
            "Each option contains adversarial lookahead search, a principal variation, tactical features, "
            "and values learned from previous games. Prefer winning/forcing lines and avoid tactical losses. "
            "Return exactly one supplied option."
        ),
        criteria=criteria,
    )

    kwargs: dict[str, Any] = {"api_key": key, "timeout": 45.0}
    if _TYPESAFE_MODEL:
        kwargs["model"] = _TYPESAFE_MODEL

    try:
        with TypeSafeClient(**kwargs) as client:
            response = client.system_one(
                state=_state(board, side, analyses, "typesafe"),
                questions={"move": question},
            )
        answer = response.choices["move"]
        selected = answer.choice
        confidence = getattr(answer, "confidence", None)
        probabilities = getattr(answer, "probabilities", {})
        if hasattr(probabilities, "model_dump"):
            probabilities = probabilities.model_dump()
    except Exception as exc:
        raise AgentUnavailable(f"TypeSafe/Jev API-Fehler: {type(exc).__name__}: {exc}") from exc

    by_id = {a["id"]: a for a in analyses}
    if selected not in by_id:
        raise AgentUnavailable(f"TypeSafe/Jev lieferte einen unbekannten Zug: {selected!r}")

    chosen = by_id[selected]
    return chosen["move"], {
        "agent": "typesafe",
        "model": _TYPESAFE_MODEL or "Jev / server default",
        "selected": selected,
        "notation": chosen["notation"],
        "confidence": confidence,
        "probabilities": probabilities,
        "lookahead_score": chosen["lookahead_score"],
        "learning_bonus": chosen.get("learning_bonus", 0.0),
        "combined_score": chosen.get("combined_score"),
        "learned_q": chosen.get("learned_q", 0.0),
        "visits": chosen.get("visits", 0),
        "principal_variation": chosen.get("principal_variation", []),
        "search_depth": chosen["search_depth"],
    }
