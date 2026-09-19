from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from vision import BoardObservation, RANK_VALUE


@dataclass
class Move:
    source: int
    destination: int
    start_index: int
    moving: list[str]
    score: float
    reason: str

    def notation(self) -> str:
        head = self.moving[0] if self.moving else "?"
        tail = self.moving[-1] if self.moving else "?"
        seq = head if len(self.moving) == 1 else f"{head}..{tail}"
        return f"C{self.source + 1}:{seq}->C{self.destination + 1}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "destination": self.destination,
            "start_index": self.start_index,
            "moving": list(self.moving),
            "score": round(self.score, 3),
            "reason": self.reason,
            "notation": self.notation(),
        }


def _descending(seq: list[str]) -> bool:
    if not seq:
        return False
    try:
        values = [RANK_VALUE[x] for x in seq]
    except KeyError:
        return False
    return all(values[i] == values[i + 1] + 1 for i in range(len(values) - 1))


def legal_moves(board: BoardObservation) -> list[Move]:
    if not board.complete:
        return []

    out: list[Move] = []

    for source_col in board.columns:
        ranks = [c.rank for c in source_col.cards]
        if not ranks:
            continue

        for start in range(len(ranks)):
            moving = ranks[start:]
            if not _descending(moving):
                continue

            head_value = RANK_VALUE[moving[0]]

            for dest_col in board.columns:
                if dest_col.index == source_col.index:
                    continue

                if dest_col.cards:
                    dest_rank = dest_col.cards[-1].rank
                    if dest_rank not in RANK_VALUE:
                        continue
                    if RANK_VALUE[dest_rank] != head_value + 1:
                        continue
                else:
                    if not dest_col.empty_confident:
                        continue

                score = 0.0
                reasons: list[str] = []

                if start == 0 and source_col.hidden_above:
                    score += 50.0
                    reasons.append("deckt verdeckte Karte auf")

                score += len(moving) * 4.0
                if len(moving) > 1:
                    reasons.append(f"verschiebt Folge mit {len(moving)} Karten")

                if dest_col.cards:
                    score += 10.0
                    reasons.append("baut absteigende Folge")
                else:
                    score -= 18.0
                    reasons.append("nutzt leere Spalte")
                    if start == 0 and source_col.hidden_above:
                        score += 15.0

                # Avoid moving a complete visible run away from a useful stack
                # unless it reveals a hidden card.
                if start == 0 and not source_col.hidden_above and len(source_col.cards) == len(moving):
                    score -= 8.0

                out.append(
                    Move(
                        source=source_col.index,
                        destination=dest_col.index,
                        start_index=start,
                        moving=moving,
                        score=score,
                        reason=", ".join(reasons) or "legaler Spider-Zug",
                    )
                )

    out.sort(key=lambda m: m.score, reverse=True)
    return out


def best_move(board: BoardObservation) -> Move | None:
    moves = legal_moves(board)
    return moves[0] if moves else None
