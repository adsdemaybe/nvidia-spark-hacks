#!/usr/bin/env bash
# Serve Nemotron-3-Nano-Omni (NVFP4) as the *reviewer* tier, beside Coder-Next.
#
# Two models, two ports, one box. The split is not about capacity — it is about what
# each tier is for, and there are three reasons this particular model earns the second
# slot:
#
# 1. **It can see.** The pipeline renders assembly.png, pcb.png and schematic.png for
#    every board and has never once shown them to a local model. `contentOf` currently
#    tells every local reviewer: "You are a text-only model, so the rendered views were
#    not attached. Do not describe them or claim to have seen them", and substitutes a
#    textual geometry digest. That substitution is correct — a text-only model handed an
#    image answers confidently about something it never received — but it is a
#    workaround, not the design. Omni is multimodal, so the layout reviewer can look at
#    the board it is reviewing.
#
# 2. **It is not the model that wrote the HDL.** Principle 4 of this project is two
#    independent implementations for anything load-bearing, on the grounds that
#    agreement between two derivations of the same mistake is worth nothing. A model
#    reviewing its own output is exactly that failure at the model level: it does not
#    see its own blind spots, because they are the same blind spots. Different family
#    for review than for generation is the model-level form of the same rule.
#
# 3. **The tradeoffs differ by role.** The designer wants throughput — it emits whole
#    files. A reviewer emits a few hundred tokens and wants judgement, so a reasoning
#    model is worth its slower tokens there and not in the designer.
#
# 30B total, 3B active, NVFP4 — so it costs ~22 GB of weights and decodes fast for the
# same reason Coder-Next does: on this box only the active experts are read per token.
#
# Utilisation is deliberately small. Coder-Next is the throughput tier and keeps the
# larger share; this one answers short prompts and does not need a big KV cache. The two
# together must still leave room for Isaac Sim and the CAD service on a shared machine.
set -euo pipefail

MODEL_DIR="${MODEL_DIR:-/models/Nemotron-3-Nano-Omni-NVFP4}"
NAME="${NAME:-nemotron-omni}"
PORT="${PORT:-8101}"
CONTAINER="${CONTAINER:-nemotron-vllm}"
UTIL="${UTIL:-0.18}"
MAXLEN="${MAXLEN:-16384}"
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
    --served-model-name "$NAME" \
    --port "$PORT" \
    --max-model-len "$MAXLEN" \
    --gpu-memory-utilization "$UTIL" \
    --trust-remote-code

echo "started $CONTAINER; waiting for /v1/models on :$PORT"
for _ in $(seq 1 180); do
  if curl -sf -m 3 "http://127.0.0.1:$PORT/v1/models" >/dev/null 2>&1; then
    echo "ready:"; curl -s "http://127.0.0.1:$PORT/v1/models" | head -c 300; echo
    exit 0
  fi
  sleep 5
done

echo "did not come up within 15 minutes; last 40 log lines:" >&2
docker logs --tail 40 "$CONTAINER" >&2
exit 1
