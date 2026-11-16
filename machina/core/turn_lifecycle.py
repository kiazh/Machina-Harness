"""Transport-neutral turn lifecycle around :class:`TurnEngine`.

Every transport (stdio gateway, HTTP SSE, headless CLI, scheduler) used to
hand-roll the same sequence: claim the session turn, load the fresh session,
build the agent, register an approval handler, start renewal, drive the
agent, release the claim and close the provider. This module owns that
sequence once; transports keep only their wire serialization, terminal frame
mapping, and outer cancellation/timeout supervisors.

The seam is intentionally small:

- :class:`TurnLifecycle` owns claim acquisition, session loading, agent
  construction, approval-handler registration, managed renewal, the
  :class:`TurnEngine` relay/run, and bounded cleanup/release.
- :class:`TurnStepSupervisor` is the shared per-step supervisor used by the
  SSE transport (single-task drivers call ``TurnEngine.stream`` directly).
- :class:`ApprovalPolicy` makes the one legitimate approval divergence
  explicit: ``interactive`` surfaces cards through the broker handler and
  suppresses stream pause markers; ``headless`` registers no handler and
  relays the typed pause markers (the broker returns a structured pause).

Ordering and failure semantics are preserved from the audited transports:
approval handler and renewal start before agent construction, cleanup clears
the handler, cancels transport-owned approval waits, then runs the engine's
bounded source/background/provider/claim tail. ``close()`` never turns an
ordinary cleanup failure into a raise; diagnostics are recorded under the
transport ``boundary``.
"""

from __future__ import annotations

import asyncio
import contextvars
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from machina.core.agent_factory import build_agent_for_session
from machina.core.exceptions import StaleOwnershipError
from machina.core.models import Session
from machina.core.session import session_manager
from machina.core.turn_engine import (
    TurnClose,
    TurnEngine,
    TurnEvent,
    TurnHooks,
    TurnSpec,
)
from machina.observability.diagnostics import record_failure

__all__ = [
    "ApprovalPolicy",
    "TurnAgentBuilder",
    "TurnLifecycle",
    "TurnStepSupervisor",
]


class TurnAgentBuilder(Protocol):
    """Build the agent for one loaded session."""

    async def __call__(self, session: Session) -> Any: ...


@dataclass(slots=True)
class ApprovalPolicy:
    """How one transport handles human approval for a turn.

    ``interactive`` requires a handler and relays approval cards through it;
    the engine suppresses stream pause markers so a card is never emitted
    twice. ``headless`` installs no handler (the broker returns a structured
    pause) and relays the typed pause marker.
    """

    mode: Literal["interactive", "headless"] = "headless"
    handler: Callable[[dict], Awaitable[str]] | None = None
    principal: str = "tui"
    on_resolved: Callable[[dict], None] | None = None

    def __post_init__(self) -> None:
        if self.mode not in ("interactive", "headless"):
            raise ValueError(f"unknown approval mode: {self.mode!r}")
        if self.mode == "interactive" and self.handler is None:
            raise ValueError("interactive approval policy requires a handler")

    @property
    def emits_pause_markers(self) -> bool:
        return self.mode == "headless"


