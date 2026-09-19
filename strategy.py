from __future__ import annotations

import math
from typing import Any

import engine

WIN_SCORE = 100000.0


def board_key(board: list[list[str]], side_to_move: str) -> str:
    return "".join("".join(row) for row in board) + "|" + side_to_move


def _center_value(r: int, c: int) -> float:
    return max(0.0, 7.0 - (abs(3.5 - r) + abs(3.5 - c)))


def static_eval(board: list[list[str]], perspective: str) -> float:
    score = 0.0
    for r, row in enumerate(board):
        for c, piece in enumerate(row):
            if piece == engine.EMPTY:
                continue
            sign = 1.0 if engine.belongs(piece, perspective) else -1.0
            value = 175.0 if engine.is_king(piece) else 100.0
            center = _center_value(r, c) * 2.0
            advancement = 0.0
            if not engine.is_king(piece):
                advancement = ((7 - r) if piece.lower() == "b" else r) * 1.5
            score += sign * (value + center + advancement)

    # Mobility is deliberately a smaller signal than material.
    own_mobility = len(engine.legal_moves(board, perspective))
    opp_mobility = len(engine.legal_moves(board, engine.opponent(perspective)))
    score += (own_mobility - opp_mobility) * 3.0
    return score


def _minimax(
    board: list[list[str]],
    turn: str,
    perspective: str,
    depth: int,
    alpha: float,
    beta: float,
) -> tuple[float, list[str]]:
    moves = engine.legal_moves(board, turn)
    if not moves:
        if turn == perspective:
            return -WIN_SCORE - depth, []
        return WIN_SCORE + depth, []

    if depth <= 0:
        return static_eval(board, perspective), []

    maximizing = turn == perspective
    best_score = -math.inf if maximizing else math.inf
    best_line: list[str] = []

    for move in moves:
        after = engine.apply_move(board, move)
        child_score, child_line = _minimax(
            after,
            engine.opponent(turn),
            perspective,
            depth - 1,
            alpha,
            beta,
        )
        line = [engine.move_notation(move)] + child_line

        if maximizing:
            if child_score > best_score:
                best_score = child_score
                best_line = line
            alpha = max(alpha, best_score)
        else:
            if child_score < best_score:
                best_score = child_score
                best_line = line
            beta = min(beta, best_score)

        if beta <= alpha:
            break

    return best_score, best_line


def analyze_moves(
    board: list[list[str]],
    side: str,
    depth: int = 3,
) -> list[dict[str, Any]]:
    depth = max(1, min(int(depth), 6))
    moves = engine.legal_moves(board, side)
    analyses: list[dict[str, Any]] = []
    enemy = engine.opponent(side)

    for move in moves:
        after = engine.apply_move(board, move)
        replies = engine.legal_moves(after, enemy)
        max_reply_captures = max((len(m.captures) for m in replies), default=0)
        lookahead_score, pv = _minimax(
            after,
            enemy,
            side,
            depth - 1,
            -math.inf,
            math.inf,
        )

        er, ec = move.end
        analyses.append(
            {
                "move": move,
                "id": move.id,
                "notation": engine.move_notation(move),
                "captures": len(move.captures),
                "promotes": bool(move.promotes),
                "material_after": round(engine.material(after, side), 3),
                "opponent_replies": len(replies),
                "opponent_max_capture_next": max_reply_captures,
                "own_future_mobility": len(engine.legal_moves(after, side)),
                "center_value": round(_center_value(er, ec), 3),
                "lookahead_score": round(float(lookahead_score), 3),
                "principal_variation": pv[: max(0, depth - 1)],
                "search_depth": depth,
            }
        )

    return analyses


def learning_features(analysis: dict[str, Any]) -> dict[str, float]:
    """Small normalized feature vector used by the online learner."""
    return {
        "capture": min(float(analysis["captures"]) / 3.0, 1.0),
        "promotion": 1.0 if analysis["promotes"] else 0.0,
        "material": max(-1.0, min(1.0, float(analysis["material_after"]) / 5.0)),
        "mobility": max(-1.0, min(1.0, (float(analysis["own_future_mobility"]) - 6.0) / 10.0)),
        "reply_risk": -min(float(analysis["opponent_max_capture_next"]) / 3.0, 1.0),
        "center": max(0.0, min(1.0, float(analysis["center_value"]) / 7.0)),
        "lookahead": math.tanh(float(analysis["lookahead_score"]) / 300.0),
    }
