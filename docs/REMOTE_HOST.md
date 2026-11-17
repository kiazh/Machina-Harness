# Remote host shell

`machina host` keeps the terminal UI running on a Linux or macOS host and gives
you two ways back into the same shell:

- SSH into the host and attach to the `machina` tmux session.
- Open the token-protected browser terminal served by `ttyd`.

The gateway and the agent remain local to the host. The stdio gateway token
model is unchanged; the network boundary is SSH or a private tunnel.

## One-shot setup from Machina (recommended)

Run `/host` in the Machina TUI on the computer that will stay online. The
default path is a short wizard:

1. Choose **Browser + SSH**, **Browser only**, or **SSH only**.
2. Choose the device you will connect from (macOS, Windows, or Linux).
3. Accept the default execution mode, workspace, and optional client SSH key.

To find the client public key, run `cat ~/.ssh/id_ed25519.pub` on macOS or Linux,
or `Get-Content $env:USERPROFILE\.ssh\id_ed25519.pub` in Windows PowerShell. If
the file does not exist, create a key first with `ssh-keygen -t ed25519`.
You can also leave the key empty and run `/host add-key` later.

The setup then installs and verifies the pieces it needs:

- a WSL2 Ubuntu runtime on Windows, or native `tmux`/`ttyd` on Linux/macOS;
- the Machina host runtime and a hidden `ttyd` bridge on `127.0.0.1`;
- Tailscale, including the private HTTPS URL from `tailscale serve --bg`;
- Windows OpenSSH Server with a firewall rule restricted to the Tailscale
  address range, while broad OpenSSH/LAN rules are disabled;
- an optional client SSH public key;
- a Windows logon entry that starts the host after a reboot.

Windows may ask for administrator approval (UAC) while installing WSL,
Tailscale, OpenSSH, or the firewall rule. Approve the prompt on the host. If a
Windows feature needs a restart, `/host` saves the setup and shows
**Resume setup** the next time you open it. After the restart, open Machina on
the host (or use the browser path if it is already running) and choose
**Resume setup**.

Use `/host advanced` when you need a custom browser port, tmux session name,
or the deliberately unguarded `full` mode. Use `/host add-key` to install
another client key later. `/host setup` is idempotent, so it is also the repair
path.

Everything after this section is the manual equivalent for operators who want
to perform the steps themselves.

## Requirements

- Linux (recommended) or macOS.
- `tmux`.
- `ttyd` for the browser terminal.
- Node.js and the normal Machina runtime on the host.
- A private network or tunnel. Do not expose `ttyd` directly to the public
  internet.

Debian/Ubuntu:

```bash
sudo apt update
sudo apt install tmux ttyd
```

For other distributions, install `tmux` from the package manager and `ttyd`
from its release page or package repository.

## Windows host through WSL2

`machina host` uses tmux and ttyd, so on Windows run the host from WSL2. Keep
the Windows Machina install as the normal desktop client, and install a
separate Linux runtime in WSL for the host shell.

From an Ubuntu WSL shell:

```bash
# Host dependencies
mkdir -p ~/.local/bin
curl -fsSL -o ~/.local/bin/ttyd \
  https://github.com/tsl0922/ttyd/releases/download/1.7.7/ttyd.x86_64
chmod +x ~/.local/bin/ttyd

# A Linux Machina runtime, separate from the Windows .venv
uv venv ~/.machina-host/venv --python 3.12
uv pip install --python ~/.machina-host/venv/bin/python \
  -e /mnt/c/Users/<you>/machina
```

Then start the host with the Windows checkout `.env` and with database
management disabled, because the Windows PostgreSQL is already serving the
database:

```bash
export MACHINA_ENV_FILE=/mnt/c/Users/<you>/machina/.env
export MACHINA_DB_MANAGE=0
export PATH="$HOME/.local/bin:$PATH"

~/.machina-host/venv/bin/machina host start \
  --workspace /mnt/c/Users/<you> \
  --mode ask
```

The browser page is available from Windows at `http://127.0.0.1:7681` because
WSL forwards localhost. From another Wi-Fi, put Tailscale or Cloudflare in
front of it:

```powershell
tailscale serve --bg 7681
```

For the SSH path, enable Windows OpenSSH Server in an elevated PowerShell:

```powershell
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Start-Service sshd
Set-Service -Name sshd -StartupType Automatic
```

Restrict SSH to the Tailscale address range and disable the broad LAN rule:

```powershell
Remove-NetFirewallRule -Name 'sshd-tailscale' -ErrorAction SilentlyContinue
New-NetFirewallRule -Name 'sshd-tailscale' `
  -DisplayName 'OpenSSH Server (Tailscale)' `
  -Enabled True -Direction Inbound -Protocol TCP -Action Allow `
  -LocalPort 22 -RemoteAddress 100.64.0.0/10 -Profile Any
