from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import BinaryIO

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


class BootLock:
    """跨进程启动锁：多 worker 同时首启时，只有一个进程执行迁移与种子。"""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._handle: BinaryIO | None = None

    def acquire(self, timeout: float = 60.0) -> bool:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle = self._path.open("a+b")
        deadline = time.monotonic() + timeout
        while True:
            try:
                if sys.platform == "win32":
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                if time.monotonic() >= deadline:
                    handle.close()
                    return False
                time.sleep(0.1)
                continue
            handle.seek(0)
            handle.write(str(os.getpid()).encode())
            handle.truncate()
            handle.flush()
            self._handle = handle
            return True

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            if sys.platform == "win32":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        handle.close()
