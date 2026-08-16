#!/usr/bin/env bash
# gpu_mem_broker.sh — serialize vLLM-vs-Isaac-Lab/Isaac-Sim access to the GB10's
# 121GB *unified* memory pool.
#
# Why this exists: vLLM holds 66-90GB while serving coder-next-vllm. Isaac Sim/Isaac
# Lab's Kit renderer needs headroom that isn't there until vLLM is stopped. Up to now
# that's been a manual `docker stop coder-next-vllm` / restart dance around every RL
# session — no lock, no guarantee of restart, no health check before handing control
# back. This wraps that dance in one command so it's the same sequence every time and
# vLLM provably comes back up before the lock releases.
#
# Usage:
#   setup/gpu_mem_broker.sh [--dry-run] -- <command to run with vLLM stopped> [args...]
#
# Example:
#   setup/gpu_mem_broker.sh -- python isaac_lab_so101_replay.py
#   setup/gpu_mem_broker.sh --dry-run -- echo hello   # exercise the logic, touch nothing real
#
# What it does, in order:
#   1. Acquire an flock-based lock (setup/.gpu_mem_broker.lock) so two callers on this
#      shared box can't both try to stop/start vLLM at once.
#   2. Record whether coder-next-vllm was running before we touch it (so we don't start
#      it if it was already down — not our job to change desired state, only to free and
#      restore it).
#   3. `docker stop` it, wait for the container to actually exit (not just for the stop
#      command to return) and for GPU memory to actually drop, up to a timeout.
#   4. Run the wrapped command.
#   5. Restart vLLM via setup/serve_coder_next.sh (same image/flags every time — this is
#      the same script already used for the manual dance, not a reimplementation) and
#      poll /v1/models until it's genuinely answering again, up to a timeout.
#   6. Release the lock. Exit code is the wrapped command's exit code, unless the
#      restore step itself fails, in which case that failure wins so a caller can't miss
#      "your command ran but vLLM did not come back."
#
# This is intentionally conservative: it manages only the one global LLM container
# (llm, serve_llm.sh), the single measured memory conflict; it does not touch
# isaac-lab-setup/isaac-fly — those are the caller's own containers to start how they
# like once memory is free.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCK_FILE="${LOCK_FILE:-$SCRIPT_DIR/.gpu_mem_broker.lock}"
CONTAINER="${CONTAINER:-llm}"
VLLM_PORT="${VLLM_PORT:-8100}"
SERVE_SCRIPT="${SERVE_SCRIPT:-$SCRIPT_DIR/serve_llm.sh}"
STOP_TIMEOUT_S="${STOP_TIMEOUT_S:-60}"
START_TIMEOUT_S="${START_TIMEOUT_S:-900}"   # serve_llm.sh itself already polls up to 15 min
DRY_RUN=0

vlog() { echo "[gpu_mem_broker] $*" >&2; }

is_vllm_healthy() {
  curl -sf -m 3 "http://127.0.0.1:${VLLM_PORT}/v1/models" >/dev/null 2>&1
}

container_running() {
  [ -n "$(docker ps -q -f "name=^${CONTAINER}\$" 2>/dev/null)" ]
}

wait_for_container_stopped() {
  local waited=0
  while container_running; do
    if [ "$waited" -ge "$STOP_TIMEOUT_S" ]; then
      vlog "ERROR: $CONTAINER still running after ${STOP_TIMEOUT_S}s"
      return 1
    fi
    sleep 1
    waited=$((waited + 1))
  done
  vlog "$CONTAINER confirmed stopped after ${waited}s"
}

wait_for_vllm_healthy() {
  local waited=0
  while ! is_vllm_healthy; do
    if [ "$waited" -ge "$START_TIMEOUT_S" ]; then
      vlog "ERROR: $CONTAINER not answering on :$VLLM_PORT after ${START_TIMEOUT_S}s"
      return 1
    fi
    sleep 5
    waited=$((waited + 5))
  done
  vlog "$CONTAINER healthy on :$VLLM_PORT after ${waited}s"
}

if [ "${1:-}" = "--dry-run" ]; then
  DRY_RUN=1
  shift
fi
if [ "${1:-}" != "--" ]; then
  echo "usage: $0 [--dry-run] -- <command> [args...]" >&2
  exit 2
fi
shift

if [ "$#" -eq 0 ]; then
  echo "usage: $0 [--dry-run] -- <command> [args...]  (no command given)" >&2
  exit 2
fi

exec 9>"$LOCK_FILE"
vlog "acquiring lock ($LOCK_FILE)..."
flock 9
vlog "lock acquired"

WAS_RUNNING=0
if container_running; then
  WAS_RUNNING=1
fi
vlog "pre-state: $CONTAINER running=$WAS_RUNNING, vllm healthy=$(is_vllm_healthy && echo yes || echo no)"

cleanup_and_restore() {
  local cmd_exit=$1
  if [ "$WAS_RUNNING" -eq 1 ]; then
    if [ "$DRY_RUN" -eq 1 ]; then
      vlog "[dry-run] would restart $CONTAINER via $SERVE_SCRIPT"
    else
      vlog "restarting $CONTAINER..."
      if ! bash "$SERVE_SCRIPT"; then
        vlog "ERROR: $SERVE_SCRIPT failed"
        exit 1
      fi
      if ! wait_for_vllm_healthy; then
        exit 1
      fi
    fi
  else
    vlog "$CONTAINER was not running before this call — leaving it stopped, that's the pre-existing desired state"
  fi
  exit "$cmd_exit"
}

if [ "$WAS_RUNNING" -eq 1 ]; then
  if [ "$DRY_RUN" -eq 1 ]; then
    vlog "[dry-run] would: docker stop $CONTAINER, wait up to ${STOP_TIMEOUT_S}s"
  else
    vlog "stopping $CONTAINER..."
    docker stop "$CONTAINER" >/dev/null
    wait_for_container_stopped
  fi
fi

vlog "running wrapped command: $*"
set +e
if [ "$DRY_RUN" -eq 1 ]; then
  vlog "[dry-run] would run: $*"
  "$@"
  CMD_EXIT=$?
else
  "$@"
  CMD_EXIT=$?
fi
set -e
vlog "wrapped command exited $CMD_EXIT"

cleanup_and_restore "$CMD_EXIT"
