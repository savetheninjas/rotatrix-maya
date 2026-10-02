# Rotatrix for Maya

Community OpenAxis plugin that gives Autodesk Maya the same 3-axis trackball
orbit as the official Rotatrix Blender extension, plus the same Wacom pen
chords.

This is not an official Icebound Flame / Rotatrix product. Maya is not in
the Rotatrix Integrations list yet. The plugin talks to the same local
OpenAxis server (`ws://127.0.0.1:6607`) the Blender add-on uses: the
Rotatrix app owns the trackball math, and the plugin applies the view. The
pen path does not need the Rotatrix app.

## Requirements

- Autodesk Maya 2020–2027 (developed against Maya 2027). Maya 2020 must be launched in Python 3 mode (`MAYA_PYTHON_VERSION=3`). Trackball connection needs Maya 2023 or newer; pen camera works on the whole range.
- Rotatrix app running, device connected (pen camera works without it)
- Python 3.7 or newer (bundled with Maya; Maya 2020’s default is Python 2.7).

## Install

From the repository root, double-click **`install.bat`**, or run:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

That script:

1. Copies `plug-ins`, `scripts`, and `lib` into
   `Documents\maya\modules\rotatrix_maya\` and writes `rotatrix_maya.mod`
   so Maya loads them.
2. Merges the Maya profile into `%LOCALAPPDATA%\Rotatrix\config.yaml`
   (other Rotatrix user settings are kept).
3. Restarts Rotatrix if it is already running, so the profile is
   actually loaded. Use `-NoRestartRotatrix` if you would rather Apply in
   the Config UI yourself.

Restart Maya. A **Rotatrix** menu should appear and the plugin should
auto-connect. Open **Rotatrix > Control Panel** and confirm it reads
**Connected**. Status in the Rotatrix app should show profile **maya**
when Maya is focused.

## Usage (matches the Blender profile)

Orbit mode (default):

- Hold **Win** or **`** — 3D orbit on all three axes (including twist/roll)
- … add **Shift** — pan and twist-zoom
- … add **Btn1** (bottom-left) — snap to world axes
- Hold **Btn4** (top-right) — pan first; add **Shift** to orbit
- The Rotatrix OSD overlay (same as Blender) only appears once this Maya
  profile is active. If you still see Mouse / twist-to-scroll, the profile
  did not load.

Free Camera mode is a toggle in the Maya control panel.

If Win does nothing, Maya is connected but the Rotatrix profile is not
matching. Open **Status** in the Rotatrix app with Maya focused, note the
exact app name, and put that name in the `app:...` trigger in the Maya
profile.

## How it works

1. Plugin connects as OpenAxis client `Maya` with tags `app.maya` + `orbit`.
2. On motion start it sends the current camera pose, world orientation
   (Y-up or Z-up), and an orbit pivot (selection center, or world origin).
3. The Rotatrix app streams `camera.pose` frames; Maya applies them to the
   active model-panel camera.

Logs: `%TEMP%\RotatrixMaya.log` and the Maya Script Editor (`[Rotatrix]`).

## ZBrush

ZBrush 2026 or newer. Sideways and up-and-down use ZBrush's own background stroke. Twist is added to the Z rotation after that stroke. Quit ZBrush and start it again after install so the plugin loads.

Pen, with ZBrush in front:

- Hold **Win** and touch the canvas — X/Y orbit. Twist in place turns the model about the fixed view.
- **Win+Shift** — X/Y pan, twist zooms

The trackball uses the same Win / Win+Shift chords. A scribble does not add twist. Logs: `%TEMP%\RotatrixZBrush.log`.
