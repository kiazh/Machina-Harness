# Harbor adapter

`machina_agent.py` exposes Machina as a Harbor installed agent.

## What it does

1. Installs Python, pip, PostgreSQL, and pgvector in the task container.
2. Uploads and installs a locally built Machina wheel.
3. Starts a task-local PostgreSQL instance and runs `machina init`.
4. Runs Machina's headless full-mode CLI in `/app`.
5. Leaves the agent log at `/logs/agent/machina.txt`, writes durable token/cost
   accounting to `/logs/agent/machina-usage.json`, and always lets Harbor run
   the task verifier, even when Machina exits nonzero.
6. Copies prompt, cache, completion, and cost totals into Harbor's
   `AgentContext` for benchmark comparison.

## Build and run

From the Machina repository:

```bash
uv build --wheel
export PYTHONPATH="$PWD/integrations/harbor"
export MACHINA_HARBOR_WHEEL="$PWD/dist/machina-1.2.14-py3-none-any.whl"
export DEEPSEEK_API_KEY=...

harbor run -p /path/to/terminal-bench-task \
  --agent machina_agent:Machina \
  --model deepseek/deepseek-chat \
  --api-key-name DEEPSEEK_API_KEY \
  --n-concurrent 1 --yes
```

The adapter hardcodes the task workdir to `/app`, matching Terminal-Bench 2.0
tasks. It uses a task-local PostgreSQL database at
`postgresql://postgres:postgres@127.0.0.1:5432/machina`; pgvector is required by
Machina's schema.

## Verified trial

On 2026-11-10 the adapter completed a real `data-anonymization` trial:

- Harbor trials: 1
- Exceptions: 0
- Reward: 0.0
- Runtime: 6m 35s

This verifies the integration path: wheel installation, pgvector schema
initialization, PostgreSQL startup, Machina execution, and verifier handoff.
It does not claim that Machina solved the task.

## Limits

- Installing PostgreSQL and pgvector per trial is slower than using a prebuilt
  agent image. A production Harbor integration should publish a Machina agent
  image or use a database sidecar.
- The adapter currently assumes Debian/Ubuntu task images and `/app`.
- Model credentials are supplied through Harbor's normal environment contract.
