from __future__ import annotations

from pathlib import Path
from typing import IO

import portalocker


class MutexBusyError(RuntimeError):
    pass


class LocalMutex:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: IO[str] | None = None

    def __enter__(self) -> LocalMutex:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a+", encoding="utf-8")
        try:
            portalocker.lock(self._handle, portalocker.LOCK_EX | portalocker.LOCK_NB)
        except portalocker.exceptions.LockException as exc:
            self._handle.close()
            self._handle = None
            raise MutexBusyError("another dispatcher tick owns the local mutex") from exc
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._handle is not None:
            portalocker.unlock(self._handle)
            self._handle.close()
            self._handle = None
