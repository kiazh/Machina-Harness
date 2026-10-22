# Machina 1.2.3

Ordered full-stack shutdown.

## Added

```bash
machina stop
```

Shutdown order is fixed:

1. `machina serve` processes
2. TUI/gateway processes
3. installer-managed `machina-db` Docker container

Long-lived TUI and API-server processes register a database lease. A normal
TUI exit stops the managed database only when no other Machina client is still
running. `machina stop` waits for each client to exit before moving to the next
stage, then stops PostgreSQL.

The next `machina` or `machina serve` starts the managed container again. Native
or external PostgreSQL is never stopped. Set `MACHINA_DB_MANAGE=0` to opt out of
Docker lifecycle management.

The TUI now handles SIGTERM by closing its gateway normally, and `machina stop`
signals the UI child rather than leaving the gateway orphaned.

## Verification

- Database lifecycle and CLI shutdown tests pass.
- Typecheck and the UI suite pass.
- Installer and quoting tests pass.
