# Machina 1.2.2

Native Windows installation, no WSL required.

## Install on Windows

```powershell
irm https://raw.githubusercontent.com/kiazh/Machina-Harness/main/install.ps1 | iex
```

The PowerShell installer:

- installs missing Git, Python 3.12, or Node.js LTS with winget when available;
- clones Machina with retries and shows download progress;
- creates the `.venv` and installs backend/UI dependencies;
- generates `.env` secrets;
- starts a local pgvector PostgreSQL container through Docker Desktop when
  the configured database is unavailable;
- initializes the schema and runs `machina doctor`;
- adds `.venv\Scripts` to the user PATH so `machina` works in new terminals.

Python 3.11+, Node.js 22.19+, and either Docker Desktop or PostgreSQL with
pgvector are still required. If winget or Docker is unavailable, the installer
prints the exact manual command instead of silently leaving a broken install.

## Notes

The Bash installer remains available for macOS/Linux and WSL. No WSL is
required on Windows. If `machina` is not visible in the current PowerShell
window, open a new terminal after installation.
