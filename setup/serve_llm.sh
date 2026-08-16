#!/usr/bin/env bash
# serve_llm.sh — the ONE global LLM for STRUCT, on one endpoint.
#
# Quick-start decision: instead of the two-tier split (serve_coder_next.sh on :8100
# as designer + serve_nemotron.sh on :8101 as reviewer), the whole stack points at a
# single model on a single port:
#
#     Nemotron-3.5-Lightning-30B-A3B-NVFP4  ->  container "llm"  ->  :8100
#
# 30B total / 3B active, NVFP4 — same bandwidth-bound reasoning as Coder-Next (only the
# routed experts are read per token, see README "Models"), so it decodes fast AND is the
# stronger general model. One endpoint means nothing has to know which port is which.
#
# It answers to the canonical id "nemotron-lightning" AND to every served-model-name the
# old scripts used, so every existing caller keeps resolving with zero code change:
#   qwen3-coder-next, qwen3.8-27b  (the design loop / engine.agent_loop, OpenShell)
#   nemotron-omni                  (the layout reviewer)
#   laguna-nvfp4                   (laguna.sh clients)
# Tradeoff worth stating: nemotron-omni WAS the multimodal vision reviewer; Lightning is
# text-only, so rendered board views fall back to the text geometry digest (contentOf's
# existing text-only path). Bring serve_nemotron.sh back on :8101 if you need vision.
#
# House style matches serve_coder_next.sh: --network host, --restart no, digest-pinned
# image, /models mounted read-only. Idempotent — re-run to recreate.
#
# Env: MODEL_DIR NAME PORT CONTAINER UTIL MAXLEN IMAGE
set -euo pipefail

MODEL_DIR="${MODEL_DIR:-/models/Nemotron-3.5-Lightning-30B-A3B-NVFP4}"
NAME="${NAME:-nemotron-lightning}"
PORT="${PORT:-8100}"
CONTAINER="${CONTAINER:-llm}"
UTIL="${UTIL:-0.60}"                       # single model, but shares the box with CAD + Isaac
MAXLEN="${MAXLEN:-32768}"                  # native 1M; pipeline's longest prompt is < 32k
PARSER="${PARSER:-hermes}"                 # nemotron family uses hermes-style tool tags
# Pinned by digest — the same image already serving on this machine, so a swap changes
# the model, not the runtime.
IMAGE="${IMAGE:-nvcr.io/nvidia/vllm@sha256:9204569b17ee4c0eff75194b8e6e458479c8aee18953b5ab9cf359fcdac659e2}"

[ -e "/home/acer01/models/${MODEL_DIR#/models/}" ] || \
  echo "note: check $MODEL_DIR exists under ~/models" >&2

# Only one model fits in 121 GB at a time — clear the per-app zoo this replaces.
for c in coder-next-vllm nemotron-vllm qwen-vllm laguna-vllm nemoclaw-vllm "$CONTAINER"; do
  docker rm -f "$c" 2>/dev/null && echo "removed $c" || true
done

docker run -d --name "$CONTAINER" \
  --network host \
  --gpus all \
  --restart no \
  -v /home/acer01/models:/models:ro \
  -v /home/acer01/.cache/huggingface:/root/.cache/huggingface \
  "$IMAGE" \
  vllm serve "$MODEL_DIR" \
    --served-model-name "$NAME" qwen3-coder-next qwen3.8-27b nemotron-omni laguna-nvfp4 \
    --enable-auto-tool-choice \
    --tool-call-parser "$PARSER" \
    --port "$PORT" \
    --max-model-len "$MAXLEN" \
    --gpu-memory-utilization "$UTIL" \
    --trust-remote-code

echo "started $CONTAINER ($NAME on :$PORT); waiting for /v1/models (NVFP4 load takes a few min)"
for _ in $(seq 1 180); do
  if curl -sf -m 3 "http://127.0.0.1:$PORT/v1/models" >/dev/null 2>&1; then
    echo "ready:"; curl -s "http://127.0.0.1:$PORT/v1/models" | head -c 500; echo
    exit 0
  fi
  if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
    echo "container exited during boot; last 40 log lines:" >&2
    docker logs --tail 40 "$CONTAINER" >&2; exit 1
  fi
  sleep 5
done
echo "did not come up within 15 minutes; last 40 log lines:" >&2
docker logs --tail 40 "$CONTAINER" >&2
exit 1
