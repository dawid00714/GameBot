from __future__ import annotations

import json
import math
import random
import threading
from collections import deque
from pathlib import Path
from typing import Any

from spider_solver import SpiderAction, add_lookahead, generate_actions
from spider_state import SpiderState

try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
except Exception:  # pragma: no cover - fallback only
    torch = None
    nn = None
    F = None

_PATH = Path("spider_learning.json")
_VERSION = 3
_REPLAY_LIMIT = 6000
_BATCH = 32
_GAMMA = 0.96
_LR = 7e-4
_TARGET_SYNC = 100

# 10 columns * 5 values + 4 global values.
_STATE_DIM = 54
# action kind/source/dest/start/len + 6 strategic flags + 2 heuristic scores.
_ACTION_DIM = 13
_INPUT_DIM = _STATE_DIM + _ACTION_DIM


def _blank_agent() -> dict[str, Any]:
    return {
        "games": 0,
        "wins": 0,
        "losses": 0,
        "moves": 0,
        "transitions": 0,
        "updates": 0,
        "reward_sum": 0.0,
        "last_reward": 0.0,
        "last_loss": None,
    }


def _blank() -> dict[str, Any]:
    return {
        "version": _VERSION,
        "agents": {
            "laya": _blank_agent(),
            "typesafe": _blank_agent(),
        },
    }


def _tail_run_length(col) -> int:
    cards = list(col.cards)
    if not cards:
        return 0
    n = 1
    for i in range(len(cards) - 2, -1, -1):
        upper = cards[i]
        lower = cards[i + 1]
        if upper.value == lower.value + 1 and upper.suit == lower.suit:
            n += 1
        else:
            break
    return n


def state_vector(state: SpiderState) -> list[float]:
    out: list[float] = []
    visible_total = 0
    hidden_cols = 0
    empty_cols = 0

    for col in state.columns[:10]:
        count = len(col.cards)
        visible_total += count
        hidden = 1.0 if col.hidden_above else 0.0
        hidden_cols += int(hidden)
        empty = 1.0 if not col.cards and not col.hidden_above else 0.0
        empty_cols += int(empty)
        top_value = (col.cards[-1].value / 13.0) if col.cards else 0.0
        run = _tail_run_length(col) / 13.0
        out.extend([
            hidden,
            min(1.0, count / 13.0),
            top_value,
            min(1.0, run),
            empty,
        ])

    while len(out) < 50:
        out.extend([0.0] * 5)

    out.extend([
        1.0 if state.stock_available else 0.0,
        min(1.0, visible_total / 104.0),
        hidden_cols / 10.0,
        empty_cols / 10.0,
    ])
    return out[:_STATE_DIM]


def action_vector(action: SpiderAction) -> list[float]:
    is_deal = 1.0 if action.kind == "deal" else 0.0
    src = -1.0 if action.source is None else action.source / 9.0
    dst = -1.0 if action.destination is None else action.destination / 9.0
    start = -1.0 if action.start_index is None else min(1.0, action.start_index / 12.0)
    moving_len = min(1.0, len(action.moving) / 13.0)

    def flag(name: str) -> float:
        return 1.0 if float(action.features.get(name, 0.0)) > 0 else 0.0

    return [
        is_deal,
        src,
        dst,
        start,
        moving_len,
        flag("reveal_hidden"),
        flag("same_suit_join"),
        flag("source_empty"),
        flag("complete_run"),
        flag("empty_destination"),
        1.0 if action.kind == "move" else 0.0,
        math.tanh(float(action.immediate_score) / 60.0),
        math.tanh(float(action.lookahead_score) / 100.0),
    ]


def state_action_vector(state: SpiderState, action: SpiderAction) -> list[float]:
    return state_vector(state) + action_vector(action)


if nn is not None:
    class _QNetwork(nn.Module):
        def __init__(self):
            super().__init__()
            self.net = nn.Sequential(
                nn.Linear(_INPUT_DIM, 128),
                nn.ReLU(),
                nn.Linear(128, 128),
                nn.ReLU(),
                nn.Linear(128, 64),
                nn.ReLU(),
                nn.Linear(64, 1),
            )

        def forward(self, x):
            return self.net(x).squeeze(-1)
else:
    _QNetwork = None


