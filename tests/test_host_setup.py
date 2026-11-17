"""Hosted-shell inspection and setup contracts without launching a real host."""

from __future__ import annotations

import subprocess
import sys

import pytest

from machina import host_setup


def _status(**overrides) -> host_setup.HostStatus:
    values = {
        "platform": "windows",
        "runtime": "wsl",
        "distro": "Ubuntu",
        "configured": True,
        "running": True,
        "session": "machina",
        "workspace": "/mnt/c/Users/me",
        "execution_mode": "ask",
        "bind": "127.0.0.1",
        "port": 7681,
        "browser_url": "https://desktop.tailnet.ts.net",
        "tailnet_url": "https://desktop.tailnet.ts.net",
        "tailnet_name": "desktop.tailnet.ts.net",
        "tailscale_connected": True,
        "sshd_running": True,
        "ssh_command": 'ssh me@desktop -t "wsl -d Ubuntu -- tmux attach -t =machina"',
        "runtime_ready": True,
        "browser_ready": True,
        "ssh_ready": True,
        "missing": [],
    }
    values.update(overrides)
    return host_setup.HostStatus(**values)


def test_status_reports_a_running_wsl_host(monkeypatch):
    monkeypatch.setattr(host_setup, "_is_windows", lambda: True)
    monkeypatch.setattr(host_setup, "_select_distro", lambda preferred="Ubuntu": "Ubuntu")
    monkeypatch.setattr(host_setup, "_wsl_user", lambda distro: "me")
    monkeypatch.setattr(
        host_setup,
        "_read_state",
        lambda distro, user: {"session": "machina", "ttyd_pid": 42, "workspace": "/mnt/work"},
    )
    monkeypatch.setattr(host_setup, "_wsl_runtime_ready", lambda distro, user: (True, True))
    monkeypatch.setattr(host_setup, "_wsl_session_alive", lambda distro, session, pid: True)
    monkeypatch.setattr(
        host_setup,
        "_tailscale_status",
        lambda: (True, "desktop.tailnet.ts.net", "https://desktop.tailnet.ts.net"),
    )
    monkeypatch.setattr(host_setup, "_sshd_running", lambda: True)

    status = host_setup.status()

    assert status.running is True
    assert status.runtime_ready is True
    assert status.tailnet_name == "desktop.tailnet.ts.net"
    assert status.missing == []
    assert "wsl -d Ubuntu -- tmux attach -t =machina" in (status.ssh_command or "")


def test_status_names_every_missing_piece(monkeypatch):
    monkeypatch.setattr(host_setup, "_is_windows", lambda: True)
    monkeypatch.setattr(host_setup, "_select_distro", lambda preferred="Ubuntu": "Ubuntu")
    monkeypatch.setattr(host_setup, "_wsl_user", lambda distro: "me")
    monkeypatch.setattr(host_setup, "_read_state", lambda distro, user: None)
    monkeypatch.setattr(host_setup, "_wsl_runtime_ready", lambda distro, user: (False, False))
    monkeypatch.setattr(host_setup, "_tailscale_status", lambda: (False, None, None))
    monkeypatch.setattr(host_setup, "_sshd_running", lambda: False)

    status = host_setup.status()

    assert status.running is False
    assert "WSL Machina runtime" in status.missing
    assert "ttyd" in status.missing
    assert "Tailscale" in status.missing
    assert "SSH server" in status.missing


def test_credentials_are_only_returned_on_explicit_request(monkeypatch):
    monkeypatch.setattr(host_setup, "_is_windows", lambda: True)
    monkeypatch.setattr(host_setup, "_select_distro", lambda preferred="Ubuntu": "Ubuntu")
    monkeypatch.setattr(host_setup, "_wsl_user", lambda distro: "me")
    monkeypatch.setattr(
        host_setup,
        "_read_state",
        lambda distro, user: {"username": "machina", "password": "secret-token"},
    )

    assert host_setup.credentials() == {"username": "machina", "password": "secret-token"}


