from __future__ import annotations

import ctypes
from dataclasses import dataclass
from typing import Any

import numpy as np

try:
    import win32gui
    import win32ui
except Exception:
    win32gui = None
    win32ui = None


class CaptureError(RuntimeError):
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
        raise CaptureError("SpiderVision2 läuft nur unter Windows.")


def window_info(hwnd: int) -> WindowInfo:
    _require_windows()
    if not win32gui.IsWindow(hwnd):
        raise CaptureError(f"Fenster {hwnd} existiert nicht mehr.")
    left, top = win32gui.ClientToScreen(hwnd, (0, 0))
    rect = win32gui.GetClientRect(hwnd)
    right, bottom = win32gui.ClientToScreen(hwnd, (rect[2], rect[3]))
    return WindowInfo(
        hwnd=int(hwnd),
        title=win32gui.GetWindowText(hwnd),
        left=int(left),
        top=int(top),
        width=max(1, int(right - left)),
        height=max(1, int(bottom - top)),
    )


def list_windows() -> list[WindowInfo]:
    _require_windows()
    out: list[WindowInfo] = []

    def callback(hwnd: int, _extra: Any) -> bool:
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            title = (win32gui.GetWindowText(hwnd) or "").strip()
            if not title:
                return True
            info = window_info(hwnd)
            if info.width >= 500 and info.height >= 300:
                out.append(info)
        except Exception:
            pass
        return True

    win32gui.EnumWindows(callback, None)
    out.sort(key=lambda w: (
        "solitaire" not in w.title.lower() and "casual games" not in w.title.lower(),
        w.title.lower(),
    ))
    return out


def _printwindow(hwnd: int) -> np.ndarray | None:
    if win32ui is None:
        return None

    hwnd_dc = src_dc = mem_dc = bitmap = None
    try:
        wl, wt, wr, wb = win32gui.GetWindowRect(hwnd)
        full_w = max(1, wr - wl)
        full_h = max(1, wb - wt)
        info = window_info(hwnd)
        client_left, client_top = win32gui.ClientToScreen(hwnd, (0, 0))
        ox = max(0, client_left - wl)
        oy = max(0, client_top - wt)

        hwnd_dc = win32gui.GetWindowDC(hwnd)
        src_dc = win32ui.CreateDCFromHandle(hwnd_dc)
        mem_dc = src_dc.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(src_dc, full_w, full_h)
        mem_dc.SelectObject(bitmap)

        rendered = ctypes.windll.user32.PrintWindow(
            hwnd,
            mem_dc.GetSafeHdc(),
            2,  # PW_RENDERFULLCONTENT
        )
        if not rendered:
            return None

        bits = bitmap.GetBitmapBits(True)
        frame = np.frombuffer(bits, dtype=np.uint8)
        frame.shape = (full_h, full_w, 4)
        frame = frame[:, :, :3].copy()

        crop = frame[
            oy:min(full_h, oy + info.height),
            ox:min(full_w, ox + info.width),
        ]
        if crop.size and float(crop.std()) > 4.0:
            return crop.copy()
        return None
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
                win32gui.ReleaseDC(hwnd, hwnd_dc)
        except Exception:
            pass
        try:
            if bitmap is not None:
                win32gui.DeleteObject(bitmap.GetHandle())
        except Exception:
            pass


def capture_window(hwnd: int) -> np.ndarray:
    _require_windows()
    frame = _printwindow(hwnd)
    if frame is not None:
        return frame

    if win32gui.IsIconic(hwnd):
        raise CaptureError(
            "Das Solitaire-Fenster ist minimiert. Für den Screenshot muss es sichtbar sein."
        )

    info = window_info(hwnd)
    try:
        import mss
    except Exception as exc:
        raise CaptureError(f"mss fehlt: {exc}") from exc

    with mss.mss() as sct:
        raw = np.asarray(sct.grab({
            "left": info.left,
            "top": info.top,
            "width": info.width,
            "height": info.height,
        }))
    return raw[:, :, :3].copy()
