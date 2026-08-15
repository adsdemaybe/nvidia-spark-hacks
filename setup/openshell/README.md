# OpenShell / NemoClaw

NVIDIA's sandboxed agent runtime, installed on this box as `openshell` + `nemoclaw`
(sandbox-base v0.0.90, OpenClaw agent v2026.6.10).

## What it is, and what it is not

**Not a training framework.** There is no `train`, `tune`, `dataset`, `eval` or
`experiment` subcommand in either CLI — checked, not assumed. Nothing here fine-tunes a
model.

**What it is:** a sandboxed, always-on agent runtime with

| capability | why this project cares |
|---|---|
| `nemoclaw <name> agent` | one agent turn, non-interactively — the primitive a batch of design runs is built from |
| `snapshot create/restore` | every run starts from an identical state, so two results are comparable |
| pluggable OpenAI-compatible inference | point it at our own vLLM instead of the NVIDIA cloud endpoint |
| sandbox isolation | `freeform` executes model-written build123d; a subprocess is good, a sandbox is better |
| three agent runtimes | `openclaw`, `hermes` (self-improving, learning loop), `langchain-deepagents-code` |

So "iterate training sessions" here means iterating **design and evaluation sessions**:
many reproducible runs of the F1/F2 loop, each from an identical snapshot, each scored by
the deterministic harness rather than by a model's opinion.

## Current state on this box

Run `./configure.sh` for a live check.

**Working:**

- **Gateway: running and Connected** on `https://127.0.0.1:17670`. It had been dead since
  July. It runs from `~/.local/state/nemoclaw/openshell-docker-gateway-17670/`, which also
  holds its PKI.
- **Provider `vllm-local`** created on the gateway (`type: openai`,
  `endpoint=http://host.openshell.internal:8100/v1`).
- **Gateway inference route** set to `vllm-local / qwen3-coder-next`.
- **The 4917-restart crashloop is stopped.** The sandbox cannot fetch a policy without a
  gateway, so it exited and restarted forever.

**Blocked, and it needs a decision that is not mine to make:**

The sandbox rebuild fails its container-DNS preflight — Docker containers on this host
cannot resolve `registry.npmjs.org`, which the agent install needs. The documented fix is
to add a `dns` entry to `/etc/docker/daemon.json` and **restart the Docker daemon**. That
restarts every container on this box: `coder-next-vllm` (the model everything is currently
using) and whatever the CAD session has running. So it is a maintenance-window change, not
a background one.

```bash
# after the daemon restart, and with the model server back up:
NEMOCLAW_SKIP_HOST_DNS_PREFLIGHT=1 nemoclaw my-assistant rebuild --yes
```

`NEMOCLAW_SKIP_HOST_DNS_PREFLIGHT=1` is needed because the *host* preflight also resolves
`integrate.api.nvidia.com`, which is irrelevant to a box doing only local inference. The
container-DNS check is separate and is the one that actually blocks.

### Secondary agents

`--agents <agents.yaml>` declares secondary OpenClaw agents and is **baked into the
sandbox image at onboard time**, so it is downstream of the same blocker. Nothing to
configure until the rebuild succeeds.

Worth separating, because the words collide: the *PCB pipeline's own* agents — parts,
designer, physicist, layout, spec, chief — are unrelated to OpenShell and are working.
A full run exercises all six.

### Things that cost a cycle each

- `NEMOCLAW_GATEWAY_PORT=<n>` does not move the existing gateway; it **registers a second
  one** named `nemoclaw-<n>` and makes it active. The sandbox is recorded against
  `nemoclaw`, so the rebuild then reports "cannot determine the recorded inference
  provider and model" — which sounds like missing config and is really a gateway-name
  mismatch. Remove the duplicate with `openshell gateway remove nemoclaw-<n>`.
- After `nemoclaw onboard` starts a gateway it generates a **fresh PKI**. Client certs
  from a previous gateway then fail with `invalid peer certificate: BadSignature`. Copy
  the matching ones:
  `~/.local/state/nemoclaw/openshell-docker-gateway-<port>/tls/client/{tls.crt,tls.key}`
  and `tls/ca.crt` → `~/.config/openshell/gateways/<name>/mtls/`.
- `openshell inference set` verifies against the provider by default. For a local endpoint
  whose credential is a placeholder, pass `--no-verify` or it tries `api.openai.com` and
  reports an OpenAI auth error.
- `nemoclaw onboard --non-interactive` always selects the NVIDIA cloud provider and
  demands `NVIDIA_INFERENCE_API_KEY`. There is no provider flag. Choosing local inference
  requires the interactive flow.

## Ports

| port | service |
|---|---|
| 8080 | CAD viewer (`cad_api.viewer`) |
| 8100 | vLLM — Qwen3-Coder-Next |
| 8210 | CAD API |
| 8220 | docs RAG |
| 8500 | PCB viewer UI |
| 17670 | OpenShell gateway — **running, Connected** |
| 18790 | `my-assistant` dashboard |
