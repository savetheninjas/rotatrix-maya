# SPDX-License-Identifier: GPL-3.0-or-later
"""UI-thread pump for the ZBrush plugin.

ZBrush Python has no timer, so this creates a private message window and
handles a 16 ms WM_TIMER there. The ZBrush frame itself is left alone:
replacing its window procedure crashed the app. Pen and trackball code call
ZBrush only from that timer. A low-level mouse hook eats the tip click
while Win is held, and a keyboard hook keeps the Start menu closed when
Rotatrix itself is not already handling the Win key.
"""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes

from .log import log
from .tablet import get_tablet

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
HC_ACTION = 0
LLKHF_UP = 0x80
GWLP_WNDPROC = -4
WM_TIMER = 0x0113
WM_DESTROY = 0x0002
WM_NCDESTROY = 0x0082
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
VK_LBUTTON = 0x01
VK_SHIFT = 0x10
VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1
VK_LWIN = 0x5B
VK_RWIN = 0x5C
TIMER_ID = 0x5207
TIMER_MS = 16

_user32 = None
_hwnd = None
_old_proc = None
_wnd_proc = None
_key_hook = None
_key_proc = None
_mouse_hook = None
_mouse_proc = None
_pass_win_through = False
_capture_win = False
_busy = False
_installed = False
_fallback_hwnd = None
_fallback_proc = None
_fallback_hwnd = None
_fallback_proc = None


class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


def _user32_dll():
    global _user32
    if _user32 is None:
        library = ctypes.WinDLL("user32", use_last_error=True)
        library.SetWindowsHookExW.argtypes = [
            ctypes.c_int, ctypes.c_void_p, wintypes.HINSTANCE, wintypes.DWORD,
        ]
        library.SetWindowsHookExW.restype = ctypes.c_void_p
        library.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
        library.UnhookWindowsHookEx.restype = wintypes.BOOL
        library.CallNextHookEx.argtypes = [
            ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM,
        ]
        library.CallNextHookEx.restype = ctypes.c_ssize_t
        library.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
        library.SetWindowLongPtrW.restype = ctypes.c_void_p
        library.CallWindowProcW.argtypes = [
            ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
        ]
        library.CallWindowProcW.restype = ctypes.c_ssize_t
        library.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        library.GetWindowThreadProcessId.restype = wintypes.DWORD
        library.GetCursorPos.argtypes = [ctypes.POINTER(_POINT)]
        library.GetCursorPos.restype = wintypes.BOOL
        library.GetForegroundWindow.restype = wintypes.HWND
        library.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
        library.GetWindow.restype = wintypes.HWND
        library.IsWindowVisible.argtypes = [wintypes.HWND]
        library.IsWindowVisible.restype = wintypes.BOOL
        library.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        library.GetWindowRect.restype = wintypes.BOOL
        library.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
        library.EnumWindows.restype = wintypes.BOOL
        library.SetTimer.argtypes = [
            wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p,
        ]
        library.SetTimer.restype = ctypes.c_size_t
        library.KillTimer.argtypes = [wintypes.HWND, ctypes.c_size_t]
        library.KillTimer.restype = wintypes.BOOL
        library.DefWindowProcW.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
        ]
        library.DefWindowProcW.restype = ctypes.c_ssize_t
        library.RegisterClassW.argtypes = [ctypes.c_void_p]
        library.RegisterClassW.restype = wintypes.ATOM
        library.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_void_p, ctypes.c_void_p, wintypes.HINSTANCE, ctypes.c_void_p,
        ]
        library.CreateWindowExW.restype = wintypes.HWND
        library.DestroyWindow.argtypes = [wintypes.HWND]
        library.DestroyWindow.restype = wintypes.BOOL
        _user32 = library
    return _user32


def key_down(vk: int) -> bool:
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)


def win_down() -> bool:
    return key_down(VK_LWIN) or key_down(VK_RWIN)


def shift_down() -> bool:
    # Left and right are the keys the hook and the hardware actually report.
    # The combined VK_SHIFT state lags when another program handles the key.
    return key_down(VK_LSHIFT) or key_down(VK_RSHIFT) or key_down(VK_SHIFT)


def tip_button_down() -> bool:
    return key_down(VK_LBUTTON)


def cursor_pos() -> tuple[float, float]:
    point = _POINT()
    _user32_dll().GetCursorPos(ctypes.byref(point))
    return float(point.x), float(point.y)


def zbrush_is_foreground() -> bool:
    hwnd = _user32_dll().GetForegroundWindow()
    if not hwnd:
        return False
    pid = wintypes.DWORD()
    _user32_dll().GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value) == os.getpid()


def main_hwnd():
    """Window that owns the timer and the pen context. Not the ZBrush frame."""
    return _fallback_hwnd or _hwnd


def set_pass_win_through(enabled: bool) -> None:
    global _pass_win_through
    _pass_win_through = bool(enabled)


def install() -> bool:
    """Install input hooks and a private UI-thread timer window."""
    if sys.platform != "win32":
        return False
    _install_hooks()
    _start_fallback_pump()
    return _fallback_hwnd is not None


def shutdown() -> None:
    global _installed, _hwnd, _old_proc, _wnd_proc
    get_tablet().shutdown()
    _remove_hooks()
    _stop_fallback_pump()
    hwnd = _hwnd
    if hwnd and _old_proc:
        try:
            _user32_dll().KillTimer(hwnd, TIMER_ID)
        except Exception:
            pass
        _user32_dll().SetWindowLongPtrW(hwnd, GWLP_WNDPROC, _old_proc)
    _hwnd = None
    _old_proc = None
    _wnd_proc = None
    _installed = False


