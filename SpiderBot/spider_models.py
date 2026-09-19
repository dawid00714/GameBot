from __future__ import annotations

import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from spider_solver import SpiderAction
from spider_state import SpiderState

_LAYA_MODEL = os.getenv("LAYA_MODEL", "convaiinnovations/laya-multilingual")
_TYPESAFE_MODEL = os.getenv("TYPESAFE_MODEL") or None

_laya_agent = None
_laya_error: str | None = None
_laya_loading = False
_laya_started: float | None = None
_laya_lock = threading.Lock()
_laya_load_path: str | None = None

_typesafe_key = os.getenv("TYPESAFE_API_KEY", "").strip() or None
_typesafe_lock = threading.Lock()
_typesafe_validated = False
_typesafe_validation_error: str | None = None
_typesafe_available_models: list[str] = []


class SpiderModelError(RuntimeError):
    pass


def _local_model_dir() -> Path:
    safe_name = _LAYA_MODEL.replace("/", "__").replace("\\", "__")
    return Path(__file__).resolve().parent / ".models" / safe_name


def _download_laya_windows_safe() -> str:
    from huggingface_hub import snapshot_download

    target = _local_model_dir()
    target.mkdir(parents=True, exist_ok=True)
    kwargs = {
        "repo_id": _LAYA_MODEL,
        "local_dir": str(target),
        "token": os.environ.get("HF_TOKEN"),
    }
    try:
        snapshot_download(local_dir_use_symlinks=False, **kwargs)
    except TypeError as exc:
        if "local_dir_use_symlinks" not in str(exc):
            raise
        snapshot_download(**kwargs)
    return str(target)


def _load_laya_worker() -> None:
    global _laya_agent, _laya_error, _laya_loading, _laya_load_path
    try:
        import laya
        path = _download_laya_windows_safe() if os.name == "nt" else _LAYA_MODEL
        agent = laya.load(path)
        with _laya_lock:
            _laya_agent = agent
            _laya_load_path = path
            _laya_error = None
    except Exception as exc:
        with _laya_lock:
            _laya_error = f"{type(exc).__name__}: {exc}"
    finally:
        with _laya_lock:
            _laya_loading = False


def start_laya() -> None:
    global _laya_loading, _laya_started
    with _laya_lock:
        if _laya_agent is not None or _laya_loading or _laya_error is not None:
            return
        _laya_loading = True
        _laya_started = time.time()
    threading.Thread(target=_load_laya_worker, name="spider-laya-loader", daemon=True).start()


def retry_laya(clear_local: bool = False) -> None:
    global _laya_agent, _laya_error, _laya_loading, _laya_started, _laya_load_path
    with _laya_lock:
        if _laya_loading:
            return
        _laya_agent = None
        _laya_error = None
        _laya_started = None
        _laya_load_path = None
    if clear_local:
        shutil.rmtree(_local_model_dir(), ignore_errors=True)
    start_laya()


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
            "load_path": _laya_load_path,
            "elapsed_seconds": round(time.time() - _laya_started, 1) if _laya_started else 0.0,
        }


def _normalize_typesafe_key(key: str | None) -> str | None:
    value = (key or "").strip()
    if not value:
        return None

    # Make copy/paste from documentation/chat tolerant of common wrappers.
    if value.lower().startswith("bearer "):
        value = value[7:].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1].strip()

    # Bearer credentials are HTTP header tokens and therefore must be ASCII.
    # If this fails, httpx/httpcore later raises the much less useful
    # UnicodeEncodeError seen by the user.
    try:
        value.encode("ascii")
    except UnicodeEncodeError as exc:
        bad = value[exc.start]
        raise SpiderModelError(
            "Der eingegebene TypeSafe API-Key enthält ein Nicht-ASCII-Zeichen "
            f"an Position {exc.start + 1} (U+{ord(bad):04X}). "
            "Bitte den reinen API-Key direkt aus TypeSafe kopieren – ohne "
            "Beschriftung, Anführungszeichen oder zusätzlichen Text."
        ) from exc

    # HTTP bearer values must not contain whitespace/control characters.
    if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        raise SpiderModelError(
            "Der eingegebene TypeSafe API-Key enthält Leer-/Steuerzeichen. "
            "Bitte nur den reinen API-Key einfügen."
        )
    return value


