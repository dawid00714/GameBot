from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from spider_state import ColumnState, SpiderState, VisibleCard


@dataclass
class SpiderAction:
    id: str
    kind: str  # "move" or "deal"
    source: int | None = None
    destination: int | None = None
    start_index: int | None = None
    moving: list[tuple[str, str]] = field(default_factory=list)
    immediate_score: float = 0.0
    lookahead_score: float = 0.0
    principal_variation: list[str] = field(default_factory=list)
    learned_bonus: float = 0.0
    learned_q: float = 0.0
    visits: int = 0
    features: dict[str, float] = field(default_factory=dict)

    @property
    def total_score(self) -> float:
        return self.lookahead_score + self.learned_bonus

    def notation(self) -> str:
        if self.kind == "deal":
            return "STOCK"
        cards = "-".join(r for r, _s in self.moving)
        return f"C{self.source + 1}:{cards}->C{self.destination + 1}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "source": self.source,
            "destination": self.destination,
            "start_index": self.start_index,
            "moving": self.moving,
            "notation": self.notation(),
            "immediate_score": round(self.immediate_score, 3),
            "lookahead_score": round(self.lookahead_score, 3),
            "learned_bonus": round(self.learned_bonus, 3),
            "total_score": round(self.total_score, 3),
            "learned_q": round(self.learned_q, 4),
            "visits": self.visits,
            "features": self.features,
            "principal_variation": self.principal_variation,
        }


def _movable_sequence(cards: list[VisibleCard], start: int) -> bool:
    if start < 0 or start >= len(cards):
        return False
    for i in range(start, len(cards) - 1):
        upper = cards[i]
        lower = cards[i + 1]
        if upper.value != lower.value + 1:
            return False
        if upper.suit != lower.suit:
            return False
    return True


def _complete_run(cards: list[VisibleCard]) -> bool:
    if len(cards) < 13:
        return False
    tail = cards[-13:]
    if tail[0].rank != "K" or tail[-1].rank != "A":
        return False
    suit = tail[0].suit
    return all(
        tail[i].suit == suit
        and tail[i].value == 13 - i
        for i in range(13)
    )


def generate_actions(state: SpiderState) -> list[SpiderAction]:
    actions: list[SpiderAction] = []
    aid = 0

    for src in state.columns:
        if not src.cards:
            continue

        for start in range(len(src.cards)):
            if not _movable_sequence(src.cards, start):
                continue
            moving_cards = src.cards[start:]
            first = moving_cards[0]

            for dst in state.columns:
                if dst.index == src.index:
                    continue
                if dst.cards:
                    target = dst.cards[-1]
                    if target.value != first.value + 1:
                        continue
                # Spider lets a packed sequence move into an empty column.
                # Moving an entire known column to an empty column with no reveal
                # creates no new information and usually just loops; suppress it.
                if not dst.cards and start == 0 and not src.hidden_above:
                    continue

                moving = [(c.rank, c.suit) for c in moving_cards]
                reveal = bool(start == 0 and src.hidden_above)
                source_empty = bool(start == 0 and not src.hidden_above)
                same_suit_join = bool(dst.cards and dst.cards[-1].suit == first.suit)

                score = 0.0
                score += 42.0 if reveal else 0.0
                score += 10.0 if same_suit_join else 0.0
                score += min(8.0, len(moving_cards) * 1.5)
                score += 7.0 if source_empty else 0.0
                # Empty columns are valuable resources. Do not waste one just
                # to park a card/sequence unless the move exposes a hidden card.
                if not dst.cards:
                    score -= 34.0
                    if reveal:
                        score += 24.0
                    if len(moving_cards) == 1:
                        score -= 10.0
                score -= 5.0 if not dst.cards and first.rank == "K" and not reveal else 0.0

                # Estimate whether the destination forms a full K..A run.
                merged = list(dst.cards) + list(moving_cards)
                completes = _complete_run(merged)
                if completes:
                    score += 120.0

                features = {
                    "reveal_hidden": 1.0 if reveal else 0.0,
                    "same_suit_join": 1.0 if same_suit_join else 0.0,
                    "source_empty": 1.0 if source_empty else 0.0,
                    "sequence_length": min(1.0, len(moving_cards) / 13.0),
                    "complete_run": 1.0 if completes else 0.0,
                    "empty_destination": 1.0 if not dst.cards else 0.0,
                }

                actions.append(
                    SpiderAction(
                        id=f"a{aid}",
                        kind="move",
                        source=src.index,
                        destination=dst.index,
                        start_index=start,
                        moving=moving,
                        immediate_score=score,
                        features=features,
                    )
                )
                aid += 1

    # Strategy guard: if a normal non-empty destination exists, do not let
    # the decision model waste an empty column for a move that reveals nothing.
    # Empty-column moves that expose a hidden card remain available.
    nonempty_moves = [
        a for a in actions
        if a.kind == "move" and not a.features.get("empty_destination", 0.0)
    ]
    if nonempty_moves:
        actions = [
            a for a in actions
            if not (
                a.kind == "move"
                and a.features.get("empty_destination", 0.0)
                and not a.features.get("reveal_hidden", 0.0)
            )
        ]

    # Deal is legal only when every tableau column contains at least one card.
    if state.stock_available and all(col.cards or col.hidden_above for col in state.columns):
        no_moves = not actions
        score = 18.0 if no_moves else -22.0
        actions.append(
            SpiderAction(
                id=f"a{aid}",
                kind="deal",
                immediate_score=score,
                features={
                    "deal": 1.0,
                    "reveal_hidden": 0.0,
                    "same_suit_join": 0.0,
                    "source_empty": 0.0,
                    "sequence_length": 0.0,
                    "complete_run": 0.0,
                    "empty_destination": 0.0,
                },
            )
        )

    return actions


