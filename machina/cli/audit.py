"""Read-only access to the durable sanitized audit trail."""

from __future__ import annotations

import json
from datetime import datetime

import typer
from rich.markup import escape

from machina.cli.common import _db_connected, _run
from machina.cli.output import console
from machina.db.connection import db


def _parse_since(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise typer.BadParameter(
            "must be an ISO-8601 timestamp, for example 2026-01-01T00:00:00Z"
        ) from None


def audit(
    limit: int = typer.Option(50, "--limit", "-n", min=1, max=500, help="Rows to show"),
    since: str = typer.Option("", "--since", help="Only rows at or after this ISO-8601 time"),
    as_json: bool = typer.Option(False, "--json", help="Emit one JSON object per line"),
):
    """Show recent sanitized audit events from PostgreSQL."""

    async def _audit() -> None:
        cutoff = _parse_since(since)
        async with _db_connected():
            if cutoff is None:
                rows = await db.fetch(
                    "SELECT id, event, payload, created_at FROM audit_events "
                    "ORDER BY id DESC LIMIT $1",
                    limit,
                )
            else:
                rows = await db.fetch(
                    "SELECT id, event, payload, created_at FROM audit_events "
                    "WHERE created_at >= $1 ORDER BY id DESC LIMIT $2",
                    cutoff,
                    limit,
                )
        for row in rows:
            payload = row["payload"]
            if as_json:
                console.print(
                    json.dumps(
                        {
                            "id": row["id"],
                            "event": row["event"],
                            "payload": payload,
                            "createdAt": row["created_at"].isoformat()
                            if row["created_at"]
                            else None,
                        },
                        ensure_ascii=False,
                    )
                )
            else:
                stamp = (
                    row["created_at"].strftime("%Y-%m-%d %H:%M:%S") if row["created_at"] else "-"
                )
                body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                console.print(
                    f"[dim]{stamp}[/dim] [cyan]{escape(str(row['event']))}[/cyan] "
                    f"[dim]#{row['id']}[/dim] {escape(body[:300])}"
                )

    _run(_audit())
