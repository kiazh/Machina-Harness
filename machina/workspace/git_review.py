"""Bounded, read-only Git metadata and policy-authorized disk comparisons.

Git never inspects working-file contents here: status/diff-files/hash-object can
execute repository clean filters. Cached-only diffs, ls-files and cat-file do
not. The index stat cache avoids reading unchanged files, while every actual
disk read uses the same descriptor containment as the workspace editor.
"""

from __future__ import annotations

import asyncio
import contextlib
import difflib
import os
import re
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from weakref import WeakKeyDictionary

from machina.core.background_tasks import await_joined
from machina.workspace.files import WorkspaceFiles
from machina.workspace.models import WorkspaceError

MAX_ENTRIES = 2000
MAX_GIT_OUTPUT = 2 * 1024 * 1024
MAX_FILE_BYTES = 512 * 1024
MAX_PATCH_BYTES = 256 * 1024
MAX_DIFF_LINES = 4000
MAX_CHANGED_READS = 200
PROCESS_TIMEOUT = 4.0
REVIEW_TIMEOUT = 12.0

_PROCESS_SLOTS: WeakKeyDictionary[Any, asyncio.Semaphore] = WeakKeyDictionary()
_INDEX_STATS = re.compile(
    rb"  ctime: (\d+):(\d+)\n  mtime: (\d+):(\d+)\n"
    rb"  dev: (\d+)\tino: (\d+)\n  uid: (\d+)\tgid: (\d+)\n"
    rb"  size: (\d+)\tflags: ([0-9a-fA-F]+)\n"
)
_OID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


class GitFailure(Exception):
    """A safe diagnostic, deliberately excluding Git stderr and local paths."""


@dataclass(frozen=True)
class ProcessResult:
    stdout: bytes
    returncode: int
    truncated: bool = False