@dataclass
class _SimColumn:
    cards: list[tuple[int, str]]
    hidden: bool


@dataclass
class _SimState:
    columns: list[_SimColumn]
    stock_available: bool


def _to_sim(state: SpiderState) -> _SimState:
    return _SimState(
        columns=[
            _SimColumn(
                cards=[(c.value, c.suit) for c in col.cards],
                hidden=col.hidden_above,
            )
            for col in state.columns
        ],
        stock_available=state.stock_available,
    )


def _sim_signature(s: _SimState) -> tuple:
    return tuple(
        (tuple(col.cards), col.hidden)
        for col in s.columns
    ) + (s.stock_available,)


def _sim_actions(s: _SimState) -> list[tuple[int, int, int, float, str]]:
    out: list[tuple[int, int, int, float, str]] = []
    for si, src in enumerate(s.columns):
        cards = src.cards
        for start in range(len(cards)):
            ok = True
            for j in range(start, len(cards) - 1):
                if cards[j][0] != cards[j + 1][0] + 1 or cards[j][1] != cards[j + 1][1]:
                    ok = False
                    break
            if not ok:
                continue
            first_val, first_suit = cards[start]
            for di, dst in enumerate(s.columns):
                if si == di:
                    continue
                if dst.cards and dst.cards[-1][0] != first_val + 1:
                    continue
                if not dst.cards and start == 0 and not src.hidden:
                    continue
                reveal = start == 0 and src.hidden
                same = bool(dst.cards and dst.cards[-1][1] == first_suit)
                score = (42.0 if reveal else 0.0) + (10.0 if same else 0.0)
                score += min(8.0, (len(cards) - start) * 1.5)
                if not dst.cards:
                    score -= 34.0
                    if reveal:
                        score += 24.0
                    if (len(cards) - start) == 1:
                        score -= 10.0
                notation = f"C{si+1}->{di+1}"
                out.append((si, di, start, score, notation))
    return out


def _apply_sim(s: _SimState, action: tuple[int, int, int, float, str]) -> _SimState:
    si, di, start, _score, _notation = action
    cols = [_SimColumn(cards=list(c.cards), hidden=c.hidden) for c in s.columns]
    moving = cols[si].cards[start:]
    cols[si].cards = cols[si].cards[:start]
    cols[di].cards.extend(moving)

    if not cols[si].cards and cols[si].hidden:
        # We know a hidden card would be revealed, but not its identity.
        # Keep an unknown blocker by ending deterministic simulation on this
        # branch after rewarding the reveal.
        cols[si].hidden = False

    # Remove a completed same-suit K..A run from the simulation.
    if len(cols[di].cards) >= 13:
        tail = cols[di].cards[-13:]
        suit = tail[0][1]
        if all(tail[i][0] == 13 - i and tail[i][1] == suit for i in range(13)):
            del cols[di].cards[-13:]

    return _SimState(columns=cols, stock_available=s.stock_available)


def _best_future(
    s: _SimState,
    depth: int,
    visited: set[tuple],
) -> tuple[float, list[str]]:
    if depth <= 0:
        return 0.0, []

    sig = _sim_signature(s)
    if sig in visited:
        return -15.0, []

    actions = _sim_actions(s)
    if not actions:
        return (8.0 if s.stock_available else -30.0), []

    next_visited = set(visited)
    next_visited.add(sig)
    best_score = -10**9
    best_line: list[str] = []

    # Beam-limit the branch factor using immediate heuristic.
    actions = sorted(actions, key=lambda a: a[3], reverse=True)[:10]
    for action in actions:
        child = _apply_sim(s, action)
        future, line = _best_future(child, depth - 1, next_visited)
        total = action[3] + 0.78 * future
        if total > best_score:
            best_score = total
            best_line = [action[4]] + line

    return best_score, best_line


def add_lookahead(state: SpiderState, actions: list[SpiderAction], depth: int) -> list[SpiderAction]:
    depth = max(1, min(int(depth), 5))
    sim = _to_sim(state)

    for action in actions:
        if action.kind == "deal":
            action.lookahead_score = action.immediate_score
            action.principal_variation = ["STOCK"]
            continue

        assert action.source is not None
        assert action.destination is not None
        assert action.start_index is not None
        sim_action = (
            action.source,
            action.destination,
            action.start_index,
            action.immediate_score,
            action.notation(),
        )
        child = _apply_sim(sim, sim_action)
        future, line = _best_future(child, depth - 1, {_sim_signature(sim)})
        action.lookahead_score = action.immediate_score + 0.78 * future
        action.principal_variation = [action.notation()] + line

    return actions


def state_signature(state: SpiderState) -> str:
    parts: list[str] = []
    for col in state.columns:
        ranks = "".join(c.rank + c.suit for c in col.cards)
        parts.append(("H" if col.hidden_above else "-") + ranks)
    return "|".join(parts) + ("|stock" if state.stock_available else "|nostock")
