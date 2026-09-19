from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from spider_solver import SpiderAction

_PATH = Path("spider_learning.json")
_FEATURES = (
    "reveal_hidden",
    "same_suit_join",
    "source_empty",
    "sequence_length",
    "complete_run",
    "empty_destination",
    "deal",
)


def _blank_agent() -> dict[str, Any]:
    return {
        "games": 0,
        "wins": 0,
        "losses": 0,
        "moves": 0,
        "q": {},
        "weights": {k: 0.0 for k in _FEATURES},
    }


def _blank() -> dict[str, Any]:
    return {
        "version": 1,
        "agents": {
            "laya": _blank_agent(),
            "typesafe": _blank_agent(),
        },
    }


class SpiderLearning:
    def __init__(self, path: Path = _PATH):
        self.path = path
        self.lock = threading.Lock()
        self.data = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return _blank()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if "agents" not in data:
                return _blank()
            for name in ("laya", "typesafe"):
                data["agents"].setdefault(name, _blank_agent())
                data["agents"][name].setdefault("q", {})
                data["agents"][name].setdefault("weights", {})
                for f in _FEATURES:
                    data["agents"][name]["weights"].setdefault(f, 0.0)
            return data
        except Exception:
            return _blank()

    def _save(self) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def enrich(self, agent: str, state_sig: str, action: SpiderAction) -> SpiderAction:
        with self.lock:
            ad = self.data["agents"][agent]
            exact = ad["q"].get(state_sig, {}).get(action.notation(), {"q": 0.0, "n": 0})
            weights = dict(ad["weights"])

        q = float(exact.get("q", 0.0))
        n = int(exact.get("n", 0))
        global_bonus = 0.0
        for key in _FEATURES:
            global_bonus += float(weights.get(key, 0.0)) * float(action.features.get(key, 0.0))

        action.learned_q = q
        action.visits = n
        action.learned_bonus = q * 28.0 + global_bonus * 9.0
        return action

    def record_move(self, agent: str) -> None:
        with self.lock:
            self.data["agents"][agent]["moves"] += 1
            self._save()

    def finish_game(
        self,
        agent: str,
        trajectory: list[dict[str, Any]],
        won: bool,
    ) -> None:
        reward = 1.0 if won else -1.0
        with self.lock:
            ad = self.data["agents"][agent]
            ad["games"] += 1
            ad["wins" if won else "losses"] += 1

            for item in trajectory:
                state_sig = item["state_sig"]
                action_name = item["action"]
                features = item.get("features", {})
                bucket = ad["q"].setdefault(state_sig, {})
                entry = bucket.setdefault(action_name, {"q": 0.0, "n": 0})
                n = int(entry.get("n", 0)) + 1
                old = float(entry.get("q", 0.0))
                entry["n"] = n
                entry["q"] = old + (reward - old) / n

                lr = 0.02
                for key in _FEATURES:
                    val = float(features.get(key, 0.0))
                    w = float(ad["weights"].get(key, 0.0))
                    ad["weights"][key] = max(-3.0, min(3.0, w + lr * reward * val))

            self._save()

    def summary(self) -> dict[str, Any]:
        with self.lock:
            return {
                name: {
                    "games": ad["games"],
                    "wins": ad["wins"],
                    "losses": ad["losses"],
                    "moves": ad["moves"],
                    "known_states": len(ad["q"]),
                    "weights": {k: round(float(v), 4) for k, v in ad["weights"].items()},
                }
                for name, ad in self.data["agents"].items()
            }

    def reset(self) -> None:
        with self.lock:
            self.data = _blank()
            self._save()
