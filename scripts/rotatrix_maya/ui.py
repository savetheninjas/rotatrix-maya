# SPDX-License-Identifier: GPL-3.0-or-later
"""Rotatrix control panel for Maya."""

from __future__ import annotations

import maya.OpenMayaUI as omui
import maya.cmds as cmds

from . import openaxis_handler, pen_nav, prefs
from . import log as rotatrix_log
from .qtcompat import QtCore, QtWidgets, qt_flag, wrapInstance

_PANEL = None
MENU_NAME = "RotatrixMenu"


def maya_main_window():
    ptr = omui.MQtUtil.mainWindow()
    return wrapInstance(int(ptr), QtWidgets.QWidget) if ptr else None


class RotatrixPanel(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent or maya_main_window())
        self.setObjectName("RotatrixPanel")
        self.setWindowTitle("Rotatrix")
        self.setWindowFlags(
            qt_flag(
                "Tool",
                "WindowTitleHint",
                "WindowCloseButtonHint",
                "WindowMinimizeButtonHint",
            )
        )
        self.setMinimumWidth(280)
        self._build()
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._refresh_status)
        self._timer.start(250)
        self._refresh_status()

    def _build(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        self.status_btn = QtWidgets.QPushButton("Connect")
        self.status_btn.clicked.connect(self._toggle_connect)
        layout.addWidget(self.status_btn)

        self.stats_label = QtWidgets.QLabel("")
        self.stats_label.setStyleSheet("color: #888;")
        layout.addWidget(self.stats_label)

        pen_box = QtWidgets.QGroupBox("Pen Camera")
        pen_layout = QtWidgets.QVBoxLayout(pen_box)
        self.pen_chk = QtWidgets.QCheckBox("Win + pen tip orbits  (Shift pans)")
        self.pen_chk.setChecked(prefs.get_bool(prefs.PEN_NAV))
        self.pen_chk.toggled.connect(self._toggle_pen)
        pen_layout.addWidget(self.pen_chk)
        self.pen_probe = QtWidgets.QCheckBox("Log pen samples")
        self.pen_probe.setChecked(prefs.get_bool(prefs.PEN_PROBE))
        self.pen_probe.toggled.connect(
            lambda v: prefs.set_bool(prefs.PEN_PROBE, v)
        )
        pen_layout.addWidget(self.pen_probe)
        self.pen_status = QtWidgets.QLabel(pen_nav.status_text())
        self.pen_status.setStyleSheet("color: #888;")
        self.pen_status.setWordWrap(True)
        pen_layout.addWidget(self.pen_status)
        layout.addWidget(pen_box)

        mode_row = QtWidgets.QHBoxLayout()
        mode_row.addWidget(QtWidgets.QLabel("Mode:"))
        self.orbit_btn = QtWidgets.QPushButton("Orbit")
        self.orbit_btn.setCheckable(True)
        self.orbit_btn.clicked.connect(lambda: self._set_mode("orbit"))
        self.free_btn = QtWidgets.QPushButton("Free Camera")
        self.free_btn.setCheckable(True)
        self.free_btn.clicked.connect(lambda: self._set_mode("free_camera"))
        mode_row.addWidget(self.orbit_btn)
        mode_row.addWidget(self.free_btn)
        layout.addLayout(mode_row)

        lock_row = QtWidgets.QHBoxLayout()
        self.ground_btn = QtWidgets.QPushButton("Ground Lock")
        self.ground_btn.setCheckable(True)
        self.ground_btn.clicked.connect(self._toggle_ground)
        self.horizon_btn = QtWidgets.QPushButton("Horizon Lock")
        self.horizon_btn.setCheckable(True)
        self.horizon_btn.clicked.connect(self._toggle_horizon)
        lock_row.addWidget(self.ground_btn)
        lock_row.addWidget(self.horizon_btn)
        layout.addLayout(lock_row)

        self.speed_widget = QtWidgets.QWidget()
        speed_row = QtWidgets.QHBoxLayout(self.speed_widget)
        speed_row.setContentsMargins(0, 0, 0, 0)
        speed_row.addWidget(QtWidgets.QLabel("Walk Speed"))
        self.speed_spin = QtWidgets.QDoubleSpinBox()
        self.speed_spin.setRange(0.01, 1000.0)
        self.speed_spin.setValue(prefs.get_float(prefs.WALK_SPEED))
        self.speed_spin.valueChanged.connect(
            lambda v: prefs.set_float(prefs.WALK_SPEED, v)
        )
        speed_row.addWidget(self.speed_spin)
        layout.addWidget(self.speed_widget)

        self.lock_pivot_btn = QtWidgets.QPushButton("Lock Pivot to Selection")
        self.lock_pivot_btn.clicked.connect(self._toggle_pivot_lock)
        layout.addWidget(self.lock_pivot_btn)

        pivot_box = QtWidgets.QGroupBox("Pivot Auto-Selection")
        pivot_layout = QtWidgets.QVBoxLayout(pivot_box)
        self.pivot_mouse = QtWidgets.QCheckBox("Mouse Cursor")
        self.pivot_mouse.setChecked(prefs.get_bool(prefs.PIVOT_MOUSE))
        self.pivot_mouse.toggled.connect(
            lambda v: prefs.set_bool(prefs.PIVOT_MOUSE, v)
        )
        self.pivot_selection = QtWidgets.QCheckBox("Selection")
        self.pivot_selection.setChecked(prefs.get_bool(prefs.PIVOT_SELECTION))
        self.pivot_selection.toggled.connect(
            lambda v: prefs.set_bool(prefs.PIVOT_SELECTION, v)
        )
        self.pivot_center = QtWidgets.QCheckBox("Screen Center")
        self.pivot_center.setChecked(prefs.get_bool(prefs.PIVOT_CENTER))
        self.pivot_center.toggled.connect(
            lambda v: prefs.set_bool(prefs.PIVOT_CENTER, v)
        )
        self.pivot_last = QtWidgets.QCheckBox("Last Used")
        self.pivot_last.setChecked(prefs.get_bool(prefs.PIVOT_LAST_USED))
        self.pivot_last.toggled.connect(
            lambda v: prefs.set_bool(prefs.PIVOT_LAST_USED, v)
        )
        pivot_layout.addWidget(self.pivot_mouse)
        pivot_layout.addWidget(self.pivot_selection)
        pivot_layout.addWidget(self.pivot_center)
        pivot_layout.addWidget(self.pivot_last)

        fallback_row = QtWidgets.QHBoxLayout()
        fallback_row.addWidget(QtWidgets.QLabel("Fallback"))
        self.fallback = QtWidgets.QComboBox()
        self.fallback.addItems(["origin", "target"])
        self.fallback.setCurrentText(prefs.get_str(prefs.PIVOT_FALLBACK))
        self.fallback.currentTextChanged.connect(
            lambda v: prefs.set_str(prefs.PIVOT_FALLBACK, v)
        )
        fallback_row.addWidget(self.fallback)
        pivot_layout.addLayout(fallback_row)
        layout.addWidget(pivot_box)

        self.recenter_chk = QtWidgets.QCheckBox("Recenter on Pan")
        self.recenter_chk.setChecked(prefs.get_bool(prefs.RECENTER_ON_PAN))
        self.recenter_chk.toggled.connect(
            lambda v: prefs.set_bool(prefs.RECENTER_ON_PAN, v)
        )
        layout.addWidget(self.recenter_chk)

        url_row = QtWidgets.QHBoxLayout()
        url_row.addWidget(QtWidgets.QLabel("Server"))
        self.url_edit = QtWidgets.QLineEdit(prefs.get_str(prefs.URL))
        self.url_edit.editingFinished.connect(
            lambda: prefs.set_str(prefs.URL, self.url_edit.text().strip())
        )
        url_row.addWidget(self.url_edit)
        layout.addLayout(url_row)

        self.auto_chk = QtWidgets.QCheckBox("Auto Connect")
        self.auto_chk.setChecked(prefs.get_bool(prefs.AUTO_CONNECT))
        self.auto_chk.toggled.connect(
            lambda v: prefs.set_bool(prefs.AUTO_CONNECT, v)
        )
        layout.addWidget(self.auto_chk)

        self.debug_chk = QtWidgets.QCheckBox("Script Editor debug log")
        self.debug_chk.setChecked(prefs.get_bool(prefs.DEBUG_LOG))
        self.debug_chk.toggled.connect(self._toggle_debug)
        layout.addWidget(self.debug_chk)

        reset_btn = QtWidgets.QPushButton("Reset to Defaults")
        reset_btn.clicked.connect(self._reset)
        layout.addWidget(reset_btn)
        layout.addStretch(1)

    def _handler(self):
        return openaxis_handler.get_handler()

    def _toggle_connect(self) -> None:
        handler = self._handler()
        if handler.is_running():
            handler.stop()
        else:
            handler.start(prefs.get_str(prefs.URL))
        self._refresh_status()

    def _set_mode(self, mode: str) -> None:
        self._handler().set_navigation_mode(mode)
        self._refresh_status()

    def _toggle_ground(self) -> None:
        handler = self._handler()
        handler.set_lock_preferences(handler.lock_roll, not handler.lock_translation_plane)
        self._refresh_status()

    def _toggle_horizon(self) -> None:
        handler = self._handler()
        handler.set_lock_preferences(not handler.lock_roll, handler.lock_translation_plane)
        self._refresh_status()

    def _toggle_pivot_lock(self) -> None:
        handler = self._handler()
        if handler.has_locked_pivot:
            handler.clear_locked_pivot()
        elif not handler.set_locked_pivot():
            cmds.warning("Rotatrix: no selection to lock pivot to")
        self._refresh_status()

    def _toggle_pen(self, enabled: bool) -> None:
        prefs.set_bool(prefs.PEN_NAV, enabled)
        if enabled:
            pen_nav.start()
        else:
            pen_nav.shutdown()
        self._refresh_status()

    def _toggle_debug(self, enabled: bool) -> None:
        prefs.set_bool(prefs.DEBUG_LOG, enabled)
        rotatrix_log.set_debug(enabled)

    def _reset(self) -> None:
        prefs.reset_visible()
        self.pivot_mouse.setChecked(prefs.get_bool(prefs.PIVOT_MOUSE))
        self.pivot_selection.setChecked(prefs.get_bool(prefs.PIVOT_SELECTION))
        self.pivot_center.setChecked(prefs.get_bool(prefs.PIVOT_CENTER))
        self.pivot_last.setChecked(prefs.get_bool(prefs.PIVOT_LAST_USED))
        self.fallback.setCurrentText(prefs.get_str(prefs.PIVOT_FALLBACK))
        self.recenter_chk.setChecked(prefs.get_bool(prefs.RECENTER_ON_PAN))
        self.speed_spin.setValue(prefs.get_float(prefs.WALK_SPEED))
        self.debug_chk.setChecked(prefs.get_bool(prefs.DEBUG_LOG))
        rotatrix_log.set_debug(prefs.get_bool(prefs.DEBUG_LOG))
        self.pen_chk.setChecked(prefs.get_bool(prefs.PEN_NAV))
        self.pen_probe.setChecked(prefs.get_bool(prefs.PEN_PROBE))
        if prefs.get_bool(prefs.PEN_NAV):
            pen_nav.start()
        else:
            pen_nav.shutdown()
        self._handler().set_navigation_mode(prefs.get_str(prefs.NAV_MODE))
        self._handler().set_lock_preferences(
            prefs.get_bool(prefs.LOCK_ROLL),
            prefs.get_bool(prefs.LOCK_PLANE),
        )
        self._refresh_status()

    def _refresh_status(self) -> None:
        handler = self._handler()
        state = handler.connection_state
        if state == "connected":
            self.status_btn.setText("Connected — click to disconnect")
        elif state == "connecting":
            self.status_btn.setText("Connecting… — click to cancel")
        else:
            self.status_btn.setText("Connect")

        stats = handler.last_motion_stats
        self.stats_label.setText(f"{stats['fps']} fps" if stats else "")

        is_orbit = handler.navigation_mode == "orbit"
        self.orbit_btn.setChecked(is_orbit)
        self.free_btn.setChecked(not is_orbit)
        self.ground_btn.setVisible(not is_orbit)
        self.horizon_btn.setVisible(not is_orbit)
        self.speed_widget.setVisible(not is_orbit)
        self.ground_btn.setChecked(handler.lock_translation_plane)
        self.horizon_btn.setChecked(handler.lock_roll)
        self.lock_pivot_btn.setVisible(is_orbit)
        self.lock_pivot_btn.setText(
            "Clear Pivot Lock" if handler.has_locked_pivot else "Lock Pivot to Selection"
        )
        self.pen_status.setText(pen_nav.status_text())

    def closeEvent(self, event) -> None:
        self._timer.stop()
        super().closeEvent(event)


def show_panel() -> None:
    global _PANEL
    if _PANEL is not None:
        try:
            _PANEL.show()
            _PANEL.raise_()
            _PANEL.activateWindow()
            return
        except Exception:
            _PANEL = None
    _PANEL = RotatrixPanel()
    _PANEL.show()


def close_panel() -> None:
    global _PANEL
    if _PANEL is not None:
        try:
            _PANEL.close()
            _PANEL.deleteLater()
        except Exception:
            pass
        _PANEL = None


def create_menu() -> None:
    delete_menu()
    cmds.menu(MENU_NAME, label="Rotatrix", parent="MayaWindow", tearOff=True)
    cmds.menuItem(label="Control Panel", command=lambda *_: show_panel())
    cmds.menuItem(label="Connect", command=lambda *_: openaxis_handler.get_handler().start(prefs.get_str(prefs.URL)))
    cmds.menuItem(label="Disconnect", command=lambda *_: openaxis_handler.get_handler().stop())


def delete_menu() -> None:
    if cmds.menu(MENU_NAME, exists=True):
        cmds.deleteUI(MENU_NAME)
