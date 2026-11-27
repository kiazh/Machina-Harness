from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

ADAPTER_DIR = Path(__file__).resolve().parents[1]


def load_module(filename: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ADAPTER_DIR / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_config_renderer_produces_expected_json() -> None:
    installer = load_module("install.py", "machina_harness_bench_install")
    rendered = installer.render_config(
        base_url="http://proxy.local/v1/",
        model="deepseek-flash",
        max_tokens=32768,
        temperature=0.5,
        context_window=200000,
        max_agent_turns=150,
    )
    assert json.loads(rendered) == {
        "base_url": "http://proxy.local/v1",
        "model": "deepseek-flash",
        "max_tokens": 32768,
        "temperature": 0.5,
        "context_window": 200000,
        "max_agent_turns": 150,
    }


def test_runtime_patch_is_idempotent() -> None:
    installer = load_module("install.py", "machina_harness_bench_install_patch")
    source = """SPECS = {
    "claude-code": HarnessSpec(
        harness_id="claude-code",
    ),
}

def render_harness_config(spec):
    return json.dumps(config)
    raise ValueError(f"no config renderer for {spec.harness_id}")
"""
    patched = installer.patch_runtime(source)
    assert installer.patch_runtime(patched) == patched
    compile(patched, "harness_runtime.py", "exec")
    assert patched.count('"machina": HarnessSpec(') == 1
    assert patched.count('if spec.harness_id == "machina":') == 1
    assert 'config_name="config.json"' in patched
    assert 'protocol="chat_completions"' in patched
    assert "trace_complete=False" in patched


def test_patched_runtime_renderer_produces_expected_json() -> None:
    installer = load_module("install.py", "machina_harness_bench_renderer")
    source = """import json

class HarnessSpec:
    def __init__(self, harness_id, **kwargs):
        self.harness_id = harness_id

SPECS = {
    "claude-code": HarnessSpec(harness_id="claude-code"),
}

def render_harness_config(
    spec, *, base_url, model, max_tokens, temperature,
    thinking="high", context_window=200000, max_agent_turns=150
):
    base_url = base_url.rstrip("/")
    raise ValueError(f"no config renderer for {spec.harness_id}")
    """
    patched = installer.patch_runtime(source)
    namespace: dict[str, object] = {"HARNESS_DOCKER_DIR": Path("docker/harnesses")}
    exec(compile(patched, "harness_runtime.py", "exec"), namespace)
    renderer = namespace["render_harness_config"]
    rendered = renderer(
        namespace["SPECS"]["machina"],
        base_url="http://proxy.local/v1/",
        model="deepseek-flash",
        max_tokens=32768,
        temperature=0.5,
        context_window=200000,
        max_agent_turns=150,
    )
    assert json.loads(rendered) == {
        "base_url": "http://proxy.local/v1",
        "model": "deepseek-flash",
        "max_tokens": 32768,
        "temperature": 0.5,
        "context_window": 200000,
        "max_agent_turns": 150,
    }


def test_entrypoint_uses_no_embedded_upstream_secret() -> None:
    source = (ADAPTER_DIR / "entrypoint.sh").read_text(encoding="utf-8")
    assert 'CUSTOM_API_KEY="local-vllm-no-auth"' in source
    assert "DEEPSEEK_API_KEY" not in source
    assert re.search(r"sk-[A-Za-z0-9]{16,}", source) is None
    assert re.search(r"Bearer\s+[A-Za-z0-9._-]{16,}", source) is None


def test_run_script_defaults() -> None:
    runner = load_module("run.py", "machina_harness_bench_run")
    args = runner.build_parser().parse_args(["--harness-bench", "/tmp/harness-bench"])
    assert args.model == "deepseek-flash"
    assert args.base_url == "https://api.deepseek.com/v1"
    assert args.workers == 4
    assert args.tasks is None
