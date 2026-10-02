# SPDX-License-Identifier: GPL-3.0-or-later
"""Wintab sample used while Win is held.

This opens a system cursor context on our own timer window, not a digitizing
context on the ZBrush frame. A digitizing context on that frame crashed
ZBrush. Packets carry pressure, barrel twist, and pen position. The cursor
does not move while the tip is down, so orbit and pan use the packet.
The mouse hook keeps the tip click from starting a stroke.

A size check refuses to open a context the driver describes differently
than this struct.
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

from .log import log

WTI_DEFSYSCTX = 4
CXO_SYSTEM = 0x0001
CXO_MESSAGES = 0x0004
PK_X = 0x0080
PK_Y = 0x0100
PK_NORMAL_PRESSURE = 0x0400
PK_ORIENTATION = 0x1000
PACKET_BITS = PK_X | PK_Y | PK_NORMAL_PRESSURE | PK_ORIENTATION


class _LOGCONTEXT(ctypes.Structure):
    _fields_ = [
        ("lcName", ctypes.c_char * 40),
        ("lcOptions", wintypes.UINT),
        ("lcStatus", wintypes.UINT),
        ("lcLocks", wintypes.UINT),
        ("lcMsgBase", wintypes.UINT),
        ("lcDevice", wintypes.UINT),
        ("lcPktRate", wintypes.UINT),
        ("lcPktData", wintypes.DWORD),
        ("lcPktMode", wintypes.DWORD),
        ("lcMoveMask", wintypes.DWORD),
        ("lcBtnDnMask", wintypes.DWORD),
        ("lcBtnUpMask", wintypes.DWORD),
        ("lcInOrgX", wintypes.LONG),
        ("lcInOrgY", wintypes.LONG),
        ("lcInOrgZ", wintypes.LONG),
        ("lcInExtX", wintypes.LONG),
        ("lcInExtY", wintypes.LONG),
        ("lcInExtZ", wintypes.LONG),
        ("lcOutOrgX", wintypes.LONG),
        ("lcOutOrgY", wintypes.LONG),
        ("lcOutOrgZ", wintypes.LONG),
        ("lcOutExtX", wintypes.LONG),
        ("lcOutExtY", wintypes.LONG),
        ("lcOutExtZ", wintypes.LONG),
        ("lcSensX", wintypes.DWORD),
        ("lcSensY", wintypes.DWORD),
        ("lcSensZ", wintypes.DWORD),
        ("lcSysMode", wintypes.BOOL),
        ("lcSysOrgX", ctypes.c_int),
        ("lcSysOrgY", ctypes.c_int),
        ("lcSysExtX", ctypes.c_int),
        ("lcSysExtY", ctypes.c_int),
        ("lcSysSensX", wintypes.DWORD),
        ("lcSysSensY", wintypes.DWORD),
    ]


class _ORIENTATION(ctypes.Structure):
    _fields_ = [
        ("orAzimuth", ctypes.c_int),
        ("orAltitude", ctypes.c_int),
        ("orTwist", ctypes.c_int),
    ]


class _PACKET(ctypes.Structure):
    _fields_ = [
        ("pkX", wintypes.LONG),
        ("pkY", wintypes.LONG),
        ("pkNormalPressure", wintypes.UINT),
        ("pkOrientation", _ORIENTATION),
    ]


class TabletSample:
    def __init__(self) -> None:
        self.pressure = 0
        self.twist: float | None = None
        # Pen position in pixels, Y down the screen, or None before the first packet.
        self.x: float | None = None
        self.y: float | None = None
        self.at = 0.0

    def fresh(self, now: float | None = None) -> bool:
        moment = time.monotonic() if now is None else now
        return (moment - self.at) < 0.15 and self.pressure >= 0 and self.at > 0.0


class Tablet:
    def __init__(self) -> None:
        self.sample = TabletSample()
        self._lib = None
        self._ctx = None
        self._msg_base = 0
        self._disabled = False
        self._logged = False
        self._x_scale = 1.0
        self._y_scale = 1.0

    def available(self) -> bool:
        return self._load() is not None and not self._disabled

    def message_base(self) -> int:
        return self._msg_base

    def context(self):
        return self._ctx

    def set_capture(self, hwnd, enabled: bool) -> None:
        if enabled:
            self._open(hwnd)
        else:
            self._close()

    def handle_message(self, hwnd, msg: int, wparam, lparam) -> bool:
        """Consume a packet aimed at our context. True if the message was ours."""
        ctx = self._ctx
        if ctx is None or msg != self._msg_base or self._msg_base == 0:
            return False
        if int(lparam) != int(ctx):
            return False
        self._read(int(wparam))
        return True

    def poll(self) -> None:
        """Read the newest queued packet. Called from the UI timer."""
        library = self._lib
        ctx = self._ctx
        if library is None or ctx is None:
            return
        packet = _PACKET()
        pressure = 0
        twist = 0.0
        got = False
        for _ in range(32):
            try:
                count = int(library.WTPacketsGet(ctx, 1, ctypes.byref(packet)))
            except Exception:
                return
            if count < 1:
                break
            pressure = int(packet.pkNormalPressure)
            twist = _twist_degrees(int(packet.pkOrientation.orTwist))
            point = (int(packet.pkX), int(packet.pkY))
            got = True
        if not got:
            return
        self._store(pressure, twist, point)

    def shutdown(self) -> None:
        self._close()

    def _load(self):
        if self._lib is not None or self._disabled:
            return self._lib
        try:
            library = ctypes.WinDLL("wintab32", use_last_error=True)
        except OSError:
            self._disabled = True
            return None
        library.WTInfoA.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.c_void_p]
        library.WTInfoA.restype = wintypes.UINT
        library.WTOpenA.argtypes = [wintypes.HWND, ctypes.POINTER(_LOGCONTEXT), wintypes.BOOL]
        library.WTOpenA.restype = wintypes.HANDLE
        library.WTClose.argtypes = [wintypes.HANDLE]
        library.WTClose.restype = wintypes.BOOL
        library.WTPacket.argtypes = [wintypes.HANDLE, wintypes.UINT, ctypes.c_void_p]
        library.WTPacket.restype = wintypes.BOOL
        library.WTPacketsGet.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p]
        library.WTPacketsGet.restype = ctypes.c_int
        self._lib = library
        return library

    def _open(self, hwnd) -> None:
        if self._ctx is not None or hwnd is None or self._disabled:
            return
        library = self._load()
        if library is None:
            return
        reported = int(library.WTInfoA(WTI_DEFSYSCTX, 0, None))
        expected = ctypes.sizeof(_LOGCONTEXT)
        if reported != expected:
            self._disabled = True
            if not self._logged:
                self._logged = True
                log(
                    f"pen twist capture is off (Wintab context is {reported} bytes, "
                    f"expected {expected})",
                    "WARN",
                )
            return
        context = _LOGCONTEXT()
        got = int(library.WTInfoA(WTI_DEFSYSCTX, 0, ctypes.byref(context)))
        if got != expected:
            self._disabled = True
            return
        context.lcPktData = PACKET_BITS
        context.lcPktMode = 0
        context.lcMoveMask = PACKET_BITS
        context.lcOptions |= CXO_SYSTEM | CXO_MESSAGES
        self._x_scale, self._y_scale = _pixel_scales(context)
        handle = library.WTOpenA(hwnd, ctypes.byref(context), True)
        if not handle:
            if not self._logged:
                self._logged = True
                log("pen twist capture could not open a Wintab context", "WARN")
            return
        self._ctx = handle
        self._msg_base = int(context.lcMsgBase)
        log(f"pen capture open (x {self._x_scale:.4g}, y {self._y_scale:.4g})")

    def _close(self) -> None:
        ctx = self._ctx
        self._ctx = None
        self._msg_base = 0
        self.sample = TabletSample()
        library = self._lib
        if ctx is not None and library is not None:
            try:
                library.WTClose(ctx)
            except Exception:
                pass
            log("pen capture closed")

    def _read(self, serial: int) -> None:
        library = self._lib
        ctx = self._ctx
        if library is None or ctx is None:
            return
        packet = _PACKET()
        try:
            ok = library.WTPacket(ctx, serial, ctypes.byref(packet))
        except Exception:
            return
        if not ok:
            return
        self._store(
            int(packet.pkNormalPressure),
            _twist_degrees(int(packet.pkOrientation.orTwist)),
            (int(packet.pkX), int(packet.pkY)),
        )

    def _store(self, pressure: int, twist: float, point: tuple[int, int]) -> None:
        self.sample.pressure = pressure
        self.sample.twist = twist
        self.sample.x = point[0] * self._x_scale
        self.sample.y = point[1] * self._y_scale
        self.sample.at = time.monotonic()


def _pixel_scales(context: _LOGCONTEXT) -> tuple[float, float]:
    """Map packet X/Y into pixels with Y down the screen.

    A system context already reports screen pixels. A digitizing context
    reports tablet units, often tens of thousands across, and a negative
    Y extent means the axis points up.
    """
    screen_w, screen_h = _screen_size()
    extent_x = int(context.lcOutExtX) or int(context.lcSysExtX) or screen_w
    extent_y = int(context.lcOutExtY) or int(context.lcSysExtY) or screen_h
    return _axis_scale(extent_x, screen_w), _axis_scale(extent_y, screen_h)


def _axis_scale(extent: int, screen: int) -> float:
    if extent == 0:
        return 1.0
    if abs(extent) > screen * 2:
        return screen / float(extent)
    return 1.0 if extent > 0 else -1.0


def _screen_size() -> tuple[int, int]:
    try:
        user32 = ctypes.windll.user32
        width = int(user32.GetSystemMetrics(78)) or 1920
        height = int(user32.GetSystemMetrics(79)) or 1080
    except Exception:
        return 1920, 1080
    return max(width, 1), max(height, 1)


def _twist_degrees(raw: int) -> float:
    """Art Pen twist is tenths of a degree, wrapped through a full turn.

    Treating the first 36° as whole degrees made that part of the barrel
    ten times faster than the rest.
    """
    tenths = (raw + 1800) % 3600 - 1800
    return tenths * 0.1


_tablet: Tablet | None = None


def get_tablet() -> Tablet:
    global _tablet
    if _tablet is None:
        _tablet = Tablet()
    return _tablet
