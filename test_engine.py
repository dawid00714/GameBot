import engine


def test_initial_position_has_moves():
    board = engine.initial_board()
    moves = engine.legal_moves(board, engine.BLACK)
    assert len(moves) > 0
    assert all(not m.captures for m in moves)


def test_capture_is_mandatory():
    board = [[engine.EMPTY for _ in range(8)] for _ in range(8)]
    board[5][0] = "b"
    board[4][1] = "r"
    board[5][4] = "b"

    moves = engine.legal_moves(board, engine.BLACK)
    assert len(moves) == 1
    assert moves[0].start == (5, 0)
    assert moves[0].end == (3, 2)
    assert len(moves[0].captures) == 1


def test_promotion():
    board = [[engine.EMPTY for _ in range(8)] for _ in range(8)]
    board[1][2] = "b"
    move = engine.legal_moves(board, engine.BLACK)[0]
    assert move.promotes
    after = engine.apply_move(board, move)
    assert after[0][move.end[1]] == "B"
