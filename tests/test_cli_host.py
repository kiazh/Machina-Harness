"""`machina host` command construction and lifecycle without a real tmux/ttyd."""

from __future__ import annotations

import json
import os
import subprocess

import pytest
from typer.testing import CliRunner

from machina import host as host_mod
from machina.cli import app


def _state(**overrides) -> host_mod.HostState:
    values = {
        "version": host_mod.STATE_VERSION,
        "session": "machina",
        "bind": "127.0.0.1",
        "port": 7681,
        "username": "machina",
        "password": "secret-token",
        "ttyd_pid": 4321,
        "workspace": "/srv/work",
        "mode": "ask",
        "started_at": "2026-11-17T00:00:00+00:00",
        "ttyd_log": "/tmp/host-ttyd.log",
        "owns_session": True,
    }
    values.update(overrides)
    return host_mod.HostState(**values)


class _Process:
    def __init__(self, pid: int = 4321, exit_code: int | None = None) -> None:
        self.pid = pid
        self._exit_code = exit_code

    def poll(self) -> int | None:
        return self._exit_code


def test_host_state_roundtrip_is_private(tmp_path, monkeypatch):
    monkeypatch.setenv("MACHINA_HOME", str(tmp_path))
    state = _state()
    path = host_mod.save_state(state)
    assert host_mod.load_state() == state
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600


def test_ttyd_command_is_loopback_and_writable():
    state = _state()
    command = host_mod.build_ttyd_command("/usr/bin/ttyd", "/usr/bin/tmux", state)
    assert command[:2] == ["/usr/bin/ttyd", "-i"]
    assert command[command.index("-i") + 1] == "127.0.0.1"
    assert "-W" in command
    assert "-O" in command
    assert command[command.index("-c") + 1] == "machina:secret-token"
    assert command[-4:] == ["/usr/bin/tmux", "attach-session", "-t", "machina"]


def test_tmux_command_runs_the_tui_in_the_workspace(monkeypatch):
    command = host_mod.build_tmux_command("/usr/bin/tmux", "machina", "/srv/work", "ask")
    assert command[:5] == ["/usr/bin/tmux", "new-session", "-d", "-s", "machina"]
    assert command[command.index("-c") + 1] == "/srv/work"
    assert "MACHINA_AUTO_UPDATE=0" in command
    assert command[-4:] == ["-m", "machina", "--mode", "ask"][-4:]


def test_tmux_command_resumes_a_machina_session():
    session_id = "11111111-2222-3333-4444-555555555555"
    command = host_mod.build_tmux_command(
        "/usr/bin/tmux", "machina", "/srv/work", "ask", session_id
    )
    assert "chat" in command
    assert "--interactive" in command
    assert command[command.index("--session") + 1] == session_id
    assert command[-2:] == ["--mode", "ask"]


def test_start_refuses_non_loopback_bind(tmp_path, monkeypatch):
    monkeypatch.setattr(host_mod, "_ensure_supported", lambda: None)
    with pytest.raises(host_mod.HostError, match="loopback"):
        host_mod.start_host(workspace=tmp_path, bind="0.0.0.0")


def test_start_requires_tmux_and_ttyd(tmp_path, monkeypatch):
    monkeypatch.setattr(host_mod, "_ensure_supported", lambda: None)
    monkeypatch.setattr(host_mod, "_which", lambda name: {"tmux": "/usr/bin/tmux"}.get(name))
    with pytest.raises(host_mod.HostError, match="ttyd"):
        host_mod.start_host(workspace=tmp_path)

    monkeypatch.setattr(host_mod, "_which", lambda name: None)
    with pytest.raises(host_mod.HostError, match="tmux"):
        host_mod.start_host(workspace=tmp_path)


