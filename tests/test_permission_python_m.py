"""``python -m <writer>`` is reviewed like the writer itself.

Regression: the sensitive-path classifier unwrapped only ``uv run``. A
routine command such as ``python -m ruff --fix .machina/config`` therefore
kept ``first == "python"``, skipped the protected-path write review, and was
auto-allowed in ask mode.
"""

from __future__ import annotations

from machina.permissions.policy import _sensitive_capabilities, build_request


def _caps(argv: list[str], workspace: str) -> set[str]:
    request = build_request(
        operation="process.exec",
        argv=argv,
        workspace=workspace,
        cwd=workspace,
        mode="ask",
    )
    return set(_sensitive_capabilities(request))


def test_python_m_writer_touching_protected_path_is_reviewed(tmp_path):
    workspace = str(tmp_path)

    caps = _caps(["python", "-m", "ruff", "--fix", ".machina/config.toml"], workspace)

    assert "security_settings" in caps


def test_python3_m_writer_touching_git_is_reviewed(tmp_path):
    workspace = str(tmp_path)

    caps = _caps(["python3", "-m", "ruff", "--fix", ".git/config"], workspace)

    assert "security_settings" in caps


def test_plain_python_module_read_stays_routine(tmp_path):
    workspace = str(tmp_path)

    caps = _caps(["python", "-m", "pytest", "-q"], workspace)

    assert "security_settings" not in caps
