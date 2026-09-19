from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from strategy import learning_features

_DEFAULT_PATH = Path(os.getenv("ARENA_LEARNING_FILE", "arena_learning.json"))
_FEATURES = ("capture", "promotion", "material", "mobility", "reply_risk", "center", "lookahead")


def _new_agent() -> dict[str, Any]:
    return {
        "positions": {},
        "weights": {name: 0.0 for name in _FEATURES},
        "games": 0,
        "wins": 0,
        "losses": 0,
        "draws": 0,
    }


def _new_data() -> dict[str, Any]:
    return {
        "version": 1,
        "total_games": 0,
        "agents": {
            "laya": _new_agent(),
            "typesafe": _new_agent(),
        },
    }


class LearningStore:
    def __init__(self, path: Path = _DEFAULT_PATH):
        self.path = path
        self.lock = threading.Lock()
        self.data = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return _new_data()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or "agents" not in raw:
                return _new_data()
            for name in ("laya", "typesafe"):
                raw.setdefault("agents", {}).setdefault(name, _new_agent())
                raw["agents"][name].setdefault("positions", {})
                raw["agents"][name].setdefault("weights", {k: 0.0 for k in _FEATURES})
                for key in _FEATURES:
                    raw["agents"][name]["weights"].setdefault(key, 0.0)
            raw.setdefault("total_games", 0)
            return raw
        except Exception:
            return _new_data()

    def _save(self) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def bonus(
        self,
        agent: str,
        state_key: str,
        move_notation: str,
        analysis: dict[str, Any],
    ) -> dict[str, Any]:
        with self.lock:
            agent_data = self.data["agents"][agent]
            exact = agent_data["positions"].get(state_key, {}).get(move_notation, {"q": 0.0, "visits": 0})
            weights = dict(agent_data["weights"])

        feats = learning_features(analysis)
        global_value = sum(float(weights.get(k, 0.0)) * feats[k] for k in _FEATURES)
        exact_q = float(exact.get("q", 0.0))
        visits = int(exact.get("visits", 0))

        # Keep learning important but secondary to deep tactical search.
        bonus = exact_q * 35.0 + global_value * 12.0
        return {
            "learning_bonus": round(bonus, 3),
            "learned_q": round(exact_q, 4),
            "visits": visits,
            "features": feats,
        }

    def record_game(
        self,
        trajectory: list[dict[str, Any]],
        winner_agent: str | None,
    ) -> None:
        with self.lock:
            self.data["total_games"] = int(self.data.get("total_games", 0)) + 1

            for agent_name in ("laya", "typesafe"):
                agent_data = self.data["agents"][agent_name]
                agent_data["games"] = int(agent_data.get("games", 0)) + 1
                if winner_agent is None:
                    agent_data["draws"] = int(agent_data.get("draws", 0)) + 1
                elif winner_agent == agent_name:
                    agent_data["wins"] = int(agent_data.get("wins", 0)) + 1
                else:
                    agent_data["losses"] = int(agent_data.get("losses", 0)) + 1

            for item in trajectory:
                agent_name = item["agent"]
                reward = 0.0 if winner_agent is None else (1.0 if winner_agent == agent_name else -1.0)
                agent_data = self.data["agents"][agent_name]

                state_bucket = agent_data["positions"].setdefault(item["state_key"], {})
                entry = state_bucket.setdefault(item["move"], {"q": 0.0, "visits": 0})
                old_visits = int(entry.get("visits", 0))
                old_q = float(entry.get("q", 0.0))
                new_visits = old_visits + 1
                # Incremental Monte-Carlo mean for exact repeated positions.
                entry["visits"] = new_visits
                entry["q"] = old_q + (reward - old_q) / new_visits

                # Small global policy update so experience can generalize to new positions.
                feats = item.get("features", {})
                weights = agent_data["weights"]
                lr = 0.025
                for key in _FEATURES:
                    value = float(feats.get(key, 0.0))
                    updated = float(weights.get(key, 0.0)) + lr * reward * value
                    weights[key] = max(-3.0, min(3.0, updated))

            self._save()

    def summary(self) -> dict[str, Any]:
        with self.lock:
            return {
                "total_games": int(self.data.get("total_games", 0)),
                "agents": {
                    name: {
                        "games": int(self.data["agents"][name].get("games", 0)),
                        "wins": int(self.data["agents"][name].get("wins", 0)),
                        "losses": int(self.data["agents"][name].get("losses", 0)),
                        "draws": int(self.data["agents"][name].get("draws", 0)),
                        "weights": {
                            k: round(float(v), 4)
                            for k, v in self.data["agents"][name].get("weights", {}).items()
                        },
                        "known_positions": len(self.data["agents"][name].get("positions", {})),
                    }
                    for name in ("laya", "typesafe")
                },
            }

    def reset(self) -> None:
        with self.lock:
            self.data = _new_data()
            self._save()
