"""Review runs attached to one prompt snapshot.

The Changes screen shows a prompt's diff and the review run that examined it.
Runs are deliberately workspace-scoped and snapshot-scoped: they never depend
on a Git commit, and re-running a review does not touch working-tree state.

The first engine is deterministic ("rules"): it reads the prompt's stable
diff and reports concrete hazards. The record shape, lifecycle, and storage
are engine-neutral so a read-only reviewer agent can write the same records
later without changing the UI or RPC surface.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from machina.workspace import snapshots

logger = logging.getLogger(__name__)

REVIEW_VERSION = 1
ENGINES = ("rules",)
MAX_FINDINGS = 200
SEVERITIES = ("critical", "high", "medium", "low", "info")
_SEVERITY_ORDER = {name: index for index, name in enumerate(SEVERITIES)}
_SNAPSHOT_ID_RE = re.compile(r"^[0-9a-f]{32}$")

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_COMMENT_PREFIXES = ("#", "//", "/*", "*", "--", '"""', "'''")
_TODO_RE = re.compile(r"\b(?:TODO|FIXME|HACK)\b", re.IGNORECASE)
_BARE_EXCEPT_RE = re.compile(r"^\s*except\s*:")
_BASE_EXCEPT_RE = re.compile(r"\bexcept\s+BaseException\b")
_EXCEPT_RE = re.compile(r"\bexcept\s+Exception\b")
_SHELL_TRUE_RE = re.compile(r"\bshell\s*=\s*True\b")
_VERIFY_FALSE_RE = re.compile(r"\bverify\s*=\s*False\b")
_CHMOD_RE = re.compile(r"\bchmod\b[^\n]*\b(?:0o?777|777)\b")
_EVAL_RE = re.compile(r"\b(?:eval|exec)\s*\(")
_DESERIALIZE_RE = re.compile(r"\b(?:pickle|dill|marshal)\.loads?\s*\(")
_YAML_UNSAFE_RE = re.compile(r"\byaml\.(?:load|unsafe_load)\s*\(")
_JWT_NOVERIFY_RE = re.compile(
    r"verify_signature\s*[\"']?\s*:\s*False|algorithms\s*=\s*\[\s*[\"']none[\"']",
    re.IGNORECASE,
)
_SQL_FSTRING_RE = re.compile(
    r"\.(?:execute|executemany)\s*\(\s*f[\"']|\.(?:execute|executemany)\s*\([^)\n]*\.format\s*\(",
    re.IGNORECASE,
)
_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS access key"),
    (re.compile(r"(?:sk|rk)-(?:live|test|proj)-[A-Za-z0-9_-]{16,}"), "API secret key"),
    (re.compile(r"sk-[A-Za-z0-9]{24,}"), "API secret key"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"), "GitHub token"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"), "Slack token"),
    (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"), "private key"),
    (
        re.compile(
            r"(?i)\b(?:api[_-]?key|secret|password|passwd|access[_-]?token)\b\s*[:=]\s*"
            r"['\"][^'\"]{8,}['\"]"
        ),
        "hardcoded credential",
    ),
)
_TEST_PARTS = frozenset({"test", "tests", "__tests__", "spec", "specs"})
_TEST_FILE_RE = re.compile(r"(?:^test_|_test\.|\.test\.|\.spec\.)")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def reviews_root(workspace: Path | None = None) -> Path:
    return snapshots.store_root(workspace) / "reviews"


def _path(workspace: Path, snapshot_id: str) -> Path:
    if not _SNAPSHOT_ID_RE.fullmatch(snapshot_id):
        raise ValueError("invalid snapshot id")
    return reviews_root(workspace) / f"{snapshot_id}.json"


