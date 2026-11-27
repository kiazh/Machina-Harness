"""Tool registry — decorator-based with JSON Schema inference."""

from __future__ import annotations

import importlib as _importlib
import inspect
import sys as _sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, get_type_hints

from machina.observability.instrumentation import instrument

if TYPE_CHECKING:
    from machina.core.models import ToolDefinition


# SEAM: leaf-core seam for machina.core (exceptions ToolError/ValidationError,
# models ToolDefinition, provider audit_log). Deferred via sys.modules/importlib
# so this module imports with machina.core blocked; resolves to the same objects
# when core is present. No logic change.
def _core_symbol(module: str, name: str):
    mod = _sys.modules.get(module)
    if mod is None:
        mod = _importlib.import_module(module)
    return getattr(mod, name)


def _result_size(value: object) -> int:
    """Bounded size signal for a tool result (never the body itself)."""
    try:
        if isinstance(value, (str, bytes)):
            return len(value)
        if isinstance(value, (list, tuple, dict, set, frozenset)):
            return len(value)
        return len(str(value))
    except Exception:
        return 0


@dataclass
class Tool:
    """A registered tool.

    ``effects`` declares side-effect capabilities for the permission broker
    (e.g. ``fs.read``, ``fs.write``, ``exec``, ``net``, ``memory``,
    ``delegate``). It is broker metadata only — never sent to the model
    (see get_tool_definitions). Tools without mutating effects stay
    in-process work; extensible side-effecting tools must declare effects or
    they fall through to approval as unknown mutating operations.
    """

    name: str
    description: str
    parameters: dict  # JSON Schema
    func: Callable
    is_async: bool = False
    effects: tuple[str, ...] = ()


