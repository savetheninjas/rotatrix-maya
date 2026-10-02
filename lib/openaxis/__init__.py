"""OpenAxis v0.2 — Python client for the OpenAxis 6DoF input protocol.

Import from the submodule that matches the tier you need — each one declares
its public surface in ``__all__``:

- ``openaxis.types``    wire-format messages and enums (no third-party deps)
- ``openaxis.geometry`` pure-Python Vec3/Quat and pose conversion helpers
- ``openaxis.client``   the async WebSocket client (requires websockets + msgpack)

Nothing is re-exported at the package root, so importing the geometry or wire
types never pulls in the websockets/msgpack transport stack.
"""
