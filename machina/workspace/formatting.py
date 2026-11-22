"""Optional post-write formatters.

Formatting is opt-in through ``formatter: auto`` (or a command template). The
write is never rolled back because a formatter is missing or fails; the tool
reports the formatter outcome separately.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from machina.core.config import config

_DEFAULT_TIMEOUT = 20


def _configured() -> str:
    raw = config.get("formatter")
    return str(raw or "off").strip()


def _command_for(path: Path) -> tuple[list[str], str] | None:
    suffix = path.suffix.casefold()
    if suffix == ".py":
        if shutil.which("ruff"):
            return ["ruff", "format", str(path)], "ruff"
        if shutil.which("black"):
            return ["black", "--quiet", str(path)], "black"
        return None
    if suffix in {".js", ".jsx", ".ts", ".tsx", ".json", ".css", ".md", ".yaml", ".yml"}:
        if shutil.which("prettier"):
            return ["prettier", "--write", str(path)], "prettier"
        return None
    if suffix == ".go" and shutil.which("gofmt"):
        return ["gofmt", "-w", str(path)], "gofmt"
    if suffix == ".rs" and shutil.which("rustfmt"):
        return ["rustfmt", str(path)], "rustfmt"
    if suffix in {".c", ".h", ".cc", ".cpp", ".hpp"} and shutil.which("clang-format"):
        return ["clang-format", "-i", str(path)], "clang-format"
    if suffix == ".sh" and shutil.which("shfmt"):
        return ["shfmt", "-w", str(path)], "shfmt"
    return None


def format_file(path: Path) -> str | None:
    """Return a short status when the file was formatted, else ``None``."""
    configured = _configured()
    if configured.casefold() in {"", "off", "false", "no", "0"}:
        return None
    selected = _command_for(path)
    if selected is None:
        return f"No formatter available for {path.name}"
    command, name = selected
    try:
        result = subprocess.run(
            command,
            cwd=str(path.parent),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_DEFAULT_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return f"Formatter {name} failed: {type(error).__name__}"
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        return f"Formatter {name} failed: {detail[0][:300] if detail else 'non-zero exit'}"
    return f"Formatted with {name}"