def test_setup_runs_every_windows_step_in_order(tmp_path, monkeypatch):
    calls: list[str] = []
    session_ids: list[str] = []
    running = False
    monkeypatch.setattr(host_setup, "_is_windows", lambda: True)
    monkeypatch.setattr(host_setup, "_select_distro", lambda preferred="Ubuntu": "Ubuntu")
    monkeypatch.setattr(host_setup, "_wsl_user", lambda distro: "me")
    monkeypatch.setattr(host_setup, "_wsl_distros", lambda: ["Ubuntu"])
    monkeypatch.setattr(host_setup, "_ensure_wsl_packages", lambda distro: None)
    monkeypatch.setattr(host_setup, "status", lambda: _status(running=running, runtime_ready=False))

    def fake_setup(
        distro, workspace, mode, port, session, allow_full=False, machina_session=""
    ):
        calls.append("runtime")
        session_ids.append(machina_session)

    def fake_start():
        nonlocal running
        running = True
        calls.append("start")
        return _status(running=True)

    monkeypatch.setattr(host_setup, "_setup_windows", fake_setup)
    monkeypatch.setattr(host_setup, "_ensure_tailscale", lambda op: calls.append("tailscale"))
    monkeypatch.setattr(host_setup, "_ensure_ssh", lambda op: calls.append("ssh"))
    monkeypatch.setattr(host_setup, "start", fake_start)
    monkeypatch.setattr(host_setup, "_ensure_serve", lambda op, port: calls.append("serve"))
    monkeypatch.setattr(host_setup, "_configure_autostart", lambda op: calls.append("autostart"))
    monkeypatch.setattr(host_setup, "_ssh_firewall_ready", lambda: True)

    result = host_setup.setup(
        workspace=str(tmp_path),
        mode="ask",
        machina_session="11111111-2222-3333-4444-555555555555",
    )

    assert result.running is True
    assert calls == ["runtime", "tailscale", "start", "autostart", "serve", "ssh"]
    assert session_ids == ["11111111-2222-3333-4444-555555555555"]


def test_setup_refuses_full_mode(monkeypatch):
    monkeypatch.setattr(host_setup, "_is_windows", lambda: True)
    with pytest.raises(host_setup.HostSetupError, match="Full host mode"):
        host_setup.setup(workspace="C:\\work", mode="full")


def test_setup_plan_lists_runtime_tailscale_and_ssh(monkeypatch):
    monkeypatch.setattr(
        host_setup,
        "status",
        lambda: _status(
            running=False, runtime_ready=False, tailscale_connected=False, sshd_running=False
        ),
    )
    monkeypatch.setattr(host_setup, "_autostart_ready", lambda: False)
    monkeypatch.setattr(host_setup, "_ssh_firewall_ready", lambda: False)
    plan = host_setup.setup_plan()
    ids = {step["id"] for step in plan["steps"]}
    assert ids == {"runtime", "tailscale", "ssh", "start", "autostart", "verify"}


def test_setup_operation_reports_done(monkeypatch):
    def fake_execute(op):
        with op.lock:
            op.state = "done"
            op.step = "done"
            op.message = "ready"
            op.result = {"ok": True}

    monkeypatch.setattr(host_setup, "_execute_setup", fake_execute)
    operation_id = host_setup.start_setup(host_setup.SetupOptions())
    deadline = __import__("time").monotonic() + 2
    state = host_setup.setup_status(operation_id)
    while not state["done"] and __import__("time").monotonic() < deadline:
        __import__("time").sleep(0.01)
        state = host_setup.setup_status(operation_id)
    assert state["state"] == "done"
    assert state["result"] == {"ok": True}


def test_setup_operation_can_be_cancelled(monkeypatch):
    def fake_execute(op):
        op.cancelled.wait(2)
        with op.lock:
            op.state = "cancelled"
            op.message = "cancelled"

    monkeypatch.setattr(host_setup, "_execute_setup", fake_execute)
    operation_id = host_setup.start_setup(host_setup.SetupOptions())
    host_setup.cancel_setup(operation_id)
    deadline = __import__("time").monotonic() + 2
    state = host_setup.setup_status(operation_id)
    while not state["done"] and __import__("time").monotonic() < deadline:
        __import__("time").sleep(0.01)
        state = host_setup.setup_status(operation_id)
    assert state["state"] == "cancelled"


def test_open_url_rejects_non_http_schemes():
    assert host_setup.open_url("file:///etc/passwd") is False


