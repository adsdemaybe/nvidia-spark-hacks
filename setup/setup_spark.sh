#!/usr/bin/env bash
# ============================================================================
# STRUCT — DGX Spark setup (run ON the Spark)
#
#   ./setup_spark.sh                 # base setup
#   ./setup_spark.sh --with-model    # + download Laguna S 2.1 NVFP4 (~70 GB)
#
# Target: DGX OS (Ubuntu 24.04 base), GB10 Grace Blackwell, aarch64.
# Idempotent: safe to re-run; each section skips what already exists.
#
# Relationship to realsim/spark/preflight.sh: this script INSTALLS; preflight
# VERIFIES. Run preflight after this completes — it does the checks this
# script cannot (real CUDA kernel launch, CUDA-extension compile probe, Isaac
# NuRec pixel-variance render).
# ============================================================================
set -uo pipefail

WITH_MODEL=0
[[ "${1:-}" == "--with-model" ]] && WITH_MODEL=1

STRUCT_HOME="${STRUCT_HOME:-$HOME/struct}"
VENV="$STRUCT_HOME/.venv"
HF_MODEL="poolside/Laguna-S-2.1-NVFP4"
PASS=(); FAIL=(); WARN=()

log()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
ok()   { PASS+=("$1"); printf '   \033[1;32m✔ %s\033[0m\n' "$1"; }
bad()  { FAIL+=("$1"); printf '   \033[1;31m✘ %s\033[0m\n' "$1"; }
warn() { WARN+=("$1"); printf '   \033[1;33m⚠ %s\033[0m\n' "$1"; }

# ---------------------------------------------------------------- sanity ----
log "Sanity checks"
[[ "$(uname -m)" == "aarch64" ]] && ok "aarch64" || bad "not aarch64 — this is not the Spark"
command -v nvidia-smi >/dev/null && nvidia-smi -L | head -1 && ok "NVIDIA driver" \
  || bad "nvidia-smi missing — fix the driver before anything else"

# ------------------------------------------------------------ apt layer -----
log "APT packages"
sudo apt-get update -y
sudo apt-get install -y --no-install-recommends \
  build-essential cmake ninja-build pkg-config git git-lfs curl wget unzip \
  openssh-server mosh tmux htop jq \
  colmap ngspice kicad \
  libgl1 libglib2.0-0 ffmpeg \
  mkcert libnss3-tools \
  postgresql-client \
  && ok "apt packages" || bad "apt packages"

# Java 25 (Freerouting) — Adoptium Temurin
if ! java -version 2>&1 | grep -q 'version "25'; then
  log "Temurin JDK 25"
  sudo mkdir -p /etc/apt/keyrings
  wget -qO- https://packages.adoptium.net/artifactory/api/gpg/key/public \
    | sudo tee /etc/apt/keyrings/adoptium.asc >/dev/null
  echo "deb [signed-by=/etc/apt/keyrings/adoptium.asc] https://packages.adoptium.net/artifactory/deb $(lsb_release -cs) main" \
    | sudo tee /etc/apt/sources.list.d/adoptium.list >/dev/null
  sudo apt-get update -y && sudo apt-get install -y temurin-25-jdk \
    && ok "Temurin 25" || warn "Temurin 25 install failed — Freerouting fallback unavailable"
else ok "Java 25 present"; fi

# ------------------------------------------------------------- docker -------
log "Docker + NVIDIA container toolkit"
if command -v docker >/dev/null; then ok "docker present"
else curl -fsSL https://get.docker.com | sudo sh && sudo usermod -aG docker "$USER" \
  && ok "docker installed (re-login for group)" || bad "docker install"; fi
docker info 2>/dev/null | grep -qi nvidia && ok "nvidia runtime" \
  || warn "nvidia container runtime not visible — DGX OS usually ships it; check nvidia-ctk"

# --------------------------------------------------------------- uv/py ------
log "Python (uv) + Struct venv"
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
mkdir -p "$STRUCT_HOME"
[[ -d "$VENV" ]] || uv venv --python 3.11 "$VENV"
# shellcheck disable=SC1091
source "$VENV/bin/activate"

log "Python stack (torch CUDA aarch64, sim, splat, robot)"
uv pip install --upgrade pip
uv pip install torch torchvision                                  && ok "torch"        || bad "torch"
python - <<'PY' && ok "torch sees GPU" || bad "torch CUDA"
import torch; assert torch.cuda.is_available()
PY
uv pip install mujoco pin trimesh coacd                           && ok "mujoco/pinocchio/coacd" || bad "sim deps"
uv pip install nerfstudio                                         && ok "nerfstudio"   || warn "nerfstudio (check aarch64 deps)"
uv pip install git+https://github.com/nerfstudio-project/gsplat   && ok "gsplat (built)" || warn "gsplat build failed — retry with CUDA_HOME set"
uv pip install "lerobot[smolvla]" || uv pip install lerobot       && ok "lerobot"      || bad "lerobot"
uv pip install vllm                                               && ok "vllm"         || warn "vllm pip failed — use NVIDIA's DGX Spark vLLM container playbook instead"
uv pip install fastapi uvicorn dramatiq[redis] langgraph langchain-core \
  pydantic psycopg[binary] pgvector minio pyarrow rerun-sdk \
  build123d huggingface_hub anthropic                             && ok "struct core py" || bad "struct core py"

