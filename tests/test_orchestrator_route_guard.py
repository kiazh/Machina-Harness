"""Delegated children never upgrade a free Hermes parent to a paid model."""

from machina.core.orchestrator import keep_free_hermes_parent_route


def test_free_hermes_parent_route_is_sticky_for_children():
    assert keep_free_hermes_parent_route(
        "hermes", "tencent/hy4-flash:free", "hermes", "~z-ai/glm-flash-latest"
    ) == ("hermes", "tencent/hy4-flash:free")


def test_paid_or_other_provider_routes_are_left_alone():
    assert keep_free_hermes_parent_route(
        "hermes", "~z-ai/glm-flash-latest", "hermes", "tencent/hy4-flash:free"
    ) == ("hermes", "tencent/hy4-flash:free")
    assert keep_free_hermes_parent_route("kiro", "auto", "hermes", "tencent/hy4-flash:free") == (
        "hermes",
        "tencent/hy4-flash:free",
    )
    assert keep_free_hermes_parent_route(None, None, "hermes", "x") == ("hermes", "x")
