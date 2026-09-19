from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Iterable
import copy

EMPTY = "."
BLACK = "black"
RED = "red"


@dataclass(frozen=True)
class Move:
    id: str
    start: tuple[int, int]
    path: tuple[tuple[int, int], ...]
    captures: tuple[tuple[int, int], ...]
    piece: str
    promotes: bool = False

    @property
    def end(self) -> tuple[int, int]:
        return self.path[-1]

    def to_dict(self) -> dict:
        data = asdict(self)
        data["start"] = list(self.start)
        data["path"] = [list(x) for x in self.path]
        data["captures"] = [list(x) for x in self.captures]
        data["end"] = list(self.end)
        return data


def initial_board() -> list[list[str]]:
    board = [[EMPTY for _ in range(8)] for _ in range(8)]
    for r in range(3):
        for c in range(8):
            if (r + c) % 2 == 1:
                board[r][c] = "r"
    for r in range(5, 8):
        for c in range(8):
            if (r + c) % 2 == 1:
                board[r][c] = "b"
    return board


def opponent(side: str) -> str:
    return RED if side == BLACK else BLACK


def belongs(piece: str, side: str) -> bool:
    return piece != EMPTY and piece.lower() == ("b" if side == BLACK else "r")


def is_king(piece: str) -> bool:
    return piece in ("B", "R")


def directions(piece: str) -> tuple[tuple[int, int], ...]:
    if is_king(piece):
        return ((-1, -1), (-1, 1), (1, -1), (1, 1))
    if piece == "b":
        return ((-1, -1), (-1, 1))
    return ((1, -1), (1, 1))


def inside(r: int, c: int) -> bool:
    return 0 <= r < 8 and 0 <= c < 8


def promotion_row(piece: str, row: int) -> bool:
    return (piece == "b" and row == 0) or (piece == "r" and row == 7)


def _capture_sequences(
    board: list[list[str]],
    start: tuple[int, int],
    current: tuple[int, int],
    piece: str,
    path: tuple[tuple[int, int], ...],
    captures: tuple[tuple[int, int], ...],
) -> list[tuple[tuple[tuple[int, int], ...], tuple[tuple[int, int], ...], bool]]:
    r, c = current
    found = []
    extended = False

    for dr, dc in directions(piece):
        mr, mc = r + dr, c + dc
        lr, lc = r + 2 * dr, c + 2 * dc
        if not inside(lr, lc) or not inside(mr, mc):
            continue
        middle = board[mr][mc]
        if middle == EMPTY or middle.lower() == piece.lower():
            continue
        if board[lr][lc] != EMPTY:
            continue

        extended = True
        next_board = copy.deepcopy(board)
        next_board[r][c] = EMPTY
        next_board[mr][mc] = EMPTY
        next_board[lr][lc] = piece

        next_path = path + ((lr, lc),)
        next_captures = captures + ((mr, mc),)

        # In this compact rule set, reaching the back rank ends the capture
        # sequence and promotes the man.
        if not is_king(piece) and promotion_row(piece, lr):
            found.append((next_path, next_captures, True))
            continue

        found.extend(
            _capture_sequences(
                next_board,
                start,
                (lr, lc),
                piece,
                next_path,
                next_captures,
            )
        )

    if not extended and captures:
        found.append((path, captures, False))

    return found


def legal_moves(board: list[list[str]], side: str) -> list[Move]:
    captures: list[Move] = []

    for r in range(8):
        for c in range(8):
            piece = board[r][c]
            if not belongs(piece, side):
                continue
            seqs = _capture_sequences(board, (r, c), (r, c), piece, tuple(), tuple())
            for path, taken, promotes in seqs:
                captures.append(
                    Move(
                        id="",
                        start=(r, c),
                        path=path,
                        captures=taken,
                        piece=piece,
                        promotes=promotes,
                    )
                )

    raw_moves: list[Move]
    if captures:
        raw_moves = captures
    else:
        raw_moves = []
        for r in range(8):
            for c in range(8):
                piece = board[r][c]
                if not belongs(piece, side):
                    continue
                for dr, dc in directions(piece):
                    nr, nc = r + dr, c + dc
                    if inside(nr, nc) and board[nr][nc] == EMPTY:
                        raw_moves.append(
                            Move(
                                id="",
                                start=(r, c),
                                path=((nr, nc),),
                                captures=tuple(),
                                piece=piece,
                                promotes=(not is_king(piece) and promotion_row(piece, nr)),
                            )
                        )

    result = []
    for i, move in enumerate(raw_moves):
        result.append(
            Move(
                id=f"m{i}",
                start=move.start,
                path=move.path,
                captures=move.captures,
                piece=move.piece,
                promotes=move.promotes,
            )
        )
    return result


def apply_move(board: list[list[str]], move: Move) -> list[list[str]]:
    new_board = copy.deepcopy(board)
    piece = new_board[move.start[0]][move.start[1]]
    new_board[move.start[0]][move.start[1]] = EMPTY

    for r, c in move.captures:
        new_board[r][c] = EMPTY

    er, ec = move.end
    if not is_king(piece) and promotion_row(piece, er):
        piece = piece.upper()
    new_board[er][ec] = piece
    return new_board


def material(board: list[list[str]], side: str) -> float:
    own = 0.0
    enemy = 0.0
    for row in board:
        for piece in row:
            if piece == EMPTY:
                continue
            value = 1.75 if is_king(piece) else 1.0
            if belongs(piece, side):
                own += value
            else:
                enemy += value
    return own - enemy


def mobility(board: list[list[str]], side: str) -> int:
    return len(legal_moves(board, side))


def board_ascii(board: list[list[str]]) -> str:
    legend = {EMPTY: ".", "b": "b", "B": "B", "r": "r", "R": "R"}
    rows = []
    for r, row in enumerate(board):
        rows.append(f"{r}: " + " ".join(legend[p] for p in row))
    return "\n".join(rows)


def winner(board: list[list[str]], side_to_move: str) -> str | None:
    if legal_moves(board, side_to_move):
        return None
    return opponent(side_to_move)


def move_notation(move: Move) -> str:
    def sq(pos: tuple[int, int]) -> str:
        r, c = pos
        return f"{chr(ord('a') + c)}{8-r}"

    sep = "x" if move.captures else "-"
    return sep.join([sq(move.start)] + [sq(p) for p in move.path])
