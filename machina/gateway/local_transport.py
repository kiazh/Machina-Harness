"""Transport scope for local-only capabilities.

The TUI owns a private stdio channel. The notch is also local, but reaches the
gateway over loopback HTTP; it authenticates with a 0600 notch token. This
context flag lets a single HTTP request opt into the same local-only surface
without weakening the shared API key for remote clients.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

_notch_local: ContextVar[bool] = ContextVar("machina_notch_local", default=False)


def notch_local() -> bool:
    return _notch_local.get()


@contextmanager
def allow_notch_local() -> Iterator[None]:
    token = _notch_local.set(True)
    try:
        yield
    finally:
        _notch_local.reset(token)
