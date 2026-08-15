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

**Blocked on one thing, and it is a hard stop:**

`nemoclaw onboard` step 3 (*Configuring inference provider*) **always selects the NVIDIA
cloud provider in non-interactive mode** and requires a real `NVIDIA_INFERENCE_API_KEY`.
There is no provider flag, and the key is *validated* — a placeholder returns HTTP 403 and
the step fails. Choosing local vLLM is only reachable through the interactive prompt.

So finishing this needs one of:

```bash
# a) run it interactively and pick the local vLLM option
NEMOCLAW_SKIP_HOST_DNS_PREFLIGHT=1 NEMOCLAW_GATEWAY_PORT=17670 \
  nemoclaw onboard --resume --name my-assistant

# b) or supply a real key, then repoint inference at the local model afterwards
export NVIDIA_INFERENCE_API_KEY=nvapi-...
```

**The sandbox container no longer exists.** `rebuild --force` destroyed it and the
recreate then failed, because the rebuild resets the gateway port to the default 8080,
which the CAD viewer holds. Its state was backed up first, by hand, because nemoclaw's own
backup could not read a container that would not stay running:

```
/home/acer01/nemoclaw-sandbox-backup-<timestamp>/   # 18 MB
    sandbox/          # /sandbox — .openclaw, .nemoclaw, workspace
    .openclaw/state/  # /root/.openclaw
```

### Why it was blocked, in order

Worth recording because almost every layer reported a symptom rather than the cause:

1. **Stale certificates.** The container's TLS binds pointed at
   `~/.local/state/nemoclaw/openshell-docker-gateway/tls/` — the *old* gateway — while the
   running one is `openshell-docker-gateway-17670/` with a fresh CA. It could not
   authenticate, could not fetch a policy, exited, restarted, forever.
2. Fixing that needs a rebuild, which recreates the container with correct binds.
3. The rebuild preflight requires **gateway route == sandbox record**, and they differed on
   model (`qwen3-coder-next` vs `nvidia/Qwen3.6-35B-A3B-NVFP4`).
4. Updating that record requires reading `/sandbox/.openclaw/openclaw.json` **from a
   running sandbox** — the thing that is broken. A genuine deadlock. Break it from the
   other side: point the gateway route at the *recorded* model, rebuild, fix it after.
5. `nemoclaw`'s `vllm-local` provider has **port 8000 hardcoded** and `--no-verify` does
   not skip that probe. `port_forward_8000.py` answers it without moving anything.
6. Then step 3, above, which no amount of configuration gets past.

A check that reported a *transient* failure once and passed on every retry afterwards:
"DNS resolution from inside a docker container failed". It is not the blocker. Both the
default and `openshell-docker` bridges resolve `registry.npmjs.org` fine.

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
