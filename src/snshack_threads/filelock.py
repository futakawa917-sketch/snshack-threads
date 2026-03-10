"""Simple file-based locking for JSON read/write operations.

Uses fcntl.flock on Unix to prevent concurrent writes to the same file.
"""

from __future__ import annotations

import fcntl
import logging
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)


@contextmanager
def file_lock(path: Path, timeout: float = 10.0):
    """Acquire an exclusive file lock on a .lock file adjacent to path.

    Usage:
        with file_lock(my_json_path):
            data = json.loads(my_json_path.read_text())
            # ... modify data ...
            my_json_path.write_text(json.dumps(data))

    The lock file is created automatically and never deleted (harmless).
    """
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    lock_fd = None
    try:
        lock_fd = open(lock_path, "w")
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        yield
    finally:
        if lock_fd is not None:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                lock_fd.close()
            except OSError:
                pass