def load(workspace: Path, snapshot_id: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(_path(workspace, snapshot_id).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(payload, dict) or payload.get("version") != REVIEW_VERSION:
        return None
    if payload.get("snapshotId") != snapshot_id:
        return None
    if not isinstance(payload.get("findings"), list):
        return None
    return payload


def save(workspace: Path, review: dict[str, Any]) -> dict[str, Any]:
    target = _path(workspace, str(review.get("snapshotId") or ""))
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(review, indent=2, sort_keys=True), encoding="utf-8"
    )
    temporary.replace(target)
    return review


def compact(review: dict[str, Any], limit: int = 12) -> dict[str, Any]:
    """Bound the review payload carried by the Changes list.

    Findings are sorted by severity, so the preview keeps the most important
    ones. ``findingsCount`` preserves the honest total for the row badge.
    """
    findings = review.get("findings")
    findings = findings if isinstance(findings, list) else []
    return {
        **review,
        "findings": findings[:limit],
        "findingsCount": len(findings),
    }


def list_for(
    workspace: Path, snapshot_ids: list[str] | None = None
) -> dict[str, dict[str, Any]]:
    """Latest review per snapshot, bounded to the requested ids."""
    wanted = set(snapshot_ids) if snapshot_ids is not None else None
    found: dict[str, dict[str, Any]] = {}
    root = reviews_root(workspace)
    if not root.is_dir():
        return found
    for path in root.glob("*.json"):
        snapshot_id = path.stem
        if wanted is not None and snapshot_id not in wanted:
            continue
        review = load(workspace, snapshot_id)
        if review is not None:
            found[snapshot_id] = review
    return found


def _finding(
    *,
    rule: str,
    severity: str,
    title: str,
    body: str,
    path: str,
    line: int,
) -> dict[str, Any]:
    fingerprint = hashlib.sha256(
        f"{rule}\0{path}\0{line}".encode()
    ).hexdigest()[:16]
    return {
        "rule": rule,
        "severity": severity,
        "title": title,
        "body": body,
        "file": path,
        "line": line,
        "fingerprint": fingerprint,
    }


def _is_test_path(path: str) -> bool:
    parts = {part.casefold() for part in Path(path).parts[:-1]}
    name = Path(path).name.casefold()
    return bool(parts & _TEST_PARTS) or bool(_TEST_FILE_RE.search(name))


def _added_lines(text: str):
    path = ""
    line_number = 0
    for line in text.splitlines():
        if line.startswith("+++ "):
            path = line[4:].strip()
            if path.startswith("b/"):
                path = path[2:]
            continue
        if line.startswith("--- "):
            continue
        match = _HUNK_RE.match(line)
        if match:
            line_number = int(match.group(1))
            continue
        if line.startswith("+"):
            yield path, line_number, line[1:]
            line_number += 1
        elif line.startswith(" "):
            line_number += 1


def _snippet(line: str) -> str:
    text = " ".join(line.strip().split())
    return text[:160] + ("…" if len(text) > 160 else "")


def findings_from_files(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deterministic review over added lines in stable per-file diffs."""
    findings: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(path: str, line: int, rule: str, severity: str, title: str, body: str) -> None:
        item = _finding(
            rule=rule,
            severity=severity,
            title=title,
            body=body,
            path=path,
            line=line,
        )
        if item["fingerprint"] in seen:
            return
        seen.add(item["fingerprint"])
        findings.append(item)

    for file in files:
        path = str(file.get("path") or "")
        if not path or file.get("binary"):
            continue
        for added_path, line_number, text in _added_lines(str(file.get("text") or "")):
            shown_path = added_path or path
            stripped = text.strip()
            lowered = stripped.casefold()
            if not stripped:
                continue
            comment = stripped.startswith(_COMMENT_PREFIXES)

            if stripped.startswith("<<<<<<<") or stripped.startswith(">>>>>>>"):
                add(
                    shown_path,
                    line_number,
                    "merge-conflict",
                    "critical",
                    "Merge conflict marker committed",
                    "Resolve the conflict and remove every marker before keeping this change.",
                )
            for pattern, label in _SECRET_PATTERNS:
                if pattern.search(text):
                    add(
                        shown_path,
                        line_number,
                        "hardcoded-secret",
                        "critical",
                        f"Possible {label} added",
                        "Remove the value from source, load it from the secret store, and rotate it if it was real.",
                    )
                    break
            if not comment:
                if "pdb.set_trace()" in lowered or "breakpoint()" in lowered or lowered.startswith("debugger"):
                    add(
                        shown_path,
                        line_number,
                        "debug-breakpoint",
                        "high",
                        "Debug breakpoint left in code",
                        "Remove the breakpoint; it stops or freezes the program outside a debugger.",
                    )
                if (
                    "console.log(" in lowered
                    or lowered.startswith("print(")
                ) and not _is_test_path(shown_path):
                    add(
                        shown_path,
                        line_number,
                        "debug-print",
                        "low",
                        "Debug output left in non-test code",
                        "Remove the temporary log or route it through the project logger.",
                    )
                if _BASE_EXCEPT_RE.search(stripped):
                    add(
                        shown_path,
                        line_number,
                        "broad-except",
                        "high",
                        "BaseException is caught",
                        "Catch the specific exceptions you can handle; BaseException also swallows cancellation and exit.",
                    )
                elif _EXCEPT_RE.search(stripped):
                    add(
                        shown_path,
                        line_number,
                        "broad-except",
                        "medium",
                        "Broad Exception handler added",
                        "Catch the specific exception types and let unexpected failures surface.",
                    )
                elif _BARE_EXCEPT_RE.search(stripped):
                    add(
                        shown_path,
                        line_number,
                        "bare-except",
                        "high",
                        "Bare except added",
                        "Catch the specific exception types; a bare except also swallows cancellation and programming errors.",
                    )
                if _SHELL_TRUE_RE.search(stripped):
                    add(
                        shown_path,
                        line_number,
                        "shell-true",
                        "high",
                        "subprocess runs with shell=True",
                        "Pass an argument list without a shell, or prove every interpolated value is trusted.",
                    )
                if _VERIFY_FALSE_RE.search(stripped):
                    add(
                        shown_path,
                        line_number,
                        "tls-verify-disabled",
                        "high",
                        "TLS verification disabled",
                        "Restore certificate verification; disabling it defeats transport security.",
                    )
                if _CHMOD_RE.search(stripped):
                    add(
                        shown_path,
                        line_number,
                        "world-writable",
                        "high",
                        "World-writable permissions added",
                        "Grant the narrowest permissions the process actually needs.",
                    )
                if _EVAL_RE.search(stripped):
                    add(
                        shown_path,
                        line_number,
                        "dynamic-execution",
                        "medium",
                        "Dynamic code execution added",
                        "Prefer a parser or dispatch table; eval/exec turns ordinary input into code.",
                    )
                if _DESERIALIZE_RE.search(stripped):
                    add(
                        shown_path,
                        line_number,
                        "unsafe-deserialization",
                        "high",
                        "Unsafe deserialization added",
                        "pickle/dill/marshal can execute code while loading untrusted data; use a data-only format.",
                    )
                if _YAML_UNSAFE_RE.search(stripped) and "safeloader" not in lowered:
                    add(
                        shown_path,
                        line_number,
                        "unsafe-yaml-load",
                        "high",
                        "yaml.load without SafeLoader",
                        "Use yaml.safe_load or pass SafeLoader; the default loader can construct arbitrary objects.",
                    )
                if _JWT_NOVERIFY_RE.search(stripped):
                    add(
                        shown_path,
                        line_number,
                        "jwt-no-verify",
                        "critical",
                        "JWT signature verification disabled",
                        "Verify the signature and reject the none algorithm.",
                    )
                if _SQL_FSTRING_RE.search(stripped):
                    add(
                        shown_path,
                        line_number,
                        "sql-injection",
                        "high",
                        "SQL built with string interpolation",
                        "Pass bound parameters to execute; interpolated SQL lets input change the query.",
                    )
            if _TODO_RE.search(stripped):
                add(
                    shown_path,
                    line_number,
                    "unfinished-marker",
                    "info",
                    "Unfinished marker added",
                    f"Follow up on: {_snippet(stripped)}",
                )
            if re.search(r"://[^/\s:@]+:[^/\s@]+@", text):
                add(
                    shown_path,
                    line_number,
                    "url-credentials",
                    "high",
                    "Credentials embedded in a URL",
                    "Move the credential out of the URL and into the secret store.",
                )

    findings.sort(
        key=lambda item: (
            _SEVERITY_ORDER.get(str(item.get("severity")), 99),
            str(item.get("file") or ""),
            int(item.get("line") or 0),
        )
    )
    return findings[:MAX_FINDINGS]


def _summary(findings: list[dict[str, Any]]) -> str:
    if not findings:
        return "No issues found by the rules engine."
    counts = {severity: 0 for severity in SEVERITIES}
    for finding in findings:
        severity = str(finding.get("severity") or "info")
        counts[severity] = counts.get(severity, 0) + 1
    parts = [
        f"{counts[severity]} {severity}"
        for severity in SEVERITIES
        if counts.get(severity)
    ]
    return f"{len(findings)} finding{'s' if len(findings) != 1 else ''}: " + ", ".join(parts)


def run_rules(
    workspace: Path,
    snapshot: dict[str, Any],
    *,
    force: bool = False,
) -> dict[str, Any]:
    snapshot_id = str(snapshot.get("snapshot_id") or "")
    existing = load(workspace, snapshot_id) if _SNAPSHOT_ID_RE.fullmatch(snapshot_id) else None
    if existing is not None and not force and existing.get("status") in {
        "queued",
        "running",
        "complete",
    }:
        return existing

    started = _now()
    review: dict[str, Any] = {
        "version": REVIEW_VERSION,
        "id": uuid.uuid4().hex,
        "snapshotId": snapshot_id,
        "sessionId": str(snapshot.get("session_id") or ""),
        "engine": "rules",
        "status": "running",
        "provider": str(snapshot.get("provider") or ""),
        "model": str(snapshot.get("model") or ""),
        "createdAt": started,
        "startedAt": started,
        "finishedAt": None,
        "summary": "",
        "findings": [],
        "error": None,
    }
    try:
        diff = snapshots.diff_files(snapshot)
        findings = findings_from_files(list(diff.get("files") or []))
        if diff.get("truncated"):
            findings.append(
                _finding(
                    rule="diff-truncated",
                    severity="info",
                    title="Review covered a truncated diff",
                    body=(
                        "The prompt changed more text than one review payload can hold. "
                        "Split the change or review the remaining files separately."
                    ),
                    path="",
                    line=0,
                )
            )
        review["findings"] = findings
        review["summary"] = _summary(findings)
        review["status"] = "complete"
    except Exception as error:  # Review completion must not fail the RPC server.
        review["status"] = "failed"
        review["error"] = f"{type(error).__name__}: {error}"
        review["summary"] = "Review failed before it could inspect the diff."
    review["finishedAt"] = _now()
    logger.debug(
        "review.rules snapshot=%s status=%s findings=%d",
        snapshot_id,
        review["status"],
        len(review["findings"]),
    )
    return save(workspace, review)


def cancel(workspace: Path, snapshot_id: str) -> dict[str, Any] | None:
    review = load(workspace, snapshot_id)
    if review is None:
        return None
    if review.get("status") in {"queued", "running"}:
        review["status"] = "cancelled"
        review["finishedAt"] = _now()
        review["summary"] = "Cancelled before the review completed."
        return save(workspace, review)
    return review