def _start_fallback_pump() -> None:
    """Timer window on this thread, used until the ZBrush frame exists."""
    global _fallback_hwnd, _fallback_proc
    if _fallback_hwnd is not None:
        return
    user = _user32_dll()
    proc_type = ctypes.WINFUNCTYPE(
        ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
    )
    _fallback_proc = proc_type(_fallback_proc_body)

    class _WNDCLASSW(ctypes.Structure):
        _fields_ = [
            ("style", wintypes.UINT),
            ("lpfnWndProc", ctypes.c_void_p),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", ctypes.c_void_p),
            ("hCursor", ctypes.c_void_p),
            ("hbrBackground", ctypes.c_void_p),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
        ]

    instance_fn = ctypes.WinDLL("kernel32", use_last_error=True)
    instance_fn.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    instance_fn.GetModuleHandleW.restype = wintypes.HINSTANCE
    instance = instance_fn.GetModuleHandleW(None)
    klass = _WNDCLASSW()
    klass.lpfnWndProc = ctypes.cast(_fallback_proc, ctypes.c_void_p)
    klass.hInstance = instance
    klass.lpszClassName = "RotatrixZBrushPump"
    user.RegisterClassW(ctypes.byref(klass))
    hwnd = user.CreateWindowExW(
        0, "RotatrixZBrushPump", "Rotatrix", 0,
        0, 0, 0, 0, ctypes.c_void_p(-3), None, instance, None,
    )
    if not hwnd:
        log(f"navigation timer window failed (error {ctypes.get_last_error()})", "WARN")
        return
    _fallback_hwnd = hwnd
    user.SetTimer(hwnd, TIMER_ID, TIMER_MS, None)
    log("navigation timer started")


def _stop_fallback_pump() -> None:
    global _fallback_hwnd
    hwnd = _fallback_hwnd
    _fallback_hwnd = None
    if not hwnd:
        return
    try:
        _user32_dll().KillTimer(hwnd, TIMER_ID)
    except Exception:
        pass


def _fallback_proc_body(hwnd, msg, wparam, lparam):
    try:
        if msg == WM_TIMER and int(wparam) == TIMER_ID:
            _run_timer()
            return 0
    except Exception as exc:
        log(f"timer window failed: {exc!r}", "WARN")
    return _user32_dll().DefWindowProcW(hwnd, msg, wparam, lparam)


def _run_timer() -> None:
    global _busy
    if _busy:
        return
    _busy = True
    try:
        _on_timer()
    finally:
        _busy = False


def _on_timer() -> None:
    from . import openaxis_handler, pen_nav, viewport

    viewport.hold_navigation_detail(win_down() and zbrush_is_foreground())
    get_tablet().poll()
    pen_nav.tick()
    openaxis_handler.get_handler().tick()


def _install_hooks() -> None:
    global _key_hook, _key_proc, _mouse_hook, _mouse_proc
    user = _user32_dll()
    key_type = ctypes.WINFUNCTYPE(
        ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM,
    )
    _key_proc = key_type(_key_hook_proc)
    _key_hook = user.SetWindowsHookExW(
        WH_KEYBOARD_LL, ctypes.cast(_key_proc, ctypes.c_void_p), None, 0,
    )
    if not _key_hook:
        log(f"Win-key hook failed (error {ctypes.get_last_error()})", "WARN")
        _key_proc = None
    mouse_type = ctypes.WINFUNCTYPE(
        ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM,
    )
    _mouse_proc = mouse_type(_mouse_hook_proc)
    _mouse_hook = user.SetWindowsHookExW(
        WH_MOUSE_LL, ctypes.cast(_mouse_proc, ctypes.c_void_p), None, 0,
    )
    if not _mouse_hook:
        log(f"pen-click hook failed (error {ctypes.get_last_error()})", "WARN")
        _mouse_proc = None


def _remove_hooks() -> None:
    global _key_hook, _key_proc, _mouse_hook, _mouse_proc, _capture_win
    user = _user32_dll()
    _capture_win = False
    if _key_hook:
        user.UnhookWindowsHookEx(_key_hook)
        _key_hook = None
    _key_proc = None
    if _mouse_hook:
        user.UnhookWindowsHookEx(_mouse_hook)
        _mouse_hook = None
    _mouse_proc = None


def _key_hook_proc(n_code, w_param, l_param):
    """Eat Win while ZBrush is in front and Rotatrix is not using it."""
    global _capture_win
    try:
        if n_code == HC_ACTION and not _pass_win_through and zbrush_is_foreground():
            info = ctypes.cast(l_param, ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
            if info.vkCode in (VK_LWIN, VK_RWIN):
                is_up = bool(info.flags & LLKHF_UP)
                if not is_up:
                    _capture_win = True
                if _capture_win:
                    if is_up:
                        _capture_win = False
                    return 1
    except Exception:
        pass
    return _user32_dll().CallNextHookEx(None, n_code, w_param, l_param)


def _mouse_hook_proc(n_code, w_param, l_param):
    """Drop the tip click while Win navigates, and let the cursor keep moving."""
    try:
        if (
            n_code == HC_ACTION
            and int(w_param) in (WM_LBUTTONDOWN, WM_LBUTTONUP, WM_LBUTTONDBLCLK)
            and zbrush_is_foreground()
            and win_down()
        ):
            return 1
    except Exception:
        pass
    return _user32_dll().CallNextHookEx(None, n_code, w_param, l_param)
