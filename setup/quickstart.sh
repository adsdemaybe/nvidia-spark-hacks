#!/usr/bin/env bash
# quickstart.sh — bring up the whole STRUCT stack in one command, in order.
#
#   1. get_model.sh      download Nemotron-3.5-Lightning (only if missing)
#   2. serve_llm.sh      the one global LLM (nemotron-lightning) on :8100
#   3. setup_runtime.sh  CAD API :8210 + docs RAG :8220 (infra probed)
#   4. setup_frontend.sh the console on :8600
#
# Assumes the box is already provisioned (setup/setup_spark.sh has run once: apt,
# docker, venvs, node, infra compose). Each step is idempotent.
#
#   ./quickstart.sh            full stack (downloads the model if absent)
#   ./quickstart.sh --no-llm   runtime + frontend only (LLM already serving)
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
say() { printf '\n\033[1;35m### %s\033[0m\n' "$*"; }

if [ "${1:-}" != "--no-llm" ]; then
  say "1/4  model"
  bash "$HERE/get_model.sh" || { echo "model download failed" >&2; exit 1; }
  say "2/4  LLM"
  bash "$HERE/serve_llm.sh" || { echo "LLM failed — see above" >&2; exit 1; }
fi
say "3/4  runtime"
bash "$HERE/setup_runtime.sh" || { echo "runtime failed" >&2; exit 1; }
say "4/4  frontend"
bash "$HERE/setup_frontend.sh" || { echo "frontend failed" >&2; exit 1; }

say "stack up — open the console printed above (:8600)"
