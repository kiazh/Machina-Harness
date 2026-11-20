"""Container sandbox backend selection for the terminal tool.

The Docker invocation is shared by every OCI runtime. The backend only
selects the runtime; gVisor and Kata are therefore real isolation options
rather than separate command implementations. Kata can be configured with
its Firecracker VMM on Linux by the host's Kata runtime configuration.
"""

from __future__ import annotations

import os

HOST_BACKENDS = frozenset({"disabled", "local"})
CONTAINER_BACKENDS = frozenset({"docker", "gvisor", "kata", "firecracker"})
WASM_BACKENDS = frozenset({"wasm"})
ALL_BACKENDS = HOST_BACKENDS | CONTAINER_BACKENDS | WASM_BACKENDS

DEFAULT_RUNTIMES = {
    "gvisor": "runsc",
    "kata": "kata-runtime",
    # Firecracker is selected by the host's Kata runtime configuration; the
    # OCI runtime remains kata-runtime rather than a separate Docker runtime.
    "firecracker": "kata-runtime",
}
_RUNTIME_ENV = {
    "gvisor": "MACHINA_GVISOR_RUNTIME",
    "kata": "MACHINA_KATA_RUNTIME",
    "firecracker": "MACHINA_FIRECRACKER_RUNTIME",
}


def validate_backend(backend: str) -> str:
    value = (backend or "disabled").strip().lower()
    if value not in ALL_BACKENDS:
        allowed = ", ".join(sorted(ALL_BACKENDS))
        raise ValueError(f"terminal sandbox must be one of: {allowed}")
    return value


def is_container_backend(backend: str) -> bool:
    """Best-effort classification for callers that must not raise on legacy values."""
    return (backend or "").strip().lower() in CONTAINER_BACKENDS


def is_isolated_backend(backend: str) -> bool:
    return is_container_backend(backend) or is_wasm_backend(backend)


def is_wasm_backend(backend: str) -> bool:
    return (backend or "").strip().lower() in WASM_BACKENDS


def container_backend(backend: str) -> bool:
    return validate_backend(backend) in CONTAINER_BACKENDS


def runtime_args(backend: str) -> list[str]:
    """Return the Docker ``--runtime`` prefix for an isolation backend."""
    value = validate_backend(backend)
    if value not in DEFAULT_RUNTIMES:
        return []
    runtime = os.environ.get(_RUNTIME_ENV[value], "").strip() or DEFAULT_RUNTIMES[value]
    return ["--runtime", runtime]


def isolation_required_error(backend: str) -> str:
    return (
        f"sandbox execution requires terminal_sandbox=docker, gvisor, kata, "
        f"firecracker, or wasm (configured: {validate_backend(backend)})"
    )