# --------------------------------------------------------------- node -------
log "Node 22 + pnpm + agent CLIs"
if ! command -v node >/dev/null || [[ "$(node -v | cut -c2-3)" -lt 22 ]]; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt-get install -y nodejs
fi
sudo corepack enable 2>/dev/null || sudo npm i -g pnpm
node -v && ok "node $(node -v)" || bad "node"
sudo npm i -g @anthropic-ai/claude-code @tscircuit/cli \
  && ok "claude-code + tscircuit CLIs" || warn "global npm CLIs"

# ------------------------------------------------------- compose stack ------
log "Compose stack (postgres+pgvector, redis, minio)"
if [[ ! -f "$STRUCT_HOME/compose.yaml" ]]; then
cat > "$STRUCT_HOME/compose.yaml" <<'YAML'
services:
  db:
    image: pgvector/pgvector:pg16
    environment: { POSTGRES_USER: struct, POSTGRES_PASSWORD: struct, POSTGRES_DB: struct }
    ports: ["5432:5432"]
    volumes: [dbdata:/var/lib/postgresql/data]
  redis:
    image: redis:7
    ports: ["6379:6379"]
  minio:
    image: minio/minio
    command: server /data --console-address ":9001"
    environment: { MINIO_ROOT_USER: struct, MINIO_ROOT_PASSWORD: structstruct }
    ports: ["9000:9000", "9001:9001"]
    volumes: [miniodata:/data]
volumes: { dbdata: {}, miniodata: {} }
YAML
fi
(cd "$STRUCT_HOME" && docker compose up -d) && ok "compose up" || warn "compose (docker group? re-login)"
sleep 3
docker exec "$(docker ps -qf name=db)" psql -U struct -c 'CREATE EXTENSION IF NOT EXISTS vector;' \
  && ok "pgvector extension" || warn "pgvector extension (db still starting? re-run)"

# ------------------------------------------------------------- Isaac --------
log "Isaac Sim / Isaac Lab (NVIDIA DGX Spark playbooks)"
if [[ ! -d "$STRUCT_HOME/dgx-spark-playbooks" ]]; then
  git clone --depth 1 https://github.com/NVIDIA/dgx-spark-playbooks "$STRUCT_HOME/dgx-spark-playbooks" \
    && ok "playbooks cloned — run nvidia/isaac per its README (interactive, ~30 min)" \
    || warn "playbooks clone failed"
else ok "playbooks present"; fi

# ---------------------------------------------------------- Laguna model ----
if [[ $WITH_MODEL -eq 1 ]]; then
  log "Downloading $HF_MODEL (~70 GB — this is the long pole)"
  "$VENV/bin/hf" download "$HF_MODEL" && ok "Laguna weights cached" || bad "Laguna download"
else
  warn "Laguna weights NOT downloaded (re-run with --with-model; needs ~70 GB free)"
fi

# ------------------------------------------------------------- SSH ----------
log "SSH for the team + cmux"
sudo systemctl enable --now ssh && ok "sshd running" || bad "sshd"
mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh" && touch "$HOME/.ssh/authorized_keys" && chmod 600 "$HOME/.ssh/authorized_keys"
# Harden lightly + keep sessions alive through demos:
sudo tee /etc/ssh/sshd_config.d/struct.conf >/dev/null <<'CONF'
PasswordAuthentication no
ClientAliveInterval 30
ClientAliveCountMax 240
CONF
sudo systemctl reload ssh
if command -v ufw >/dev/null && sudo ufw status | grep -q active; then
  sudo ufw allow 22/tcp; sudo ufw allow 60000:61000/udp   # mosh
fi
ok "key-only auth; mosh UDP range open (if ufw active)"
echo "   Add each teammate:  echo '<their-pubkey>' >> ~/.ssh/authorized_keys"
echo "   Spark addresses:    $(hostname -I 2>/dev/null || true)"

# ----------------------------------------------------------- summary --------
log "PREFLIGHT SUMMARY"
printf '   PASS %d: %s\n' "${#PASS[@]}" "${PASS[*]:-—}"
printf '   WARN %d: %s\n' "${#WARN[@]}" "${WARN[*]:-—}"
printf '   FAIL %d: %s\n' "${#FAIL[@]}" "${FAIL[*]:-—}"
echo
echo "Next: 1) teammates run setup_mac_cmux.sh on their Macs"
echo "      2) ./serve_laguna.sh   (starts the local model for the agent fleet)"
echo "      3) run the Isaac playbook in $STRUCT_HOME/dgx-spark-playbooks/nvidia/isaac"
echo "      4) cd <repo>/realsim && bash spark/preflight.sh --group host"
[[ ${#FAIL[@]} -eq 0 ]] || exit 1
