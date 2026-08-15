#!/usr/bin/env bash
# Preflight for NVIDIA DGX Spark (GB10 Grace Blackwell, sm_121, aarch64, CUDA 13).
#
# Every check prints "PASS <id> <detail>" or "FAIL <id> <detail>" and, on
# failure, a named remedy. Exit code = number of failures.
#
# Design notes, because two of these checks look redundant and are not:
#
#  * B3 does a REAL matmul. `torch.cuda.is_available()` returns True on a torch
#    build that has no sm_121 cubin; you only discover it at the first kernel
#    launch with "no kernel image is available for execution on the device".
#    Never trust is_available() on this machine.
#
#  * B4 compiles a trivial CUDA extension. It is a 3-minute proxy for a 4-hour
#    3DGRUT build. If B4 fails, do not start the big build -- fix the arch list
#    first. The winning TORCH_CUDA_ARCH_LIST is recorded and every downstream
#    build consumes it.
#
#  * C5 renders NVIDIA's OWN NuRec sample, not ours. That isolates "Isaac can
#    render NuRec on aarch64" (closed binary, no fallback) from "our export is
#    valid" (our bug, tractable). Buy that answer first.
#
# Usage:
#   ./preflight.sh                      # all groups
#   ./preflight.sh --group host         # host|recon|sim|contract|all
#   ./preflight.sh --json out.json      # machine-readable, for `spark.sh doctor`
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GROUP="all"
JSON_OUT=""
RESULTS=()
FAILURES=0

# Image names; override to test a candidate tag without editing this file.
RECON_IMAGE="${RECON_IMAGE:-realsim/recon-gpu:dev}"
TOOLS_IMAGE="${TOOLS_IMAGE:-realsim/tools-cpu:dev}"
SIM_IMAGE="${SIM_IMAGE:-nvcr.io/nvidia/isaac-sim:5.1.0}"
WORK_DIR="${WORK_DIR:-/data/work}"
# Candidate arch lists, tried in order. sm_120 PTX JITs cleanly to sm_121 and is
# the reported-working value; PyTorch's arch parser may not accept "12.1" at all.
ARCH_CANDIDATES="${ARCH_CANDIDATES:-12.0 12.0+PTX 12.1 12.0;12.1}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --group) GROUP="$2"; shift 2 ;;
    --json)  JSON_OUT="$2"; shift 2 ;;
    -h|--help) sed -n '2,30p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

# ---------------------------------------------------------------- reporting --
c_pass=$'\033[32m'; c_fail=$'\033[31m'; c_warn=$'\033[33m'; c_dim=$'\033[2m'; c_off=$'\033[0m'
[[ -t 1 ]] || { c_pass=""; c_fail=""; c_warn=""; c_dim=""; c_off=""; }

_json_escape() { printf '%s' "$1" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))' 2>/dev/null || printf '""'; }

pass() { # id, detail
  printf "%sPASS%s %-24s %s\n" "$c_pass" "$c_off" "$1" "${2:-}"
  RESULTS+=("{\"id\":\"$1\",\"status\":\"PASS\",\"detail\":$(_json_escape "${2:-}")}")
}
fail() { # id, detail, remedy
  printf "%sFAIL%s %-24s %s\n" "$c_fail" "$c_off" "$1" "${2:-}"
  printf "     %s-> %s%s\n" "$c_dim" "${3:-no remedy recorded}" "$c_off"
  RESULTS+=("{\"id\":\"$1\",\"status\":\"FAIL\",\"detail\":$(_json_escape "${2:-}"),\"remedy\":$(_json_escape "${3:-}")}")
  FAILURES=$((FAILURES + 1))
}
skip() { # id, why
  printf "%sSKIP%s %-24s %s\n" "$c_warn" "$c_off" "$1" "${2:-}"
  RESULTS+=("{\"id\":\"$1\",\"status\":\"SKIP\",\"detail\":$(_json_escape "${2:-}")}")
}
section() { printf "\n%s== %s ==%s\n" "$c_dim" "$1" "$c_off"; }

