from __future__ import annotations

import ctypes
import time
from dataclasses import dataclass
from typing import Any

import numpy as np

try:
    import win32api
    import win32con
    import win32gui
    import win32ui
except Exception:  # imported on non-Windows during static inspection
    win32api = None
    win32con = None
    win32gui = None
    win32ui = None


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
    rect = win32gui.GetClientRect(hwnd)
    right_bottom = win32gui.ClientToScreen(hwnd, (max(0, rect[2]), max(0, rect[3])))
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
            if info.width >= 300 and info.height >= 200:
                result.append(info)
        except Exception:
            pass
        return True

    win32gui.EnumWindows(callback, None)
    result.sort(key=lambda x: ("solitaire" not in x.title.lower(), x.title.lower()))
    return result


class WindowController:
    """Capture and control one Windows window without touching the real mouse.

    HARD GUARANTEE FOR INPUT:
    - no SetCursorPos
    - no mouse_event / SendInput
    - no SetForegroundWindow / BringWindowToTop
    - no activation of the selected game window

    Input is delivered only as WM_MOUSE* messages to the selected HWND or the
    most specific child HWND that covers the drag path. If the game refuses
    background window messages, the action fails visibly instead of stealing
    the user's physical mouse.
    """

    def __init__(self, hwnd: int):
        _require_windows()
        self.hwnd = int(hwnd)
        self.last_input_target: dict[str, Any] | None = None

    @property
    def info(self) -> WindowInfo:
        return _client_geometry(self.hwnd)

    def is_minimized(self) -> bool:
        return bool(win32gui.IsIconic(self.hwnd))

    def _capture_printwindow(self) -> np.ndarray | None:
        """Try to render the HWND independently of screen occlusion/focus."""
        if win32ui is None:
            return None

        hwnd_dc = None
        src_dc = None
        mem_dc = None
        bitmap = None
        try:
            wl, wt, wr, wb = win32gui.GetWindowRect(self.hwnd)
            full_w = max(1, wr - wl)
            full_h = max(1, wb - wt)
            info = self.info
            client_left, client_top = win32gui.ClientToScreen(self.hwnd, (0, 0))
            ox = max(0, client_left - wl)
            oy = max(0, client_top - wt)

            hwnd_dc = win32gui.GetWindowDC(self.hwnd)
            src_dc = win32ui.CreateDCFromHandle(hwnd_dc)
            mem_dc = src_dc.CreateCompatibleDC()
            bitmap = win32ui.CreateBitmap()
            bitmap.CreateCompatibleBitmap(src_dc, full_w, full_h)
            mem_dc.SelectObject(bitmap)

            rendered = ctypes.windll.user32.PrintWindow(
                self.hwnd,
                mem_dc.GetSafeHdc(),
                2,  # PW_RENDERFULLCONTENT
            )

            bits = bitmap.GetBitmapBits(True)
            frame = np.frombuffer(bits, dtype=np.uint8)
            frame.shape = (full_h, full_w, 4)
            frame = frame[:, :, :3].copy()

            crop = frame[
                oy:min(full_h, oy + info.height),
                ox:min(full_w, ox + info.width),
            ]
            if (
                rendered
                and crop.shape[0] >= max(50, info.height - 4)
                and crop.shape[1] >= max(50, info.width - 4)
                and float(crop.std()) > 4.0
            ):
                return crop
        except Exception:
            return None
        finally:
            try:
                if mem_dc is not None:
                    mem_dc.DeleteDC()
            except Exception:
                pass
            try:
                if src_dc is not None:
                    src_dc.DeleteDC()
            except Exception:
                pass
            try:
                if hwnd_dc is not None:
                    win32gui.ReleaseDC(self.hwnd, hwnd_dc)
            except Exception:
                pass
            try:
                if bitmap is not None:
                    win32gui.DeleteObject(bitmap.GetHandle())
            except Exception:
                pass
        return None

    def capture(self) -> np.ndarray:
        frame = self._capture_printwindow()
        if frame is not None:
            return frame

        # DirectX/WinUI can refuse PrintWindow. Screen capture is then the only
        # read-only fallback; it still does NOT move or activate the game.
        if self.is_minimized():
            raise WindowAutomationError(
                "Das Spiel ist minimiert und unterstützt PrintWindow nicht. "
                "Für die Bilderkennung muss es sichtbar sein."
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
        return frame[:, :, :3].copy()

    @staticmethod
    def _contains(rect: tuple[int, int, int, int], p: tuple[int, int]) -> bool:
        l, t, r, b = rect
        return l <= p[0] < r and t <= p[1] < b

    def _background_target(
        self,
        start_client: tuple[float, float],
        end_client: tuple[float, float] | None = None,
    ) -> int:
        """Pick the smallest child HWND covering the whole input path."""
        sx, sy = int(round(start_client[0])), int(round(start_client[1]))
        start_screen = win32gui.ClientToScreen(self.hwnd, (sx, sy))
        if end_client is None:
            end_screen = start_screen
        else:
            ex, ey = int(round(end_client[0])), int(round(end_client[1]))
            end_screen = win32gui.ClientToScreen(self.hwnd, (ex, ey))

        candidates: list[tuple[int, int]] = []

        def consider(hwnd: int) -> None:
            try:
                if not win32gui.IsWindowVisible(hwnd) or not win32gui.IsWindowEnabled(hwnd):
                    return
                rect = win32gui.GetWindowRect(hwnd)
                if not self._contains(rect, start_screen) or not self._contains(rect, end_screen):
                    return
                l, t, r, b = rect
                area = max(1, (r - l) * (b - t))
                candidates.append((area, hwnd))
            except Exception:
                return

        consider(self.hwnd)

        try:
            win32gui.EnumChildWindows(self.hwnd, lambda child, _extra: (consider(child), True)[1], None)
        except Exception:
            pass

        target = min(candidates, key=lambda item: item[0])[1] if candidates else self.hwnd
        try:
            cls = win32gui.GetClassName(target)
        except Exception:
            cls = ""
        try:
            title = win32gui.GetWindowText(target)
        except Exception:
            title = ""
        self.last_input_target = {
            "hwnd": int(target),
            "class": cls,
            "title": title,
        }
        return target

    @staticmethod
    def _lparam_for_target(target: int, screen_point: tuple[int, int]) -> int:
        x, y = win32gui.ScreenToClient(target, screen_point)
        return win32api.MAKELONG(int(x), int(y))

    def _post_mouse(
        self,
        target: int,
        msg: int,
        wparam: int,
        screen_point: tuple[int, int],
    ) -> None:
        lp = self._lparam_for_target(target, screen_point)
        if not win32gui.PostMessage(target, msg, wparam, lp):
            raise WindowAutomationError(
                f"WM_MOUSE-Nachricht {msg} konnte nicht an HWND {target} gesendet werden."
            )

    def virtual_click(self, x: float, y: float, hold_ms: int = 70) -> None:
        point_client = (float(x), float(y))
        target = self._background_target(point_client)
        screen = win32gui.ClientToScreen(self.hwnd, (int(round(x)), int(round(y))))

        self._post_mouse(target, win32con.WM_MOUSEMOVE, 0, screen)
        self._post_mouse(target, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, screen)
        time.sleep(max(0.02, hold_ms / 1000.0))
        self._post_mouse(target, win32con.WM_LBUTTONUP, 0, screen)

    def virtual_drag(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        duration_ms: int = 520,
        steps: int = 34,
    ) -> None:
        """Background-only drag: down -> held moves -> up, no physical cursor."""
        target = self._background_target(start, end)
        sx, sy = start
        ex, ey = end
        steps = max(8, int(steps))
        delay = max(0.003, duration_ms / 1000.0 / steps)

        start_screen = win32gui.ClientToScreen(
            self.hwnd,
            (int(round(sx)), int(round(sy))),
        )
        self._post_mouse(target, win32con.WM_MOUSEMOVE, 0, start_screen)
        self._post_mouse(
            target,
            win32con.WM_LBUTTONDOWN,
            win32con.MK_LBUTTON,
            start_screen,
        )
        time.sleep(delay)

        for i in range(1, steps + 1):
            t = i / steps
            u = t * t * (3.0 - 2.0 * t)
            x = int(round(sx + (ex - sx) * u))
            y = int(round(sy + (ey - sy) * u))
            screen = win32gui.ClientToScreen(self.hwnd, (x, y))
            self._post_mouse(
                target,
                win32con.WM_MOUSEMOVE,
                win32con.MK_LBUTTON,
                screen,
            )
            time.sleep(delay)

        end_screen = win32gui.ClientToScreen(
            self.hwnd,
            (int(round(ex)), int(round(ey))),
        )
        self._post_mouse(target, win32con.WM_LBUTTONUP, 0, end_screen)

    def normalized_to_client(self, nx: float, ny: float) -> tuple[int, int]:
        info = self.info
        nx = min(1.0, max(0.0, float(nx)))
        ny = min(1.0, max(0.0, float(ny)))
        return int(nx * info.width), int(ny * info.height)

    def click_normalized(self, nx: float, ny: float) -> None:
        x, y = self.normalized_to_client(nx, ny)
        self.virtual_click(x, y)

    def input_status(self) -> dict[str, Any]:
        return {
            "mode": "background_only",
            "physical_mouse_touched": False,
            "foreground_changed": False,
            "target": self.last_input_target,
        }


def frame_difference(before: np.ndarray, after: np.ndarray) -> float:
    if before.shape != after.shape:
        return 1.0
    a = before.astype(np.int16)
    b = after.astype(np.int16)
    return float(np.abs(a - b).mean() / 255.0)
