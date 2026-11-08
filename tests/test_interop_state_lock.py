"""Concurrent interop state writes must serialize across the lock file.

The Windows lock-file initializer used to race: two first writers could both
observe an empty file, then the loser's sentinel write raised PermissionError
after the winner took the msvcrt lock. This reproduces the read-modify-write
window with all three platform keys.
"""

from __future__ import annotations

import os
import threading
import time
import types

import pytest

from machina import interop


def test_concurrent_state_writers_keep_all_keys(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(interop.STATE_ENV_OVERRIDE, str(tmp_path / "state" / "interop.json"))
    real_save = interop._save_state
    barrier = threading.Barrier(3)
    errors: list[BaseException] = []

    def slow_save(state):
        # Widen the window between acquiring the lock and replacing the file.
        time.sleep(0.05)
        real_save(state)

    monkeypatch.setattr(interop, "_save_state", slow_save)

    def writer(key: str) -> None:
        barrier.wait()
        try:
            interop._record_state(
                interop.PLATFORMS[key],
                enabled=True,
                scope="global",
                verify={"ok": True, "detail": key},
                binary=f"/fake/bin/{key}",
                version=f"{key} v1",
            )
        except BaseException as exc:  # pragma: no cover - failure path
            errors.append(exc)

    threads = [
        threading.Thread(target=writer, args=(key,))
        for key in ("opencode", "codex", "hermes")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not any(thread.is_alive() for thread in threads), "writers must not deadlock"
    assert errors == []
    state = interop.load_state()
    for key in ("opencode", "codex", "hermes"):
        assert state[key]["version"] == f"{key} v1"


@pytest.mark.skipif(os.name != "nt", reason="Windows msvcrt lock-file initialization")
def test_sentinel_write_race_is_tolerated_when_the_byte_exists(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(interop.STATE_ENV_OVERRIDE, str(tmp_path / "interop.json"))
    lock_path = tmp_path / "interop.json.lock"
    lock_path.write_bytes(b"\0")

    calls = 0
    real_fstat = os.fstat

    def fake_fstat(fd):
        nonlocal calls
        calls += 1
        # The writer observes the empty file before the other writer's
        # sentinel lands; the re-check must then see the byte.
        if calls == 1:
            return types.SimpleNamespace(st_size=0)
        return real_fstat(fd)

    def losing_write(fd, data):
        raise PermissionError(13, "locked by the winning writer")

    monkeypatch.setattr(interop.os, "fstat", fake_fstat)
    monkeypatch.setattr(interop.os, "write", losing_write)

    with interop._state_lock():
        pass

    assert calls >= 2


@pytest.mark.skipif(os.name != "nt", reason="Windows msvcrt lock-file initialization")
def test_sentinel_race_reraises_when_the_byte_is_absent(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(interop.STATE_ENV_OVERRIDE, str(tmp_path / "interop.json"))

    monkeypatch.setattr(
        interop.os, "fstat", lambda fd: types.SimpleNamespace(st_size=0)
    )
    monkeypatch.setattr(
        interop.os,
        "write",
        lambda fd, data: (_ for _ in ()).throw(PermissionError(13, "denied")),
    )

    with pytest.raises(PermissionError):
        with interop._state_lock():
            pass
