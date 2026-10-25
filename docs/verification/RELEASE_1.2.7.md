# Machina 1.2.7

Native PostgreSQL provisioning and a truthful Windows installer.

## What changed

`machina init` no longer stops at:

```text
database "machina" does not exist
```

If PostgreSQL is reachable and the selected database is missing, it connects
to the `postgres` maintenance database, creates the target database, enables
`vector` and `pg_trgm`, and then applies the schema. This covers the common
case where Windows already has a native PostgreSQL service on port 5432.

If pgvector is not available on that server, the installer falls back to the
managed Docker container. When port 5432 is occupied, the container is
published on the next free port (normally 5433), and both `DATABASE_URL` and
`MACHINA_DB_PORT` are updated. The lifecycle uses that port on subsequent
launches.

The installer no longer reports success when `machina init` failed. It prints
the database URL and the exact next action, and exits with an error. Docker
install failures are also explained instead of silently falling through.
Re-running the installer from inside an existing checkout now fast-forwards
the repository first, so the latest installer fixes are actually used.

## Verification

- A real PostgreSQL probe created a missing database, enabled pgvector, applied
  the schema, and was removed afterward.
- Lifecycle tests cover alternate-port creation and credential persistence.
- Installer, CLI, backend, UI, and Docker CI jobs pass.