class _Brain:
    def __init__(self, name: str):
        self.name = name
        self.enabled = torch is not None and _QNetwork is not None
        self.replay: deque[dict[str, Any]] = deque(maxlen=_REPLAY_LIMIT)
        self.updates = 0
        self.last_loss: float | None = None
        self.path = Path(f"spider_deep_rl_{name}.pt")

        if not self.enabled:
            self.q = None
            self.target = None
            self.optimizer = None
            return

        self.q = _QNetwork()
        self.target = _QNetwork()
        self.target.load_state_dict(self.q.state_dict())
        self.target.eval()
        self.optimizer = torch.optim.Adam(self.q.parameters(), lr=_LR)
        self._load()

    def _load(self) -> None:
        if not self.enabled or not self.path.exists():
            return
        try:
            blob = torch.load(self.path, map_location="cpu", weights_only=False)
            if int(blob.get("input_dim", -1)) != _INPUT_DIM:
                return
            self.q.load_state_dict(blob["q"])
            self.target.load_state_dict(blob.get("target", blob["q"]))
            self.updates = int(blob.get("updates", 0))
        except Exception:
            # A stale/corrupt checkpoint must never stop the game.
            pass

    def save(self) -> None:
        if not self.enabled:
            return
        tmp = self.path.with_suffix(".pt.tmp")
        torch.save(
            {
                "version": 1,
                "input_dim": _INPUT_DIM,
                "updates": self.updates,
                "q": self.q.state_dict(),
                "target": self.target.state_dict(),
            },
            tmp,
        )
        tmp.replace(self.path)

    def predict_many(self, vectors: list[list[float]]) -> list[float]:
        if not self.enabled or not vectors:
            return [0.0 for _ in vectors]
        with torch.no_grad():
            x = torch.tensor(vectors, dtype=torch.float32)
            values = self.q(x).cpu().tolist()
        return [float(v) for v in values]

    def add_transition(
        self,
        current: list[float],
        reward: float,
        next_vectors: list[list[float]],
        done: bool,
    ) -> None:
        self.replay.append({
            "x": current,
            "r": float(reward),
            "next": next_vectors,
            "done": bool(done),
        })

    def train(self, gradient_steps: int = 2) -> float | None:
        if not self.enabled or len(self.replay) < _BATCH:
            return None

        losses: list[float] = []
        for _ in range(max(1, int(gradient_steps))):
            batch = random.sample(list(self.replay), _BATCH)
            x = torch.tensor([b["x"] for b in batch], dtype=torch.float32)
            rewards = torch.tensor([b["r"] for b in batch], dtype=torch.float32)
            dones = torch.tensor([1.0 if b["done"] else 0.0 for b in batch], dtype=torch.float32)

            q_values = self.q(x)
            next_values: list[float] = []
            with torch.no_grad():
                for b in batch:
                    if b["done"] or not b["next"]:
                        next_values.append(0.0)
                    else:
                        nx = torch.tensor(b["next"], dtype=torch.float32)
                        next_values.append(float(self.target(nx).max().item()))
                next_t = torch.tensor(next_values, dtype=torch.float32)
                target = rewards + (1.0 - dones) * _GAMMA * next_t

            loss = F.smooth_l1_loss(q_values, target)
            self.optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(self.q.parameters(), 2.0)
            self.optimizer.step()

            self.updates += 1
            losses.append(float(loss.item()))
            if self.updates % _TARGET_SYNC == 0:
                self.target.load_state_dict(self.q.state_dict())

        self.last_loss = sum(losses) / len(losses)
        return self.last_loss


