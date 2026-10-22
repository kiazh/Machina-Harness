# Machina 1.2.1

Installer reliability patch.

## Fixed

- Transient GitHub clone failures are retried three times before the installer
  gives up.
- WSL is detected separately from a regular Linux host and gets the correct
  `python3-venv` guidance.
- Python selection prefers an installation with working `ensurepip`.
- If only a no-pip venv can be created, the installer creates it with
  `--without-pip` and bootstraps pip from the official bootstrap endpoint.
- Windows users are directed to WSL2 or Git Bash; native PowerShell is not a
  valid shell for the `curl | bash` command.
- The installer now shows a Machina banner and a download spinner while cloning.

## Verification

- Installer and quoting tests: 58 passed.
- CI: backend, UI, and Docker jobs pass on the release commit.
- The one-command installer remains the first item in the README.
