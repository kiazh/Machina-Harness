"""Agent Client Protocol (ACP) bridge for Zen/editor integrations.

The bridge deliberately delegates execution to the same session factory and
turn lifecycle used by the gateway and HTTP API. It does not create a second
tool/permission path: human approval cards are translated into ACP
``session/request_permission`` calls and resolved through the durable
permission store before the tool executes.

Protocol details are owned by ``agent-client-protocol``; this module is the
Machina session/prompt adapter.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from pathlib import Path
from typing import Any

from acp import (
    PROTOCOL_VERSION,
    Agent,
    RequestError,
    run_agent,
)
from acp.interfaces import Client
from acp.schema import (
    AgentCapabilities,
    AgentMessageChunk,
    AuthenticateResponse,
    CloseSessionResponse,
    DeleteSessionResponse,
    Implementation,
    InitializeResponse,
    ListSessionsResponse,
    LoadSessionResponse,
    McpCapabilities,
    NewSessionResponse,
    PermissionOption,
    PromptCapabilities,
    PromptResponse,
    SessionCapabilities,
    SessionInfo,
    SessionMode,
    SessionModeState,
    SessionUpdateNotice,
    SetSessionModeResponse,
    TextContentBlock,
    ToolCallProgress,
    ToolCallStart,
    ToolCallUpdate,
)

from machina.core.models import Session
from machina.core.session import session_manager
from machina.core.session_mode import (
    MODES,
    activate_durable_mode,
    get_global_default,
    turn_scope,
)
from machina.core.turn_lifecycle import ApprovalPolicy, TurnLifecycle
from machina.db.connection import db
from machina.observability.diagnostics import record_failure

logger = logging.getLogger(__name__)

#: Mode shown by ACP clients. The IDs are Machina's durable mode names.
_MODE_LABELS = {
    "ask": "Ask",
    "workspace": "Workspace",
    "sandbox": "Sandbox",
    "full": "Full host",
}
_MODE_DESCRIPTIONS = {
    "ask": "Ask before governed tool calls.",
    "workspace": "Allow workspace-scoped work; ask for sensitive actions.",
    "sandbox": "Run governed actions in the sandbox.",
    "full": "Full host authority for this session.",
}
_TOOL_KINDS = {
    "file.read": "read",
    "file.write": "edit",
    "file.edit": "edit",
    "file.delete": "delete",
    "terminal": "execute",
    "terminal.exec": "execute",
    "search": "search",
    "web": "fetch",
    "network": "fetch",
}


def _mode_state(mode: str) -> SessionModeState:
    current = mode if mode in MODES else "ask"
    return SessionModeState(
        current_mode_id=current,
        available_modes=[
            SessionMode(
                id=name,
                name=_MODE_LABELS[name],
                description=_MODE_DESCRIPTIONS[name],
            )
            for name in MODES
        ],
    )


def _target_title(card: dict[str, Any]) -> str:
    operation = str(card.get("operation") or card.get("tool") or "action")
    targets = card.get("targets")
    if isinstance(targets, list) and targets:
        target = str(targets[0])
    else:
        target = str(card.get("cwd") or "")
    title = f"{operation}: {target}" if target else operation
    return title[:240]


def _tool_kind(card: dict[str, Any]) -> str:
    operation = str(card.get("operation") or card.get("tool") or "").lower()
    if operation in _TOOL_KINDS:
        return _TOOL_KINDS[operation]
    for prefix, kind in _TOOL_KINDS.items():
        if operation.startswith(prefix):
            return kind
    return "other"


def _prompt_text(prompt: list[Any]) -> str:
    """Extract the text portions of ACP content blocks.

    ACP content is a tagged union. Text is the only supported input; resource
    links are represented as their URI so a client can still reference a file
    without the model receiving an unannounced attachment.
    """
    parts: list[str] = []
    for block in prompt:
        text = getattr(block, "text", None)
        if isinstance(text, str) and text:
            parts.append(text)
            continue
        uri = getattr(block, "uri", None)
        if isinstance(uri, str) and uri:
            parts.append(f"[resource: {uri}]")
    return "\n".join(parts).strip()


class MachinaACPAgent(Agent):
    """Machina implementation of the ACP agent interface."""

    def __init__(self, *, default_mode: str | None = None) -> None:
        if default_mode is not None and default_mode not in MODES:
            raise ValueError(f"mode must be one of: {', '.join(MODES)}")
        self._conn: Client | None = None
        self._default_mode = default_mode or get_global_default()
        if self._default_mode not in MODES:
            self._default_mode = "ask"
        self._sessions: dict[str, uuid.UUID] = {}
        self._acp_for_session: dict[str, str] = {}
        self._default_cwd = os.getcwd()
        self._tasks: dict[str, asyncio.Task[PromptResponse]] = {}
        self._schema_ready = False

    def on_connect(self, conn: Client) -> None:
        self._conn = conn

    async def _ensure_database(self) -> None:
        if self._schema_ready:
            return
        await db.connect()
        await db.initialize_runtime_schema()
        self._schema_ready = True

    async def _load(self, session_id: str) -> Session:
        session_uuid = self._sessions.get(session_id)
        if session_uuid is None:
            try:
                session_uuid = uuid.UUID(session_id)
            except ValueError as error:
                raise RequestError(-32602, "session not found") from error
        session = await session_manager.get_fresh(session_uuid)
        if session is None:
            raise RequestError(-32602, "session not found")
        self._sessions[session_id] = session.id
        self._acp_for_session[str(session.id)] = session_id
        return session

    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: Any | None = None,
        client_info: Implementation | None = None,
        **kwargs: Any,
    ) -> InitializeResponse:
        # The SDK already validates the client's protocol version. We support
        # the stable version exposed by this SDK and advertise it explicitly.
        return InitializeResponse(
            protocol_version=PROTOCOL_VERSION,
            agent_capabilities=AgentCapabilities(
                load_session=True,
                prompt_capabilities=PromptCapabilities(
                    image=False,
                    audio=False,
                    embedded_context=True,
                ),
                mcp_capabilities=McpCapabilities(),
                session_capabilities=SessionCapabilities(list={}, delete={}),
            ),
            agent_info=Implementation(
                name="machina",
                title="Machina",
                version="1.2.14",
            ),
            auth_methods=[],
        )

    async def _notify_unsupported_session_features(
        self,
        session_id: str,
        *,
        mcp_servers: list[Any] | None,
        additional_directories: list[str] | None,
    ) -> None:
        if self._conn is None:
            return
        if mcp_servers:
            await self._conn.session_update(
                session_id=session_id,
                update=SessionUpdateNotice(
                    severity="warning",
                    title="MCP servers not applied",
                    description=(
                        "This Machina ACP build does not attach per-session MCP "
                        "servers yet. Configure MCP with `machina mcp`."
                    ),
                ),
            )
        if additional_directories:
            await self._conn.session_update(
                session_id=session_id,
                update=SessionUpdateNotice(
                    severity="warning",
                    title="Additional directories not activated",
                    description=(
                        "Machina currently executes in the primary workspace. "
                        "Additional roots are recorded for diagnostics only."
                    ),
                ),
            )

    async def new_session(
        self,
        cwd: str,
        additional_directories: list[str] | None = None,
        mcp_servers: list[Any] | None = None,
        **kwargs: Any,
    ) -> NewSessionResponse:
        await self._ensure_database()
        resolved_cwd = str(Path(cwd).expanduser().resolve())
        self._default_cwd = resolved_cwd
        state: dict[str, Any] = {"desktop_workspace_root": resolved_cwd}
        if additional_directories:
            # Machina's workspace authority is the primary root today. Keep
            # the requested roots for diagnostics rather than silently
            # widening execution scope before that authority model is wired.
            state["acp_additional_directories"] = list(additional_directories)
        session = await session_manager.create(
            title="Machina ACP session",
            state=state,
            execution_mode=self._default_mode,
        )
        if self._default_mode == "full":
            await activate_durable_mode(str(session.id), session.agent_id, "full")
        session_id = str(session.id)
        self._sessions[session_id] = session.id
        self._acp_for_session[str(session.id)] = session_id
        await self._notify_unsupported_session_features(
            session_id,
            mcp_servers=mcp_servers,
            additional_directories=additional_directories,
        )
        return NewSessionResponse(
            session_id=session_id,
            modes=_mode_state(session.execution_mode),
        )

    async def load_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[Any] | None = None,
        additional_directories: list[str] | None = None,
        **kwargs: Any,
    ) -> LoadSessionResponse | None:
        await self._ensure_database()
        session = await self._load(session_id)
        if not session.state.get("desktop_workspace_root"):
            await session_manager.update_state_fields(
                session.id, {"desktop_workspace_root": str(Path(cwd).resolve())}
            )
        await self._notify_unsupported_session_features(
            session_id,
            mcp_servers=mcp_servers,
            additional_directories=additional_directories,
        )
        return LoadSessionResponse(modes=_mode_state(session.execution_mode))

    async def list_sessions(
        self, cwd: str | None = None, cursor: str | None = None, **kwargs: Any
    ) -> ListSessionsResponse:
        await self._ensure_database()
        try:
            offset = max(0, int(cursor or "0"))
        except ValueError:
            offset = 0
        sessions = await session_manager.list_sessions(limit=50, offset=offset)
        infos: list[SessionInfo] = []
        for session in sessions:
            detailed = session
            root = session.state.get("desktop_workspace_root")
            if cwd and not isinstance(root, str):
                # The list projection omits state_msgpack for efficiency. ACP
                # filters by cwd, so read durable state only for matching rows.
                fresh = await session_manager.get_fresh(session.id)
                if fresh is None:
                    continue
                detailed = fresh
                root = detailed.state.get("desktop_workspace_root")
            if not isinstance(root, str) or not root:
                if cwd:
                    continue
                root = self._default_cwd
            if cwd and str(Path(root).resolve()) != str(Path(cwd).resolve()):
                continue
            infos.append(
                SessionInfo(
                    session_id=str(detailed.id),
                    cwd=root,
                    title=detailed.title,
                    updated_at=detailed.last_activity.isoformat(),
                )
            )
        next_cursor = str(offset + 50) if len(sessions) >= 50 else None
        return ListSessionsResponse(sessions=infos, next_cursor=next_cursor)

    async def set_session_mode(
        self, session_id: str, mode_id: str, **kwargs: Any
    ) -> SetSessionModeResponse | None:
        if mode_id not in MODES:
            raise RequestError(-32602, f"mode must be one of: {', '.join(MODES)}")
        session = await self._load(session_id)
        await activate_durable_mode(str(session.id), session.agent_id, mode_id)
        session.execution_mode = mode_id
        return SetSessionModeResponse()

    async def authenticate(self, method_id: str, **kwargs: Any) -> AuthenticateResponse | None:
        # Authentication is configured in the Machina environment/credential
        # store before the editor launches the agent. No ACP-local secret flow
        # is exposed.
        return AuthenticateResponse()

    async def _approve(self, card: dict[str, Any]) -> str:
        """Translate a broker approval card into ACP request_permission."""
        from machina.permissions import store as permission_store

        request_id = str(card.get("request_id") or "")
        machina_session_id = str(card.get("session_id") or "")
        turn_id = str(card.get("turn_id") or "")
        session_id = self._acp_for_session.get(machina_session_id)
        if not request_id or not machina_session_id:
            return "denied"

        try:
            record = await permission_store.get_approval(request_id)
        except Exception as error:
            record_failure("acp.load_approval", error)
            return "denied"
        if record is None or str(record.get("session_id") or "") != machina_session_id:
            record_failure(
                "acp.approval_session_mismatch",
                RuntimeError("approval record does not belong to the ACP session"),
            )
            return "denied"
        if str(record.get("status") or "") != "pending":
            return "denied"
        record_turn = str(record.get("turn_id") or "")
        if turn_id and record_turn and record_turn != turn_id:
            record_failure(
                "acp.approval_turn_mismatch",
                RuntimeError("approval record does not belong to the ACP turn"),
            )
            return "denied"

        if not session_id or self._conn is None:
            # Delegated child sessions are not directly addressable by the
            # editor. Retire the owned pending row instead of leaving a cardless
            # approval that poisons later identical actions.
            try:
                await permission_store.resolve_decision(request_id, "cancelled", principal="acp")
            except Exception as error:
                record_failure("acp.resolve_unaddressable", error)
            return "denied"

        tool_call = ToolCallUpdate(
            tool_call_id=request_id,
            kind=_tool_kind(card),
            status="pending",
            title=_target_title(card),
            name=str(card.get("tool") or card.get("operation") or "tool"),
            raw_input={
                "operation": card.get("operation"),
                "targets": card.get("targets"),
                "argv": card.get("argv"),
                "shell": card.get("shell"),
                "cwd": card.get("cwd"),
                "network": card.get("network"),
                "content_preview": card.get("content_preview"),
                "file_diff": card.get("file_diff"),
            },
        )
        options = [
            PermissionOption(option_id="allow_once", name="Allow once", kind="allow_once"),
            PermissionOption(
                option_id="allow_always",
                name="Allow for this session",
                kind="allow_always",
            ),
            PermissionOption(option_id="reject_once", name="Reject", kind="reject_once"),
        ]
        try:
            response = await self._conn.request_permission(
                session_id=session_id,
                tool_call=tool_call,
                options=options,
            )
        except Exception as error:
            record_failure("acp.request_permission", error)
            try:
                await permission_store.resolve_decision(request_id, "cancelled", principal="acp")
            except Exception as resolve_error:
                record_failure("acp.resolve_cancelled", resolve_error)
            return "denied"

        outcome = getattr(response, "outcome", None)
        option_id = getattr(outcome, "option_id", None)
        if option_id is None and isinstance(outcome, dict):
            option_id = outcome.get("optionId")
        selected = str(option_id or "")
        if selected not in {"allow_once", "allow_always"}:
            try:
                await permission_store.resolve_decision(
                    request_id, "cancelled" if not selected else "denied", principal="acp"
                )
            except Exception as error:
                record_failure("acp.resolve_rejected", error)
            return "denied"

        try:
            resolved = await permission_store.resolve_decision(
                request_id, "approved", principal="acp"
            )
        except Exception as error:
            record_failure("acp.resolve_approved", error)
            return "denied"
        if resolved is None:
            return "denied"
        if selected == "allow_always":
            rec = await permission_store.get_approval(request_id)
            if rec is not None:
                targets = [t for t in str(rec.get("target", "")).split("|") if t]
                try:
                    await permission_store.save_grant(
                        {
                            "session_id": rec.get("session_id", ""),
                            "agent_id": rec.get("agent_id", ""),
                            "mode": "ask",
                            "capability": permission_store.grant_capability(rec),
                            "scope_path": targets[0] if targets else None,
                            "scope_type": "file",
                            "grant_kind": "session",
                            "digest": rec.get("digest", ""),
                        }
                    )
                except Exception as error:
                    # The one-time approval still stands; a failed reusable
                    # grant only means the next action asks again.
                    record_failure("acp.save_grant", error)
        return "approved"

    async def _send_text(self, session_id: str, text: str) -> None:
        if not text or self._conn is None:
            return
        await self._conn.session_update(
            session_id=session_id,
            update=AgentMessageChunk(content=TextContentBlock(text=text)),
        )

    async def _run_prompt(self, session_id: str, session: Session, text: str) -> PromptResponse:
        turn_id = uuid.uuid4().hex[:12]
        token = await TurnLifecycle.acquire_claim(session.id, turn_id=turn_id)
        if not token:
            raise RequestError(-32000, "session already has a running turn")

        lifecycle = TurnLifecycle(
            session_id=session.id,
            turn_id=turn_id,
            token=token,
            text=text,
            verbose=False,
            approval=ApprovalPolicy(
                mode="interactive",
                handler=self._approve,
                principal="acp",
            ),
            boundary="acp",
        )
        cancelled = False
        sent_text = False
        result: PromptResponse | None = None
        lifecycle.start()
        try:
            with turn_scope():
                await lifecycle.prepare(session)
                async for event in lifecycle.stream():
                    if event.kind == "delta":
                        value = str(event.payload.get("text") or "")
                        if value:
                            sent_text = True
                            await self._send_text(session_id, value)
                    elif event.kind == "tool_start":
                        call_id = str(event.payload.get("id") or "")
                        name = str(event.payload.get("name") or "tool")
                        if call_id and self._conn is not None:
                            await self._conn.session_update(
                                session_id=session_id,
                                update=ToolCallStart(
                                    tool_call_id=call_id,
                                    title=name[:240],
                                    name=name,
                                    status="in_progress",
                                    raw_input=event.payload.get("args") or {},
                                ),
                            )
                    elif event.kind == "tool_complete":
                        call_id = str(event.payload.get("id") or "")
                        if call_id and self._conn is not None:
                            await self._conn.session_update(
                                session_id=session_id,
                                update=ToolCallProgress(
                                    tool_call_id=call_id,
                                    status=(
                                        "failed" if event.payload.get("isError") else "completed"
                                    ),
                                    raw_output=event.payload.get("result"),
                                ),
                            )
                    elif event.kind == "done":
                        final = lifecycle.final
                        if not sent_text and final is not None:
                            content = str(getattr(final, "content", "") or "")
                            if content:
                                sent_text = True
                                await self._send_text(session_id, content)
                        if final is not None and getattr(final, "needs_approval", None):
                            result = PromptResponse(stop_reason="refusal")
                    elif event.kind == "needs_approval":
                        # Interactive policy owns cards; a marker here means
                        # a delegated/headless pause that ACP cannot resolve.
                        logger.warning("ACP turn produced an unhandled approval marker")
                        result = PromptResponse(stop_reason="refusal")
            if result is None:
                result = PromptResponse(stop_reason="end_turn")
            return result
        except asyncio.CancelledError:
            cancelled = True
            return PromptResponse(stop_reason="cancelled")
        finally:
            try:
                await lifecycle.close(cancelled=cancelled)
            except Exception as error:
                record_failure("acp.turn_close", error)

    async def prompt(
        self,
        session_id: str,
        prompt: list[Any],
        **kwargs: Any,
    ) -> PromptResponse:
        await self._ensure_database()
        session = await self._load(session_id)
        text = _prompt_text(prompt)
        if not text:
            return PromptResponse(stop_reason="refusal")

        task = asyncio.create_task(
            self._run_prompt(session_id, session, text),
            name=f"machina-acp-prompt-{session_id[:8]}",
        )
        self._tasks[session_id] = task
        try:
            return await task
        except asyncio.CancelledError:
            # ACP cancellation cancels this task through cancel(); report a
            # protocol-level cancelled turn when the handler itself survives.
            if task.cancelled():
                return PromptResponse(stop_reason="cancelled")
            raise
        finally:
            if self._tasks.get(session_id) is task:
                self._tasks.pop(session_id, None)

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        task = self._tasks.get(session_id)
        if task is not None and not task.done():
            task.cancel()

    async def close_session(self, session_id: str, **kwargs: Any) -> CloseSessionResponse | None:
        task = self._tasks.get(session_id)
        if task is not None and not task.done():
            task.cancel()
        self._sessions.pop(session_id, None)
        return CloseSessionResponse()

    async def delete_session(self, session_id: str, **kwargs: Any) -> DeleteSessionResponse:
        session = await self._load(session_id)
        await session_manager.delete(session.id)
        self._acp_for_session.pop(str(session.id), None)
        self._sessions.pop(session_id, None)
        return DeleteSessionResponse()

    async def shutdown(self) -> None:
        for task in list(self._tasks.values()):
            if not task.done():
                task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()


async def serve(*, default_mode: str | None = None) -> None:
    """Run the ACP agent over stdin/stdout after initializing Machina."""
    from machina.observability.audit import audit_persistence

    agent = MachinaACPAgent(default_mode=default_mode)
    await agent._ensure_database()
    audit_persistence.start()
    try:
        await run_agent(agent)
    finally:
        try:
            await agent.shutdown()
        finally:
            await audit_persistence.stop()
            await db.close()


def main(*, default_mode: str | None = None) -> None:
    asyncio.run(serve(default_mode=default_mode))


# Kept small and import-safe for tests: no DB or stdio work happens until the
# CLI invokes main/serve.
__all__ = ["MachinaACPAgent", "main", "serve"]