have() { command -v "$1" >/dev/null 2>&1; }

# Run a python snippet inside an image. Returns the container's exit code.
in_image() { # image, code
  docker run --rm --gpus all --memory=96g --memory-swap=96g \
    -v "$HERE/probes:/probes:ro" "$1" python -c "$2" 2>&1
}

# ======================================================================= HOST =
group_host() {
  section "A -- host"

  local arch; arch="$(uname -m)"
  [[ "$arch" == "aarch64" ]] \
    && pass A1_ARCH "$arch" \
    || fail A1_ARCH "$arch" "R-WRONG-MACHINE: this is not the Spark. Fatal."

  if ! have nvidia-smi; then
    fail A2_GPU "nvidia-smi missing" "R-NO-DRIVER: DGX OS should ship it. Do not hand-install a driver."
    return
  fi

  local gpu; gpu="$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)"
  [[ "$gpu" == *GB10* ]] \
    && pass A2_GPU "$gpu" \
    || fail A2_GPU "${gpu:-unknown}" "R-WRONG-GPU: expected GB10. Fatal."

  local cc; cc="$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>/dev/null | head -1 | tr -d ' ')"
  if [[ "$cc" == "12.1" ]]; then
    pass A3_COMPUTE_CAP "$cc"
  elif [[ -n "$cc" ]]; then
    # Not fatal: record it. Every downstream arch flag derives from this value.
    fail A3_COMPUTE_CAP "$cc (expected 12.1)" \
      "R-ARCH-DRIFT: set ARCH_CANDIDATES to match and re-run; record into env.json."
  else
    fail A3_COMPUTE_CAP "unreadable" "R-ARCH-DRIFT: nvidia-smi did not report compute_cap."
  fi

  local cuda; cuda="$(nvidia-smi 2>/dev/null | sed -n 's/.*CUDA Version: *\([0-9.]*\).*/\1/p' | head -1)"
  if [[ -n "$cuda" ]] && [[ "${cuda%%.*}" -ge 13 ]]; then
    pass A4_CUDA "$cuda"
  else
    fail A4_CUDA "${cuda:-unknown} (need >=13.0)" \
      "R-CUDA-OLD: use the DGX OS updater ONLY. A hand-installed driver bricks this machine."
  fi

  local drv; drv="$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1)"
  pass A5_DRIVER "$drv (recorded, not gated)"

  local memtot; memtot="$(nvidia-smi --query-gpu=memory.total --format=csv,noheader 2>/dev/null | head -1)"
  local sysmem; sysmem="$(free -g 2>/dev/null | awk '/^Mem:/{print $2}')"
  pass A6_MEMORY "gpu=$memtot system=${sysmem}Gi (unified)"

  if have docker && docker info >/dev/null 2>&1; then
    if docker run --rm --gpus all "$SIM_IMAGE" true >/dev/null 2>&1 \
       || docker run --rm --gpus all nvcr.io/nvidia/cuda:13.0.1-devel-ubuntu24.04 nvidia-smi -L >/dev/null 2>&1; then
      pass A7_DOCKER_GPU "nvidia runtime reaches the GPU"
    else
      fail A7_DOCKER_GPU "container cannot see the GPU" \
        "R-CTK: sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker"
    fi
  else
    fail A7_DOCKER_GPU "docker unavailable" "R-DOCKER: install/start Docker."
  fi

  local freeg; freeg="$(df -BG /var/lib/docker 2>/dev/null | awk 'NR==2{gsub("G","",$4); print $4}')"
  [[ -n "$freeg" && "$freeg" -ge 250 ]] \
    && pass A8_DISK "${freeg}G free" \
    || fail A8_DISK "${freeg:-?}G free (need >=250G)" \
       "R-DISK: Isaac ~50G + NGC PyTorch ~25G + 3DGRUT tree + scans. Move docker root to NVMe or prune."

  grep -q 'nvcr.io' "$HOME/.docker/config.json" 2>/dev/null \
    && pass A9_NGC_LOGIN "nvcr.io credentials present" \
    || fail A9_NGC_LOGIN "no nvcr.io entry" \
       "R-NGC-LOGIN: docker login nvcr.io  (user \$oauthtoken, password = NGC API key)"

  local missing=""
  for t in git rsync ffmpeg python3; do have "$t" || missing="$missing $t"; done
  [[ -z "$missing" ]] \
    && pass A10_BASETOOLS "git rsync ffmpeg python3" \
    || fail A10_BASETOOLS "missing:$missing" "R-BASETOOLS: sudo apt install -y git git-lfs rsync ffmpeg"

  # Unified memory means an unbounded allocation takes the whole box down --
  # including your SSH session. This is not optional here.
  have earlyoom \
    && pass A11_WATCHDOG "earlyoom present" \
    || fail A11_WATCHDOG "earlyoom absent" \
       "R-WATCHDOG: sudo apt install -y earlyoom && sudo systemctl enable --now earlyoom"
}

