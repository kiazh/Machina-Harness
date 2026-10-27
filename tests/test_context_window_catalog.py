"""Runtime capacities agree with the provider-scoped model menu catalog."""

from machina.core.context_windows import context_window_for
from machina.core.provider.models_dev import _snapshot, provider_models
from machina.core.provider.specs import PROVIDER_SPECS


def test_bundled_model_windows_match_runtime_for_each_provider():
    for provider in _snapshot()["providers"]:
        for model in provider_models(provider):
            assert context_window_for(model["id"], provider) == model["contextWindow"]


def test_direct_provider_instance_resolves_its_own_catalog():
    class Provider:
        spec = PROVIDER_SPECS["openai"]

    assert context_window_for("gpt-5.6-sol", Provider()) == 1_050_000


def test_chatgpt_subscription_keeps_its_account_catalog_capacity():
    assert context_window_for("gpt-6.1-sol", "chatgpt") == 500_000
