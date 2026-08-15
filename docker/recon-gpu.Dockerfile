# recon-gpu -- everything that needs CUDA on GB10 (sm_121, aarch64, CUDA 13).
#
# Base is the NGC PyTorch container, NOT pip torch. NVIDIA's own guidance for
# DGX Spark: pip's generic wheels are built against CUDA 12.x and die with
# "ImportError: libcudart.so.12". The NGC image carries a Blackwell-optimized
# torch build for this exact machine.
#
# HARD RULE: never `pip install torch` (or anything that pins torch) into this
# image. That clobbers the NGC build and is the single most common way people
# break their Spark. Every requirements file installed here must have torch*
# pins stripped first.
#
# Pin the tag once preflight B1-B4 pass against it, then `docker save` the
# result -- on this platform a working image is a research artifact you may not
# be able to reproduce from a version pin.
ARG NGC_PYTORCH_TAG=25.11-py3
FROM nvcr.io/nvidia/pytorch:${NGC_PYTORCH_TAG}

# Filled in by build-arg from preflight B4's winning value. Do not guess it:
# sm_120 PTX JITs cleanly to sm_121, and PyTorch's arch parser may reject "12.1"
# outright even though CUDA 13's nvcc accepts the target.
ARG TORCH_CUDA_ARCH_LIST=12.0
ENV TORCH_CUDA_ARCH_LIST=${TORCH_CUDA_ARCH_LIST}
# 20 Arm cores running nvcc concurrently will exhaust 128GB of unified memory
# and take the whole machine down, SSH session included.
ARG MAX_JOBS=8
ENV MAX_JOBS=${MAX_JOBS}
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
      git git-lfs cmake ninja-build build-essential \
      ffmpeg libgl1 libglib2.0-0 \
      libboost-program-options-dev libboost-graph-dev libboost-system-dev \
      libeigen3-dev libflann-dev libfreeimage-dev libmetis-dev \
      libgoogle-glog-dev libgflags-dev libsqlite3-dev libglew-dev \
      libceres-dev qtbase5-dev libqt5opengl5-dev \
    && rm -rf /var/lib/apt/lists/*

# --- COLMAP -----------------------------------------------------------------
# No aarch64 Linux wheel for pycolmap exists, so this is a source build.
# CUDA SIFT is a nice-to-have: CPU SIFT is ~10x slower and entirely acceptable
# at a few hundred frames. If the CUDA build fights back, set -DCUDA_ENABLED=OFF
# and move on -- SfM is not where this project's risk lives.
ARG COLMAP_REF=3.11.1
ARG COLMAP_CUDA=ON
RUN git clone --depth 1 --branch "${COLMAP_REF}" https://github.com/colmap/colmap.git /tmp/colmap \
    && cmake -S /tmp/colmap -B /tmp/colmap/build -GNinja \
         -DCMAKE_BUILD_TYPE=Release \
         -DCUDA_ENABLED=${COLMAP_CUDA} \
         -DCMAKE_CUDA_ARCHITECTURES=120 \
    && cmake --build /tmp/colmap/build --target install -j "${MAX_JOBS}" \
    && rm -rf /tmp/colmap

# --- python deps (NO torch pins -- see HARD RULE above) ----------------------
RUN pip install --no-cache-dir \
      "pydantic>=2.7" "fsspec>=2024.6" "typer>=0.12" "rich>=13.7" "structlog>=24.1" \
      "trimesh>=4.4" "scipy>=1.13" "shapely>=2.0" "plyfile>=1.0" "pillow>=10.3" \
      "opencv-python-headless>=4.9" "imageio-ffmpeg>=0.5" "usd-core>=24.5" \
      "transformers>=4.44" "pyarrow>=16.0"

# 3DGRUT is installed at run time from a mounted checkout rather than baked in:
# it is the highest-churn dependency and rebuilding a 40GB image to bump it is
# not a reasonable iteration loop. See spark/gate5_3dgrut.sh.
ENV THREEDGRUT_ROOT=/opt/3dgrut

WORKDIR /repo
COPY packages /repo/packages
COPY pyproject.toml /repo/
RUN pip install --no-cache-dir --no-deps \
      -e packages/r2s-core -e packages/scan -e packages/envgen -e packages/r2s-cli

CMD ["bash"]
