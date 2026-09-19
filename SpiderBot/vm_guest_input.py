from __future__ import annotations

import ctypes
import json
import math
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any


class GuestInputError(RuntimeError):
    pass


def detect_virtual_machine() -> dict[str, Any]:
    """Best-effort VM detection. Guest real-input mode is blocked on bare metal."""
    ps = (
        "$c=Get-CimInstance Win32_ComputerSystem;"
        "$o=[ordered]@{Manufacturer=$c.Manufacturer;Model=$c.Model};"
        "$o|ConvertTo-Json -Compress"
    )
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", ps],
            text=True,
            timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).strip()
        data = json.loads(out)
    except Exception as exc:
        return {
            "virtual": False,
            "manufacturer": "",
            "model": "",
            "error": f"{type(exc).__name__}: {exc}",
        }

    manufacturer = str(data.get("Manufacturer") or "")
    model = str(data.get("Model") or "")
    hay = (manufacturer + " " + model).lower()
    markers = (
        "virtualbox",
        "vmware",
        "virtual machine",
        "kvm",
        "qemu",
        "parallels",
        "xen",
        "hyper-v",
        "microsoft corporation virtual",
    )
    return {
        "virtual": any(m in hay for m in markers),
        "manufacturer": manufacturer,
        "model": model,
        "error": None,
    }


_abort_input = threading.Event()


def guest_real_input_enabled() -> bool:
    return os.getenv("SPIDER_VM_GUEST", "").strip() == "1"


def host_real_input_enabled() -> bool:
    return os.getenv("SPIDER_REAL_MOUSE", "").strip() == "1"


def real_mouse_input_enabled() -> bool:
    return guest_real_input_enabled() or host_real_input_enabled()


def request_input_abort() -> None:
    _abort_input.set()


def clear_input_abort() -> None:
    _abort_input.clear()


def require_guest_vm() -> dict[str, Any]:
    if not guest_real_input_enabled():
        raise GuestInputError(
            "SPIDER_VM_GUEST ist nicht aktiviert."
        )

    info = detect_virtual_machine()
    allow_unsafe = os.getenv("SPIDER_ALLOW_BARE_METAL_INPUT", "").strip() == "1"
    if not info.get("virtual") and not allow_unsafe:
        raise GuestInputError(
            "Echter SendInput-Drag ist aus Sicherheitsgründen nur in einer "
            "virtuellen Maschine erlaubt. Auf dem Host würde er deine echte "
            "Windows-Maus bewegen."
        )
    return info


# Win32 input declarations.
user32 = ctypes.WinDLL("user32", use_last_error=True)

INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_ulong),
        ("union", INPUTUNION),
    ]


user32.SendInput.argtypes = (ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = ctypes.c_uint


def _screen_to_absolute(x: int, y: int) -> tuple[int, int]:
    vx = int(user32.GetSystemMetrics(SM_XVIRTUALSCREEN))
    vy = int(user32.GetSystemMetrics(SM_YVIRTUALSCREEN))
    vw = max(1, int(user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)))
    vh = max(1, int(user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)))
    ax = int(round((x - vx) * 65535 / max(1, vw - 1)))
    ay = int(round((y - vy) * 65535 / max(1, vh - 1)))
    return max(0, min(65535, ax)), max(0, min(65535, ay))


def _send_mouse(x: int, y: int, flags: int) -> None:
    ax, ay = _screen_to_absolute(x, y)
    inp = INPUT(
        type=INPUT_MOUSE,
        union=INPUTUNION(
            mi=MOUSEINPUT(
                dx=ax,
                dy=ay,
                mouseData=0,
                dwFlags=flags | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
                time=0,
                dwExtraInfo=None,
            )
        ),
    )
    sent = user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
    if sent != 1:
        err = ctypes.get_last_error()
        raise GuestInputError(f"SendInput fehlgeschlagen (winerr={err}).")


def _real_input_context() -> dict[str, Any]:
    if guest_real_input_enabled():
        vm = require_guest_vm()
        return {"scope": "vm_guest", "virtual_machine": vm}
    if host_real_input_enabled():
        return {"scope": "host_real_mouse", "virtual_machine": None}
    raise GuestInputError(
        "Echte Maussteuerung ist nicht aktiviert. "
        "Starte SpiderBot ueber start.bat oder setze SPIDER_REAL_MOUSE=1."
    )


def real_mouse_click(screen_x: int, screen_y: int, hold_ms: int = 100) -> dict[str, Any]:
    ctx = _real_input_context()
    clear_input_abort()
    _send_mouse(screen_x, screen_y, MOUSEEVENTF_MOVE)
    time.sleep(0.035)
    if _abort_input.is_set():
        return {"ok": False, "aborted": True, "mode": ctx["scope"]}
    _send_mouse(screen_x, screen_y, MOUSEEVENTF_LEFTDOWN)
    try:
        deadline = time.time() + max(0.03, hold_ms / 1000.0)
        while time.time() < deadline:
            if _abort_input.is_set():
                break
            time.sleep(0.01)
    finally:
        _send_mouse(screen_x, screen_y, MOUSEEVENTF_LEFTUP)
    return {
        "ok": not _abort_input.is_set(),
        "aborted": _abort_input.is_set(),
        "mode": "real_sendinput_click",
        "scope": ctx["scope"],
        "virtual_machine": ctx["virtual_machine"],
    }


def real_mouse_drag(
    start_screen: tuple[int, int],
    end_screen: tuple[int, int],
    duration_ms: int = 850,
    steps: int = 52,
) -> dict[str, Any]:
    ctx = _real_input_context()
    clear_input_abort()
    sx, sy = map(int, start_screen)
    ex, ey = map(int, end_screen)
    steps = max(16, int(steps))
    delay = max(0.004, duration_ms / 1000.0 / steps)

    _send_mouse(sx, sy, MOUSEEVENTF_MOVE)
    time.sleep(0.05)
    if _abort_input.is_set():
        return {"ok": False, "aborted": True, "mode": "real_sendinput_drag"}

    _send_mouse(sx, sy, MOUSEEVENTF_LEFTDOWN)
    time.sleep(max(0.06, delay * 2))
    last_x, last_y = sx, sy

    try:
        for i in range(1, steps + 1):
            if _abort_input.is_set():
                break
            t = i / steps
            u = t * t * (3.0 - 2.0 * t)
            last_x = int(round(sx + (ex - sx) * u))
            last_y = int(round(sy + (ey - sy) * u))
            _send_mouse(last_x, last_y, MOUSEEVENTF_MOVE)
            time.sleep(delay)
    finally:
        # Alt+L stop can interrupt the drag. Always release immediately.
        _send_mouse(last_x, last_y, MOUSEEVENTF_LEFTUP)

    return {
        "ok": not _abort_input.is_set(),
        "aborted": _abort_input.is_set(),
        "mode": "real_sendinput_drag",
        "scope": ctx["scope"],
        "sequence": "MOVE -> LEFTDOWN -> MOVE*N -> LEFTUP",
        "steps": steps,
        "duration_ms": duration_ms,
        "virtual_machine": ctx["virtual_machine"],
    }


def real_guest_click(screen_x: int, screen_y: int, hold_ms: int = 100) -> dict[str, Any]:
    return real_mouse_click(screen_x, screen_y, hold_ms=hold_ms)


def real_guest_drag(
    start_screen: tuple[int, int],
    end_screen: tuple[int, int],
    duration_ms: int = 850,
    steps: int = 52,
) -> dict[str, Any]:
    return real_mouse_drag(
        start_screen,
        end_screen,
        duration_ms=duration_ms,
        steps=steps,
    )
