#!/usr/bin/env python3
"""Install the Machina OCI adapter into a Harness-Bench checkout."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

RENDERER_MARKER = "    # MACHINA HARNESS-BENCH ADAPTER CONFIG"

SPEC_BLOCK = """    # MACHINA HARNESS-BENCH ADAPTER
    "machina": HarnessSpec(
        harness_id="machina",
        version="local",
        image="harnessbench/machina:local",
        protocol="chat_completions",
        config_name="config.json",
        docker_context=HARNESS_DOCKER_DIR / "machina",
        user="1000:1000",
        trace_complete=False,
    ),
"""

RENDERER_BLOCK = """    # MACHINA HARNESS-BENCH ADAPTER CONFIG
    if spec.harness_id == "machina":
        config = {
            "base_url": base_url,
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "context_window": context_window,
            "max_agent_turns": max_agent_turns,
        }
        return json.dumps(config, ensure_ascii=False, indent=2) + "\\n"

"""


def render_config(
    *,
    base_url: str,
    model: str,
    max_tokens: int,
    temperature: float,
    context_window: int,
    max_agent_turns: int,
) -> str:
    """Render the JSON config expected by the adapter's entrypoint."""
    import json

    config = {
        "base_url": base_url.rstrip("/"),
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "context_window": context_window,
        "max_agent_turns": max_agent_turns,
    }
    return json.dumps(config, ensure_ascii=False, indent=2) + "\n"


def patch_runtime(source: str) -> str:
    """Add Machina to a Harness-Bench runtime module, exactly once."""
    if '"machina": HarnessSpec(' not in source:
        insertion_point = '    "claude-code": HarnessSpec('
        if insertion_point not in source:
            raise ValueError("could not find SPECS insertion point in harness_runtime.py")
        source = source.replace(insertion_point, SPEC_BLOCK + insertion_point, 1)

    if RENDERER_MARKER not in source:
        insertion_point = '    raise ValueError(f"no config renderer for {spec.harness_id}")'
        if insertion_point not in source:
            raise ValueError(
                "could not find render_harness_config insertion point in harness_runtime.py"
            )
        source = source.replace(insertion_point, RENDERER_BLOCK + insertion_point, 1)
    return source


def install(checkout: Path, wheel: Path) -> None:
    checkout = checkout.expanduser().resolve()
    wheel = wheel.expanduser().resolve()
    runtime_path = checkout / "tools" / "harness_runtime.py"
    if not runtime_path.is_file():
        raise FileNotFoundError(f"not a Harness-Bench checkout: {checkout}")
    if not wheel.is_file() or wheel.suffix != ".whl":
        raise FileNotFoundError(f"Machina wheel does not exist: {wheel}")

    adapter_dir = Path(__file__).resolve().parent
    docker_dir = checkout / "docker" / "harnesses" / "machina"
    docker_dir.mkdir(parents=True, exist_ok=True)
    for stale_wheel in docker_dir.glob("machina-*.whl"):
        if stale_wheel.resolve() != wheel:
            stale_wheel.unlink()
    for filename in ("Dockerfile", "entrypoint.sh"):
        shutil.copy2(adapter_dir / filename, docker_dir / filename)
    copied_wheel = docker_dir / wheel.name
    if wheel.resolve() != copied_wheel.resolve():
        shutil.copy2(wheel, copied_wheel)

    original = runtime_path.read_text(encoding="utf-8")
    patched = patch_runtime(original)
    if patched != original:
        runtime_path.write_text(patched, encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("harness_bench", type=Path, help="Harness-Bench checkout")
    parser.add_argument("--wheel", required=True, type=Path, help="built Machina wheel")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    install(args.harness_bench, args.wheel)


if __name__ == "__main__":
    main()
