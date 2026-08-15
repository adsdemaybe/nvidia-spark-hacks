#!/usr/bin/env bash
# Serve Qwen3-Coder-Next (NVFP4) on the Spark.
#
# Why this model, on this box, in one line: decode speed here is set by memory
# bandwidth, not arithmetic. Measured on the GB10 while generating, the GPU sat at 96%
# "utilization" but was doing 0.6% of its bf16 math — it was waiting on memory the whole
# time. So the number that matters is *bytes of weights read per token*:
#
#   Qwen3.8-27B, dense bf16   ~45 GB/token   ->  3.8 tok/s   (measured)
#   Qwen3-Coder-Next, NVFP4    ~2 GB/token   ->  see below
#
# 80B total parameters but only 3B active (512 experts, 10 routed + 1 shared), quantised
# to 4-bit. A token reads the router's chosen slice, not the whole model. That is the
# entire reason this is worth swapping to: it is a *larger* model that is roughly an order
# of magnitude faster here, because sparsity and bandwidth-bound decode multiply.
#
# NVFP4 rather than AWQ/GPTQ because FP4 is native to Blackwell, and this box already
# proved the path with Laguna.
#
# --gpu-memory-utilization 0.75 matches the qwen3.8-27b container it replaces, and leaves
# room for the CAD work and Isaac Sim that share this machine. The upstream GB10-specific
# quant warns that 0.93 "leaves very little system headroom" — on a shared box, don't.
#
# --max-model-len 32768 rather than the model's native 262144: the KV cache is what the
# remaining memory buys, and the pipeline's longest prompt is well under 32k (plan §8.8).
#
# It answers to `qwen3.8-27b` as well as its own name, and that alias is deliberate but
# worth knowing about. Only one model fits in 121 GB at a time, so this container replaces
# the one that was serving that name on this port; without the alias every caller still
# asking for `qwen3.8-27b` — including the CAD session sharing this box — would start
# getting 404s the moment the swap happened. The cost is that a request for the old name
# is now answered by a different model, so if something behaves oddly after the swap,
# check which name it asked for before blaming the model.
set -euo pipefail

MODEL_DIR="${MODEL_DIR:-/models/Qwen3-Coder-Next-NVFP4}"
NAME="${NAME:-qwen3-coder-next}"
PORT="${PORT:-8100}"
CONTAINER="${CONTAINER:-coder-next-vllm}"
UTIL="${UTIL:-0.75}"
MAXLEN="${MAXLEN:-32768}"

# Pinned by digest, the same image already serving on this machine — so a swap is a
# change of model, not an untested change of runtime as well.
IMAGE="${IMAGE:-nvcr.io/nvidia/vllm@sha256:9204569b17ee4c0eff75194b8e6e458479c8aee18953b5ab9cf359fcdac659e2}"

docker rm -f "$CONTAINER" 2>/dev/null || true

docker run -d --name "$CONTAINER" \
  --network host \
  --gpus all \
  --restart no \
  -v /home/acer01/models:/models:ro \
  -v /home/acer01/.cache/huggingface:/root/.cache/huggingface \
  "$IMAGE" \
  vllm serve "$MODEL_DIR" \
    --served-model-name "$NAME" qwen3.8-27b \
    --port "$PORT" \
    --max-model-len "$MAXLEN" \
    --gpu-memory-utilization "$UTIL" \
    --trust-remote-code

echo "started $CONTAINER; waiting for /v1/models on :$PORT"
for _ in $(seq 1 180); do
  if curl -sf -m 3 "http://127.0.0.1:$PORT/v1/models" >/dev/null 2>&1; then
    echo "ready:"
    curl -s "http://127.0.0.1:$PORT/v1/models" | head -c 400
    echo
    exit 0
  fi
  sleep 5
done

echo "did not come up within 15 minutes; last 40 log lines:" >&2
docker logs --tail 40 "$CONTAINER" >&2
exit 1
