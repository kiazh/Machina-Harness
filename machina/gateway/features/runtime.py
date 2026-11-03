"""Read-only engine discovery that does not require a database connection."""

from typing import TYPE_CHECKING, Any

from machina import __version__
from machina.protocol import PROTOCOL_VERSION

if TYPE_CHECKING:
    from machina.gateway.server import Gateway


async def runtime_capabilities(gw: "Gateway", params: dict[str, Any]) -> dict[str, Any]:
    return {
        "engineVersion": __version__,
        "protocolVersion": PROTOCOL_VERSION,
        "supportedProtocolVersions": [1, PROTOCOL_VERSION],
        "storageProfile": "postgres",
        "database": "ready" if gw._db_ready else "not-initialized",
        "methods": sorted(gw._methods),
    }
