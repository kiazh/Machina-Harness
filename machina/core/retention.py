"""Age-based retention for operational tables.

The durable transcript and archive are the product's memory and are never
pruned automatically. Operational evidence (audit rows, usage ledger,
expired artifacts) otherwise grows without bound; operators can bound it
with ``*_retention_days``. ``0`` disables a pruner and keeps rows forever.
"""

from __future__ import annotations

from typing import Any

from machina.core.config import config as default_config
from machina.db.lazy import LazyDb
from machina.observability.diagnostics import record_failure

db = LazyDb()

__all__ = ["prune_expired"]


async def prune_expired(*, config: Any | None = None) -> dict[str, int]:
    """Delete operational rows older than their configured retention.

    Returns ``{table: deleted_count}``. A missing database is a no-op; each
    pruner fails independently so one bad statement cannot block the others.
    """
    settings = config or default_config
    if not getattr(db, "connected", False):
        return {}
    plan: tuple[tuple[str, str, str], ...] = (
        ("audit_events", "created_at", "audit_retention_days"),
        ("llm_usage", "created_at", "usage_retention_days"),
        ("tool_artifacts", "expires_at", "artifact_retention_days"),
    )
    removed: dict[str, int] = {}
    for table, column, key in plan:
        try:
            days = int(settings.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if days <= 0:
            continue
        condition = f"{column} IS NOT NULL AND {column} < now() - ($1 * interval '1 day')"
        try:
            from machina.db.connection import parse_command_count

            result = await db.execute(
                f"DELETE FROM {table} WHERE {condition}",  # nosec B608 - fixed identifiers/bound value
                days,
            )
            removed[table] = parse_command_count(result)
        except Exception as error:
            record_failure(f"retention.{table}", error)
    return removed
