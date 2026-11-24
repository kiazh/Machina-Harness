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


def test_free_hermes_parent_is_not_replaced_by_a_broken_other_provider():
    # Observed failure: the router ranked DeepSeek (402, no balance) and
    # OpenRouter (401, invalid key) above the free Hermes model that was
    # already answering, so every delegated child failed before work began.
    assert keep_free_hermes_parent_route(
        "hermes", "meituan/longcat-2.5:free", "deepseek", "deepseek-v4-pro"
    ) == ("hermes", "meituan/longcat-2.5:free")
    assert keep_free_hermes_parent_route(
        "hermes", "meituan/longcat-2.5:free", "openrouter", "minimax/minimax-m3.1-flash:free"
    ) == ("hermes", "meituan/longcat-2.5:free")