class TurnLifecycle:
    """One session turn: claim -> build -> drive -> cleanup.

    Construction is side-effect free. :meth:`start` installs the approval
    handler and starts managed renewal; :meth:`prepare` builds/attaches the
    agent (callers that need a supervised build pass ``builder=``); :meth:`stream`
    or :meth:`run` drives the engine; :meth:`close` owns the cleanup tail.
    """

    def __init__(
        self,
        *,
        session_id: uuid.UUID,
        turn_id: str,
        token: str | None = None,
        text: str = "",
        images: list[dict[str, str]] | None = None,
        verbose: bool = False,
        resume: tuple[dict, dict] | None = None,
        approval: ApprovalPolicy | None = None,
        agent_builder: TurnAgentBuilder | None = None,
        hooks: TurnHooks | None = None,
        boundary: str = "turn_lifecycle",
        renew_interval_s: float = 30.0,
        join_timeout: float = 1.7,
        settle_grace: float = 0.35,
        source_close_timeout_s: float = 5.0,
        engine: TurnEngine | None = None,
    ) -> None:
        self.session_id = session_id
        self.turn_id = turn_id
        self.token = token
        self.approval = approval or ApprovalPolicy()
        self._agent_builder = agent_builder
        self._boundary = boundary
        self._session: Session | None = None
        self._agent: Any | None = None
        self._started = False
        self._approval_installed = False
        self._renew_task: asyncio.Task | None = None
        self.engine = engine or TurnEngine(
            TurnSpec(
                session_id=session_id,
                turn_id=turn_id,
                token=token,
                text=text,
                images=images,
                verbose=verbose,
                resume=resume,
            ),
            hooks,
            boundary=boundary,
            renew_interval_s=renew_interval_s,
            join_timeout=join_timeout,
            settle_grace=settle_grace,
            source_close_timeout_s=source_close_timeout_s,
            emit_pause_markers=self.approval.emits_pause_markers,
        )

    # ─── shared primitives (claim / session) ───────────────────────────────

    @staticmethod
    async def acquire_claim(session_id: uuid.UUID, *, turn_id: str | None = None) -> str | None:
        """Claim the session turn; ``None`` means another owner is live."""
        from machina.core.turns import try_begin_turn

        return await try_begin_turn(session_id, turn_id=turn_id)

    @staticmethod
    async def load_session(session_id: uuid.UUID) -> Session | None:
        """Execution-critical fresh read (never the display cache)."""
        return await session_manager.get_fresh(session_id)

    # ─── lifecycle phases ──────────────────────────────────────────────────

    def start(
        self,
        *,
        ownership_lost: asyncio.Event | None = None,
        renew: bool = True,
    ) -> asyncio.Task | None:
        """Install approval + start managed renewal (idempotent).

        ``renew=False`` is for DB-less callers (scheduler local claims never
        expire); claim release still runs in :meth:`close`.
        """
        if self._started:
            return self._renew_task
        self._install_approval()
        self._started = True
        if not renew:
            return None
        self._renew_task = self.engine.start_renewal(ownership_lost=ownership_lost)
        return self._renew_task

    async def prepare(
        self,
        session: Session | None,
        *,
        builder: TurnAgentBuilder | None = None,
    ) -> Any:
        """Build and attach the agent through the shared/session factory."""
        self._session = session
        build = builder or self._agent_builder or self._default_builder
        agent = await build(session)  # type: ignore[arg-type]
        if agent is None:
            raise RuntimeError("agent builder returned no agent")
        self._agent = agent
        self.engine.attach(agent)
        return agent

    async def stream(self) -> AsyncIterator[TurnEvent]:
        """Relay semantic events; the transport owns per-step supervision."""
        source = self.engine.stream()
        try:
            async for event in source:
                yield event
        finally:
            # Drivers close the relay promptly on every exit (exceptions/
            # cancellation included); the engine's source-close barrier then
            # owns the bounded provider/stream tail.
            await source.aclose()

    async def run(self, *, prompt: str | None = None) -> Any:
        """Drive the attached agent's non-streaming ``run`` (jobs)."""
        return await self.engine.run(prompt=prompt)

    async def close(
        self,
        *,
        cancelled: bool = False,
        timed_out: bool = False,
        retirement_task: asyncio.Task | None = None,
        on_closing: Callable[[], None] | None = None,
    ) -> TurnClose:
        """Clear the approval handler, run transport cleanup, close the engine."""
        if self._approval_installed:
            self._approval_installed = False
            try:
                from machina.permissions.broker import set_approval_handler

                set_approval_handler(None)
            except Exception as exc:
                self._record("approval_clear", exc)
        if on_closing is not None:
            try:
                on_closing()
            except Exception as exc:
                self._record("on_closing", exc)
        return await self.engine.close(
            cancelled=cancelled,
            timed_out=timed_out,
            retirement_task=retirement_task,
        )

    # ─── engine proxies (terminal/accounting stay single-sourced) ──────────

    @property
    def final(self) -> Any | None:
        return self.engine.final

    @property
    def tokens(self) -> int:
        return self.engine.tokens

    @property
    def tool_count(self) -> int:
        return self.engine.tool_count

    @property
    def pauses(self) -> list[dict[str, Any]]:
        return self.engine.pauses

    @property
    def ownership_lost(self) -> asyncio.Event:
        return self.engine.ownership_lost

    def flush_tail(self) -> str:
        return self.engine.flush_tail()

    def build_terminal(self, *, cancelled: bool = False) -> dict[str, Any]:
        return self.engine.build_terminal(cancelled=cancelled)

    def schedule_auto_compact(self) -> None:
        self.engine.schedule_auto_compact()

    # ─── internals ─────────────────────────────────────────────────────────

    async def _default_builder(self, session: Session) -> Any:
        return await build_agent_for_session(session)

    def _install_approval(self) -> None:
        from machina.permissions.broker import set_approval_handler

        if self.approval.mode == "interactive":
            set_approval_handler(
                self.approval.handler,
                principal=self.approval.principal,
                turn_id=self.turn_id,
                on_resolved=self.approval.on_resolved,
            )
        else:
            set_approval_handler(
                None,
                principal=self.approval.principal,
                turn_id=self.turn_id,
            )
        self._approval_installed = True

    def _record(self, site: str, exc: BaseException) -> None:
        record_failure(f"{self._boundary}.{site}", exc)


class TurnStepSupervisor:
    """Run each relay step in its own task with ownership-loss/deadline races.

    The SSE transport drives an async generator whose ``__anext__`` may be
    cancelled mid-flight. This supervisor owns the shared step Context (F07),
    the active-task handoff used by quarantine cleanup, and the one loss event
    shared with renewal. Single-task drivers (gateway, CLI) do not need it.
    """

    def __init__(self, *, ownership_lost: asyncio.Event | None = None) -> None:
        self.ownership_lost = ownership_lost or asyncio.Event()
        self.active: asyncio.Task | None = None
        self._context: contextvars.Context | None = None

    def arm(self, context: contextvars.Context) -> None:
        """Run subsequent steps in *context* (after transport ContextVars)."""
        self._context = context

    async def run(self, awaitable: Awaitable[Any], timeout: float | None = None) -> Any:
        context = self._context
        if context is None:
            task = asyncio.ensure_future(awaitable)
        else:
            task = asyncio.get_running_loop().create_task(awaitable, context=context)
        task.add_done_callback(_observe_task)
        self.active = task
        lost = asyncio.create_task(self.ownership_lost.wait())
        try:
            done, _ = await asyncio.wait(
                {task, lost},
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if self.ownership_lost.is_set():
                task.cancel()
                raise StaleOwnershipError("turn ownership lost")
            if task not in done:
                task.cancel()
                raise TimeoutError("turn timed out")
            return task.result()
        finally:
            lost.cancel()
            if task.done():
                self.active = None

    def cancel_active(self) -> None:
        """Stop the step currently driving the relay (ownership loss)."""
        active = self.active
        if active is not None and not active.done():
            active.cancel()


def _observe_task(task: asyncio.Task) -> None:
    """Consume a step outcome so a failed step is never a silent orphan."""
    if not task.cancelled():
        task.exception()
