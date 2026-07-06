"""Minimal stand-ins for the GObject/GLib APIs used by vendored Meld code.

The vendored engine touches GTK's object system in exactly two ways: the
Differ emits a "diffs-changed" signal, and the background matcher schedules
callbacks onto the main loop with GLib.idle_add. This module provides just
enough of both, with a pluggable scheduler so the TUI can route idle
callbacks onto its own event loop.
"""

from typing import Any, Callable, Optional


class _SignalFlags:
    RUN_FIRST = 1
    RUN_LAST = 2


class _GObjectBase:
    """Signal-capable base class mimicking GObject.GObject."""

    def __init__(self) -> None:
        self._signal_handlers: dict = {}
        self._next_handler_id = 1

    def connect(self, signal: str, callback: Callable, *user_data: Any) -> int:
        handler_id = self._next_handler_id
        self._next_handler_id += 1
        self._signal_handlers[handler_id] = (signal, callback, user_data)
        return handler_id

    def disconnect(self, handler_id: int) -> None:
        self._signal_handlers.pop(handler_id, None)

    def emit(self, signal: str, *args: Any) -> None:
        for sig, callback, user_data in list(self._signal_handlers.values()):
            if sig == signal:
                callback(self, *args, *user_data)


class GObject:
    """Namespace mirroring gi.repository.GObject as used by meld code."""

    SignalFlags = _SignalFlags
    GObject = _GObjectBase


# GLib.idle_add scheduling. Defaults to immediate invocation, which is
# correct for synchronous/CLI use; the TUI must install a scheduler that
# defers onto its event loop (e.g. asyncio call_soon_threadsafe), since
# helpers.py calls idle_add from worker threads.
_idle_scheduler: Optional[Callable[..., Any]] = None


def set_idle_scheduler(scheduler: Optional[Callable[..., Any]]) -> None:
    global _idle_scheduler
    _idle_scheduler = scheduler


class GLib:
    """Namespace mirroring gi.repository.GLib as used by meld code."""

    @staticmethod
    def idle_add(callback: Callable, *args: Any) -> None:
        if _idle_scheduler is not None:
            _idle_scheduler(callback, *args)
        else:
            callback(*args)
