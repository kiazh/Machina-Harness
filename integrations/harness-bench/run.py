#!/usr/bin/env python3
"""Build Machina and run it as a Harness-Bench harness."""

from __future__ import annotations

import argparse
import ipaddress
import os
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-flash"
IMAGE = "harnessbench/machina:local"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build the Machina Harness-Bench image and run an evaluation."
    )
    parser.add_argument(
        "--harness-bench",
        type=Path,
        default=Path(os.environ["HARNESS_BENCH_CHECKOUT"])
        if os.environ.get("HARNESS_BENCH_CHECKOUT")
        else None,
        required=not bool(os.environ.get("HARNESS_BENCH_CHECKOUT")),
        help="Harness-Bench checkout (or set HARNESS_BENCH_CHECKOUT)",
    )
    parser.add_argument("--tasks", help="comma-separated task subset")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--model-service-id")
    parser.add_argument("--upstream-api-key-file", type=Path)
    parser.add_argument("--run-root", type=Path)
    return parser


def run(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> None:
    subprocess.run(command, cwd=cwd, check=True, env=env)


def container_host_gateway() -> str | None:
    """Address task containers use to reach this machine's model proxy.

    Docker's ``host-gateway`` means the daemon's host. Under Lima the daemon
    runs in a VM, so that address is the VM and the proxy on macOS is
    unreachable. Lima publishes the real host as ``host.lima.internal``.
    """
    override = os.environ.get("HARNESSBENCH_HOST_GATEWAY", "").strip()
    if override:
        return override
    try:
        context = subprocess.run(
            ["docker", "context", "show"],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    if not context.startswith("lima-"):
        return None
    try:
        hosts = subprocess.run(
            ["limactl", "shell", context[len("lima-") :], "--", "getent", "hosts", "host.lima.internal"],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        ).stdout
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    for token in hosts.split():
        try:
            ipaddress.ip_address(token)
        except ValueError:
            continue
        return token
    return None


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args, passthrough = parser.parse_known_args(argv)
    if args.workers < 1:
        parser.error("--workers must be positive")
    if passthrough and passthrough[0] == "--":
        passthrough = passthrough[1:]

    checkout = args.harness_bench.expanduser().resolve()
    if not (checkout / "tools" / "run_benchmark.py").is_file():
        parser.error(f"not a Harness-Bench checkout: {checkout}")
    repo_root = Path(__file__).resolve().parents[2]
    adapter_dir = Path(__file__).resolve().parent

    with tempfile.TemporaryDirectory(prefix="machina-harness-bench-") as temp_dir:
        wheel_dir = Path(temp_dir)
        run(
            ["uv", "build", "--wheel", "--out-dir", str(wheel_dir)],
            cwd=repo_root,
        )
        wheels = sorted(wheel_dir.glob("machina-*.whl"))
        if len(wheels) != 1:
            raise RuntimeError(f"expected one Machina wheel, found {len(wheels)}")
        wheel_in_context = checkout / "docker" / "harnesses" / "machina" / wheels[0].name
        try:
            run(
                [
                    sys.executable,
                    str(adapter_dir / "install.py"),
                    str(checkout),
                    "--wheel",
                    str(wheels[0]),
                ],
                cwd=repo_root,
            )
            run(
                [
                    "docker",
                    "build",
                    "--pull=false",
                    "--tag",
                    IMAGE,
                    "--file",
                    "docker/harnesses/machina/Dockerfile",
                    ".",
                ],
                cwd=checkout,
            )

            # Harness-Bench's controller imports PyYAML and its own task/runtime
            # dependencies. Keep that environment provisioned by its lockfile.
            run(["uv", "sync", "--project", str(checkout), "--extra", "dev"])

            benchmark = [
                "uv",
                "run",
                "--project",
                str(checkout),
                "--no-sync",
                "python",
                str(checkout / "tools" / "run_benchmark.py"),
                "--base-url",
                args.base_url,
                "--model",
                args.model,
                "--harness",
                "machina",
                "--harness-image",
                IMAGE,
                "--workers",
                str(args.workers),
            ]
            if args.tasks:
                benchmark.extend(["--tasks", args.tasks])
            if args.model_service_id:
                benchmark.extend(["--model-service-id", args.model_service_id])
            if args.upstream_api_key_file:
                benchmark.extend(
                    [
                        "--upstream-api-key-file",
                        str(args.upstream_api_key_file.expanduser().resolve()),
                    ]
                )
            if args.run_root:
                benchmark.extend(["--run-root", str(args.run_root.expanduser().resolve())])
            benchmark.extend(passthrough)
            benchmark_env = os.environ.copy()
            gateway = container_host_gateway()
            if gateway:
                benchmark_env["HARNESSBENCH_HOST_GATEWAY"] = gateway
            run(benchmark, cwd=checkout, env=benchmark_env)
        finally:
            wheel_in_context.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