Set-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -Enabled False
```

Then connect from another machine and attach to the WSL tmux session:

```bash
ssh <windows-user>@<host> -t "wsl -d Ubuntu -- tmux attach -t machina"
```

`/host setup` performs these steps for you. Do not expose port 22 directly to
the internet. Use Tailscale SSH or a private tunnel.

## Start the host

```bash
machina host start --workspace ~/work --mode ask
```

By default this:

- creates or reuses a tmux session named `machina`;
- starts the TUI in that session with automatic updates disabled;
- starts `ttyd` on `127.0.0.1:7681`;
- generates a browser username and password;
- writes `~/.machina/host.json` with mode `0600`;
- prints the SSH and browser commands.

Useful options:

```bash
machina host start --workspace ~/work --mode workspace --port 7681
machina host status
machina host url --show-credentials
machina host attach
machina host stop
machina host stop --keep-session
```

`--mode full` is refused unless you pass `--allow-full`. A remote shell already
has the same authority as your host user; do not make that the default.

The browser bridge only accepts loopback binds. Use one of the access paths
below.

## SSH access

From the school machine:

```bash
ssh user@home-host -t 'machina host attach'
```

The exact command is printed by `machina host start`.

## Browser access

### SSH port forwarding

The smallest setup, with no extra service:

```bash
ssh -L 7681:127.0.0.1:7681 user@home-host
```

Leave that terminal open, then browse to:

```text
http://127.0.0.1:7681
```

Enter the generated username and password from `machina host url
--show-credentials`.

### Tailscale

Install Tailscale on the host and the client, then keep the bridge on loopback
and serve it only on your tailnet:

```bash
tailscale serve --bg 7681
```

Open the HTTPS URL Tailscale prints. Traffic stays on your tailnet.

### Cloudflare Tunnel

Use Cloudflare Tunnel when the school device cannot install a VPN or SSH client.
Put Cloudflare Access in front of the hostname, or use another identity-aware
proxy. Do not expose the `ttyd` port directly.

```bash
cloudflared tunnel --url http://127.0.0.1:7681
```

Then protect the generated hostname with Cloudflare Access. The basic-auth
credential from `machina host` is a second layer, not a replacement for
identity-aware access.

## Security notes

- `machina host start` defaults to `ask` mode. The browser terminal can control
  the host through the normal approval flow.
- `ttyd` receives the browser credential on its command line, so a local user
  who can inspect the host process table can read it. The credential file is
  `0600`; use a private tunnel and a dedicated host user.
- The browser password is printed once at `start`. Recover it with
  `machina host url --show-credentials`.
- The remote shell keeps the host user's files, API keys, and `~/.machina`
  state. Run it as a dedicated user and point `--workspace` at the directory
  you want exposed.
- Do not expose `ttyd` directly on a public interface. Use SSH forwarding,
  Tailscale, or Cloudflare Access with TLS.
- Approval cards remain required for mutating tools. A remote approval is still
  a human approval; inspect the target before accepting it.

## Autostart with systemd

`machina host start` exits after it has created the tmux session and started
ttyd, so a oneshot unit with `RemainAfterExit` is enough:

```ini
[Unit]
Description=Machina remote host shell
After=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
User=machina
WorkingDirectory=/home/machina/work
ExecStart=/usr/local/bin/machina host start --workspace /home/machina/work --mode ask
ExecStop=/usr/local/bin/machina host stop

[Install]
WantedBy=multi-user.target
```

Install it as `/etc/systemd/system/machina-host.service`, then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now machina-host
```

Keep the bridge on loopback. Put Tailscale Serve or Cloudflare Access in front
of it; do not add a public port just because the service runs at boot.

## Troubleshooting

**`tmux` or `ttyd` is missing.** Install both, then rerun `machina host start`.

**The browser page cannot connect.** Confirm `machina host status` shows both
`tmux` and `ttyd` running, then check the tunnel or SSH forward. The bridge is
bound to `127.0.0.1`, so a remote browser cannot reach it without a forward.

**The port is already in use.** Use `--port` with a free port and update the
forward or tunnel command.

**The session survived but the bridge died.** `machina host start` repairs the
host. `machina host stop --keep-session` stops only the browser bridge and
leaves the tmux shell running.

## Later work

- Bundle the browser terminal instead of depending on `ttyd`.
- Add `machina connect` over a WebSocket JSON-RPC transport for a native remote
  TUI.
- Add systemd user units and a Docker Compose profile for the host shell.
- Add explicit remote policy for secret-display commands and multi-client
  attach.
