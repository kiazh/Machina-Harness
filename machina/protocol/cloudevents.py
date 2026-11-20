"""CloudEvents 1.0 projection for the existing Machina event schema.

The native JSON-RPC/SSE event remains the source of truth. This adapter is
opt-in per HTTP prompt (``eventFormat: "cloudevents"``) so existing clients
keep the byte-compatible native frames while event routers can consume a
standard envelope.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

CLOUDEVENTS_SPEC_VERSION = "1.0"
CLOUDEVENTS_SOURCE = "/machina"


def wrap_event(
    event: dict[str, Any],
    *,
    source: str = CLOUDEVENTS_SOURCE,
    event_id: str | None = None,
    event_time: datetime | None = None,
) -> dict[str, Any]:
    """Wrap one native event without changing its payload or validation."""
    event_type = str(event.get("type") or "event")
    occurred = event_time or datetime.now(UTC)
    return {
        "specversion": CLOUDEVENTS_SPEC_VERSION,
        "id": event_id or uuid.uuid4().hex,
        "source": source,
        "type": f"io.machina.{event_type}",
        "time": occurred.isoformat(),
        "datacontenttype": "application/json",
        "data": event,
    }