# ====================================================================== RECON =
group_recon() {
  section "B -- recon-gpu image"

  if ! docker image inspect "$RECON_IMAGE" >/dev/null 2>&1; then
    skip B_ALL "image $RECON_IMAGE not built yet (docker/recon-gpu.Dockerfile)"
    return
  fi

  local out
  out="$(in_image "$RECON_IMAGE" 'import torch;print("OK",torch.__version__,torch.version.cuda)')"
  [[ "$out" == OK* ]] \
    && pass B1_TORCH "$out" \
    || fail B1_TORCH "$out" \
       "R-TORCH: you pip-clobbered NGC torch. Rebuild and strip every torch* pin from requirements first."

  out="$(in_image "$RECON_IMAGE" 'import torch;print("OK",torch.cuda.get_device_capability())')"
  [[ "$out" == OK* ]] \
    && pass B2_DEVICE "$out" \
    || fail B2_DEVICE "$out" "R-NOCUDA: was --gpus all passed?"

  # THE important one. See header.
  out="$(docker run --rm --gpus all -v "$HERE/probes:/probes:ro" "$RECON_IMAGE" \
         python /probes/b3_real_matmul.py 2>&1)"
  [[ "$out" == OK* ]] \
    && pass B3_KERNEL_LAUNCH "$out" \
    || fail B3_KERNEL_LAUNCH "$out" \
       "R-NOKERNEL: torch has no sm_121 cubin. is_available() lied. Rebuild torch or use the NGC container."

  # 3-minute proxy for a 4-hour build.
  out="$(docker run --rm --gpus all -v "$HERE/probes:/probes:ro" \
         -e ARCH_CANDIDATES="$ARCH_CANDIDATES" "$RECON_IMAGE" \
         python /probes/b4_compile_ext.py 2>&1 | tail -3)"
  if [[ "$out" == *OK* ]]; then
    pass B4_CUDA_EXT "$out"
    printf "     %s-> record this TORCH_CUDA_ARCH_LIST into env.json; every build consumes it%s\n" "$c_dim" "$c_off"
  else
    fail B4_CUDA_EXT "$out" \
      "R-EXTBUILD: DO NOT start the 3DGRUT build. Try other ARCH_CANDIDATES first."
  fi

  for probe in \
    "B6_PYCOLMAP:import pycolmap;print('OK',pycolmap.__version__)" \
    "B8_KAOLIN:import kaolin;kaolin.ops.gaussian;kaolin.metrics;print('OK',kaolin.__version__)" \
    "B10_GSPLAT:import gsplat;print('OK',getattr(gsplat,'__version__','?'))" \
    "B15_WARP:import warp;warp.init();print('OK')" ; do
    local id="${probe%%:*}" code="${probe#*:}"
    out="$(in_image "$RECON_IMAGE" "$code")"
    if [[ "$out" == OK* ]]; then
      pass "$id" "$out"
    else
      case "$id" in
        B8_KAOLIN)  fail "$id" "${out##*$'\n'}" "R-KAOLIN: optional. Fall back to r2s.metrics (scipy cKDTree chamfer)." ;;
        B10_GSPLAT) fail "$id" "${out##*$'\n'}" "R-GSPLAT: fallback rung for 3DGRUT. Build from source with the B4 arch list." ;;
        B15_WARP)   skip "$id" "warp absent (nice-to-have, not a gate)" ;;
        *)          fail "$id" "${out##*$'\n'}" "R-PYCOLMAP: no aarch64 wheel exists. Source build, or apt colmap + CLI driver." ;;
      esac
    fi
  done

  if docker run --rm "$RECON_IMAGE" colmap -h >/dev/null 2>&1; then
    local sift; sift="$(docker run --rm "$RECON_IMAGE" colmap feature_extractor --help 2>&1 | grep -ci 'use_gpu' || true)"
    pass B7_COLMAP "binary present (gpu_sift_flag=$sift)"
  else
    fail B7_COLMAP "colmap binary missing" \
      "R-COLMAP: apt install colmap, or build with -DCMAKE_CUDA_ARCHITECTURES from B4. CPU SIFT is acceptable (~10x slower)."
  fi
}

