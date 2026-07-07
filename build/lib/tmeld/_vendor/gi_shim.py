"""Minimal stand-ins for the GObject/GLib APIs used by vendored Meld code.

The vendored engine touches GTK's object system in exactly two ways: the
Differ emits a "diffs-changed" signal, and the background matcher schedules
callbacks onto the main loop with GLib.idle_add. This module provides just
enough of both, with a pluggable scheduler so the TUI can route idle
callbacks onto its own event loop.
"""

import os
import stat as _stat
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


class _GLibError(Exception):
    """GLib.Error lookalike, as raised by the Gio shim below."""

    def __init__(self, message: str = "", domain: str = "g-io-error-quark",
                 code: int = 0) -> None:
        super().__init__(message)
        self.domain = domain
        self.code = code

    def matches(self, domain: str, code: int) -> bool:
        return self.domain == domain and self.code == code


class GLib:
    """Namespace mirroring gi.repository.GLib as used by meld code."""

    Error = _GLibError

    @staticmethod
    def idle_add(callback: Callable, *args: Any) -> None:
        if _idle_scheduler is not None:
            _idle_scheduler(callback, *args)
        else:
            callback(*args)


# --- Gio: just the file-info surface meld/vc/_vc.py touches -----------------
# (Vc.get_entries enumerates a directory; Vc.get_entry stats one path.)


class _GioFileInfo:
    def __init__(self, name: str, is_dir: bool) -> None:
        self._name = name
        self._is_dir = is_dir

    def get_name(self) -> str:
        return self._name

    def get_display_name(self) -> str:
        return self._name

    def get_file_type(self) -> int:
        return Gio.FileType.DIRECTORY if self._is_dir else Gio.FileType.REGULAR


class _GioFileEnumerator:
    def __init__(self, base: str, entries) -> None:
        self._base = base
        self._entries = entries

    def __iter__(self):
        return iter(self._entries)

    def get_child(self, file_info: _GioFileInfo) -> "_GioFile":
        return _GioFile(os.path.join(self._base, file_info.get_name()))


class _GioFile:
    def __init__(self, path: str) -> None:
        self._path = path

    def get_path(self) -> str:
        return self._path

    def enumerate_children(self, attrs, flags, cancellable) -> _GioFileEnumerator:
        try:
            with os.scandir(self._path) as scan:
                # NOFOLLOW_SYMLINKS is the only flag callers pass
                entries = [
                    _GioFileInfo(e.name, e.is_dir(follow_symlinks=False))
                    for e in scan
                ]
        except PermissionError as err:
            raise _GLibError(
                str(err), code=Gio.IOErrorEnum.PERMISSION_DENIED
            ) from err
        except OSError as err:
            raise _GLibError(str(err), code=Gio.IOErrorEnum.FAILED) from err
        return _GioFileEnumerator(self._path, entries)

    def query_info(self, attrs, flags, cancellable) -> _GioFileInfo:
        try:
            st_result = os.lstat(self._path)
        except OSError as err:
            raise _GLibError(
                str(err), code=Gio.IOErrorEnum.NOT_FOUND
            ) from err
        return _GioFileInfo(
            os.path.basename(self._path) or self._path,
            _stat.S_ISDIR(st_result.st_mode),
        )


class Gio:
    """Namespace mirroring gi.repository.Gio as used by meld code."""

    class FileType:
        UNKNOWN = 0
        REGULAR = 1
        DIRECTORY = 2
        SYMBOLIC_LINK = 3

    class FileQueryInfoFlags:
        NONE = 0
        NOFOLLOW_SYMLINKS = 1

    class IOErrorEnum:
        FAILED = 0
        NOT_FOUND = 1
        PERMISSION_DENIED = 14

    class File:
        @staticmethod
        def new_for_path(path: str) -> _GioFile:
            return _GioFile(path)

    @staticmethod
    def io_error_quark() -> str:
        return "g-io-error-quark"
