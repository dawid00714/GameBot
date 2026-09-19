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
    ) -> int:
        """Post one mouse message without ever touching the system cursor.

        pywin32's PostMessage wrapper can return None on success and on some
        WinUI/UWP windows can surface the confusing
        "(0, 'PostMessage', 'No error message is available')" exception.
        Use the native BOOL-returning PostMessageW API instead and, when a
        selected child HWND rejects the message, retry against the root game
        HWND with coordinates converted for that HWND.
        """
        user32 = ctypes.windll.user32

        def post(hwnd: int) -> bool:
            lp = self._lparam_for_target(hwnd, screen_point)
            ctypes.set_last_error(0)
            ok = user32.PostMessageW(
                ctypes.c_void_p(int(hwnd)),
                ctypes.c_uint(int(msg)),
                ctypes.c_size_t(int(wparam)),
                ctypes.c_ssize_t(int(lp)),
            )
            return bool(ok)

        if post(target):
            return target

        if target != self.hwnd and post(self.hwnd):
            self.last_input_target = {
                "hwnd": int(self.hwnd),
                "class": win32gui.GetClassName(self.hwnd),
                "title": win32gui.GetWindowText(self.hwnd),
                "fallback_from_child": int(target),
            }
            return self.hwnd

        err = ctypes.get_last_error()
        raise WindowAutomationError(
            f"Background-PostMessageW wurde vom Spiel abgelehnt "
            f"(msg={msg}, hwnd={target}, winerr={err}). "
            "Die echte Maus wurde nicht bewegt."
        )

    def _uia_candidates_at(
        self,
        client_point: tuple[float, float],
    ) -> list[Any]:
        """Return UIA wrappers under a client point, smallest first.

        UI Automation actions do not use the physical mouse and do not require
        SetCursorPos/SendInput. This is therefore the preferred control path for
        Microsoft Solitaire when the app exposes actionable accessibility peers.
        """
        try:
            from pywinauto import Desktop
        except Exception as exc:
            raise WindowAutomationError(f"pywinauto/UIA ist nicht verfügbar: {exc}") from exc

        x, y = int(round(client_point[0])), int(round(client_point[1]))
        screen = win32gui.ClientToScreen(self.hwnd, (x, y))
        sx, sy = screen

        try:
            root = Desktop(backend="uia").window(handle=self.hwnd)
            wrappers = [root] + list(root.descendants())
        except Exception as exc:
            raise WindowAutomationError(
                f"UIA-Baum des Solitaire-Fensters konnte nicht gelesen werden: {exc}"
            ) from exc

        candidates: list[tuple[int, Any]] = []
        for wrapper in wrappers:
            try:
                rect = wrapper.rectangle()
                if rect.left <= sx < rect.right and rect.top <= sy < rect.bottom:
                    area = max(1, (rect.right - rect.left) * (rect.bottom - rect.top))
                    candidates.append((area, wrapper))
            except Exception:
                continue

        candidates.sort(key=lambda item: item[0])
        return [w for _area, w in candidates]

    @staticmethod
    def _uia_action(wrapper: Any, source: bool) -> tuple[bool, str]:
        """Try non-pointer UIA actions on one wrapper."""
        # A source card should preferably become selected first. A destination
        # should preferably be invoked. Both operations are accessibility
        # patterns, not simulated mouse input.
        order = ("select", "invoke", "legacy") if source else ("invoke", "select", "legacy")

        for action in order:
            try:
                if action == "select":
                    wrapper.select()
                    return True, "SelectionItem.Select"
                if action == "invoke":
                    wrapper.invoke()
                    return True, "InvokePattern.Invoke"
                if action == "legacy":
                    iface = wrapper.iface_legacy_iaccessible
                    iface.DoDefaultAction()
                    return True, "LegacyIAccessible.DoDefaultAction"
            except Exception:
                continue
        return False, ""

    def uia_activate_at(
        self,
        client_point: tuple[float, float],
        *,
        source: bool,
    ) -> dict[str, Any]:
        attempts: list[str] = []
        for wrapper in self._uia_candidates_at(client_point):
            try:
                info = wrapper.element_info
                label = f"{getattr(info, 'control_type', '')}:{getattr(info, 'name', '')}"
            except Exception:
                label = repr(wrapper)

            ok, method = self._uia_action(wrapper, source=source)
            attempts.append(label)
            if ok:
                result = {
                    "ok": True,
                    "method": method,
                    "element": label,
                    "physical_mouse_touched": False,
                    "foreground_changed": False,
                }
                self.last_input_target = {
                    "mode": "uia",
                    "element": label,
                    "method": method,
                }
                return result

        return {
            "ok": False,
            "method": None,
            "element": None,
            "attempts": attempts[:12],
            "physical_mouse_touched": False,
            "physical_keyboard_touched": False,
            "foreground_changed": False,
        }

    def uia_move(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
    ) -> dict[str, Any]:
        """Try a card move entirely through accessibility patterns."""
        source_result = self.uia_activate_at(start, source=True)
        if not source_result["ok"]:
            return {
                "ok": False,
                "stage": "source",
                "source": source_result,
                "physical_mouse_touched": False,
                "foreground_changed": False,
            }

        time.sleep(0.10)
        target_result = self.uia_activate_at(end, source=False)
        if not target_result["ok"]:
            return {
                "ok": False,
                "stage": "target",
                "source": source_result,
                "target": target_result,
                "physical_mouse_touched": False,
                "foreground_changed": False,
            }

        return {
            "ok": True,
            "stage": "complete",
            "source": source_result,
            "target": target_result,
            "physical_mouse_touched": False,
            "foreground_changed": False,
        }

    def uia_invoke_stock(
        self,
        fallback_point: tuple[float, float],
    ) -> dict[str, Any]:
        """Invoke the stock/new-cards control without pointer injection."""
        try:
            from pywinauto import Desktop
            root = Desktop(backend="uia").window(handle=self.hwnd)
            names = ("neu", "neue karten", "deal", "stock", "new")
            matches: list[Any] = []
            for wrapper in root.descendants():
                try:
                    name = (wrapper.element_info.name or "").strip().lower()
                except Exception:
                    continue
                if name and any(token == name or token in name for token in names):
                    matches.append(wrapper)

            for wrapper in matches:
                ok, method = self._uia_action(wrapper, source=False)
                if ok:
                    label = f"{wrapper.element_info.control_type}:{wrapper.element_info.name}"
                    self.last_input_target = {
                        "mode": "uia",
                        "element": label,
                        "method": method,
                    }
                    return {
                        "ok": True,
                        "method": method,
                        "element": label,
                        "physical_mouse_touched": False,
                        "foreground_changed": False,
                    }
        except Exception:
            pass

        return self.uia_activate_at(fallback_point, source=False)

    @staticmethod
    def _key_lparam(vk: int, key_up: bool = False) -> int:
        user32 = ctypes.windll.user32
        scan = int(user32.MapVirtualKeyW(int(vk), 0)) & 0xFF
        lp = 1 | (scan << 16)
        if key_up:
            lp |= (1 << 30) | (1 << 31)
        return lp

    def _send_key_message(self, hwnd: int, vk: int) -> bool:
        """Send one key press without generating physical keyboard input."""
        user32 = ctypes.windll.user32
        down_lp = self._key_lparam(vk, False)
        up_lp = self._key_lparam(vk, True)

        ctypes.set_last_error(0)
        down_ok = bool(user32.PostMessageW(
            ctypes.c_void_p(int(hwnd)),
            ctypes.c_uint(int(win32con.WM_KEYDOWN)),
            ctypes.c_size_t(int(vk)),
            ctypes.c_ssize_t(int(down_lp)),
        ))
        up_ok = bool(user32.PostMessageW(
            ctypes.c_void_p(int(hwnd)),
            ctypes.c_uint(int(win32con.WM_KEYUP)),
            ctypes.c_size_t(int(vk)),
            ctypes.c_ssize_t(int(up_lp)),
        ))
        if down_ok and up_ok:
            return True

        # Some packaged/XAML windows reject PostMessage but still process a
        # synchronous keyboard message. SendMessageTimeout does not move the
        # real mouse/keyboard and does not synthesize global input.
        result = ctypes.c_size_t()
        SMTO_ABORTIFHUNG = 0x0002
        down_send = user32.SendMessageTimeoutW(
            ctypes.c_void_p(int(hwnd)),
            ctypes.c_uint(int(win32con.WM_KEYDOWN)),
            ctypes.c_size_t(int(vk)),
            ctypes.c_ssize_t(int(down_lp)),
            ctypes.c_uint(SMTO_ABORTIFHUNG),
            ctypes.c_uint(250),
            ctypes.byref(result),
        )
        up_send = user32.SendMessageTimeoutW(
            ctypes.c_void_p(int(hwnd)),
            ctypes.c_uint(int(win32con.WM_KEYUP)),
            ctypes.c_size_t(int(vk)),
            ctypes.c_ssize_t(int(up_lp)),
            ctypes.c_uint(SMTO_ABORTIFHUNG),
            ctypes.c_uint(250),
            ctypes.byref(result),
        )
        return bool(down_send and up_send)

    def virtual_key(self, vk: int, repeats: int = 1, delay_ms: int = 45) -> dict[str, Any]:
        """Background keyboard navigation; never uses SendInput/keybd_event."""
        repeats = max(1, int(repeats))
        targets: list[int] = []

        # Prefer the same child surface UIA/background mouse resolved, then root.
        if self.last_input_target and isinstance(self.last_input_target.get("hwnd"), int):
            targets.append(int(self.last_input_target["hwnd"]))
        if self.hwnd not in targets:
            targets.append(self.hwnd)

        errors: list[str] = []
        for target in targets:
            ok_all = True
            for _ in range(repeats):
                if not self._send_key_message(target, int(vk)):
                    ok_all = False
                    errors.append(f"HWND {target} rejected VK {vk}")
                    break
                time.sleep(max(0.005, delay_ms / 1000.0))
            if ok_all:
                self.last_input_target = {
                    "mode": "background_keyboard",
                    "hwnd": int(target),
                    "vk": int(vk),
                }
                return {
                    "ok": True,
                    "target": int(target),
                    "vk": int(vk),
                    "repeats": repeats,
                    "physical_mouse_touched": False,
                    "physical_keyboard_touched": False,
                    "foreground_changed": False,
                }

        return {
            "ok": False,
            "vk": int(vk),
            "repeats": repeats,
            "errors": errors,
            "physical_mouse_touched": False,
            "physical_keyboard_touched": False,
            "foreground_changed": False,
        }

    def keyboard_spider_move(
        self,
        source_column: int,
        destination_column: int,
        start_index: int,
        visible_count: int,
        *,
        vertical_from_top: bool = True,
    ) -> dict[str, Any]:
        """Attempt Spider move with arrow-key navigation + Enter.

        Microsoft Solitaire has historically supported arrow-key navigation and
        Enter for selecting/moving cards. This route remains fully background:
        it sends window messages only and does not synthesize system input.
        """
        VK_ESCAPE = win32con.VK_ESCAPE
        VK_LEFT = win32con.VK_LEFT
        VK_RIGHT = win32con.VK_RIGHT
        VK_UP = win32con.VK_UP
        VK_DOWN = win32con.VK_DOWN
        VK_RETURN = win32con.VK_RETURN

        trace: list[dict[str, Any]] = []

        def key(vk: int, repeats: int = 1) -> bool:
            res = self.virtual_key(vk, repeats=repeats)
            trace.append(res)
            return bool(res.get("ok"))

        # Cancel any previous selection. Repeated LEFT normalizes horizontal
        # focus to the leftmost tableau item without knowing previous focus.
        key(VK_ESCAPE)
        if not key(VK_LEFT, 14):
            return {"ok": False, "stage": "left-normalize", "trace": trace}

        if source_column > 0 and not key(VK_RIGHT, source_column):
            return {"ok": False, "stage": "source-column", "trace": trace}

        # Normalize within the visible run, then select the requested start.
        visible_count = max(1, int(visible_count))
        start_index = max(0, min(int(start_index), visible_count - 1))
        if vertical_from_top:
            key(VK_UP, visible_count + 3)
            if start_index > 0:
                key(VK_DOWN, start_index)
        else:
            key(VK_DOWN, visible_count + 3)
            from_bottom = (visible_count - 1) - start_index
            if from_bottom > 0:
                key(VK_UP, from_bottom)

        if not key(VK_RETURN):
            return {"ok": False, "stage": "select-source", "trace": trace}

        delta = int(destination_column) - int(source_column)
        if delta > 0:
            if not key(VK_RIGHT, delta):
                return {"ok": False, "stage": "destination-right", "trace": trace}
        elif delta < 0:
            if not key(VK_LEFT, -delta):
                return {"ok": False, "stage": "destination-left", "trace": trace}

        if not key(VK_RETURN):
            return {"ok": False, "stage": "place", "trace": trace}

        return {
            "ok": True,
            "stage": "complete",
            "trace": trace,
            "source_column": source_column,
            "destination_column": destination_column,
            "start_index": start_index,
            "vertical_from_top": vertical_from_top,
            "physical_mouse_touched": False,
            "physical_keyboard_touched": False,
            "foreground_changed": False,
        }

    def keyboard_spider_deal(self) -> dict[str, Any]:
        """Try the Spider deal shortcut without physical keyboard input."""
        # 'D' is the standard deal shortcut in current keyboard-accessible
        # Spider implementations; if the app ignores it the caller verifies
        # that no board change occurred and can try other mouse-free paths.
        return self.virtual_key(ord("D"))

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
            "mode": "uia_then_background_messages",
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