# ======================================================================== SIM =
group_sim() {
  section "C -- Isaac Sim (the answer that changes the architecture)"

  if ! docker image inspect "$SIM_IMAGE" >/dev/null 2>&1; then
    skip C_ALL "image $SIM_IMAGE not pulled (docker pull $SIM_IMAGE)"
    return
  fi

  local out
  out="$(docker run --rm --gpus all -e ACCEPT_EULA=Y -e PRIVACY_CONSENT=Y \
         "$SIM_IMAGE" bash -lc 'echo OK' 2>&1 | tail -1)"
  [[ "$out" == OK ]] \
    && pass C1_START "container starts" \
    || fail C1_START "$out" "R-ISAAC-START: check UID mapping (5.1 runs non-root) and --network=host."

  out="$(docker run --rm --gpus all -e ACCEPT_EULA=Y -e PRIVACY_CONSENT=Y \
         -e LD_PRELOAD=/lib/aarch64-linux-gnu/libgomp.so.1 "$SIM_IMAGE" \
         bash -lc '${ISAACSIM_PATH:-/isaac-sim}/python.sh -c "import omni.usd,pxr;print(\"OK\",pxr.Usd.GetVersion())"' 2>&1 | tail -1)"
  [[ "$out" == OK* ]] \
    && pass C2_KIT_PYTHON "$out" \
    || fail C2_KIT_PYTHON "$out" \
       "R-KITPY: LD_PRELOAD=/lib/aarch64-linux-gnu/libgomp.so.1 is the documented aarch64 OpenMP workaround."

  # C4/C5 assert on PIXELS, not exit codes. A booted Kit that renders black
  # passes every exit-code check and is the classic headless failure.
  if [[ -f "$HERE/probes/c4_render_cube.py" ]]; then
    out="$(docker run --rm --gpus all -e ACCEPT_EULA=Y -e PRIVACY_CONSENT=Y \
           -e LD_PRELOAD=/lib/aarch64-linux-gnu/libgomp.so.1 \
           -v "$HERE/probes:/probes:ro" -v "$WORK_DIR:/work" "$SIM_IMAGE" \
           bash -lc '${ISAACSIM_PATH:-/isaac-sim}/python.sh /probes/c4_render_cube.py' 2>&1 | tail -1)"
    [[ "$out" == OK* ]] \
      && pass C4_RTX_RENDER "$out" \
      || fail C4_RTX_RENDER "$out" \
         "R-RTXRENDER: Kit booted but RTX did not rasterize. Capture the full Kit log before going further."
  else
    skip C4_RTX_RENDER "probe not present"
  fi

  if [[ -n "${NUREC_SAMPLE:-}" ]]; then
    out="$(docker run --rm --gpus all -e ACCEPT_EULA=Y -e PRIVACY_CONSENT=Y \
           -e LD_PRELOAD=/lib/aarch64-linux-gnu/libgomp.so.1 \
           -e NUREC_SAMPLE="$NUREC_SAMPLE" \
           -v "$HERE/probes:/probes:ro" -v "$WORK_DIR:/work" "$SIM_IMAGE" \
           bash -lc '${ISAACSIM_PATH:-/isaac-sim}/python.sh /probes/c5_render_nurec.py' 2>&1 | tail -1)"
    [[ "$out" == OK* ]] \
      && pass C5_NUREC_RENDER "$out" \
      || fail C5_NUREC_RENDER "$out" \
         "R-NUREC: THE make-or-break result. If NVIDIA's own sample will not render, the architecture changes: USD-only validation via pxr, visual checks through 3DGRUT's playground."
  else
    skip C5_NUREC_RENDER "set NUREC_SAMPLE=/path/to/nvidia_sample.usdz (HuggingFace NVIDIA NuRec dataset)"
  fi
}

# =================================================================== CONTRACT =
group_contract() {
  section "D -- cross-container contract"

  # Isaac Sim 5.1 containers run non-root by default. A UID mismatch on the
  # shared work mount breaks the pipeline at 2am with a permissions error.
  if [[ -d "$WORK_DIR" ]] && have docker; then
    local probe="$WORK_DIR/.preflight_uid_probe"
    if docker run --rm -v "$WORK_DIR:/work" "$TOOLS_IMAGE" \
         bash -lc 'echo tools > /work/.preflight_uid_probe' >/dev/null 2>&1 \
       && docker run --rm -u "$(id -u):$(id -g)" -v "$WORK_DIR:/work" "$SIM_IMAGE" \
         bash -lc 'echo sim >> /work/.preflight_uid_probe' >/dev/null 2>&1; then
      pass D1_UID_SHARING "both images can write $WORK_DIR"
      rm -f "$probe" 2>/dev/null || true
    else
      fail D1_UID_SHARING "cross-image write failed" \
        "R-UID: share a group and use -u \$(id -u):\$(id -g), or chmod g+rws $WORK_DIR"
    fi
  else
    skip D1_UID_SHARING "$WORK_DIR absent or docker unavailable"
  fi

  if docker image inspect "$TOOLS_IMAGE" >/dev/null 2>&1; then
    local out
    out="$(docker run --rm -v "$HERE/..:/repo:ro" "$TOOLS_IMAGE" \
           bash -lc 'cd /repo && python -c "import r2s.core; print(\"OK\", len(r2s.core.__all__))"' 2>&1 | tail -1)"
    [[ "$out" == OK* ]] \
      && pass D2_CORE_IMPORT "$out" \
      || fail D2_CORE_IMPORT "$out" "R-CORE: r2s-core must import with no GPU deps. This is a packaging bug."
  else
    skip D2_CORE_IMPORT "image $TOOLS_IMAGE not built"
  fi
}

# ======================================================================= main =
case "$GROUP" in
  host)     group_host ;;
  recon)    group_recon ;;
  sim)      group_sim ;;
  contract) group_contract ;;
  all)      group_host; group_recon; group_sim; group_contract ;;
  *) echo "unknown group: $GROUP (host|recon|sim|contract|all)" >&2; exit 2 ;;
esac

section "summary"
if [[ $FAILURES -eq 0 ]]; then
  printf "%sall checks passed%s\n" "$c_pass" "$c_off"
else
  printf "%s%d failure(s)%s -- fix in the order printed; earlier groups gate later ones\n" \
    "$c_fail" "$FAILURES" "$c_off"
fi

if [[ -n "$JSON_OUT" ]]; then
  { printf '{"group":"%s","failures":%d,"host":"%s","results":[' "$GROUP" "$FAILURES" "$(hostname)"
    printf '%s' "$(IFS=,; echo "${RESULTS[*]}")"
    printf ']}\n'
  } > "$JSON_OUT"
  printf "wrote %s\n" "$JSON_OUT"
fi

exit "$FAILURES"
