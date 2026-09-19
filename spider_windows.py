from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import numpy as np

try:
    import win32api
    import win32con
    import win32gui
except Exception:  # imported on non-Windows during static inspection
    win32api = None
    win32con = None
    win32gui = None


class WindowAutomationError(RuntimeError):
    pass


@dataclass
class WindowInfo:
    hwnd: int
    title: str
    left: int
    top: int
    width: int
    height: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "hwnd": self.hwnd,
            "title": self.title,
            "left": self.left,
            "top": self.top,
            "width": self.width,
            "height": self.height,
        }


def _require_windows() -> None:
    if win32gui is None:
        raise WindowAutomationError("Windows-Automation ist nur unter Windows verfügbar.")


def _client_geometry(hwnd: int) -> WindowInfo:
    _require_windows()
    if not win32gui.IsWindow(hwnd):
        raise WindowAutomationError(f"Fenster {hwnd} existiert nicht mehr.")
    title = win32gui.GetWindowText(hwnd)
    left_top = win32gui.ClientToScreen(hwnd, (0, 0))
    right_bottom = win32gui.ClientToScreen(
        hwnd,
        (
            max(0, win32gui.GetClientRect(hwnd)[2]),
            max(0, win32gui.GetClientRect(hwnd)[3]),
        ),
    )
    left, top = left_top
    right, bottom = right_bottom
    return WindowInfo(
        hwnd=hwnd,
        title=title,
        left=left,
        top=top,
        width=max(1, right - left),
        height=max(1, bottom - top),
    )


def list_windows(title_contains: str | None = None) -> list[WindowInfo]:
    _require_windows()
    result: list[WindowInfo] = []
    needle = (title_contains or "").strip().lower()

    def callback(hwnd: int, _extra: Any) -> bool:
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            title = win32gui.GetWindowText(hwnd).strip()
            if not title:
                return True
            if needle and needle not in title.lower():
                return True
            info = _client_geometry(hwnd)
            # Ignore tiny utility windows.
            if info.width >= 300 and info.height >= 200:
                result.append(info)
        except Exception:
            pass
        return True

    win32gui.EnumWindows(callback, None)
    result.sort(key=lambda x: ("solitaire" not in x.title.lower(), x.title.lower()))
    return result


class WindowController:
    """Capture and control one Windows game window.

    The default input method sends mouse messages directly to the selected HWND.
    This is a *virtual/background mouse*: it does not move the user's physical
    pointer. Some games deliberately ignore posted mouse messages. In that case
    the UI reports that the action was not observed rather than silently taking
    over the real pointer.
    """

    def __init__(self, hwnd: int):
        _require_windows()
        self.hwnd = int(hwnd)

    @property
    def info(self) -> WindowInfo:
        return _client_geometry(self.hwnd)

    def is_minimized(self) -> bool:
        return bool(win32gui.IsIconic(self.hwnd))

    def capture(self) -> np.ndarray:
        if self.is_minimized():
            raise WindowAutomationError(
                "Das ausgewählte Spiel ist minimiert. Für die Bilderkennung muss es sichtbar sein."
            )
        info = self.info
        try:
            import mss
        except Exception as exc:
            raise WindowAutomationError(f"mss fehlt: {exc}") from exc

        with mss.mss() as sct:
            frame = np.asarray(
                sct.grab(
                    {
                        "left": info.left,
                        "top": info.top,
                        "width": info.width,
                        "height": info.height,
                    }
                )
            )
        # mss = BGRA; keep BGR for OpenCV.
        return frame[:, :, :3].copy()

    @staticmethod
    def _lparam(x: int, y: int) -> int:
        return win32api.MAKELONG(int(x), int(y))

    def virtual_click(self, x: float, y: float, hold_ms: int = 55) -> None:
        x_i, y_i = int(round(x)), int(round(y))
        lp = self._lparam(x_i, y_i)
        win32gui.PostMessage(self.hwnd, win32con.WM_MOUSEMOVE, 0, lp)
        win32gui.PostMessage(self.hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lp)
        time.sleep(max(0.01, hold_ms / 1000.0))
        win32gui.PostMessage(self.hwnd, win32con.WM_LBUTTONUP, 0, lp)

    def virtual_drag(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        duration_ms: int = 380,
        steps: int = 24,
    ) -> None:
        """Press left mouse, keep it held while moving, release at destination."""
        sx, sy = start
        ex, ey = end
        steps = max(4, int(steps))
        delay = max(0.002, duration_ms / 1000.0 / steps)

        start_lp = self._lparam(int(round(sx)), int(round(sy)))
        win32gui.PostMessage(self.hwnd, win32con.WM_MOUSEMOVE, 0, start_lp)
        win32gui.PostMessage(
            self.hwnd,
            win32con.WM_LBUTTONDOWN,
            win32con.MK_LBUTTON,
            start_lp,
        )
        time.sleep(delay)

        for i in range(1, steps + 1):
            t = i / steps
            # Smoothstep makes the drag more similar to a human pointer motion.
            u = t * t * (3.0 - 2.0 * t)
            x = sx + (ex - sx) * u
            y = sy + (ey - sy) * u
            lp = self._lparam(int(round(x)), int(round(y)))
            win32gui.PostMessage(
                self.hwnd,
                win32con.WM_MOUSEMOVE,
                win32con.MK_LBUTTON,
                lp,
            )
            time.sleep(delay)

        end_lp = self._lparam(int(round(ex)), int(round(ey)))
        win32gui.PostMessage(self.hwnd, win32con.WM_LBUTTONUP, 0, end_lp)

    def normalized_to_client(self, nx: float, ny: float) -> tuple[int, int]:
        info = self.info
        nx = min(1.0, max(0.0, float(nx)))
        ny = min(1.0, max(0.0, float(ny)))
        return int(nx * info.width), int(ny * info.height)

    def click_normalized(self, nx: float, ny: float) -> None:
        x, y = self.normalized_to_client(nx, ny)
        self.virtual_click(x, y)


def frame_difference(before: np.ndarray, after: np.ndarray) -> float:
    if before.shape != after.shape:
        return 1.0
    a = before.astype(np.int16)
    b = after.astype(np.int16)
    # Mean absolute difference normalized to 0..1.
    return float(np.abs(a - b).mean() / 255.0)
