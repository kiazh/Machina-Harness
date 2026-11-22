# Machina 1.2.8

Docker Desktop fallback for machines without winget.

## What changed

When Docker Desktop is missing, the Windows installer still tries winget
first. If winget is unavailable or its Docker package fails, the installer now:

1. downloads Docker's official signed Windows installer;
2. verifies its Authenticode signature;
3. runs it with `install --quiet --accept-license`;
4. starts Docker Desktop and waits for the daemon; and
5. provisions PostgreSQL on a free loopback port.

Set `MACHINA_AUTO_INSTALL_DOCKER=0` to disable this fallback.

## Limits

A fully unattended Windows install cannot bypass the operating system:

- Docker Desktop requires WSL2/Hyper-V, administrator approval, and sometimes
  a reboot.
- If Docker cannot be installed and the existing native PostgreSQL server does
  not have pgvector, the installer stops and explains what is missing rather
  than reporting a false success.
- Git, Python, Node.js, and Docker are installed with winget when available.
  Winget itself is part of Windows 11 and current Windows 10 App Installer.

## Verification

- PowerShell syntax and installer tests pass.
- Backend, UI, and Docker CI jobs pass.
- Wheel and source distribution checks pass.
