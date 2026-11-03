"""Turn-local user steering, isolated from delegated child sessions."""

from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from uuid import UUID, uuid4


@dataclass(frozen=True)
class SteeringItem:
    id: str
    text: str


class SteeringInbox:
    def __init__(self, session_id: UUID, changed: Callable | None = None):
        self.session_id = session_id
        self.items: list[SteeringItem] = []
        self.changed = changed

    def snapshot(self) -> list[dict]:
        return [asdict(item) for item in self.items]

    def notify(self, delivered: list[dict] | None = None) -> None:
        if self.changed:
            try:
                self.changed(self.snapshot(), delivered or [])
            except Exception as error:
                from machina.observability.diagnostics import record_failure

                record_failure("steering.notify", error)

    def add(self, text: str) -> SteeringItem:
        if not text.strip() or len(text) > 20_000:
            raise ValueError("steering must contain 1–20000 characters")
        if len(self.items) >= 32:
            raise ValueError("steering queue is full (32 messages)")
        item = SteeringItem(uuid4().hex, text.strip())
        self.items.append(item)
        self.notify()
        return item

    def remove(self, item_id: str) -> bool:
        before = len(self.items)
        self.items = [item for item in self.items if item.id != item_id]
        if len(self.items) != before:
            self.notify()
            return True
        return False

    def replace(self, item_id: str, text: str) -> bool:
        if not text.strip() or len(text) > 20_000:
            raise ValueError("steering must contain 1–20000 characters")
        for index, item in enumerate(self.items):
            if item.id == item_id:
                self.items[index] = SteeringItem(item_id, text.strip())
                self.notify()
                return True
        return False

    def drain(self, session_id: UUID) -> list[SteeringItem]:
        if session_id != self.session_id:
            return []
        items, self.items = self.items, []
        return items


steering_inbox: ContextVar[SteeringInbox | None] = ContextVar("steering_inbox", default=None)


def pending_steering(session_id: UUID) -> bool:
    inbox = steering_inbox.get()
    return bool(inbox and inbox.session_id == session_id and inbox.items)


async def apply_steering(session_id: UUID, agent_id: str, messages: list) -> bool:
    inbox = steering_inbox.get()
    if inbox is None or inbox.session_id != session_id or not inbox.items:
        return False
    from machina.core.assembler import get_token_count
    from machina.core.context import context_manager

    delivered = []
    # Persist before exposing a message to the model or marking it delivered.
    claimed = inbox.drain(session_id)
    for index, item in enumerate(claimed):
        try:
            await context_manager.add_chunk(
                session_id=session_id,
                agent_id=agent_id,
                chunk_type="user_message",
                payload={"content": item.text, "steering_id": item.id},
                token_count=get_token_count(item.text),
            )
        except BaseException:
            inbox.items = claimed[index:] + inbox.items
            inbox.notify(delivered)
            raise
        messages.append({"role": "user", "content": item.text})
        delivered.append(asdict(item))
    inbox.notify(delivered)
    return True
