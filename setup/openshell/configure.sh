#!/usr/bin/env bash
# Point OpenShell/NemoClaw at this box's local vLLM, and check the things that
# actually go wrong first.
#
# What OpenShell is, for this project: a sandboxed, always-on agent runtime with
# snapshots, non-interactive agent turns (`nemoclaw <name> agent`) and a pluggable
# OpenAI-compatible inference endpoint.
#
# What it is not: a training framework. There is no train, tune, dataset or eval
# subcommand in either CLI — checked, not assumed. So "iterate training sessions" here
# means iterating *design and evaluation* sessions: many reproducible runs of the F1/F2
# loop, each starting from an identical sandbox snapshot, each scored by the deterministic
# harness rather than by a model's opinion. That is what `experiments/` drives, and
# OpenShell is where those runs can be isolated and repeated.
#
# It also matters for a capability this repo just gained: `freeform` executes
# model-written build123d. That already runs in a subprocess with a restricted namespace,
# but a sandbox is a strictly better place for it.
set -euo pipefail

GATEWAY="${GATEWAY:-nemoclaw}"
# Our vLLM, not the blueprint's default. The `vllm` profile ships pointing at
# localhost:8000 with an FP8 Nemotron; this box serves Qwen3-Coder-Next on 8100.
LLM_ENDPOINT="${LLM_ENDPOINT:-http://localhost:8100/v1}"
LLM_MODEL="${LLM_MODEL:-qwen3-coder-next}"

say() { printf '  %-34s %s\n' "$1" "$2"; }

echo "preflight"

# 1. Is a model actually being served? Pointing the gateway at a dead endpoint produces
#    an error at agent-run time that reads like an agent bug.
if curl -sf -m 5 "${LLM_ENDPOINT%/v1}/v1/models" >/dev/null 2>&1; then
  # Parsed, not regexed: the response carries a nested `permission[].id` and a greedy
  # match reports that instead of the model, which is confusing in exactly the moment
  # you are checking whether the right model is up.
  served=$(curl -s -m 5 "${LLM_ENDPOINT%/v1}/v1/models" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"][0]["id"])' 2>/dev/null || echo "?")
  say "inference endpoint" "up — serving '${served}'"
else
  say "inference endpoint" "DOWN at ${LLM_ENDPOINT} — start setup/serve_coder_next.sh first"
fi

# 2. The gateway's registered port. This is the failure that cost real time: the gateway
#    is registered on 127.0.0.1:8080 and the CAD viewer (`cad_api.viewer`) binds 8080 too.
#    `openshell status` then reports `tls handshake eof`, which reads like a certificate
#    problem and is really "something else is listening here".
meta="${HOME}/.config/openshell/gateways/${GATEWAY}/metadata.json"
if [[ -f "$meta" ]]; then
  endpoint=$(sed -n 's/.*"gateway_endpoint": *"\([^"]*\)".*/\1/p' "$meta")
  port="${endpoint##*:}"
  say "gateway endpoint" "$endpoint"
  if ss -lntp 2>/dev/null | grep -q ":${port} "; then
    owner=$(ss -lntp 2>/dev/null | grep ":${port} " | grep -oP 'users:\(\("\K[^"]+' | head -1)
    if [[ "$owner" == *openshell* ]]; then
      say "port ${port}" "held by the gateway — good"
    else
      say "port ${port}" "HELD BY '${owner}' — the gateway cannot bind"
      echo
      echo "  The gateway and something else want the same port. Pick one:"
      echo "    a) move the other service   (the CAD viewer takes --port)"
      echo "    b) re-register the gateway on a free port:"
      echo "         openshell gateway remove ${GATEWAY}"
      echo "         openshell gateway add ${GATEWAY} --gateway-endpoint https://127.0.0.1:17670"
      echo "       17670 is the gateway's own default and is free on this box."
      echo
      echo "  Not done automatically: the registration carries mTLS material and the"
      echo "  sandbox's host forwards reference this endpoint, so re-pointing it while a"
      echo "  sandbox is live is a change with blast radius."
    fi
  else
    say "port ${port}" "free — gateway is simply not running"
  fi
else
  say "gateway ${GATEWAY}" "not registered; run 'nemoclaw onboard'"
fi

# 3. Apply the inference configuration.
#
# Needs a reachable gateway, and the gateway runs as a *Docker container* — `--local`
# means "a local mTLS gateway running in Docker on this machine". The certs under
# ~/.config/openshell/gateways/*/mtls are client certs (CN=openshell-client); there is no
# server cert on the host, so `openshell-gateway` cannot usefully be started by hand.
# `nemoclaw onboard --resume` is the supported path and is deliberately not run here: it
# pulls images and can rebuild the sandbox image.
echo
echo "inference"
if ! openshell status >/dev/null 2>&1; then
  say "gateway" "not reachable — run 'nemoclaw onboard --resume' first"
  echo
  echo "  Then the route this box needs is:"
  echo "    nemoclaw inference set --provider compatible-endpoint \\"
  echo "        --model ${LLM_MODEL} \\"
  echo "        --endpoint-url http://host.openshell.internal:${LLM_ENDPOINT##*:} \\"
  echo "        --inference-api openai-completions \\"
  echo "        --credential-env COMPATIBLE_API_KEY --sandbox my-assistant"
  echo
  echo "  Three flag details, each of which costs a cycle to rediscover:"
  echo "    - --endpoint-url is rejected for provider 'vllm-local'; only"
  echo "      'compatible-endpoint' accepts it."
  echo "    - --credential-env must be exactly COMPATIBLE_API_KEY for that provider."
  echo "    - host.openshell.internal is how the sandbox reaches a host port;"
  echo "      'localhost' inside the sandbox is the sandbox."
  exit 0
fi

if nemoclaw inference set \
      --provider compatible-endpoint \
      --model "$LLM_MODEL" \
      --endpoint-url "http://host.openshell.internal:${LLM_ENDPOINT##*:}" \
      --inference-api openai-completions \
      --credential-env COMPATIBLE_API_KEY \
      --sandbox "${SANDBOX:-my-assistant}" >/dev/null 2>&1; then
  say "set" "$LLM_MODEL @ host.openshell.internal:${LLM_ENDPOINT##*:}"
  nemoclaw inference get 2>&1 | head -4
else
  say "set" "failed — run 'nemoclaw <sandbox> doctor' for the reason"
fi
