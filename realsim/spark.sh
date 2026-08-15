#!/usr/bin/env bash
# Remote harness for the DGX Spark. Deliberately boring: ssh + git + rsync +
# docker. No k8s, no queue, no agent.
#
# Two rules that are load-bearing:
#
#  * Code travels by GIT ONLY, checked out at an explicit SHA. Never rsync
#    source. Drift between your laptop and the Spark is a class of bug you
#    cannot afford at these feedback latencies, and env.json records exactly
#    what ran.
#
#  * Long jobs are DETACHED CONTAINERS, not tmux or nohup. The container is
#    already a job runner: it survives disconnect, has a name, an exit code, and
#    log capture. tmux stays in the toolbox for interactive debugging only.
#
# Usage:
#   ./spark.sh doctor                    # preflight + diff vs last known-good
#   ./spark.sh push                      # git push, then checkout that SHA remotely
#   ./spark.sh build recon-gpu|tools-cpu
#   ./spark.sh run <stage> <run_id> [image]
#   ./spark.sh logs <run_id> <stage>
#   ./spark.sh wait <run_id> <stage>
#   ./spark.sh pull <run_id> [--full]
#   ./spark.sh shell [image]
set -euo pipefail

HOST="${SPARK_HOST:-spark}"           # define this in ~/.ssh/config
REMOTE_REPO="${SPARK_REPO:-\$HOME/work/realsim}"
WORK_DIR="${SPARK_WORK:-/data/work}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

RECON_IMAGE="realsim/recon-gpu:dev"
TOOLS_IMAGE="realsim/tools-cpu:dev"

# ControlMaster turns a 20-command debugging session into one handshake.
SSH_OPTS=(-o ControlMaster=auto -o ControlPath="$HOME/.ssh/cm-%r@%h:%p"
          -o ControlPersist=10m -o ServerAliveInterval=30)

rsh() { ssh "${SSH_OPTS[@]}" "$HOST" "$@"; }
die() { echo "error: $*" >&2; exit 1; }

cmd="${1:-}"; shift || true
case "$cmd" in

doctor)
  # Re-run preflight as JSON and diff against last known-good. On a machine
  # where a background OS update can move the driver, "what changed since it
  # worked" is the question you ask most often.
  rsh "cd $REMOTE_REPO && ./spark/preflight.sh --json /tmp/preflight.json ${*:-}" || true
  mkdir -p "$HERE/spark/state"
  scp "${SSH_OPTS[@]}" "$HOST:/tmp/preflight.json" "$HERE/spark/state/preflight.latest.json" >/dev/null
  known="$HERE/spark/state/preflight.known-good.json"
  if [[ -f "$known" ]]; then
    echo; echo "--- change since last known-good ---"
    python3 - "$known" "$HERE/spark/state/preflight.latest.json" <<'PY'
import json, sys
old = {r["id"]: r["status"] for r in json.load(open(sys.argv[1]))["results"]}
new = {r["id"]: r["status"] for r in json.load(open(sys.argv[2]))["results"]}
delta = [(k, old.get(k, "-"), v) for k, v in new.items() if old.get(k, "-") != v]
if not delta:
    print("  no change")
for k, o, n in delta:
    arrow = "REGRESSED" if n == "FAIL" else ("recovered" if o == "FAIL" else "changed")
    print(f"  {arrow:>9}  {k}: {o} -> {n}")
PY
  else
    echo; echo "no known-good baseline yet."
    echo "once preflight is clean:  cp spark/state/preflight.latest.json spark/state/preflight.known-good.json"
  fi
  ;;

push)
  git -C "$HERE" push
  sha="$(git -C "$HERE" rev-parse HEAD)"
  [[ -z "$(git -C "$HERE" status --porcelain)" ]] || echo "warn: working tree dirty; pushing committed SHA only"
  rsh "set -e; mkdir -p $REMOTE_REPO && cd $REMOTE_REPO && \
       (git rev-parse --git-dir >/dev/null 2>&1 || git clone $(git -C "$HERE" remote get-url origin) .) && \
       git fetch --all -q && git checkout -f $sha -q && git rev-parse --short HEAD"
  echo "spark now at $sha"
  ;;

build)
  img="${1:-recon-gpu}"; shift || true
  case "$img" in
    recon-gpu) tag="$RECON_IMAGE" ;;
    tools-cpu) tag="$TOOLS_IMAGE" ;;
    *) die "unknown image: $img (recon-gpu|tools-cpu)" ;;
  esac
  # ARCH comes from preflight B4's winning value; do not hardcode it elsewhere.
  arch="$(rsh "cd $REMOTE_REPO && python3 -c \"import json;print(json.load(open('spark/state/env.json'))['torch_cuda_arch_list'])\" 2>/dev/null" || echo "12.0")"
  echo "building $tag with TORCH_CUDA_ARCH_LIST=$arch"
  rsh "cd $REMOTE_REPO && DOCKER_BUILDKIT=1 docker buildx build \
         --platform linux/arm64 \
         --build-arg TORCH_CUDA_ARCH_LIST=$arch \
         --cache-to type=local,dest=.buildcache,mode=max \
         --cache-from type=local,src=.buildcache \
         -f docker/$img.Dockerfile -t $tag . $*"
  echo
  echo "if this image works, archive it NOW -- you may not be able to rebuild it:"
  echo "  ssh $HOST 'docker save $tag | zstd -o /data/images/$img-\$(date +%F).tar.zst'"
  ;;

run)
  stage="${1:?stage}"; run_id="${2:?run_id}"; image="${3:-$RECON_IMAGE}"
  name="job-${run_id}-${stage}"
  rsh "docker rm -f $name >/dev/null 2>&1 || true
       docker run -d --name $name --gpus all \
         --memory=96g --memory-swap=96g \
         -v $WORK_DIR:/work -v $REMOTE_REPO:/repo \
         -w /repo $image \
         python -m r2s.cli.main $stage --run /work/runs/$run_id"
  echo "started $name  ->  ./spark.sh logs $run_id $stage"
  ;;

logs)  rsh "docker logs -f job-${1:?run_id}-${2:?stage}" ;;
wait)  rsh "docker wait job-${1:?run_id}-${2:?stage}" ;;

pull)
  run_id="${1:?run_id}"; shift || true
  dest="$HERE/.r2s/runs/$run_id"; mkdir -p "$dest"
  if [[ "${1:-}" == "--full" ]]; then
    # Everything, including checkpoints and USDZ. Gigabytes.
    rsync -az --info=progress2 -e "ssh ${SSH_OPTS[*]}" "$HOST:$WORK_DIR/runs/$run_id/" "$dest/"
  else
    # The one you use 50 times a day: reports, thumbnails, USD text, logs.
    # Every stage must write thumbs/*.png so results are reviewable without
    # moving gigabytes over SSH.
    rsync -az --info=progress2 -e "ssh ${SSH_OPTS[*]}" \
      --include='*/' --include='*.json' --include='*.usda' --include='*.xml' \
      --include='thumbs/***' --include='logs/***' --exclude='*' \
      "$HOST:$WORK_DIR/runs/$run_id/" "$dest/"
  fi
  echo "-> $dest"
  ;;

shell) rsh -t "docker run --rm -it --gpus all -v $WORK_DIR:/work -v $REMOTE_REPO:/repo -w /repo ${1:-$RECON_IMAGE} bash" ;;

*) sed -n '/^# Usage:/,/^set -euo/p' "${BASH_SOURCE[0]}" | sed '$d'; exit 2 ;;
esac
