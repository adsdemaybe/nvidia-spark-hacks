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

Run `./configure.sh` for a live check. As of the last run:

- **Sandbox `my-assistant`** — stopped. It had accumulated **4917 restarts** in a
  crashloop: it cannot fetch its policy without the gateway, so it exits and restarts
  forever, burning CPU. Stopping it preserved the workspace.
- **Gateway** — not running. Registered at `https://127.0.0.1:17670`.
- **Inference route** — `vllm-local / nvidia/Qwen3.6-35B-A3B-NVFP4` at
  `http://host.openshell.internal:8000/v1`. **Both halves are wrong for this box**:
  nothing listens on 8000, and that model is not on disk here. The right values are port
  **8100** and model **`qwen3-coder-next`** (see `../serve_coder_next.sh`).

## The port collision, which is the actual blocker

The gateway was registered on `127.0.0.1:8080`. So is the CAD viewer:

```
python -m cad_api.viewer designs/so101_arm.ir.json --port 8080
```

The viewer had held it for ~4 hours, so the gateway could never bind, so the sandbox could
never fetch a policy, so it crashlooped. `openshell status` reports this as
`tls handshake eof`, which reads like a certificate problem and is really *"something else
is listening here"* — worth knowing, because it sends you to the wrong place entirely.

The registration has been moved to **17670**, the gateway's own default, which is free.

## Finishing the setup

The gateway runs **as a Docker container**, not as a host process — `--local` means "a
local mTLS gateway running in Docker on this machine". The certificates in
`~/.config/openshell/gateways/*/mtls/` are *client* certs (`CN=openshell-client`); there
is no server cert on the host and no gateway image pulled yet. So the gateway cannot be
started by hand, and the supported path is:

```bash
nemoclaw onboard --resume        # brings up the Docker gateway and reconciles config
```

**Not run automatically.** It pulls images and can rebuild the sandbox image, which is a
heavy, visible change to someone else's assistant on a shared machine.

After it is up:

```bash
./configure.sh                   # verifies, then applies the inference route
```

which is equivalent to:

```bash
nemoclaw inference set --provider compatible-endpoint \
    --model qwen3-coder-next \
    --endpoint-url http://host.openshell.internal:8100/v1 \
    --inference-api openai-completions \
    --credential-env COMPATIBLE_API_KEY \
    --sandbox my-assistant
```

Three flag details that cost a cycle each, recorded so they do not cost another:

- `--endpoint-url` is rejected for the `vllm-local` provider; it is only accepted for
  `compatible-endpoint`.
- `--credential-env` for `compatible-endpoint` **must** be `COMPATIBLE_API_KEY`.
- `host.openshell.internal` is how the sandbox reaches a host port. `localhost` inside the
  sandbox is the sandbox.

And one on registration: `openshell gateway add` takes the **endpoint** as its positional
argument, with `--name` for the name. `openshell gateway add nemoclaw …` registers a
gateway whose hostname is literally `nemoclaw`. Plain `--gateway-endpoint https://…`
without `--local` is treated as a *cloud* gateway and opens a browser for OIDC; when that
times out it removes the registration it was adding.

## Ports

| port | service |
|---|---|
| 8080 | CAD viewer (`cad_api.viewer`) |
| 8100 | vLLM — Qwen3-Coder-Next |
| 8210 | CAD API |
| 8220 | docs RAG |
| 8500 | PCB viewer UI |
| 17670 | OpenShell gateway (registered; not yet running) |
| 18790 | `my-assistant` dashboard |
