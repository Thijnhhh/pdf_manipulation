"""
backend/window_utils.py
─────────────────────────────────────────────────────────────
Window utility functions for desktop integration on Windows:
  - Taskbar flashing / Window attention notification
  - Safe HWND resolution from pywebview Window instances
─────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import ctypes
import sys
from typing import Any

# Win32 FlashWindow constants
FLASHW_STOP = 0
FLASHW_CAPTION = 0x00000001
FLASHW_TRAY = 0x00000002
FLASHW_ALL = FLASHW_CAPTION | FLASHW_TRAY
FLASHW_TIMER = 0x00000004
FLASHW_TIMERNOFG = 0x0000000C  # Flash continuously until window comes to foreground

if sys.platform == "win32":
    from ctypes import wintypes

    class FLASHWINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.UINT),
            ("hwnd", wintypes.HWND),
            ("dwFlags", wintypes.DWORD),
            ("uCount", wintypes.UINT),
            ("dwTimeout", wintypes.DWORD),
        ]

    _user32 = ctypes.windll.user32
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.GetForegroundWindow.argtypes = []

    _user32.FindWindowW.restype = wintypes.HWND
    _user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]

    _user32.IsWindow.restype = wintypes.BOOL
    _user32.IsWindow.argtypes = [wintypes.HWND]

    _user32.FlashWindowEx.restype = wintypes.BOOL
    _user32.FlashWindowEx.argtypes = [ctypes.POINTER(FLASHWINFO)]


def get_window_hwnd(window: Any = None, window_title: str | None = None) -> int | None:
    """
    Safely resolve the HWND of the pywebview window on Windows.
    Tries:
      1. window.native.Handle (WinForms / EdgeChromium)
      2. FindWindowW by title
    """
    if sys.platform != "win32":
        return None

    # 1. Try window.native.Handle
    if window is not None and hasattr(window, "native"):
        try:
            native = window.native
            if native is not None and hasattr(native, "Handle"):
                handle = native.Handle
                if hasattr(handle, "ToInt64"):
                    hwnd = int(handle.ToInt64())
                elif hasattr(handle, "ToInt32"):
                    hwnd = int(handle.ToInt32())
                else:
                    hwnd = int(handle)
                if hwnd and _user32.IsWindow(hwnd):
                    return hwnd
        except Exception:
            pass

    # 2. Fallback: FindWindowW by title
    title = window_title or (getattr(window, "title", None) if window else None)
    if title:
        try:
            hwnd = _user32.FindWindowW(None, str(title))
            if hwnd and _user32.IsWindow(hwnd):
                return hwnd
        except Exception:
            pass

    return None


def flash_app_window(
    window: Any = None,
    window_title: str | None = None,
    only_when_background: bool = True,
) -> bool:
    """
    Flash the taskbar icon to attract user attention without stealing focus.

    Args:
        window: The pywebview.Window instance (or None if searching by title).
        window_title: Optional window title for fallback handle resolution.
        only_when_background: If True, do nothing if window is already in the foreground.

    Returns:
        True if flash command was issued, False otherwise.
    """
    if sys.platform != "win32":
        return False

    try:
        hwnd = get_window_hwnd(window, window_title)
        if not hwnd:
            return False

        # If user is already actively working in this window, skip flashing
        if only_when_background:
            foreground_hwnd = _user32.GetForegroundWindow()
            if foreground_hwnd == hwnd:
                return False

        info = FLASHWINFO()
        info.cbSize = ctypes.sizeof(FLASHWINFO)
        info.hwnd = hwnd
        info.dwFlags = FLASHW_TRAY | FLASHW_TIMERNOFG
        info.uCount = 0
        info.dwTimeout = 0

        _user32.FlashWindowEx(ctypes.byref(info))
        return True
    except Exception as exc:
        print(f"[window_utils] FlashWindowEx failed: {exc}")
        return False
