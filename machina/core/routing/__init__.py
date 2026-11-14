"""Connected-model inventory and deterministic task routing."""

from machina.core.routing.catalog import ModelInventory, build_inventory, profile_from_row
from machina.core.routing.router import (
    build_task_profile,
    detect_parallel_intent,
    route_from_session,
    route_task,
)
from machina.core.routing.types import (
    COST_TIERS,
    ORCHESTRATION_MODES,
    ROLES,
    TRANSPORTS,
    ModelProfile,
    ModelRoute,
    RouteCandidate,
    RouteDecision,
    RoutingError,
    TaskRouteProfile,
)

__all__ = [
    "COST_TIERS",
    "ORCHESTRATION_MODES",
    "ROLES",
    "TRANSPORTS",
    "ModelInventory",
    "ModelProfile",
    "ModelRoute",
    "RouteCandidate",
    "RouteDecision",
    "RoutingError",
    "TaskRouteProfile",
    "build_inventory",
    "build_task_profile",
    "detect_parallel_intent",
    "profile_from_row",
    "route_from_session",
    "route_task",
]