def test_client_instructions_include_browser_and_ssh(monkeypatch):
    monkeypatch.setattr(host_setup, "status", lambda: _status())
    text = host_setup.client_instructions("macos")
    assert "https://desktop.tailnet.ts.net" in text
    assert "ssh me@desktop" in text
    assert "Tailscale" in text

    browser_only = host_setup.client_instructions("macos", "browser")
    assert "https://desktop.tailnet.ts.net" in browser_only
    assert "ssh me@desktop" not in browser_only


def test_pending_setup_round_trips_and_resumes(tmp_path, monkeypatch):
    monkeypatch.setattr(host_setup, "_shared_home", lambda: tmp_path)
    options = host_setup.SetupOptions(workspace=str(tmp_path), access="ssh", public_key="ssh-ed25519 AAAA")
    host_setup._save_pending(options, "Restart Windows, then run /host setup again.")

    pending = host_setup.pending_setup()
    assert pending is not None
    assert pending["reason"].startswith("Restart Windows")
    assert pending["options"]["access"] == "ssh"

    captured: list[host_setup.SetupOptions] = []
    monkeypatch.setattr(
        host_setup, "start_setup", lambda opts: captured.append(opts) or "op-resume"
    )
    assert host_setup.resume_setup() == "op-resume"
    assert captured[0].access == "ssh"
    assert captured[0].public_key == "ssh-ed25519 AAAA"


def test_pending_setup_is_cleared_when_a_new_operation_starts(tmp_path, monkeypatch):
    monkeypatch.setattr(host_setup, "_shared_home", lambda: tmp_path)
    host_setup._save_pending(host_setup.SetupOptions(), "restart")
    assert host_setup.pending_setup() is not None

    monkeypatch.setattr(
        host_setup,
        "_execute_setup",
        lambda op: setattr(op, "state", "done"),
    )
    operation_id = host_setup.start_setup(host_setup.SetupOptions())
    assert operation_id
    assert host_setup.pending_setup() is None


def test_add_key_rejects_empty_or_invalid_keys(monkeypatch):
    with pytest.raises(host_setup.HostSetupError):
        host_setup.add_key("   ")
    monkeypatch.setattr(host_setup, "status", lambda: _status())
    operation = host_setup._SetupOperation(id="test", options=host_setup.SetupOptions())
    with pytest.raises(host_setup.HostSetupError):
        host_setup._install_ssh_key(operation, "not-an-ssh-key")


def test_ensure_wsl_requests_a_restart_when_no_distro_exists(monkeypatch):
    monkeypatch.setattr(host_setup, "_wsl_distros", lambda: ["docker-desktop"])
    payloads: list[str] = []
    monkeypatch.setattr(
        host_setup,
        "_run_elevated_powershell",
        lambda payload, timeout=300: payloads.append(payload),
    )
    operation = host_setup._SetupOperation(id="test", options=host_setup.SetupOptions())
    with pytest.raises(host_setup.HostSetupRestartRequired):
        host_setup._ensure_wsl(operation)
    assert "--install" in payloads[0]


def test_ensure_ssh_skips_elevation_when_the_tailnet_rule_is_ready(monkeypatch):
    monkeypatch.setattr(host_setup, "_is_windows", lambda: True)
    monkeypatch.setattr(host_setup, "_is_wsl", lambda: False)
    monkeypatch.setattr(host_setup, "_ssh_firewall_ready", lambda: True)
    monkeypatch.setattr(
        host_setup,
        "_run_elevated_powershell",
        lambda *args, **kwargs: pytest.fail("elevation should not run"),
    )
    operation = host_setup._SetupOperation(id="test", options=host_setup.SetupOptions())
    host_setup._ensure_ssh(operation)


def test_native_key_install_appends_and_locks_down_the_file(tmp_path, monkeypatch):
    monkeypatch.setattr(host_setup, "_is_windows", lambda: False)
    monkeypatch.setattr(host_setup, "_is_wsl", lambda: False)
    monkeypatch.setattr(host_setup.Path, "home", classmethod(lambda cls: tmp_path))
    key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITest machina-client"
    operation = host_setup._SetupOperation(id="test", options=host_setup.SetupOptions())

    host_setup._install_ssh_key(operation, key)
    host_setup._install_ssh_key(operation, key)

    target = tmp_path / ".ssh/authorized_keys"
    assert target.read_text(encoding="utf-8").count(key) == 1
    if sys.platform != "win32":
        assert target.stat().st_mode & 0o777 == 0o600


