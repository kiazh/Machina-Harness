"""Low-level filesystem primitives shared by the tool, workspace, and RAG layers.

These are the single definition of the platform probes and flag sets used by
descriptor-anchored I/O. The module deliberately imports only the standard
library so every layer can use it without pulling a sibling package.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path


def nofollow() -> int:
    """``O_NOFOLLOW`` where available, else ``0``."""
    return getattr(os, "O_NOFOLLOW", 0)


def nonblock() -> int:
    """``O_NONBLOCK`` where available, else ``0``."""
    return getattr(os, "O_NONBLOCK", 0)


def dir_flags() -> int:
    """Read-only directory descriptor flags (never follows a symlink)."""
    return os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | nofollow()


def dir_fd_supported(*operations: object) -> bool:
    """Whether this platform supports ``dir_fd=`` for *operations*.

    Callers pass the exact set they use: an anchored write needs
    ``open``/``rename``/``stat``/``unlink``/``mkdir`` while a read walk needs
    only ``open``/``stat``.
    """
    try:
        supported = os.supports_dir_fd
    except AttributeError:
        return False
    return all(operation in supported for operation in operations)


def close_quietly(fd: int) -> None:
    """Close *fd*, ignoring an already-closed/invalid descriptor."""
    try:
        os.close(fd)
    except OSError:
        pass


def fsync_dir_best_effort(
    directory: Path, *, opener: Callable[..., int] = os.open
) -> None:
    """Persist a rename on POSIX; unavailable directory fsync is not fatal.

    ``opener`` lets a caller keep its own module-level open seam (tests pin
    some of those indirections to simulate descriptor races).
    """
    try:
        fd = opener(directory, dir_flags())
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        close_quietly(fd)