def set_typesafe_key(key: str | None) -> None:
    global _typesafe_key, _typesafe_validated, _typesafe_validation_error, _typesafe_available_models
    value = _normalize_typesafe_key(key)
    with _typesafe_lock:
        _typesafe_key = value
        _typesafe_validated = False
        _typesafe_validation_error = None
        _typesafe_available_models = []


def validate_typesafe() -> dict[str, Any]:
    """Verify the credential immediately instead of failing on the first move."""
    global _typesafe_validated, _typesafe_validation_error, _typesafe_available_models

    with _typesafe_lock:
        key = _typesafe_key
    if not key:
        raise SpiderModelError("TypeSafe API-Key fehlt.")

    try:
        from typesafe_sdk import TypeSafeClient
    except Exception as exc:
        raise SpiderModelError(f"typesafe-sdk fehlt: {exc}") from exc

    kwargs: dict[str, Any] = {"api_key": key, "timeout": 20.0}
    if _TYPESAFE_MODEL:
        kwargs["model"] = _TYPESAFE_MODEL

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

        with _typesafe_lock:
            _typesafe_validated = True
            _typesafe_validation_error = None
            _typesafe_available_models = names

    except UnicodeEncodeError as exc:
        # This should now only be reachable if an SDK/default header contains
        # non-ASCII. Keep the diagnostic explicit and separate from auth errors.
        with _typesafe_lock:
            _typesafe_validated = False
            _typesafe_validation_error = (
                f"UnicodeEncodeError im TypeSafe HTTP-Header: {exc}"
            )
        raise SpiderModelError(
            "TypeSafe konnte den HTTP-Request nicht senden, weil ein Header "
            f"nicht ASCII-kompatibel ist: {exc}"
        ) from exc
    except Exception as exc:
        with _typesafe_lock:
            _typesafe_validated = False
            _typesafe_validation_error = f"{type(exc).__name__}: {exc}"
        raise SpiderModelError(
            f"TypeSafe-Verbindungstest fehlgeschlagen: {type(exc).__name__}: {exc}"
        ) from exc

    return typesafe_status()


def typesafe_status() -> dict[str, Any]:
    with _typesafe_lock:
        configured = bool(_typesafe_key)
        validated = bool(_typesafe_validated)
        validation_error = _typesafe_validation_error
        models = list(_typesafe_available_models)
        key_length = len(_typesafe_key) if _typesafe_key else 0
    return {
        "configured": configured,
        "validated": validated,
        "validation_error": validation_error,
        "key_length": key_length,
        "available_models": models,
        "model": _TYPESAFE_MODEL or "server default (Jev)",
    }


def _state_payload(state: SpiderState, actions: list[SpiderAction], depth: int) -> dict[str, Any]:
    columns = []
    for col in state.columns:
        columns.append(
            {
                "column": col.index + 1,
                "hidden_cards_above_visible_run": col.hidden_above,
                "visible_cards_top_to_bottom": [
                    {"rank": c.rank, "suit": c.suit} for c in col.cards
                ],
            }
        )
    return {
        "game": "Spider Solitaire on Windows",
        "variant": "one-suit Spider unless the observed state contains other suits",
        "goal": "Win the game by completing and removing all K-to-A same-suit sequences.",
        "columns": columns,
        "stock_available": state.stock_available,
        "lookahead_depth": depth,
        "important": (
            "Every option below is a legal candidate created by the rule/vision layer. "
            "Higher lookahead_score is better. learned_bonus reflects previous game experience."
        ),
    }


def _criteria(actions: list[SpiderAction]) -> dict[str, str]:
    out: dict[str, str] = {}
    for a in actions:
        pv = " -> ".join(a.principal_variation) if a.principal_variation else a.notation()
        out[a.id] = (
            f"{a.notation()}; kind={a.kind}; "
            f"immediate={a.immediate_score:.2f}; lookahead_score={a.lookahead_score:.2f}; "
            f"learned_bonus={a.learned_bonus:.2f}; total_score={a.total_score:.2f}; "
            f"learned_q={a.learned_q:.3f}; visits={a.visits}; "
            f"features={a.features}; principal_variation={pv}"
        )
    return out


