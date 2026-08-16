#!/usr/bin/env bash
# setup_runtime.sh — the STRUCT backend runtime (everything except the LLM and the
# frontend, which have their own scripts). Brings up infra AND the backend services on
# the GB10. Idempotent: a service is started only if its port is not already answering.
#
#   infra    postgres:5432  redis:6379  minio:9000/9001   (docker compose)
#   :8210    CAD API     (cad-generation/api, cad_api.service)
#   :8220    docs RAG    (rag/, docsrag.server)
#
# The LLM is setup/serve_llm.sh (:8100). The console/frontend is setup/setup_frontend.sh
# (:8600). Run all (or setup/quickstart.sh) for the full stack.
#
# Env: REPO (auto)  STRUCT_HOME (~/struct)  CADAPI_PORT (8210)  RAG_PORT (8220)
set -uo pipefail

REPO="${REPO:-$(cd "$(dirname "$0")/.." && pwd)}"
STRUCT_HOME="${STRUCT_HOME:-$HOME/struct}"
export PATH="$HOME/.local/bin:$PATH"
LOGDIR="$REPO/setup/logs"; mkdir -p "$LOGDIR"
ENGINE_PY="$REPO/cad-generation/engine/.venv/bin/python"
CADAPI_PORT="${CADAPI_PORT:-8210}"
RAG_PORT="${RAG_PORT:-8220}"

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
up()   { curl -sf -m 3 "http://127.0.0.1:$1/" >/dev/null 2>&1 || \
         ss -ltn 2>/dev/null | grep -q ":$1 "; }

# --- infra: postgres + redis + minio via compose ----------------------------
say "starting shared infra (postgres, redis, minio)"
if ss -ltn 2>/dev/null | grep -q ":5432 " && ss -ltn 2>/dev/null | grep -q ":6379 "; then
  echo "   ✔ infra already up (5432, 6379)"
elif [ -f "$STRUCT_HOME/compose.yaml" ]; then
  ( cd "$STRUCT_HOME" && docker compose up -d ) && echo "   ✔ compose up ($STRUCT_HOME)" \
    || echo "   ✘ compose failed — check docker group / re-login"
else
  echo "   ✘ no $STRUCT_HOME/compose.yaml — run setup/setup_spark.sh first (creates it)"
fi
for pair in "5432 postgres" "6379 redis" "9000 minio"; do
  set -- $pair
  for _ in $(seq 1 20); do ss -ltn 2>/dev/null | grep -q ":$1 " && break; sleep 1; done
  ss -ltn 2>/dev/null | grep -q ":$1 " && echo "   ✔ $2 (:$1)" || echo "   ⚠ $2 (:$1) not listening yet"
done

[ -x "$ENGINE_PY" ] || { echo "engine venv missing: $ENGINE_PY — run setup/setup_spark.sh first" >&2; exit 1; }

# --- CAD API :8210 ----------------------------------------------------------
if up "$CADAPI_PORT"; then
  say "CAD API already up on :$CADAPI_PORT"
else
  say "starting CAD API on :$CADAPI_PORT"
  ( cd "$REPO/cad-generation/api" && \
    nohup "$ENGINE_PY" -m uvicorn cad_api.service:app --host 0.0.0.0 --port "$CADAPI_PORT" \
      > "$LOGDIR/cad-api.log" 2>&1 & disown )
fi

# --- docs RAG :8220 ---------------------------------------------------------
if up "$RAG_PORT"; then
  say "docs RAG already up on :$RAG_PORT"
else
  say "starting docs RAG on :$RAG_PORT"
  ( cd "$REPO/rag" && PYTHONPATH=src \
    nohup "$ENGINE_PY" -m uvicorn docsrag.server:app --host 0.0.0.0 --port "$RAG_PORT" \
      > "$LOGDIR/rag.log" 2>&1 & disown )
fi

say "waiting for backend ports"
ok=1
for svc in "CAD API:$CADAPI_PORT" "docs RAG:$RAG_PORT"; do
  name=${svc%:*}; port=${svc##*:}
  for _ in $(seq 1 30); do up "$port" && break; sleep 1; done
  if up "$port"; then echo "   ✔ $name :$port"; else echo "   ✘ $name :$port (tail $LOGDIR/)"; ok=0; fi
done
[ "$ok" = 1 ] && say "runtime up" || { echo "some services failed — check $LOGDIR/*.log" >&2; exit 1; }
