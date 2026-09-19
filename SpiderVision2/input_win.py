from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

try:
    import win32gui
except Exception:
    win32gui = None


class InputError(RuntimeError):
    pass


INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("union", INPUT_UNION),
    ]


def _send(flags: int, x: int = 0, y: int = 0) -> None:
    inp = INPUT(
        type=INPUT_MOUSE,
        union=INPUT_UNION(
            mi=MOUSEINPUT(
                dx=x,
                dy=y,
                mouseData=0,
                dwFlags=flags,
                time=0,
                dwExtraInfo=None,
            )
        ),
    )
    sent = ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
    if sent != 1:
        raise InputError("Windows SendInput hat die Mausaktion abgelehnt.")


def _absolute_point(screen_x: int, screen_y: int) -> tuple[int, int]:
    user32 = ctypes.windll.user32
    vx = user32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
    vy = user32.GetSystemMetrics(77)  # SM_YVIRTUALSCREEN
    vw = max(1, user32.GetSystemMetrics(78))
    vh = max(1, user32.GetSystemMetrics(79))
    ax = int(round((screen_x - vx) * 65535 / max(1, vw - 1)))
    ay = int(round((screen_y - vy) * 65535 / max(1, vh - 1)))
    return ax, ay


def _move_screen(screen_x: int, screen_y: int) -> None:
    ax, ay = _absolute_point(screen_x, screen_y)
    _send(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, ax, ay)


def drag_client(
    hwnd: int,
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    duration: float = 0.8,
    steps: int = 45,
) -> None:
    if win32gui is None:
        raise InputError("pywin32 ist nicht verfügbar.")

    sx, sy = win32gui.ClientToScreen(hwnd, (int(round(start[0])), int(round(start[1]))))
    ex, ey = win32gui.ClientToScreen(hwnd, (int(round(end[0])), int(round(end[1]))))

    _move_screen(sx, sy)
    time.sleep(0.07)
    _send(MOUSEEVENTF_LEFTDOWN)

    try:
        for i in range(1, max(12, steps) + 1):
            t = i / max(12, steps)
            u = t * t * (3.0 - 2.0 * t)
            x = int(round(sx + (ex - sx) * u))
            y = int(round(sy + (ey - sy) * u))
            _move_screen(x, y)
            time.sleep(max(0.004, duration / max(12, steps)))
    finally:
        _send(MOUSEEVENTF_LEFTUP)
