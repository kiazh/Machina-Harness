# Machina 1.2.4

Automatic database startup and provisioning.

## What changed

`machina`, `machina serve`, and `machina init` now ensure the managed
PostgreSQL database is available before doing work:

1. Start Docker Desktop (Windows/macOS) or the Docker service (Linux) when
   the daemon is not already running.
2. Start an existing stopped `machina-db` container.
3. If the container does not exist and `DATABASE_URL` points at the
   installer-owned loopback `machina` database, create it with a generated
   password, persist the matching `DATABASE_URL`, apply the schema, and record
   a client lease.

The generated container uses `--restart unless-stopped`. Native or external
PostgreSQL is never started, created, or stopped by Machina. Set
`MACHINA_DB_MANAGE=0` to opt out of Docker lifecycle management.

The Windows installer now starts Docker Desktop and attempts a winget install
of Docker Desktop when it is missing before provisioning the database. The
Bash installer starts Docker Desktop/Engine before starting the container.
Both scripts keep the existing container credentials in sync with `.env`.

## Verification

- Database lifecycle tests cover start, create, credential persistence, and
  last-client stop.
- Installer tests cover daemon startup and Docker provisioning paths.
- Backend, UI, and Docker CI jobs pass.

## Scope

Self-hosted, bring-your-own-model software. No hosted SLA, backup guarantee,
or managed production service is included.
