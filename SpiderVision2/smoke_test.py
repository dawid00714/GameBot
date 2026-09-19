from __future__ import annotations

import numpy as np

from solver import legal_moves
from vision import (
    BoardObservation,
    CardObservation,
    ColumnObservation,
    SpiderVision,
)


def card(rank: str, x: float = 100.0, y: float = 200.0) -> CardObservation:
    return CardObservation(
        rank=rank,
        confidence=0.99,
        x=x,
        y=y,
        patch_box=(0, 0, 20, 30),
        source="test",
    )


def main() -> None:
    # Always exactly ten columns.
    centers = SpiderVision.column_centers(1536)
    assert len(centers) == 10
    assert all(centers[i] < centers[i + 1] for i in range(9))

    cols = [
        ColumnObservation(index=i, x=centers[i], status="empty", empty_confident=True)
        for i in range(10)
    ]
    cols[0] = ColumnObservation(
        index=0,
        x=centers[0],
        cards=[card("6", centers[0])],
        status="recognized",
    )
    cols[1] = ColumnObservation(
        index=1,
        x=centers[1],
        cards=[card("7", centers[1])],
        status="recognized",
    )
    cols[2] = ColumnObservation(
        index=2,
        x=centers[2],
        cards=[card("K", centers[2])],
        status="recognized",
    )

    board = BoardObservation(
        width=1536,
        height=822,
        columns=cols,
        complete=True,
        uncertain_cards=0,
        diagnostics=[],
    )
    moves = legal_moves(board)
    notes = {m.notation() for m in moves}

    assert any(note.startswith("C1:6->C2") for note in notes)
    assert not any(note.startswith("C1:6->C3") for note in notes), notes

    # Vision should never throw on a blank frame; it should simply fail closed.
    blank = np.zeros((822, 1536, 3), dtype=np.uint8)
    vision = SpiderVision()
    detected, annotated = vision.detect(blank)
    assert len(detected.columns) == 10
    assert not detected.complete
    assert annotated.shape == blank.shape

    print("SpiderVision2 smoke test OK")


if __name__ == "__main__":
    main()
