# Machina Harness-Bench adapter

This adapter runs Machina's full-mode headless CLI as a Harness-Bench OCI
harness. The controller's per-trial OpenAI-compatible proxy remains the only
model endpoint visible to Machina; the upstream API key is read by the
controller from a key file and is never copied into the image or task
container.

## Requirements

- Docker with a running daemon (the benchmark also builds its task/grader
  images).
- `uv`, Python 3.11+, and a Machina checkout.
- A Harness-Bench checkout with its task assets.
- A DeepSeek API key for `deepseek-flash`, stored in a one-line file. Do not
  put the key on the command line or in benchmark config.

The image uses `pgvector/pgvector:pg16` and assumes that image supplies
PostgreSQL 16 server/client tools and the `vector` extension. The Dockerfile
explicitly installs `bash`, `ca-certificates`, `curl`, `git`, `passwd`,
`python3`, `python3-pip`, and `ripgrep`. The image's PostgreSQL data directory
is created under the trial's `/state` and runs as UID/GID 1000.
If substituting a PostgreSQL base image, install its equivalents of
`postgresql-16`, `postgresql-client-16`, and `postgresql-16-pgvector` (from a
repository that provides PG16 and pgvector).

## One-task smoke

From the Machina checkout, replace the two checkout/key paths with your local
paths. This smoke evaluates task `001-meeting-summary`:

```bash
python integrations/harness-bench/run.py \
  --harness-bench /path/to/harness-bench \
  --tasks 001-meeting-summary \
  --workers 1 \
  --upstream-api-key-file "$HOME/.config/machina/deepseek-api-key" \
  --run-root artifacts/harness-bench-smoke
```

The default model route is `deepseek-flash` at
`https://api.deepseek.com/v1`. Override it with `--model` and `--base-url` for
another OpenAI-compatible deployment.

## Full 274-task run

Omit `--tasks` to use Harness-Bench's active 274-task general release:

```bash
python integrations/harness-bench/run.py \
  --harness-bench /path/to/harness-bench \
  --workers 4 \
  --upstream-api-key-file "$HOME/.config/machina/deepseek-api-key" \
  --run-root artifacts/harness-bench-deepseek-flash-274
```

This can take many hours and incur substantial model-provider charges. Check
the model's current pricing and set an appropriate worker count before starting.
Benchmark-specific options not listed above (for example `--timeout` or
`--context-window`) can be passed through to `tools/run_benchmark.py`.

## Proxy and credentials

Harness-Bench creates an isolated proxy for every trial and writes its
OpenAI-compatible endpoint into the generated `config.json`. The adapter reads
that `base_url` and supplies only the fixed local placeholder key
`local-vllm-no-auth`; it never receives the upstream credential. The
`--upstream-api-key-file` option is passed only to the trusted benchmark
controller, which uses it for upstream requests and does not expose it in
provider traces. The file must contain exactly one non-empty line.

The run script builds a local Machina wheel, installs this adapter into the
specified Harness-Bench checkout, builds the harness image, and invokes the
benchmark. It removes the copied wheel from the checkout after the build; no
wheel or API key is committed by this adapter.
