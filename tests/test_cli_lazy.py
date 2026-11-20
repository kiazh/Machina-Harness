"""The installed entry point lazily imports exactly the command it runs."""

from __future__ import annotations

import subprocess
import sys

from click.testing import CliRunner

from machina.cli.lazy import _SPECS, lazy_app


def _run_python(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)


def test_package_import_does_not_import_typer_or_commands() -> None:
    result = _run_python(
        "import sys, machina.cli; "
        "assert 'typer' not in sys.modules; "
        "assert 'machina.cli.chat' not in sys.modules; "
        "print('lazy')"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "lazy"


def test_static_help_does_not_import_a_command_module() -> None:
    result = _run_python(
        "import sys; "
        "from click.testing import CliRunner; "
        "from machina.cli.lazy import lazy_app; "
        "r = CliRunner().invoke(lazy_app, ['--help']); "
        "assert r.exit_code == 0; "
        "assert 'memory-approve' in r.output; "
        "assert 'machina.cli.chat' not in sys.modules; "
        "print('help')"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "help"


def test_lazy_group_imports_only_the_selected_command() -> None:
    result = _run_python(
        "import sys; "
        "from machina.cli.lazy import lazy_app; "
        "assert lazy_app.get_command(None, 'version') is not None; "
        "assert 'machina.cli.maintenance' in sys.modules; "
        "assert 'machina.cli.chat' not in sys.modules; "
        "print('selected')"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "selected"


def test_lazy_command_set_and_order_match_the_eager_spec() -> None:
    assert lazy_app.list_commands(None) == list(_SPECS)


def test_lazy_version_command_runs_through_typer() -> None:
    runner = CliRunner()
    result = runner.invoke(lazy_app, ["version"])
    assert result.exit_code == 0
    assert "Machina v" in result.output
