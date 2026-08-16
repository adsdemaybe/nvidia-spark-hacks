#!/usr/bin/env bash
# get_model.sh — download the ONE model this stack needs: Nemotron-3.5-Lightning.
#
# Nothing else. The two-tier setup (Coder-Next + Nemotron-Omni) is gone; a newcomer
# pulls exactly this ~21 GB NVFP4 checkpoint and serves it on one endpoint (serve_llm.sh).
#
#   ./get_model.sh          download if not already present
#   FORCE=1 ./get_model.sh  re-download even if present
#
# Env: MODEL_ID  LOCAL_DIR  HF_TOKEN (only if the repo is gated)
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"

MODEL_ID="${MODEL_ID:-nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4}"
LOCAL_DIR="${LOCAL_DIR:-$HOME/models/Nemotron-3.5-Lightning-30B-A3B-NVFP4}"

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }

if [ "${FORCE:-0}" != 1 ] && [ -f "$LOCAL_DIR/config.json" ]; then
  say "already present: $LOCAL_DIR (FORCE=1 to re-download)"; exit 0
fi

DL="hf"; command -v hf >/dev/null || DL="huggingface-cli"
command -v "$DL" >/dev/null || { echo "no hf CLI — run setup/setup_spark.sh (installs uv + hf)" >&2; exit 1; }

say "downloading $MODEL_ID -> $LOCAL_DIR (~21 GB, several minutes)"
mkdir -p "$LOCAL_DIR"
"$DL" download "$MODEL_ID" --local-dir "$LOCAL_DIR" ${HF_TOKEN:+--token "$HF_TOKEN"}
say "done: $(du -sh "$LOCAL_DIR" | cut -f1) in $LOCAL_DIR"
