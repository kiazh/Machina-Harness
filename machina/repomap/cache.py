"""Content-hash cache: repeat inventory runs skip parsing, never serve stale bytes."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable

MAX_BYTES_PER_FILE = 2_000_000


def _entry_is_valid(entry: object) -> bool:
    """Whether a persisted entry has the shape this cache can trust."""
    if not isinstance(entry, dict):
        return False
    sha256 = entry.get("sha256")
    mtime = entry.get("mtime")
    size = entry.get("size")
    loc = entry.get("loc")
    imports = entry.get("imports")
    is_entry = entry.get("entry")
    return (
        isinstance(sha256, str)
        and bool(sha256)
        and isinstance(mtime, (int, float))
        and not isinstance(mtime, bool)
        and isinstance(size, int)
        and not isinstance(size, bool)
        and size >= 0
        and isinstance(loc, int)
        and not isinstance(loc, bool)
        and loc >= 0
        and isinstance(imports, list)
        and all(isinstance(name, str) for name in imports)
        and isinstance(is_entry, bool)
    )


class ContentHashCache:
    """Maps absolute path -> (content sha256, parsed tuple).

    ``lookup`` returns the parsed tuple only after re-hashing the file's
    current bytes and matching the stored sha256, so a rewrite that preserves
    mtime and size (a same-stat write) is detected instead of serving stale
    inventory data (R4-088). Only parsing is skipped — every lookup performs
    one bounded read for hashing; a stat mismatch short-circuits before the
    read. ``store`` re-stats and re-hashes the file it records, and binds the
    entry only to the byte snapshot the caller parsed (MCH-003).
    """

    def __init__(self, path: str | None = None) -> None:
        self.path = path
        self._entries: dict[str, dict] = {}
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    loaded = json.load(fh)
            except (OSError, ValueError):
                loaded = {}
            if isinstance(loaded, dict):
                self._entries = {
                    key: value
                    for key, value in loaded.items()
                    if isinstance(key, str) and _entry_is_valid(value)
                }

    def lookup(self, abs_path: str) -> tuple | None:
        ent = self._entries.get(abs_path)
        if not _entry_is_valid(ent):
            return None
        expected = ent.get("sha256")
        if not expected:
            return None
        try:
            st = os.stat(abs_path)
            if st.st_size > MAX_BYTES_PER_FILE:
                return None
            if ent.get("mtime") != st.st_mtime or ent.get("size") != st.st_size:
                return None  # stat changed -> caller re-reads + store() re-hashes
            with open(abs_path, "rb") as fh:
                current = fh.read(MAX_BYTES_PER_FILE + 1)
            if len(current) > MAX_BYTES_PER_FILE or len(current) != st.st_size:
                return None
            digest = hashlib.sha256(current).hexdigest()
        except OSError:
            return None
        if digest != expected:
            return None  # content changed under an unchanged stat
        return (ent["loc"], list(ent["imports"]), bool(ent["entry"]))

    def store(self, abs_path: str, parsed: tuple, *, source: bytes | None = None) -> bool:
        """Store *parsed* only when it belongs to the current file bytes.

        *source* is the byte buffer that was actually parsed (or ``None`` for
        legacy callers). The digest of the current file is compared against
        it, so a file mutated between the scanner's read and this insertion
        is never bound to the old parse (MCH-003). Returns ``True`` when the
        entry was recorded, ``False`` when insertion was declined.
        """
        loc, imports, entry = parsed
        try:
            st = os.stat(abs_path)
            if st.st_size > MAX_BYTES_PER_FILE:
                return False
            with open(abs_path, "rb") as fh:
                current = fh.read(MAX_BYTES_PER_FILE + 1)
        except OSError:
            return False
        if len(current) > MAX_BYTES_PER_FILE:
            return False
        digest = hashlib.sha256(current).hexdigest()
        if source is not None and hashlib.sha256(source).hexdigest() != digest:
            return False
        if st.st_size != len(current):
            # The pathname changed between stat and read; do not bind a
            # mixed observation to either state.
            return False
        self._entries[abs_path] = {
            "mtime": st.st_mtime,
            "size": st.st_size,
            "sha256": digest,
            "loc": loc,
            "imports": list(imports),
            "entry": bool(entry),
        }
        return True

    def prune(self, root: str, live_paths: Iterable[str]) -> int:
        """Drop entries under *root* that the completed scan did not observe.

        A cache can be shared across roots; entries outside *root* are left
        untouched. Callers must only invoke this after a complete scan, since
        a partial scan cannot distinguish a deleted path from an unread one.
        """
        root_abs = os.path.abspath(root)
        live = {os.path.abspath(path) for path in live_paths}
        stale: list[str] = []
        for path in self._entries:
            if not isinstance(path, str) or not os.path.isabs(path):
                continue
            candidate = os.path.abspath(path)
            try:
                within_root = os.path.commonpath((candidate, root_abs)) == root_abs
            except ValueError:
                continue
            if within_root and candidate not in live:
                stale.append(path)
        for path in stale:
            del self._entries[path]
        return len(stale)

    def save(self) -> None:
        if not self.path:
            return
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self._entries, fh, sort_keys=True)
        os.replace(tmp, self.path)
