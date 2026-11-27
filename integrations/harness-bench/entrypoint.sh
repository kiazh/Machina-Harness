#!/usr/bin/env bash
set -euo pipefail

: "${HARNESSBENCH_PROMPT:?}"
: "${HARNESSBENCH_CONFIG:?}"
: "${HARNESSBENCH_WORKSPACE:?}"
: "${HARNESSBENCH_STATE:?}"
: "${HARNESSBENCH_TRACE:?}"

WORKSPACE="${HARNESSBENCH_WORKSPACE}"
STATE="${HARNESSBENCH_STATE}"
TRACE="${HARNESSBENCH_TRACE}"
PGDATA="${STATE}/postgres"

export HOME="${STATE}/home"
export MACHINA_HOME="${STATE}/machina"
export DATABASE_URL="postgresql://postgres:postgres@127.0.0.1:5432/machina"
export MACHINA_ENV_FILE="/dev/null"
export MACHINA_WORKSPACE_ROOT="${WORKSPACE}"

mkdir -p "${HOME}" "${MACHINA_HOME}" "${PGDATA}" "${TRACE}"
mkdir -p "${STATE}/pgsocket"

finalize() {
  status=$?
  trap - EXIT
  if [[ -d "${PGDATA}" ]]; then
    pg_ctl -D "${PGDATA}" -m fast -w stop >/dev/null 2>&1 || true
  fi
  chmod -R a+rX "${WORKSPACE}" "${TRACE}" 2>/dev/null || true
  exit "${status}"
}
trap finalize EXIT

config_value() {
  python3 -c '
import json
import sys

with open(sys.argv[1], encoding="utf-8") as config_file:
    config = json.load(config_file)
value = config[sys.argv[2]]
if isinstance(value, bool) or not isinstance(value, (str, int, float)):
    raise SystemExit(f"invalid {sys.argv[2]} in harness config")
print(value)
' "${HARNESSBENCH_CONFIG}" "$1"
}

export CUSTOM_BASE_URL
CUSTOM_BASE_URL="$(config_value base_url)"
export CUSTOM_API_KEY="local-vllm-no-auth"
export MACHINA_MODEL
MACHINA_MODEL="$(config_value model)"
export MACHINA_MAX_TOKENS
MACHINA_MAX_TOKENS="$(config_value max_tokens)"
export MACHINA_TEMPERATURE
MACHINA_TEMPERATURE="$(config_value temperature)"
# The harness grants ``max_agent_turns``; align Machina's repair guard so a
# legitimate long task is not stopped at the interactive default (60 turns).
export MACHINA_TASK_RUN_MAX_TURNS
MACHINA_TASK_RUN_MAX_TURNS="$(config_value max_agent_turns)"
export MACHINA_TASK_RUN_MAX_TOOL_CALLS
MACHINA_TASK_RUN_MAX_TOOL_CALLS="$((MACHINA_TASK_RUN_MAX_TURNS * 2))"
export MACHINA_CONTEXT_BUDGET
MACHINA_CONTEXT_BUDGET="$(
  python3 -c '
import json
import sys

with open(sys.argv[1], encoding="utf-8") as config_file:
    context_window = int(json.load(config_file)["context_window"])
if context_window < 1:
    raise SystemExit("context_window must be positive")
print(min(context_window, 120000))
' "${HARNESSBENCH_CONFIG}"
)"

prompt="$(<"${HARNESSBENCH_PROMPT}")"

initdb -D "${PGDATA}" --username=postgres --auth-local=trust \
  --auth-host=scram-sha-256 >"${TRACE}/stdout.log" 2>"${TRACE}/stderr.log"
pg_ctl -D "${PGDATA}" -l "${STATE}/postgres.log" \
  -o "-h 127.0.0.1 -p 5432 -k ${STATE}/pgsocket" -w start \
  >>"${TRACE}/stdout.log" 2>>"${TRACE}/stderr.log"
psql -h "${STATE}/pgsocket" -U postgres -d postgres -v ON_ERROR_STOP=1 \
  -c "ALTER USER postgres WITH PASSWORD 'postgres';" \
  >>"${TRACE}/stdout.log" 2>>"${TRACE}/stderr.log"
PGPASSWORD=postgres createdb -h 127.0.0.1 -U postgres machina \
  >>"${TRACE}/stdout.log" 2>>"${TRACE}/stderr.log"
PGPASSWORD=postgres psql -h 127.0.0.1 -U postgres -d machina \
  -v ON_ERROR_STOP=1 -c "CREATE EXTENSION IF NOT EXISTS vector;" \
  >>"${TRACE}/stdout.log" 2>>"${TRACE}/stderr.log"

machina init --db-url "${DATABASE_URL}" \
  >>"${TRACE}/stdout.log" 2>>"${TRACE}/stderr.log"

set +e
machina chat --provider custom --model "${MACHINA_MODEL}" --mode full --quiet \
  "${prompt}" >>"${TRACE}/stdout.log" 2>>"${TRACE}/stderr.log"
chat_status=$?
set -e

machina usage --json >"${TRACE}/usage.json" 2>>"${TRACE}/stderr.log" || true
exit "${chat_status}"