def _instruction() -> str:
    return (
        "Choose the move most likely to eventually WIN Spider Solitaire. "
        "Do not optimize only the next click. Prefer exposing hidden cards, creating useful empty columns, "
        "building same-suit descending runs, completing K-to-A runs, preserving mobility, and avoiding "
        "unnecessary stock deals. Use the lookahead and learned values as evidence. "
        "Return exactly one supplied option."
    )


def choose_laya(state: SpiderState, actions: list[SpiderAction], depth: int) -> tuple[SpiderAction, dict[str, Any]]:
    start_laya()
    with _laya_lock:
        agent = _laya_agent
        err = _laya_error
    if agent is None:
        if err:
            raise SpiderModelError(f"Laya konnte nicht geladen werden: {err}")
        raise SpiderModelError("Laya lädt noch.")

    questions = {
        "move": {
            "type": "choice",
            "instructions": _instruction(),
            "criteria": _criteria(actions),
        }
    }
    try:
        result = agent.predict(_state_payload(state, actions, depth), questions)
        answer = result["answers"]["move"]
        selected = answer.get("choice")
    except Exception as exc:
        raise SpiderModelError(f"Laya-Inferenzfehler: {type(exc).__name__}: {exc}") from exc

    by_id = {a.id: a for a in actions}
    if selected not in by_id:
        raise SpiderModelError(f"Laya wählte unbekannte Aktion {selected!r}")

    return by_id[selected], {
        "model": "laya",
        "checkpoint": _LAYA_MODEL,
        "selected": selected,
        "confidence": answer.get("confidence"),
        "probabilities": answer.get("probabilities", {}),
    }


def choose_typesafe(state: SpiderState, actions: list[SpiderAction], depth: int) -> tuple[SpiderAction, dict[str, Any]]:
    with _typesafe_lock:
        key = _typesafe_key
        validated = _typesafe_validated
    if not key:
        raise SpiderModelError("TypeSafe API-Key fehlt.")
    if not validated:
        raise SpiderModelError(
            "TypeSafe API-Key wurde noch nicht erfolgreich geprüft. "
            "Bitte zuerst 'API verbinden' drücken."
        )

    try:
        from typesafe_sdk import Choice, TypeSafeClient
    except Exception as exc:
        raise SpiderModelError(f"typesafe-sdk fehlt: {exc}") from exc

    question = Choice(
        instructions=_instruction(),
        criteria=_criteria(actions),
    )
    kwargs: dict[str, Any] = {"api_key": key, "timeout": 45.0}
    if _TYPESAFE_MODEL:
        kwargs["model"] = _TYPESAFE_MODEL

    try:
        with TypeSafeClient(**kwargs) as client:
            response = client.system_one(
                state=_state_payload(state, actions, depth),
                questions={"move": question},
            )
        answer = response.choices["move"]
        selected = answer.choice
        probabilities = getattr(answer, "probabilities", {})
        if hasattr(probabilities, "model_dump"):
            probabilities = probabilities.model_dump()
        confidence = getattr(answer, "confidence", None)
    except Exception as exc:
        raise SpiderModelError(
            f"TypeSafe/Jev API-Fehler: {type(exc).__name__}: {exc}"
        ) from exc

    by_id = {a.id: a for a in actions}
    if selected not in by_id:
        raise SpiderModelError(f"TypeSafe/Jev wählte unbekannte Aktion {selected!r}")

    return by_id[selected], {
        "model": "typesafe",
        "checkpoint": _TYPESAFE_MODEL or "Jev / server default",
        "selected": selected,
        "confidence": confidence,
        "probabilities": probabilities,
    }


def choose(
    model: str,
    state: SpiderState,
    actions: list[SpiderAction],
    depth: int,
) -> tuple[SpiderAction, dict[str, Any]]:
    name = model.strip().lower()
    if name == "laya":
        return choose_laya(state, actions, depth)
    if name in ("typesafe", "jev", "typesafe/jev"):
        return choose_typesafe(state, actions, depth)
    raise SpiderModelError(f"Unbekanntes Modell: {model}")