def test_start_creates_the_tmux_shell_and_ttyd_state(tmp_path, monkeypatch):
    monkeypatch.setenv("MACHINA_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(host_mod, "_ensure_supported", lambda: None)
    monkeypatch.setattr(
        host_mod,
        "_which",
        lambda name: {"tmux": "/usr/bin/tmux", "ttyd": "/usr/bin/ttyd"}.get(name),
    )
    monkeypatch.setattr(host_mod, "_tmux_session_alive", lambda _tmux, _session: False)
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    opened: list[list[str]] = []

    def fake_popen(command, **_kwargs):
        opened.append(command)
        return _Process()

    monkeypatch.setattr(host_mod, "_run", fake_run)
    monkeypatch.setattr(host_mod, "_popen", fake_popen)
    monkeypatch.setattr(host_mod.time, "sleep", lambda _seconds: None)

    state, created = host_mod.start_host(workspace=tmp_path)

    assert created is True
    assert state.ttyd_pid == 4321
    assert state.workspace == str(tmp_path)
    assert calls and calls[0][:2] == ["/usr/bin/tmux", "new-session"]
    assert opened and opened[0][0] == "/usr/bin/ttyd"
    assert host_mod.load_state() == state


def test_start_reuses_a_running_host(tmp_path, monkeypatch):
    monkeypatch.setenv("MACHINA_HOME", str(tmp_path))
    state = _state()
    host_mod.save_state(state)
    monkeypatch.setattr(host_mod, "_ensure_supported", lambda: None)
    monkeypatch.setattr(
        host_mod,
        "_which",
        lambda name: {"tmux": "/usr/bin/tmux", "ttyd": "/usr/bin/ttyd"}.get(name),
    )
    monkeypatch.setattr(host_mod, "_tmux_session_alive", lambda _tmux, _session: True)
    monkeypatch.setattr(host_mod, "process_alive", lambda _pid: True)
    reused, created = host_mod.start_host(workspace=tmp_path)
    assert reused == state
    assert created is False


def test_start_attaches_to_an_existing_tmux_session_without_owning_it(tmp_path, monkeypatch):
    monkeypatch.setenv("MACHINA_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(host_mod, "_ensure_supported", lambda: None)
    monkeypatch.setattr(
        host_mod,
        "_which",
        lambda name: {"tmux": "/usr/bin/tmux", "ttyd": "/usr/bin/ttyd"}.get(name),
    )
    monkeypatch.setattr(host_mod, "_tmux_session_alive", lambda _tmux, _session: True)
    calls: list[list[str]] = []
    monkeypatch.setattr(
        host_mod,
        "_run",
        lambda command, **_kwargs: calls.append(command)
        or subprocess.CompletedProcess(command, 0, "", ""),
    )
    monkeypatch.setattr(host_mod, "_popen", lambda _command, **_kwargs: _Process())
    monkeypatch.setattr(host_mod.time, "sleep", lambda _seconds: None)

    state, _created = host_mod.start_host(workspace=tmp_path, session="dev")

    assert state.session == "dev"
    assert state.owns_session is False
    assert calls == []


def test_start_resumes_a_machina_session_in_a_new_tmux(tmp_path, monkeypatch):
    monkeypatch.setenv("MACHINA_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(host_mod, "_ensure_supported", lambda: None)
    monkeypatch.setattr(
        host_mod,
        "_which",
        lambda name: {"tmux": "/usr/bin/tmux", "ttyd": "/usr/bin/ttyd"}.get(name),
    )
    monkeypatch.setattr(host_mod, "_tmux_session_alive", lambda _tmux, _session: False)
    calls: list[list[str]] = []
    monkeypatch.setattr(
        host_mod,
        "_run",
        lambda command, **_kwargs: calls.append(command)
        or subprocess.CompletedProcess(command, 0, "", ""),
    )
    monkeypatch.setattr(host_mod, "_popen", lambda _command, **_kwargs: _Process())
    monkeypatch.setattr(host_mod.time, "sleep", lambda _seconds: None)
    session_id = "11111111-2222-3333-4444-555555555555"

    state, _created = host_mod.start_host(
        workspace=tmp_path, session="machina", machina_session=session_id
    )

    assert state.machina_session == session_id
    assert calls and "chat" in calls[0]
    assert calls[0][calls[0].index("--session") + 1] == session_id


def test_load_state_accepts_old_files_without_owns_session(tmp_path, monkeypatch):
    monkeypatch.setenv("MACHINA_HOME", str(tmp_path))
    state = _state()
    payload = {key: value for key, value in state.__dict__.items() if key != "owns_session"}
    (tmp_path / "host.json").write_text(json.dumps(payload), encoding="utf-8")
    loaded = host_mod.load_state()
    assert loaded is not None
    assert loaded.owns_session is True



def test_stop_terminates_the_bridge_and_session(tmp_path, monkeypatch):
    monkeypatch.setenv("MACHINA_HOME", str(tmp_path))
    host_mod.save_state(_state())
    terminated: list[int] = []
    killed: list[str] = []
    monkeypatch.setattr(host_mod, "_terminate_ttyd", lambda pid: terminated.append(pid))
    monkeypatch.setattr(host_mod, "_tmux_kill", lambda _tmux, session: killed.append(session))
    monkeypatch.setattr(host_mod, "_which", lambda _name: "/usr/bin/tmux")

    stopped = host_mod.stop_host()

    assert stopped is not None
    assert terminated == [4321]
    assert killed == ["machina"]
    saved = host_mod.load_state()
    assert saved is not None
    assert saved.ttyd_pid == 0
    assert saved.password == "secret-token"


def test_stop_leaves_a_borrowed_tmux_session_running(tmp_path, monkeypatch):
    monkeypatch.setenv("MACHINA_HOME", str(tmp_path))
    host_mod.save_state(_state(owns_session=False))
    terminated: list[int] = []
    killed: list[str] = []
    monkeypatch.setattr(host_mod, "_terminate_ttyd", lambda pid: terminated.append(pid))
    monkeypatch.setattr(host_mod, "_tmux_kill", lambda _tmux, session: killed.append(session))
    monkeypatch.setattr(host_mod, "_which", lambda _name: "/usr/bin/tmux")

    stopped = host_mod.stop_host()

    assert stopped is not None
    assert terminated == [4321]
    assert killed == []
    saved = host_mod.load_state()
    assert saved is not None
    assert saved.owns_session is False


def test_cli_status_reports_not_running(monkeypatch):
    monkeypatch.setattr(host_mod, "status_host", lambda: (None, False, False))
    result = CliRunner().invoke(app, ["host", "status"])
    assert result.exit_code == 1
    assert "not running" in result.output


def test_cli_start_prints_browser_and_ssh_paths(monkeypatch):
    state = _state()
    monkeypatch.setattr(host_mod, "start_host", lambda **_kwargs: (state, True))
    result = CliRunner().invoke(app, ["host", "start", "--workspace", "/srv/work"])
    assert result.exit_code == 0, result.output
    assert "http://127.0.0.1:7681" in result.output
    assert "ssh -L 7681:127.0.0.1:7681" in result.output
    assert "tailscale serve --bg 7681" in result.output
    assert "secret-token" in result.output
