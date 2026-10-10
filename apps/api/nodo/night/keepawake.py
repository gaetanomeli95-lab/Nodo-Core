"""Keep a Windows PC awake while the night shift is on duty (SetThreadExecutionState). No-op elsewhere.

The display may still turn off; only system sleep is prevented, and only while NODO asks for it."""
from __future__ import annotations

import logging
import sys

log = logging.getLogger("nodo.keepawake")

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
_held = False


def hold() -> bool:
    global _held
    if sys.platform != "win32" or _held:
        return _held
    try:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)  # type: ignore[attr-defined]
        _held = True
        log.info("keep-awake: system sleep blocked during the night shift")
    except Exception as e:  # pragma: no cover - Windows only
        log.warning("keep-awake unavailable: %s", e)
    return _held


def release() -> None:
    global _held
    if sys.platform != "win32" or not _held:
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover
        pass
    _held = False