class SpiderLearning:
    """Persistent online Deep-Q learning layered on top of Spider rules.

    Laya/TypeSafe proposes among legal moves, while this network learns expected
    long-term reward from actual gameplay. The network is deliberately small so
    it can train online on CPU after each accepted move.
    """

    def __init__(self, path: Path = _PATH):
        self.path = path
        self.lock = threading.RLock()
        self.data = self._load()
        self.brains = {
            "laya": _Brain("laya"),
            "typesafe": _Brain("typesafe"),
        }

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return _blank()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            # Old Build 4.x data came from broken controller/recognition runs.
            if int(data.get("version", 0)) < _VERSION:
                return _blank()
            if "agents" not in data:
                return _blank()
            for name in ("laya", "typesafe"):
                base = _blank_agent()
                current = data["agents"].setdefault(name, base)
                for k, v in base.items():
                    current.setdefault(k, v)
            return data
        except Exception:
            return _blank()

    def _save(self) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def enrich_actions(
        self,
        agent: str,
        state_sig: str,
        state: SpiderState,
        actions: list[SpiderAction],
    ) -> list[SpiderAction]:
        brain = self.brains.get(agent)
        vectors = [state_action_vector(state, a) for a in actions]
        predictions = brain.predict_many(vectors) if brain else [0.0] * len(actions)

        with self.lock:
            transitions = int(self.data["agents"][agent].get("transitions", 0))

        # Neural influence ramps in as real experience accumulates. This avoids
        # an untrained random network dominating the first games.
        maturity = min(1.0, transitions / 300.0)
        for action, q in zip(actions, predictions):
            action.learned_q = float(q)
            action.visits = transitions
            action.learned_bonus = max(-35.0, min(35.0, float(q) * 10.0 * maturity))
        return actions

    # Backward-compatible single-action API.
    def enrich(self, agent: str, state_sig: str, action: SpiderAction) -> SpiderAction:
        return action

    def reward_for_transition(
        self,
        state: SpiderState,
        action: SpiderAction,
        next_state: SpiderState,
        *,
        won: bool = False,
        repeated_state: bool = False,
    ) -> float:
        reward = -0.03  # small step cost encourages shorter solutions

        if action.features.get("reveal_hidden"):
            reward += 0.90
        if action.features.get("same_suit_join"):
            reward += 0.12
        if action.features.get("complete_run"):
            reward += 4.0
        if action.kind == "deal":
            reward -= 0.35
        if action.features.get("empty_destination") and not action.features.get("reveal_hidden"):
            reward -= 0.45

        hidden_before = sum(1 for c in state.columns if c.hidden_above)
        hidden_after = sum(1 for c in next_state.columns if c.hidden_above)
        reward += max(-1, min(1, hidden_before - hidden_after)) * 0.45

        empty_before = sum(1 for c in state.columns if not c.cards and not c.hidden_above)
        empty_after = sum(1 for c in next_state.columns if not c.cards and not c.hidden_above)
        reward += max(-2, min(2, empty_after - empty_before)) * 0.20

        visible_before = sum(len(c.cards) for c in state.columns)
        visible_after = sum(len(c.cards) for c in next_state.columns)
        # A completed Spider run removes 13 cards.
        if visible_before - visible_after >= 10:
            reward += 3.0

        if repeated_state:
            reward -= 0.80
        if won:
            reward += 12.0

        return float(max(-5.0, min(15.0, reward)))

    def observe_transition(
        self,
        agent: str,
        state: SpiderState,
        action: SpiderAction,
        next_state: SpiderState,
        *,
        reward: float,
        done: bool,
    ) -> dict[str, Any]:
        brain = self.brains[agent]
        current = state_action_vector(state, action)

        next_vectors: list[list[float]] = []
        if not done:
            try:
                next_actions = add_lookahead(next_state, generate_actions(next_state), 2)
                next_vectors = [state_action_vector(next_state, a) for a in next_actions[:16]]
            except Exception:
                next_vectors = []

        brain.add_transition(current, reward, next_vectors, done)
        loss = brain.train(gradient_steps=3)

        with self.lock:
            ad = self.data["agents"][agent]
            ad["transitions"] += 1
            ad["updates"] = brain.updates
            ad["reward_sum"] = float(ad.get("reward_sum", 0.0)) + float(reward)
            ad["last_reward"] = round(float(reward), 4)
            ad["last_loss"] = None if loss is None else round(float(loss), 6)
            transitions = int(ad["transitions"])
            self._save()

        if transitions % 20 == 0 or done:
            brain.save()

        return {
            "reward": round(float(reward), 4),
            "loss": None if loss is None else round(float(loss), 6),
            "replay": len(brain.replay),
            "updates": brain.updates,
            "network": "66→128→128→64→1",
        }

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
        with self.lock:
            ad = self.data["agents"][agent]
            ad["games"] += 1
            ad["wins" if won else "losses"] += 1
            self._save()
        self.brains[agent].save()

    def summary(self) -> dict[str, Any]:
        with self.lock:
            result: dict[str, Any] = {}
            for name, ad in self.data["agents"].items():
                transitions = int(ad.get("transitions", 0))
                reward_sum = float(ad.get("reward_sum", 0.0))
                brain = self.brains.get(name)
                result[name] = {
                    "deep_rl": bool(brain and brain.enabled),
                    "network": "66→128→128→64→1" if brain and brain.enabled else "unavailable",
                    "algorithm": "online DQN / TD reward learning",
                    "games": ad["games"],
                    "wins": ad["wins"],
                    "losses": ad["losses"],
                    "moves": ad["moves"],
                    "transitions": transitions,
                    "updates": int(brain.updates if brain else ad.get("updates", 0)),
                    "replay": len(brain.replay) if brain else 0,
                    "mean_reward": round(reward_sum / max(1, transitions), 4),
                    "last_reward": ad.get("last_reward", 0.0),
                    "last_loss": (
                        brain.last_loss if brain and brain.last_loss is not None
                        else ad.get("last_loss")
                    ),
                    "checkpoint": str(brain.path) if brain else None,
                }
            return result

    def reset(self) -> None:
        with self.lock:
            self.data = _blank()
            self._save()
            for name, brain in list(self.brains.items()):
                try:
                    if brain.path.exists():
                        brain.path.unlink()
                except Exception:
                    pass
                self.brains[name] = _Brain(name)