async def run_process(
    command: list[str],
    *,
    cwd: Path,
    timeout: float = PROCESS_TIMEOUT,
    max_bytes: int | None = None,
    env: dict[str, str] | None = None,
    input_data: bytes | None = None,
) -> ProcessResult:
    """Bound queue/spawn/run time, then join owned teardown before returning.

    A spawn already handed to the OS must finish establishing ownership before
    cancellation can return. The deadline includes that spawn wait, and cleanup
    reaps its eventual process while retaining the semaphore slot throughout.
    """
    limit = MAX_GIT_OUTPUT if max_bytes is None else max_bytes
    loop = asyncio.get_running_loop()
    slots = _PROCESS_SLOTS.setdefault(loop, asyncio.Semaphore(4))
    acquired = False
    process: asyncio.subprocess.Process | None = None
    spawning: asyncio.Task[asyncio.subprocess.Process] | None = None
    tasks: list[asyncio.Task[Any]] = []
    truncated = False

    def kill() -> None:
        if process is not None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()

    async def read(stream: asyncio.StreamReader, cap: int) -> bytes:
        nonlocal truncated
        chunks: list[bytes] = []
        length = 0
        while data := await stream.read(65536):
            remaining = cap - length
            chunks.append(data[:remaining])
            length += min(len(data), remaining)
            if len(data) > remaining:
                truncated = True
                kill()
                # Drain after killing: a paused full pipe can otherwise keep
                # asyncio's process wait from completing on Windows/POSIX.
        return b"".join(chunks)

    async def write_input() -> None:
        if process is not None and process.stdin is not None:
            try:
                process.stdin.write(input_data or b"")
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                process.stdin.close()

    async def cleanup() -> None:
        nonlocal process
        if process is None and spawning is not None:
            try:
                process = await spawning
            except OSError:
                return
        if process is None:
            return
        kill()
        if process.stdin is not None:
            process.stdin.close()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

        async def drain(stream: asyncio.StreamReader | None) -> None:
            if stream is not None:
                while await stream.read(65536):
                    pass

        await asyncio.gather(drain(process.stdout), drain(process.stderr), process.wait())

    try:
        async with asyncio.timeout(timeout):
            await slots.acquire()
            acquired = True
            spawning = asyncio.create_task(
                asyncio.create_subprocess_exec(
                    *command,
                    cwd=cwd,
                    env=env,
                    stdin=asyncio.subprocess.PIPE
                    if input_data is not None
                    else asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            )
            process = await asyncio.shield(spawning)
            assert process.stdout is not None and process.stderr is not None
            tasks = [
                asyncio.create_task(read(process.stdout, limit)),
                asyncio.create_task(read(process.stderr, min(limit, 16384))),
                asyncio.create_task(write_input()),
                asyncio.create_task(process.wait()),
            ]
            stdout, _stderr, _input, returncode = await asyncio.gather(*tasks)
            return ProcessResult(stdout, returncode, truncated)
    except BaseException as error:
        await await_joined(cleanup())
        if isinstance(error, TimeoutError):
            raise GitFailure("Git inspection timed out") from error
        if isinstance(error, OSError):
            raise GitFailure("Git could not be started") from error
        raise
    finally:
        if acquired:
            slots.release()


@dataclass(frozen=True)
class IndexEntry:
    path: str
    mode: str
    oid: str
    stage: int
    ctime_ns: int
    mtime_ns: int
    size: int
    flags: int


@dataclass(frozen=True)
class Content:
    data: bytes
    truncated: bool = False


def _decode_path(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise WorkspaceError("denied", "Git path is not valid UTF-8") from error


def _patch_path(path: str) -> str:
    # Keep ordinary paths readable; quote controls so a filename cannot create
    # fake diff headers or terminal escape sequences.
    if any(ord(char) < 32 or ord(char) == 127 for char in path) or '"' in path:
        import json

        return json.dumps(path, ensure_ascii=False)
    return path


def _render_patch(
    before: Content,
    after: Content,
    path: str,
    old_path: str | None,
    target: str,
    old_mode: str | None = None,
    mode: str | None = None,
) -> dict[str, Any]:
    binary = False
    try:
        left = before.data.decode("utf-8")
        right = after.data.decode("utf-8")
        binary = "\x00" in left or "\x00" in right
    except UnicodeDecodeError:
        left = right = ""
        binary = True
    truncated = before.truncated or after.truncated
    old = _patch_path(old_path or path)
    new = _patch_path(path)
    patch = ""
    if old_path is not None or (old_mode is not None and old_mode != mode):
        patch = f"diff --git a/{old} b/{new}\n"
    if old_path is not None:
        patch += f"rename from {old}\nrename to {new}\n"
    if old_mode is not None and old_mode != mode:
        if old_mode == "000000":
            patch += f"new file mode {mode}\n"
        elif mode == "000000":
            patch += f"deleted file mode {old_mode}\n"
        else:
            patch += f"old mode {old_mode}\nnew mode {mode}\n"
    if binary:
        patch += f"Binary files a/{old} and b/{new} differ\n"
    elif truncated:
        patch += "Diff omitted because the file exceeds the review size limit.\n"
    elif left != right:
        if left.count("\n") + right.count("\n") > MAX_DIFF_LINES:
            truncated = True
            patch += "Diff omitted because the file exceeds the review line limit.\n"
        else:
            for line in difflib.unified_diff(
                left.splitlines(keepends=True),
                right.splitlines(keepends=True),
                fromfile=f"a/{old}",
                tofile=f"b/{new}",
            ):
                patch += line
                if line and not line.endswith("\n"):
                    patch += "\n\\ No newline at end of file\n"
                if len(patch.encode("utf-8")) > MAX_PATCH_BYTES:
                    truncated = True
                    break
    encoded = patch.encode("utf-8")
    if len(encoded) > MAX_PATCH_BYTES:
        patch = encoded[:MAX_PATCH_BYTES].decode("utf-8", errors="ignore")
        truncated = True
    result: dict[str, Any] = {
        "path": path,
        "target": target,
        "patch": patch,
        "binary": binary,
        "truncated": truncated,
    }
    if old_path is not None:
        result["oldPath"] = old_path
    return result


class GitReview:
    """One inspection bound to a captured WorkspaceFiles instance."""

    def __init__(self, files: WorkspaceFiles) -> None:
        self.files = files
        self.executable = shutil.which("git")
        self.repo_root: Path | None = None
        # Selecting a workspace grants read access to its repository metadata;
        # this exact per-command exception does not change the user's config.
        self.safe_root = next(
            (parent for parent in (files.root, *files.root.parents) if (parent / ".git").exists()),
            files.root,
        )
        self.autocrlf = "false"
        self.filemode = False
        self.environment = {
            key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")
        }
        self.environment.update(
            GIT_OPTIONAL_LOCKS="0",
            GIT_TERMINAL_PROMPT="0",
            GIT_NO_LAZY_FETCH="1",
            GIT_LFS_SKIP_SMUDGE="1",
        )

    async def _git(
        self,
        *args: str,
        allow_failure: bool = False,
        max_bytes: int | None = None,
        input_data: bytes | None = None,
    ) -> ProcessResult:
        if self.executable is None:
            raise GitFailure("Git is not installed or is not available on PATH")
        command = [
            self.executable,
            "--no-pager",
            "--literal-pathspecs",
            "--no-optional-locks",
            "-c",
            f"safe.directory={self.safe_root.as_posix()}",
            "-c",
            "core.fsmonitor=false",
            "-c",
            f"core.hooksPath={os.devnull}",
            "-c",
            f"core.attributesFile={os.devnull}",
            "-c",
            "core.untrackedCache=false",
            "-c",
            "diff.external=",
            "-c",
            "gc.auto=0",
            "-c",
            "maintenance.auto=false",
            "-c",
            "protocol.allow=never",
            *args,
        ]
        result = await run_process(
            command,
            cwd=self.files.root,
            env=self.environment,
            max_bytes=max_bytes,
            input_data=input_data,
        )
        if result.returncode != 0 and not result.truncated and not allow_failure:
            raise GitFailure("Git inspection could not be completed")
        return result

    async def _prepare(self) -> bool:
        result, config = await asyncio.gather(
            self._git("rev-parse", "--show-toplevel", allow_failure=True),
            self._git(
                "config",
                "--null",
                "--get-regexp",
                r"^core\.(autocrlf|filemode)$",
                allow_failure=True,
            ),
        )
        if result.returncode != 0:
            bare = await self._git("rev-parse", "--is-bare-repository", allow_failure=True)
            if bare.returncode == 0 and bare.stdout.strip() == b"true":
                return False
            # Distinguish an absent repository from unreadable/unsafe metadata
            # without exposing stderr, usernames, paths or credentials.
            if any(
                (parent / ".git").exists() for parent in (self.files.root, *self.files.root.parents)
            ):
                raise GitFailure("Git repository metadata is unavailable")
            return False
        if result.truncated:
            raise GitFailure("Git repository metadata exceeds the review limit")
        self.repo_root = Path(_decode_path(result.stdout.removesuffix(b"\n"))).resolve()
        if not self.files.root.is_relative_to(self.repo_root):
            raise GitFailure("Git repository does not contain the workspace")
        for field in config.stdout.split(b"\x00")[:-1]:
            key, _, value = field.partition(b"\n")
            text = value.decode("utf-8", errors="replace").strip().lower()
            if key == b"core.autocrlf":
                self.autocrlf = text
            elif key == b"core.filemode":
                self.filemode = text == "true"
        return True

    def _authorize(self, path: str, *, symlink: bool = False) -> Path:
        normalized = self.files.normalized_rel(path)
        if not normalized:
            raise WorkspaceError("not-regular", "Git review requires a file path")
        resolved = self.files.resolve(normalized)
        current = self.files.root
        parts = normalized.split("/")
        for index, part in enumerate(parts):
            current /= part
            try:
                info = current.lstat()
            except FileNotFoundError:
                break
            except OSError as error:
                raise WorkspaceError("denied", "Git path is not accessible") from error
            if stat.S_ISLNK(info.st_mode) and (index != len(parts) - 1 or not symlink):
                raise WorkspaceError("denied", "Symbolic-link Git diffs are not supported")
        return resolved

    async def _index(self, path: str = ".") -> tuple[dict[str, IndexEntry], bool]:
        result = await self._git("ls-files", "--stage", "--debug", "-z", "--", path)
        entries: dict[str, IndexEntry] = {}
        position = 0
        while position < len(result.stdout):
            end = result.stdout.find(b"\x00", position)
            if end < 0:
                break
            header, raw_path = result.stdout[position:end].split(b"\t", 1)
            mode, oid, stage = header.decode("ascii").split(" ")
            stats = _INDEX_STATS.match(result.stdout, end + 1)
            if stats is None:
                if result.truncated:
                    break
                raise GitFailure("Git index format is unsupported")
            position = stats.end()
            try:
                name = _decode_path(raw_path)
                self.files.normalized_rel(name)
            except WorkspaceError:
                continue
            values = stats.groups()
            entries[name] = IndexEntry(
                name,
                mode,
                oid,
                int(stage),
                int(values[0]) * 1_000_000_000 + int(values[1]),
                int(values[2]) * 1_000_000_000 + int(values[3]),
                int(values[8]),
                int(values[9], 16),
            )
        return entries, result.truncated

    async def _staged(self) -> tuple[dict[str, dict[str, Any]], bool]:
        result = await self._git(
            "diff",
            "--cached",
            "--raw",
            "-z",
            "--no-abbrev",
            "--no-ext-diff",
            "--no-textconv",
            "--no-color",
            "--ignore-submodules=all",
            "--find-renames",
            "--relative",
            "--",
            ".",
        )
        fields = result.stdout.split(b"\x00")
        entries: dict[str, dict[str, Any]] = {}
        position = 0
        while position < len(fields) - 1:
            header = fields[position]
            position += 1
            metadata = header.removeprefix(b":").split(b" ")
            if len(metadata) != 5 or position >= len(fields) - 1:
                break
            old_mode, mode, old_oid, oid, status = metadata
            raw_path = fields[position]
            position += 1
            old_path = None
            if status.startswith((b"R", b"C")):
                if position >= len(fields) - 1:
                    break
                old_path = raw_path
                raw_path = fields[position]
                position += 1
            try:
                name = _decode_path(raw_path)
                self._authorize(name, symlink=True)
            except WorkspaceError:
                continue
            entry: dict[str, Any] = {
                "path": name,
                "code": status[:1].decode("ascii"),
                "oldOid": old_oid.decode("ascii"),
                "oid": oid.decode("ascii"),
                "mode": mode.decode("ascii"),
                "oldMode": old_mode.decode("ascii"),
            }
            if old_path is not None:
                try:
                    source = _decode_path(old_path)
                    self._authorize(source, symlink=True)
                except WorkspaceError:
                    # The selected subdirectory never returns a neighboring
                    # rename source or reads its old contents.
                    entry["code"] = "A"
                    entry["oldOid"] = "0" * len(entry["oid"])
                else:
                    entry["oldPath"] = source
            entries[name] = entry
        return entries, result.truncated

    async def _attributes(self, path: str) -> dict[str, str]:
        return (await self._attributes_many([path])).get(path, {})

    async def _attributes_many(self, paths: list[str]) -> dict[str, dict[str, str]]:
        if not paths:
            return {}
        result = await self._git(
            "check-attr",
            "-z",
            "filter",
            "working-tree-encoding",
            "ident",
            "text",
            "eol",
            "--stdin",
            input_data=b"".join(path.encode("utf-8") + b"\x00" for path in paths),
        )
        if result.truncated:
            raise WorkspaceError("too-large", "Git attributes exceed the review limit")
        values = result.stdout.split(b"\x00")
        attributes: dict[str, dict[str, str]] = {}
        for index in range(0, len(values) - 2, 3):
            attributes.setdefault(_decode_path(values[index]), {})[
                values[index + 1].decode("ascii")
            ] = values[index + 2].decode("utf-8", errors="replace")
        return attributes

    @staticmethod
    def _supported_attributes(attributes: dict[str, str]) -> None:
        if attributes.get("filter", "unspecified") not in ("unspecified", "unset"):
            raise WorkspaceError(
                "denied", "Git filter conversions are unavailable in read-only review"
            )
        if attributes.get("working-tree-encoding", "unspecified") not in ("unspecified", "unset"):
            raise WorkspaceError(
                "unsupported-encoding", "Git working-tree encoding conversion is unsupported"
            )
        if attributes.get("ident") == "set":
            raise WorkspaceError(
                "denied", "Git ident conversions are unavailable in read-only review"
            )

    def _read_disk(self, path: str) -> Content:
        resolved = self._authorize(path)
        descriptor = self.files._open_contained(path, resolved, display=path)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise WorkspaceError("not-regular", "Git path is not a regular file")
            self.files._verify_fd(descriptor, resolved, display=path)
            chunks: list[bytes] = []
            remaining = MAX_FILE_BYTES + 1
            while remaining:
                data = os.read(descriptor, min(65536, remaining))
                if not data:
                    break
                chunks.append(data)
                remaining -= len(data)
            data = b"".join(chunks)
            return Content(data[:MAX_FILE_BYTES], len(data) > MAX_FILE_BYTES)
        except OSError as error:
            raise WorkspaceError("denied", "Git file could not be read") from error
        finally:
            os.close(descriptor)

    async def _blob(self, oid: str) -> Content:
        if not oid.strip("0"):
            return Content(b"")
        if _OID.fullmatch(oid) is None:
            raise GitFailure("Git returned an invalid object identity")
        result = await self._git("cat-file", "blob", oid, max_bytes=MAX_FILE_BYTES)
        return Content(result.stdout, result.truncated)

    async def _blobs(self, oids: list[str]) -> dict[str, Content]:
        identities = list(dict.fromkeys(oids))
        if not identities:
            return {}
        if any(_OID.fullmatch(oid) is None for oid in identities):
            raise GitFailure("Git returned an invalid object identity")
        result = await self._git(
            "cat-file",
            "--batch",
            input_data="".join(oid + "\n" for oid in identities).encode("ascii"),
        )
        contents: dict[str, Content] = {}
        position = 0
        for oid in identities:
            end = result.stdout.find(b"\n", position)
            if end < 0:
                contents[oid] = Content(b"", True)
                continue
            header = result.stdout[position:end].split(b" ")
            if len(header) != 3 or header[1] != b"blob" or not header[2].isdigit():
                raise GitFailure("Git object is unavailable")
            size = int(header[2])
            start = end + 1
            available = result.stdout[start : start + min(size, MAX_FILE_BYTES)]
            contents[oid] = Content(
                available, size > MAX_FILE_BYTES or start + size > len(result.stdout)
            )
            position = start + size + 1
        return contents

    def _probe_stat(self, name: str, parents: set[str]) -> os.stat_result:
        # Only metadata is probed here. Cache each ordinary parent directory
        # once per scan; any later content read re-authorizes descriptor-wise.
        components = name.split("/")
        for index in range(1, len(components)):
            parent = "/".join(components[:index])
            if parent not in parents:
                info = (self.files.root / parent).lstat()
                if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
                    raise WorkspaceError(
                        "denied", "Git path has an unsupported symbolic-link parent"
                    )
                parents.add(parent)
        return (self.files.root / name).lstat()

    def _normalize(self, data: bytes, baseline: bytes, attributes: dict[str, str]) -> bytes:
        text = attributes.get("text", "unspecified")
        normalize = text == "set" or attributes.get("eol") in ("lf", "crlf")
        if text != "unset" and self.autocrlf in ("true", "input"):
            normalize = True
        if text == "auto":
            normalize = True
        # Git's safe CRLF conversion does not rewrite an index blob already
        # containing CRLF, or a file containing binary NUL bytes.
        if normalize and b"\x00" not in data and b"\r\n" not in baseline:
            return data.replace(b"\r\n", b"\n")
        return data

    def _stat_matches(self, entry: IndexEntry, info: os.stat_result) -> bool:
        # Skip-worktree and assume-unchanged carry their normal Git semantics.
        if entry.flags & (0x40000000 | 0x8000):
            return True
        if self.filemode and os.name != "nt":
            executable = bool(info.st_mode & stat.S_IXUSR)
            if executable != (entry.mode == "100755"):
                return False
        return (
            info.st_size == entry.size
            and info.st_mtime_ns == entry.mtime_ns
            and info.st_ctime_ns == entry.ctime_ns
        )

    async def changes(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "state": "ready",
            "branch": None,
            "entries": [],
            "truncated": False,
        }
        try:
            async with asyncio.timeout(REVIEW_TIMEOUT):
                if not await self._prepare():
                    result["state"] = "not_repository"
                    return result
                branch, indexed_result, staged_result, others = await asyncio.gather(
                    self._git("symbolic-ref", "--quiet", "--short", "HEAD", allow_failure=True),
                    self._index(),
                    self._staged(),
                    self._git("ls-files", "--others", "--exclude-standard", "-z", "--", "."),
                )
                if branch.returncode == 0:
                    result["branch"] = _decode_path(branch.stdout.removesuffix(b"\n"))
                index, index_truncated = indexed_result
                staged, staged_truncated = staged_result
                result["truncated"] = index_truncated or staged_truncated
                entries: dict[str, dict[str, Any]] = {}
                for name, change in staged.items():
                    entry = {
                        "path": name,
                        "status": change["code"] + " ",
                        "staged": True,
                        "unstaged": False,
                        "untracked": False,
                        "exists": (self.files.root / name).exists(),
                    }
                    if "oldPath" in change:
                        entry["oldPath"] = change["oldPath"]
                    entries[name] = entry
                candidates: dict[str, tuple[IndexEntry, os.stat_result]] = {}
                parents: set[str] = set()

                def set_working(name: str, code: str, exists: bool) -> None:
                    entry = entries.setdefault(
                        name,
                        {
                            "path": name,
                            "status": "  ",
                            "staged": False,
                            "unstaged": False,
                            "untracked": False,
                            "exists": exists,
                        },
                    )
                    entry.update(status=entry["status"][0] + code, unstaged=True, exists=exists)

                for name, indexed in index.items():
                    if indexed.mode == "160000":
                        continue
                    if len(entries) >= MAX_ENTRIES:
                        result["truncated"] = True
                        break
                    try:
                        info = self._probe_stat(name, parents)
                        if indexed.stage:
                            self._authorize(name, symlink=True)
                            set_working(name, "U", True)
                        elif not self._stat_matches(indexed, info):
                            self._authorize(name, symlink=True)
                            if len(candidates) >= MAX_CHANGED_READS:
                                result["truncated"] = True
                                continue
                            candidates[name] = (indexed, info)
                    except (FileNotFoundError, NotADirectoryError):
                        # Even deleted index paths must have safe lexical and
                        # resolved containment before appearing in the result.
                        try:
                            self._authorize(name, symlink=True)
                        except WorkspaceError:
                            result["truncated"] = True
                            continue
                        set_working(name, "D", False)
                    except WorkspaceError as error:
                        if error.reason == "missing":
                            set_working(name, "D", False)
                        else:
                            result["truncated"] = True
                            continue
                    except OSError:
                        result["truncated"] = True
                        continue
                attributes, blobs = await asyncio.gather(
                    self._attributes_many(list(candidates)),
                    self._blobs([entry.oid for entry, _ in candidates.values()]),
                )
                for name, (indexed, info) in candidates.items():
                    attrs = attributes.get(name, {})
                    try:
                        self._supported_attributes(attrs)
                    except WorkspaceError:
                        set_working(name, "M", True)
                        continue
                    try:
                        disk = await asyncio.to_thread(self._read_disk, name)
                    except WorkspaceError as error:
                        if error.reason == "missing":
                            set_working(name, "D", False)
                        else:
                            result["truncated"] = True
                        continue
                    baseline = blobs[indexed.oid]
                    if disk.truncated or baseline.truncated:
                        result["truncated"] = True
                        if info.st_size != indexed.size:
                            set_working(name, "M", True)
                    elif self._normalize(disk.data, baseline.data, attrs) != baseline.data:
                        set_working(name, "M", True)
                    if (
                        self.filemode
                        and os.name != "nt"
                        and bool(info.st_mode & stat.S_IXUSR) != (indexed.mode == "100755")
                    ):
                        set_working(name, "M", True)
                result["truncated"] |= others.truncated
                for raw_path in others.stdout.split(b"\x00")[:-1]:
                    if len(entries) >= MAX_ENTRIES:
                        result["truncated"] = True
                        break
                    try:
                        name = _decode_path(raw_path)
                        resolved = self._authorize(name, symlink=True)
                        if not resolved.is_file():
                            continue
                    except WorkspaceError:
                        result["truncated"] = True
                        continue
                    if name in entries:
                        entries[name].update(
                            status=entries[name]["status"][0] + "?", untracked=True, exists=True
                        )
                    else:
                        entries[name] = {
                            "path": name,
                            "status": "??",
                            "staged": False,
                            "unstaged": False,
                            "untracked": True,
                            "exists": True,
                        }
                result["entries"] = [entries[name] for name in sorted(entries)[:MAX_ENTRIES]]
                result["truncated"] |= len(entries) > MAX_ENTRIES
        except (GitFailure, TimeoutError, WorkspaceError) as error:
            result.update(
                state="unavailable",
                entries=[],
                reason=(
                    str(error)
                    if isinstance(error, GitFailure)
                    else "Git inspection could not be completed"
                ),
            )
        return result

    async def diff(self, path: str, target: str) -> dict[str, Any]:
        path = self.files.normalized_rel(path)
        self._authorize(path)
        try:
            async with asyncio.timeout(REVIEW_TIMEOUT):
                if not await self._prepare():
                    raise WorkspaceError("denied", "The workspace is not a Git repository")
                attributes, indexed_result = await asyncio.gather(
                    self._attributes(path), self._index(path)
                )
                if target == "working":
                    self._supported_attributes(attributes)
                index, index_truncated = indexed_result
                indexed = index.get(path)
                if indexed is not None and indexed.stage:
                    raise WorkspaceError(
                        "denied", "Resolve the Git merge conflict before reviewing its diff"
                    )
                if index_truncated:
                    raise WorkspaceError("too-large", "Git metadata exceeds the review limit")
                if target == "staged":
                    staged, staged_truncated = await self._staged()
                    if staged_truncated:
                        raise WorkspaceError("too-large", "Git metadata exceeds the review limit")
                    change = staged.get(path)
                    if change is None:
                        if indexed is None:
                            raise WorkspaceError("missing", "The file has no staged Git version")
                        return _render_patch(Content(b""), Content(b""), path, None, target)
                    before = await self._blob(change["oldOid"])
                    after = await self._blob(change["oid"])
                    return await asyncio.to_thread(
                        _render_patch,
                        before,
                        after,
                        path,
                        change.get("oldPath"),
                        target,
                        change["oldMode"],
                        change["mode"],
                    )
                before = await self._blob(indexed.oid) if indexed is not None else Content(b"")
                try:
                    after = await asyncio.to_thread(self._read_disk, path)
                except WorkspaceError as error:
                    if error.reason != "missing" or indexed is None:
                        raise
                    after = Content(b"")
                after = Content(
                    self._normalize(after.data, before.data, attributes), after.truncated
                )
                return await asyncio.to_thread(_render_patch, before, after, path, None, target)
        except (GitFailure, TimeoutError) as error:
            raise WorkspaceError(
                "denied", "Git diff is unavailable; refresh and try again"
            ) from error
