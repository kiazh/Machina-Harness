"""Harbor adapter for running Machina inside a Terminal-Bench task container.

Harbor imports this as a custom agent module:

    harbor run ... --agent /path/to/machina_agent.py:Machina

The adapter installs a locally built Machina wheel, starts a task-local
PostgreSQL instance, and runs Machina's headless full-mode CLI against /app.
It intentionally keeps the model route in Harbor's normal credential contract;
use ``DEEPSEEK_API_KEY`` and ``deepseek/deepseek-chat`` for the current test
track.
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path

from harbor.agents.agent_auth_resolution import (
    ModelConnectionSpec,
    ProviderAccess,
)
from harbor.agents.installed.base import BaseInstalledAgent, with_prompt_template
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.model_api_format import ModelApiFormat


class Machina(BaseInstalledAgent):
    """Install Machina into a task container and execute its repair loop."""

    DEFAULT_SETUP_TIMEOUT_SEC = 1200.0

    MODEL_CONNECTION = ModelConnectionSpec(
        agent_provider_access=ProviderAccess(
            api_key_candidate_names=["DEEPSEEK_API_KEY"],
            base_url_candidate_names=["DEEPSEEK_BASE_URL"],
        ),
        supported_api_formats=[ModelApiFormat.OPENAI_CHAT_COMPLETIONS],
        default_api_format_by_provider={
            "deepseek": ModelApiFormat.OPENAI_CHAT_COMPLETIONS,
        },
        base_url_destination_names={
            "deepseek": "DEEPSEEK_BASE_URL",
            "default": "OPENAI_BASE_URL",
        },
    )

    @staticmethod
    def name() -> str:
        return "machina"

    def version(self) -> str | None:
        return self._version

    @staticmethod
    def _wheel_path() -> Path:
        explicit = os.environ.get("MACHINA_HARBOR_WHEEL")
        if explicit:
            path = Path(explicit)
            if path.is_file():
                return path
        candidates = sorted(
            (Path(__file__).resolve().parents[2] / "dist").glob("machina-*.whl"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            raise RuntimeError(
                "No Machina wheel found. Build one with `uv build --wheel` "
                "or set MACHINA_HARBOR_WHEEL."
            )
        return candidates[0]

    async def install(self, environment: BaseEnvironment) -> None:
        await self.exec_as_root(
            environment,
            command=(
                "apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y "
                "python3 python3-pip postgresql postgresql-contrib "
                "postgresql-16-pgvector"
            ),
            timeout_sec=900,
        )
        wheel = self._wheel_path()
        remote_wheel = f"/tmp/{wheel.name}"
        await environment.upload_file(wheel, remote_wheel)
        await self.exec_as_root(
            environment,
            command=(
                "python3 -m pip install --break-system-packages --no-cache-dir "
                f"{shlex.quote(remote_wheel)}"
            ),
            timeout_sec=900,
        )
        await self.exec_as_root(
            environment,
            command=(
                "set -e; "
                "service postgresql start || pg_ctlcluster 16 main start; "
                "su postgres -c \"psql -v ON_ERROR_STOP=1 -c "
                "\\\"ALTER USER postgres PASSWORD 'postgres';\\\"\"; "
                "su postgres -c 'createdb machina' 2>/dev/null || true"
            ),
            timeout_sec=300,
        )
        await self.exec_as_root(
            environment,
            command=(
                "python3 -m machina init --db-url "
                "postgresql://postgres:postgres@127.0.0.1:5432/machina"
            ),
            env={
                "MACHINA_HOME": "/tmp/machina-home",
                "DATABASE_URL": "postgresql://postgres:postgres@127.0.0.1:5432/machina",
            },
            timeout_sec=300,
        )

    @with_prompt_template
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        if not self.model_name:
            raise ValueError("Machina requires --model provider/model")
        model = self.model_name.partition("/")[2] or self.model_name
        connection = self.model_connection
        env: dict[str, str] = {
            **connection.env,
            "DATABASE_URL": "postgresql://postgres:postgres@127.0.0.1:5432/machina",
            "MACHINA_HOME": "/tmp/machina-home",
            "MACHINA_WORKSPACE_ROOT": "/app",
            "MACHINA_ENV_FILE": "/dev/null",
            "MACHINA_PROVIDER": "deepseek",
            "MACHINA_MODEL": model,
            "MACHINA_TERMINAL_SHELL": "posix",
        }
        command = (
            "mkdir -p /logs/agent /tmp/machina-home && "
            f"cd /app && python3 -m machina chat --provider deepseek --model "
            f"{shlex.quote(model)} --mode full --quiet {shlex.quote(instruction)} "
            "2>&1 | tee /logs/agent/machina.txt || true"
        )
        await self.exec_as_root(environment, command=command, env=env)

    def populate_context_post_run(self, context: AgentContext) -> None:
        """Harbor reads token accounting from the agent output when available."""
        return None