class ToolRegistry:
    """Global tool registry — register tools with decorator.

    Built-in tool modules register lazily: a package that imports only
    ``machina.tools`` stays lightweight, and the first registry access runs
    the configured loader once. Direct ``ToolRegistry()`` instances keep the
    historical empty-registry behavior.
    """

    def __init__(self) -> None:
        self._registered_tools: dict[str, Tool] = {}
        self._definitions_cache: list[ToolDefinition] | None = None
        self._loader: Callable[[], None] | None = None
        self._loader_modules: frozenset[str] = frozenset()
        self._loader_lock = threading.RLock()
        self._loading = False
        self._tools_loaded = False

    def _ensure_tools_loaded(self) -> None:
        """Run the lazy built-in loader once (recursion-safe)."""
        loader = self._loader
        if loader is None or self._tools_loaded:
            return
        with self._loader_lock:
            # Another thread may have completed the load while we waited, and
            # registrations performed by the loader itself must not recurse.
            if self._tools_loaded or self._loading:
                return
            self._loading = True
            try:
                loader()
                self._tools_loaded = True
            finally:
                self._loading = False

    @property
    def _tools(self) -> dict[str, Tool]:
        """Registered tools; touching this mapping triggers lazy registration."""
        self._ensure_tools_loaded()
        return self._registered_tools

    def register(
        self,
        name: str | None = None,
        description: str | None = None,
        parameters: dict | None = None,
        effects: tuple[str, ...] | list[str] | None = None,
    ) -> Callable:
        """Decorator to register a function as a tool.

        Usage:
            @registry.register(description="Read a file")
            def read_file(path: str, offset: int = 1, limit: int = 2000) -> str:
                ...
        """
        declared = tuple(effects or ())

        def decorator(func: Callable) -> Callable:
            # Built-ins load before an external registration so an override of
            # a built-in name keeps the historical "custom wins" precedence.
            # Registrations from a built-in module itself must not recurse into
            # the loader (a direct `import machina.tools.builtins` is legal).
            if getattr(func, "__module__", "") not in self._loader_modules:
                self._ensure_tools_loaded()
            tool_name = name or func.__name__
            if tool_name == "__no_tools__":
                raise ValueError("__no_tools__ is reserved for an empty tool allowlist")
            tool_desc = description or (func.__doc__ or "").strip().split("\n")[0]
            tool_params = parameters or self._infer_schema(func)
            is_async = inspect.iscoroutinefunction(func)
            self._registered_tools[tool_name] = Tool(
                name=tool_name,
                description=tool_desc,
                parameters=tool_params,
                func=func,
                is_async=is_async,
                effects=declared,
            )
            # Invalidate definitions cache when a new tool is registered
            self._definitions_cache = None
            return func

        return decorator

    def declare_effects(self, effects: dict[str, tuple[str, ...] | list[str]]) -> None:
        """Attach broker effect metadata to already-registered tools.

        Explicit declarations live next to each tool module (LP additional
        check); unknown side-effecting tools without declarations fall
        through to approval instead of silent in-process execution.
        """
        for name, eff in effects.items():
            tool = self._registered_tools.get(name)
            if tool is not None:
                tool.effects = tuple(eff)
        self._definitions_cache = None

    def _infer_schema(self, func: Callable) -> dict:
        """Infer JSON Schema from function signature."""
        sig = inspect.signature(func)
        type_map = {
            str: "string",
            int: "integer",
            float: "number",
            bool: "boolean",
            list: "array",
            dict: "object",
        }
        properties = {}
        required = []
        # ``from __future__ import annotations`` makes every annotation a string
        # at runtime, so ``param.annotation`` is e.g. ``"int"`` and never matches
        # ``type_map``. Resolve the real runtime types with get_type_hints().
        try:
            hints = get_type_hints(func)
        except Exception:
            hints = {}
        for param_name, param in sig.parameters.items():
            if param_name == "self":
                continue
            annotation = hints.get(param_name, param.annotation)
            param_type = type_map.get(annotation, "string")
            properties[param_name] = {
                "type": param_type,
                "description": param_name,
            }
            if param.default is inspect.Parameter.empty:
                required.append(param_name)
        return {
            "type": "object",
            "properties": properties,
            "required": required,
        }

    def get_tool_definitions(self) -> list[ToolDefinition]:
        """Get all tool definitions for LLM function calling (cached).

        Tool definitions are immutable during a run, so we cache the result
        after the first call to avoid rebuilding the list on every LLM request.

        Every schema sent to the model declares ``additionalProperties``
        explicitly: ``_validate_tool_args`` rejects undeclared arguments
        (fail closed), so the model must not be shown JSON Schema's implicit
        default of allowing extras. An explicit ``true`` is preserved for
        tools that deliberately opt in.
        """
        self._ensure_tools_loaded()
        if self._definitions_cache is None:
            self._definitions_cache = [
                _core_symbol("machina.core.models", "ToolDefinition")(
                    name=t.name,
                    description=t.description,
                    parameters=self._declared_parameters(t.parameters),
                )
                for t in self._registered_tools.values()
            ]
        return self._definitions_cache

    @staticmethod
    def _declared_parameters(parameters: dict) -> dict:
        """Return a copy of *parameters* with an explicit additionalProperties.

        Only booleans can be honored by ``_validate_tool_args``: a property
        pattern schema (dict) is normalized to ``false`` because the runtime
        validator cannot enforce it.
        """
        if not isinstance(parameters, dict):
            return parameters
        declared = dict(parameters)
        if not isinstance(declared.get("additionalProperties"), bool):
            declared["additionalProperties"] = False
        return declared

    def _validate_tool_args(self, tool: Tool, kwargs: dict[str, Any]) -> None:
        """Validate tool arguments against the tool's JSON Schema.

        Checks:
        - Required parameters are present
        - Parameter types match the schema
        - No extra parameters are passed unless the schema declares
          ``additionalProperties: true`` (absent means fail closed, matching
          the declaration ``get_tool_definitions`` sends to the model)
        """
        schema = tool.parameters
        properties = schema.get("properties", {})
        required = schema.get("required", [])

        # Check required parameters
        for req_param in required:
            if req_param not in kwargs:
                raise _core_symbol("machina.core.exceptions", "ValidationError")(
                    f"Tool '{tool.name}' missing required parameter: '{req_param}'"
                )

        # Check for unknown parameters (honor an explicit opt-in)
        if schema.get("additionalProperties") is not True:
            allowed_params = set(properties.keys())
            provided_params = set(kwargs.keys())
            unknown_params = provided_params - allowed_params
            if unknown_params:
                raise _core_symbol("machina.core.exceptions", "ValidationError")(
                    f"Tool '{tool.name}' received unknown parameters: {unknown_params}. "
                    f"Allowed: {allowed_params}"
                )

        # Type checking
        type_map = {
            "string": str,
            "integer": int,
            "number": (int, float),
            "boolean": bool,
            "array": list,
            "object": dict,
        }
        # Maximum accepted string argument length — bounds memory / prompt
        # injection blast radius for a single tool call.
        _MAX_STRING_ARG_CHARS = 20_000

        for param_name, param_value in kwargs.items():
            if param_name not in properties:
                continue
            param_schema = properties[param_name]
            expected_type = param_schema.get("type")
            if expected_type and expected_type in type_map:
                # bool is a subclass of int: reject it explicitly where a
                # real integer/number is expected.
                if expected_type in ("integer", "number") and isinstance(param_value, bool):
                    raise _core_symbol("machina.core.exceptions", "ValidationError")(
                        f"Tool '{tool.name}' parameter '{param_name}' expected type "
                        f"'{expected_type}', got 'bool'"
                    )
                python_type = type_map[expected_type]
                if not isinstance(param_value, python_type):
                    raise _core_symbol("machina.core.exceptions", "ValidationError")(
                        f"Tool '{tool.name}' parameter '{param_name}' expected type "
                        f"'{expected_type}', got '{type(param_value).__name__}'"
                    )
            # The generic cap protects memory and prompt safety, but the file
            # tools exist to carry file content. Their own limits are explicit
            # and still bounded; a 20k cap silently made write_file unable to
            # write ordinary source files.
            limit = _MAX_STRING_ARG_CHARS
            if tool.name == "write_file" and param_name == "content":
                limit = 1_000_000
            elif tool.name == "edit_file" and param_name in {"old_text", "new_text"}:
                limit = 200_000
            if isinstance(param_value, str) and len(param_value) > limit:
                raise _core_symbol("machina.core.exceptions", "ValidationError")(
                    f"Tool '{tool.name}' parameter '{param_name}' is too long "
                    f"(max {limit} characters)"
                )

    async def execute(self, name: str, /, **kwargs) -> Any:
        """Execute a tool by name with input validation.

        Every call runs the tool fresh: results are never cached. Reads
        carry no freshness contract and mutating tools must never serve
        stale output (audit 2026-10-13: a TTL cache returned workspace A's
        file while executing in workspace B; R4-096 removed the cache —
        all shipped tools declare broker effects instead).
        """
        self._ensure_tools_loaded()
        arg_keys = sorted(str(key) for key in kwargs)
        result: Any = None
        with instrument(
            "tool",
            "execute",
            tool=name,
            arg_keys=arg_keys,
            arg_count=len(kwargs),
        ) as span:
            if name not in self._registered_tools:
                _core_symbol("machina.core.provider", "audit_log")(
                    "tool_execution_error", tool_name=name, error="not_registered"
                )
                raise _core_symbol("machina.core.exceptions", "ToolError")(
                    f"Tool '{name}' not registered"
                )

            tool = self._registered_tools[name]

            # Input validation
            try:
                self._validate_tool_args(tool, kwargs)
            except ValueError as e:
                _core_symbol("machina.core.provider", "audit_log")(
                    "tool_execution_validation_error", tool_name=name, error=str(e)
                )
                raise

            # Execute
            if tool.is_async:
                result = await tool.func(**kwargs)
            else:
                result = tool.func(**kwargs)
            span.note(result_size=_result_size(result))
        return result

    def list_tools(self) -> list[str]:
        """List all registered tool names (loading built-ins on first use)."""
        self._ensure_tools_loaded()
        return list(self._registered_tools.keys())

    # Public registry lookup; tool tests read it back, no production caller. Kept (R4-106).
    def get_tool(self, name: str) -> Tool | None:
        """Get a tool by name."""
        self._ensure_tools_loaded()
        return self._registered_tools.get(name)

    @classmethod
    def reset(cls) -> None:
        """Reset the global ToolRegistry singleton to a fresh instance."""
        global registry
        loader = registry._loader
        loader_modules = registry._loader_modules
        registry = cls()
        registry._loader = loader
        registry._loader_modules = loader_modules


# Global registry
registry = ToolRegistry()


def set_tool_loader(
    loader: Callable[[], None] | None,
    modules: tuple[str, ...] = (),
) -> None:
    """Install the built-in module loader used by the global registry.

    ``modules`` names the modules whose own registrations must not trigger the
    loader, which keeps direct submodule imports cycle-free.
    """
    registry._loader = loader
    registry._loader_modules = frozenset(modules)