def test_autostart_writes_a_hidden_wsl_startup_entry(tmp_path, monkeypatch):
    startup = tmp_path / "Machina Host.vbs"
    monkeypatch.setattr(host_setup, "_startup_path", lambda: startup)
    monkeypatch.setattr(host_setup, "_is_windows", lambda: False)
    monkeypatch.setattr(host_setup, "_is_wsl", lambda: True)
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    monkeypatch.setenv("HOME", "/home/me")
    operation = host_setup._SetupOperation(id="test", options=host_setup.SetupOptions())

    host_setup._configure_autostart(operation)

    content = startup.read_text(encoding="utf-8")
    assert 'wsl.exe -d "Ubuntu" -- "/home/me/.machina-host/start.sh"' in content.replace('""', '"')
    assert host_setup._autostart_ready() is True


def test_current_tmux_session_reads_the_tmux_name(monkeypatch):
    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,1,0")
    monkeypatch.setattr(
        host_setup.shutil,
        "which",
        lambda name: "/usr/bin/tmux" if name == "tmux" else None,
    )
    monkeypatch.setattr(
        host_setup,
        "_run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, "dev\n", ""),
    )
    assert host_setup._current_tmux_session() == "dev"


def test_current_tmux_session_is_none_outside_tmux(monkeypatch):
    monkeypatch.delenv("TMUX", raising=False)
    assert host_setup._current_tmux_session() is None


def test_status_uses_the_current_tmux_session_for_a_new_host(monkeypatch):
    monkeypatch.setattr(host_setup, "_is_windows", lambda: False)
    monkeypatch.setattr(host_setup, "_is_wsl", lambda: False)
    monkeypatch.setattr(host_setup, "_current_tmux_session", lambda: "dev")
    monkeypatch.setattr(host_setup.local_host, "status_host", lambda: (None, False, False))
    monkeypatch.setattr(host_setup, "_tailscale_status", lambda: (True, "host.ts.net", None))
    monkeypatch.setattr(host_setup, "_sshd_running", lambda: True)

    assert host_setup.status().session == "dev"


def test_start_uses_the_current_tmux_and_session(monkeypatch):
    monkeypatch.setattr(host_setup, "_is_windows", lambda: False)
    monkeypatch.setattr(host_setup, "_is_wsl", lambda: False)
    monkeypatch.setattr(host_setup, "_current_tmux_session", lambda: "dev")
    monkeypatch.setattr(
        host_setup,
        "status",
        lambda: _status(runtime="native", distro="", running=False, session="machina"),
    )
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        host_setup.local_host, "start_host", lambda **kwargs: captured.update(kwargs)
    )
    host_setup.start(
        machina_session="11111111-2222-3333-4444-555555555555",
        session="machina",
    )
    assert captured["session"] == "dev"
    assert captured["machina_session"] == "11111111-2222-3333-4444-555555555555"


def test_start_can_force_a_fresh_session(monkeypatch):
    monkeypatch.setattr(host_setup, "_is_windows", lambda: False)
    monkeypatch.setattr(host_setup, "_is_wsl", lambda: False)
    monkeypatch.setattr(host_setup, "_current_tmux_session", lambda: "dev")
    monkeypatch.setattr(
        host_setup,
        "status",
        lambda: _status(runtime="native", distro="", running=False, session="machina"),
    )
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        host_setup.local_host, "start_host", lambda **kwargs: captured.update(kwargs)
    )
    host_setup.start(machina_session="", session="machina")
    assert captured["session"] == "machina"
    assert captured["machina_session"] is None


def test_start_refuses_a_new_session_from_inside_the_hosted_tmux(monkeypatch):
    monkeypatch.setattr(host_setup, "_is_windows", lambda: False)
    monkeypatch.setattr(host_setup, "_is_wsl", lambda: False)
    monkeypatch.setattr(host_setup, "_current_tmux_session", lambda: "machina")
    monkeypatch.setattr(
        host_setup,
        "status",
        lambda: _status(runtime="native", distro="", running=True, session="machina"),
    )
    with pytest.raises(host_setup.HostSetupError, match="inside the hosted"):
        host_setup.start(machina_session="")




