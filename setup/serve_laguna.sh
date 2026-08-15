#!/usr/bin/env bash
# STRUCT — serve Laguna S 2.1 NVFP4 on the Spark via vLLM (OpenAI-compatible :8000)
# GPU memory is capped so Isaac/gsplat/SmolVLA can coexist during build hours.
# During overnight training windows (H+28+): stop this, flip agents to Claude.
set -euo pipefail
source "${STRUCT_HOME:-$HOME/struct}/.venv/bin/activate"

exec vllm serve poolside/Laguna-S-2.1-NVFP4 \
  --host 0.0.0.0 --port 8000 \
  --gpu-memory-utilization "${LAGUNA_GPU_FRAC:-0.60}" \
  --max-model-len "${LAGUNA_CTX:-131072}" \
  --enable-auto-tool-choice --tool-call-parser hermes \
  --served-model-name laguna
# Smoke test (from any teammate machine):
#   curl http://spark:8000/v1/chat/completions -H 'Content-Type: application/json' \
#     -d '{"model":"laguna","messages":[{"role":"user","content":"return {\"ok\":true} as JSON"}],"response_format":{"type":"json_object"}}'
# If pip vLLM misbehaves on aarch64, use NVIDIA's DGX Spark vLLM container playbook
# (dgx-spark-playbooks) and keep the same port so agent config doesn't change.
